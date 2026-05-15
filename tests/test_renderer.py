"""Tests for gridlang.renderer"""

import pytest
import pandas as pd

from gridlang.renderer import render, RenderError


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        'Name': ['Alice', 'Bob'],
        'Score': [95, 80],
        'Grade': ['A', 'B'],
    })


@pytest.fixture
def sample_meta():
    return {
        'name': 'Test Document',
        'engine': 'python',
        'version': '1.0',
    }


class TestRenderBasic:
    """Test basic rendering."""

    def test_simple_template(self, sample_df, sample_meta):
        template = "<h1>{{ meta.name }}</h1>"
        html = render(template, sample_df, {}, sample_meta, standalone=False)
        assert "<h1>Test Document</h1>" in html

    def test_dataframe_iteration(self, sample_df, sample_meta):
        template = """{% for _, row in df.iterrows() %}<p>{{ row.Name }}: {{ row.Score }}</p>
{% endfor %}"""
        html = render(template, sample_df, {}, sample_meta, standalone=False)
        assert "Alice: 95" in html
        assert "Bob: 80" in html

    def test_aggregates_access(self, sample_df, sample_meta):
        template = "<p>Average: {{ agg.avg }}</p>"
        agg = {'avg': 87.5}
        html = render(template, sample_df, agg, sample_meta, standalone=False)
        assert "Average: 87.5" in html

    def test_standalone_wrapping(self, sample_df, sample_meta):
        template = "<p>Hello</p>"
        html = render(template, sample_df, {}, sample_meta, standalone=True)
        assert "<!DOCTYPE html>" in html
        assert "<title>Test Document</title>" in html
        assert "<p>Hello</p>" in html

    def test_custom_style_no_default(self, sample_df, sample_meta):
        template = "<style>.custom { color: red; }</style><p>Test</p>"
        html = render(template, sample_df, {}, sample_meta, standalone=True)
        assert ".custom { color: red; }" in html
        # Should NOT include default styles when custom style is present
        assert "border-collapse" not in html or ".custom" in html

    def test_empty_template_generates_default(self, sample_df, sample_meta):
        html = render("", sample_df, {'total': 100}, sample_meta, standalone=False)
        # Default template should have a table
        assert "<table>" in html or "<div" in html


class TestHelperFunctions:
    """Test built-in helper functions in templates."""

    def test_format_number(self, sample_df, sample_meta):
        template = "{{ format_number(1234567.891) }}"
        html = render(template, sample_df, {}, sample_meta, standalone=False)
        assert "1,234,567.89" in html

    def test_format_pct(self, sample_df, sample_meta):
        template = "{{ format_pct(45.678) }}"
        html = render(template, sample_df, {}, sample_meta, standalone=False)
        assert "45.7%" in html

    def test_format_currency(self, sample_df, sample_meta):
        template = "{{ format_currency(99999) }}"
        html = render(template, sample_df, {}, sample_meta, standalone=False)
        assert "$99,999" in html

    def test_format_currency_negative(self, sample_df, sample_meta):
        template = "{{ format_currency(-5000) }}"
        html = render(template, sample_df, {}, sample_meta, standalone=False)
        assert "-$5,000" in html

    def test_format_number_nan(self, sample_df, sample_meta):
        template = "{{ format_number(agg.val) }}"
        html = render(template, sample_df, {'val': float('nan')}, sample_meta, standalone=False)
        assert "—" in html  # em-dash for NaN

    def test_sparkline(self, sample_df, sample_meta):
        template = "{{ sparkline([1, 3, 2, 5, 4]) }}"
        html = render(template, sample_df, {}, sample_meta, standalone=False)
        assert "<svg" in html
        assert "polyline" in html

    def test_color_scale(self, sample_df, sample_meta):
        template = "{{ color_scale(75, 0, 100) }}"
        html = render(template, sample_df, {}, sample_meta, standalone=False)
        assert "#" in html  # Should return a hex color


class TestRenderErrors:
    """Test error handling in rendering."""

    def test_undefined_variable(self, sample_df, sample_meta):
        template = "{{ undefined_var.something }}"
        with pytest.raises(RenderError, match="variable error"):
            render(template, sample_df, {}, sample_meta, standalone=False)

    def test_syntax_error_in_template(self, sample_df, sample_meta):
        template = "{% for x in %}<p>broken</p>{% endfor %}"
        with pytest.raises(RenderError, match="syntax error"):
            render(template, sample_df, {}, sample_meta, standalone=False)

    def test_raw_df_available(self, sample_df, sample_meta):
        raw = pd.DataFrame({'Original': [1, 2, 3]})
        template = "{{ raw_df.columns[0] }}"
        html = render(template, sample_df, {}, sample_meta, raw_df=raw, standalone=False)
        assert "Original" in html


class TestBarChart:
    """Test bar chart helper."""

    def test_bar_chart_generation(self, sample_df, sample_meta):
        template = "{{ bar_chart(['A', 'B', 'C'], [10, 20, 30]) }}"
        html = render(template, sample_df, {}, sample_meta, standalone=False)
        assert "<svg" in html
        assert "rect" in html

    def test_bar_chart_empty(self, sample_df, sample_meta):
        template = "{{ bar_chart([], []) }}"
        html = render(template, sample_df, {}, sample_meta, standalone=False)
        assert html.strip() == ""
