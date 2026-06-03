"""Tests for the GridLang Chart/Format DSL preprocessor and renderer integration."""

from __future__ import annotations

import pandas as pd
import pytest

from gridlang.chart_dsl import (
    preprocess,
    FormatDirective,
    CHART_TYPE_TO_FUNC,
    _emit_value,
    _col_letters_to_index,
    _coerce_scalar,
    _split_top_level_commas,
)
from gridlang.renderer import render
from gridlang.runtime import ConditionalFormat


# ─── Block parsing ────────────────────────────────────────────────────────

class TestBlockParsing:
    def test_chart_bar_simple(self):
        src = (
            "chart: bar\n"
            "  data: agg.values\n"
            "  labels: agg.labels\n"
            "  title: \"My Bar\"\n"
        )
        r = preprocess(src)
        assert "bar_chart(" in r.template
        assert "agg.values" in r.template
        assert "agg.labels" in r.template
        assert 'title="My Bar"' in r.template

    def test_chart_block_consumes_only_indented_lines(self):
        src = (
            "chart: bar\n"
            "  data: agg.values\n"
            "  labels: agg.labels\n"
            "<p>Outside</p>\n"
        )
        r = preprocess(src)
        # The <p> tag must remain untouched.
        assert "<p>Outside</p>" in r.template
        assert r.template.count("<p>Outside</p>") == 1

    def test_two_consecutive_blocks(self):
        src = (
            "chart: bar\n"
            "  data: agg.a\n"
            "  labels: agg.la\n"
            "\n"
            "chart: line\n"
            "  data: agg.b\n"
            "  labels: agg.lb\n"
        )
        r = preprocess(src)
        assert "bar_chart(" in r.template
        assert "line_chart(" in r.template

    def test_unknown_chart_type_emits_comment(self):
        src = "chart: hologram\n  data: agg.x\n  labels: agg.y\n"
        r = preprocess(src)
        assert "unknown chart type: hologram" in r.template

    def test_format_block_no_template_output(self):
        src = (
            "format: color_scale\n"
            "  column: Margin\n"
            "  min: 0\n"
            "  max: 100\n"
            "  min_color: \"#fee\"\n"
            "  max_color: \"#0f0\"\n"
        )
        r = preprocess(src)
        # Format blocks emit only an HTML comment in the template.
        assert "<svg" not in r.template
        assert len(r.formats) == 1
        fd = r.formats[0]
        assert fd.rule == "color_scale"
        assert fd.column == "Margin"
        assert fd.value == 0
        assert fd.value2 == 100
        assert fd.min_color == "#fee"
        assert fd.max_color == "#0f0"

    def test_format_rules_block_multiple_rules(self):
        src = (
            "format: rules\n"
            "  column: Score\n"
            "  rule: \">90 -> bold green\"\n"
            "  rule: \"<60 -> italic red\"\n"
        )
        r = preprocess(src)
        assert len(r.formats) == 2
        rules = sorted(r.formats, key=lambda d: d.rule)
        assert rules[0].rule == "greater_than"
        assert rules[0].column == "Score"
        assert rules[0].value == 90
        assert rules[0].style == "highlight-green"
        assert rules[1].rule == "less_than"
        assert rules[1].value == 60
        assert rules[1].style == "highlight-red"


# ─── Value resolution ─────────────────────────────────────────────────────

class TestValueResolution:
    def test_quoted_string_passthrough(self):
        assert _emit_value('"hello"') == '"hello"'
        assert _emit_value("'hi'") == "'hi'"

    def test_number_literal(self):
        assert _emit_value("42") == "42"
        assert _emit_value("3.14") == "3.14"

    def test_bool_and_none(self):
        assert _emit_value("true") == "True"
        assert _emit_value("false") == "False"
        assert _emit_value("none") == "None"

    def test_list_literal(self):
        out = _emit_value("[1, 2, 3]")
        assert out == "[1, 2, 3]"

    def test_list_literal_with_strings(self):
        out = _emit_value('["a", "b"]')
        assert out == '["a", "b"]'

    def test_agg_ref(self):
        assert _emit_value("agg.foo") == "agg.foo"

    def test_meta_ref(self):
        assert _emit_value("meta.name") == "meta.name"

    def test_single_column_ref(self):
        out = _emit_value("Revenue")
        assert "df['Revenue']" in out
        assert "tolist()" in out

    def test_sheet_qualified_column(self):
        out = _emit_value("sales!Revenue")
        assert "sheets['sales']['Revenue']" in out

    def test_a1_range(self):
        # B2:D4 → cols 1..3, data rows 0..2 → iloc[0:3, 1:4]
        out = _emit_value("B2:D4")
        assert out == "_a1_range(df, 0, 1, 3, 4)"

    def test_a1_range_for_heatmap(self):
        out = _emit_value("B2:D4", for_chart_type="heatmap", key="data")
        assert out == "_a1_range_df(df, 0, 1, 3, 4)"

    def test_a1_cell_requires_sheet_qualifier(self):
        # Bare A1 cell would shadow column names — must be unqualified column ref.
        out = _emit_value("Q4")
        assert "df['Q4']" in out

    def test_a1_cell_with_sheet_resolved(self):
        out = _emit_value("B2@sales")
        assert "_a1_cell(sheets['sales'], 0, 1)" == out

    def test_multi_column_list_for_line_becomes_dict(self):
        out = _emit_value("Q1,Q2,Q3", for_chart_type="line", key="data")
        assert "'Q1':" in out
        assert "'Q2':" in out
        assert "'Q3':" in out
        assert out.startswith("{") and out.endswith("}")

    def test_multi_column_list_default_concatenates(self):
        out = _emit_value("Q1,Q2")
        # Default is concatenation: (df['Q1'].tolist() + df['Q2'].tolist())
        assert "df['Q1']" in out
        assert "df['Q2']" in out
        assert " + " in out


# ─── Helpers ─────────────────────────────────────────────────────────────

class TestSmallHelpers:
    def test_col_letters_to_index(self):
        assert _col_letters_to_index("A") == 0
        assert _col_letters_to_index("B") == 1
        assert _col_letters_to_index("Z") == 25
        assert _col_letters_to_index("AA") == 26
        assert _col_letters_to_index("AB") == 27

    def test_coerce_scalar(self):
        assert _coerce_scalar("42") == 42
        assert _coerce_scalar("3.14") == 3.14
        assert _coerce_scalar('"hi"') == "hi"
        assert _coerce_scalar("true") is True
        assert _coerce_scalar("foo") == "foo"

    def test_split_top_level_commas_respects_brackets(self):
        parts = _split_top_level_commas("1, 2, [3, 4], 5")
        assert parts == ["1", "2", "[3, 4]", "5"]

    def test_split_top_level_commas_respects_quotes(self):
        parts = _split_top_level_commas('"a, b", "c"')
        assert parts == ['"a, b"', '"c"']


# ─── End-to-end render integration ───────────────────────────────────────

@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "Region": ["North", "South", "West"],
        "Jan": [120, 95, 80],
        "Feb": [135, 110, 90],
        "Mar": [150, 125, 105],
    })


@pytest.fixture
def sample_meta():
    return {"name": "Sales", "engine": "python", "version": "1.0"}


class TestRenderIntegration:
    def test_chart_bar_block_renders_svg(self, sample_df, sample_meta):
        template = (
            "<h1>{{ meta.name }}</h1>\n"
            "chart: bar\n"
            "  data: Jan\n"
            "  labels: Region\n"
            "  title: \"January\"\n"
        )
        html = render(template, sample_df, {}, sample_meta, raw_df=sample_df, standalone=False)
        assert "<svg" in html
        assert "<rect" in html      # bars
        assert "January" in html    # title

    def test_chart_pie_block(self, sample_df, sample_meta):
        template = (
            "chart: pie\n"
            "  data: Mar\n"
            "  labels: Region\n"
        )
        html = render(template, sample_df, {}, sample_meta, raw_df=sample_df, standalone=False)
        assert "<svg" in html
        assert "<path" in html

    def test_chart_a1_range_resolved(self, sample_df, sample_meta):
        template = (
            "chart: bar\n"
            "  data: B2:D4\n"
            "  labels: A2:A4\n"
        )
        html = render(template, sample_df, {}, sample_meta, raw_df=sample_df, standalone=False)
        # Should produce a bar chart from rows 0..2, cols 1..3.
        assert "<svg" in html
        assert "<rect" in html

    def test_format_color_scale_emits_inline_style(self, sample_df, sample_meta):
        template = (
            "format: color_scale\n"
            "  column: Mar\n"
            "  min: 0\n"
            "  max: 200\n"
            "  min_color: \"#ffffff\"\n"
            "  max_color: \"#000000\"\n"
            "<table>{% for _, row in df.iterrows() %}\n"
            "  <tr><td {{ cond_style('Mar', row.Mar) }}>{{ row.Mar }}</td></tr>\n"
            "{% endfor %}</table>\n"
        )
        html = render(template, sample_df, {}, sample_meta, raw_df=sample_df, standalone=False)
        assert "background-color" in html

    def test_format_rules_block_classes_apply(self, sample_df, sample_meta):
        template = (
            "format: rules\n"
            "  column: Mar\n"
            "  rule: \">120 -> bold green\"\n"
            "<table>{% for _, row in df.iterrows() %}\n"
            "  <tr><td class=\"{{ cond_class('Mar', row.Mar) }}\">{{ row.Mar }}</td></tr>\n"
            "{% endfor %}</table>\n"
        )
        html = render(template, sample_df, {}, sample_meta, raw_df=sample_df, standalone=False)
        # Mar=150 (North) and Mar=125 (South) > 120 → highlight-green class on those rows.
        assert "highlight-green" in html

    def test_dsl_format_merges_with_runtime_formats(self, sample_df, sample_meta):
        runtime_format = ConditionalFormat(
            column="Jan", rule="greater_than", value=100, style="highlight-yellow"
        )
        template = (
            "format: rules\n"
            "  column: Mar\n"
            "  rule: \">120 -> bold green\"\n"
            "<table>{% for _, row in df.iterrows() %}\n"
            "  <tr>\n"
            "    <td class=\"{{ cond_class('Jan', row.Jan) }}\">{{ row.Jan }}</td>\n"
            "    <td class=\"{{ cond_class('Mar', row.Mar) }}\">{{ row.Mar }}</td>\n"
            "  </tr>\n"
            "{% endfor %}</table>\n"
        )
        html = render(
            template, sample_df, {}, sample_meta,
            raw_df=sample_df,
            conditional_formats=[runtime_format],
            standalone=False,
        )
        # Both runtime and DSL formats should apply.
        assert "highlight-yellow" in html  # from runtime ConditionalFormat
        assert "highlight-green" in html   # from DSL `format: rules`

    def test_existing_jinja_calls_unaffected(self, sample_df, sample_meta):
        # If the user writes {{ bar_chart(...) }} directly, DSL preprocessor must NOT touch it.
        template = '{{ bar_chart(["A","B","C"], [1,2,3], title="Direct") }}'
        html = render(template, sample_df, {}, sample_meta, raw_df=sample_df, standalone=False)
        assert "<svg" in html
        assert "Direct" in html
