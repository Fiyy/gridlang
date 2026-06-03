"""Tests for the reactive bindings module."""

from __future__ import annotations

import json
import pytest
import pandas as pd

from gridlang.bindings import (
    parse_a1_ref,
    apply_edit,
    preprocess,
    make_cell_helper,
    BindingError,
    BindDirective,
    client_js,
    BINDING_STYLES,
    _csv_cell_repr,
    _locate_data_section,
)


# ─── parse_a1_ref ───────────────────────────────────────────────────────────

class TestParseA1Ref:

    def test_simple(self):
        assert parse_a1_ref('A1') == (1, 1, None)

    def test_double_letter(self):
        assert parse_a1_ref('AA10') == (10, 27, None)

    def test_lowercase_is_normalized(self):
        assert parse_a1_ref('b2') == (2, 2, None)

    def test_with_sheet(self):
        assert parse_a1_ref('B2@sales') == (2, 2, 'sales')

    def test_high_column(self):
        assert parse_a1_ref('Z1') == (1, 26, None)
        assert parse_a1_ref('AB1') == (1, 28, None)

    def test_invalid_no_row(self):
        with pytest.raises(BindingError):
            parse_a1_ref('B')

    def test_invalid_no_column(self):
        with pytest.raises(BindingError):
            parse_a1_ref('1')

    def test_invalid_format(self):
        with pytest.raises(BindingError):
            parse_a1_ref('B2:D4')  # ranges not allowed here

    def test_invalid_type(self):
        with pytest.raises(BindingError):
            parse_a1_ref(42)

    def test_whitespace_tolerated(self):
        assert parse_a1_ref('  C3  ') == (3, 3, None)


# ─── apply_edit ─────────────────────────────────────────────────────────────

SAMPLE = """\
--- meta ---
name: "Test"
engine: python
version: "1.0"

--- data ---
Region,Revenue,Profit
North,100,30
South,150,45
West,80,20

--- compute ---
def transform(df):
    return df

--- present ---
<h1>Hello</h1>
"""


class TestApplyEditBasic:

    def test_edits_simple_cell(self):
        out = apply_edit(SAMPLE, cell='B2', value='999')
        assert 'North,999,30' in out
        # Other rows untouched
        assert 'South,150,45' in out
        assert 'West,80,20' in out

    def test_edits_string_cell(self):
        out = apply_edit(SAMPLE, cell='A2', value='Northeast')
        assert 'Northeast,100,30' in out

    def test_edits_last_column(self):
        out = apply_edit(SAMPLE, cell='C4', value='25')
        assert 'West,80,25' in out

    def test_meta_section_unchanged(self):
        out = apply_edit(SAMPLE, cell='B2', value='1')
        assert '--- meta ---' in out
        assert 'name: "Test"' in out

    def test_compute_section_unchanged(self):
        out = apply_edit(SAMPLE, cell='B2', value='1')
        assert 'def transform(df):' in out

    def test_present_section_unchanged(self):
        out = apply_edit(SAMPLE, cell='B2', value='1')
        assert '<h1>Hello</h1>' in out

    def test_returns_full_source(self):
        out = apply_edit(SAMPLE, cell='B2', value='1')
        # All section delimiters preserved
        assert out.count('--- ') >= 4

    def test_rejects_header_row(self):
        with pytest.raises(BindingError, match='header row'):
            apply_edit(SAMPLE, cell='B1', value='Foo')

    def test_rejects_out_of_range_row(self):
        with pytest.raises(BindingError, match='Row 99'):
            apply_edit(SAMPLE, cell='B99', value='1')

    def test_extends_short_row(self):
        # Editing a column past the existing row width should pad with empty cells.
        src = SAMPLE.replace('North,100,30', 'North,100')
        out = apply_edit(src, cell='C2', value='42')
        assert 'North,100,42' in out


class TestApplyEditPreservesNoise:

    def test_preserves_blank_lines(self):
        src = SAMPLE.replace(
            'Region,Revenue,Profit\nNorth,100,30',
            'Region,Revenue,Profit\n\nNorth,100,30',
        )
        out = apply_edit(src, cell='B2', value='999')
        # Blank line stays in place; row 2 (first data row) is North.
        assert '\n\n' in out
        assert 'North,999,30' in out

    def test_preserves_comments(self):
        src = SAMPLE.replace(
            'Region,Revenue,Profit\nNorth,100,30',
            'Region,Revenue,Profit\n# US regions\nNorth,100,30',
        )
        out = apply_edit(src, cell='B2', value='999')
        assert '# US regions' in out
        assert 'North,999,30' in out

    def test_preserves_directives(self):
        src = SAMPLE.replace(
            '--- data ---\nRegion,Revenue,Profit',
            '--- data ---\n@source: file:///tmp/x.csv\n@cache: 1h\nRegion,Revenue,Profit',
        )
        out = apply_edit(src, cell='B2', value='999')
        assert '@source: file:///tmp/x.csv' in out
        assert '@cache: 1h' in out
        assert 'North,999,30' in out

    def test_preserves_other_rows_byte_for_byte(self):
        out = apply_edit(SAMPLE, cell='B3', value='200')
        # Row 2 untouched
        assert 'North,100,30' in out
        # Row 4 untouched
        assert 'West,80,20' in out
        # Row 3 updated
        assert 'South,200,45' in out


class TestApplyEditQuoting:

    def test_value_with_comma_is_quoted(self):
        out = apply_edit(SAMPLE, cell='A2', value='Greater, North')
        assert '"Greater, North",100,30' in out

    def test_value_with_quote_is_escaped(self):
        out = apply_edit(SAMPLE, cell='A2', value='Bob "B" North')
        assert '"Bob ""B"" North",100,30' in out

    def test_simple_value_not_quoted(self):
        out = apply_edit(SAMPLE, cell='A2', value='East')
        assert 'East,100,30' in out
        assert '"East"' not in out


class TestCsvCellRepr:

    def test_none_is_empty(self):
        assert _csv_cell_repr(None) == ''

    def test_int(self):
        assert _csv_cell_repr(42) == '42'

    def test_simple_string(self):
        assert _csv_cell_repr('hello') == 'hello'

    def test_string_with_comma(self):
        assert _csv_cell_repr('a,b') == '"a,b"'

    def test_string_with_quote(self):
        assert _csv_cell_repr('he said "hi"') == '"he said ""hi"""'

    def test_string_with_newline(self):
        assert _csv_cell_repr('line1\nline2') == '"line1\nline2"'


# ─── multi-sheet edits ──────────────────────────────────────────────────────

MULTI_SAMPLE = """\
--- meta ---
name: "Multi"
engine: python
version: "1.0"

--- data:revenue ---
Region,Q1,Q2
North,100,120
South,90,95

--- data:costs ---
Region,Q1,Q2
North,50,60
South,40,45

--- compute ---
def transform(df):
    return df

--- present ---
"""


class TestApplyEditMultiSheet:

    def test_edits_first_sheet_via_qualifier(self):
        out = apply_edit(MULTI_SAMPLE, cell='B2@revenue', value='999')
        assert 'North,999,120' in out
        # costs sheet untouched
        assert 'North,50,60' in out

    def test_edits_second_sheet_via_qualifier(self):
        out = apply_edit(MULTI_SAMPLE, cell='B2@costs', value='999')
        assert 'North,999,60' in out
        # revenue sheet untouched
        assert 'North,100,120' in out

    def test_explicit_sheet_param_overrides_ref(self):
        # Ref says @revenue but sheet param says costs — sheet wins.
        out = apply_edit(MULTI_SAMPLE, cell='B2@revenue', value='999', sheet='costs')
        assert 'North,999,60' in out
        assert 'North,100,120' in out  # revenue untouched

    def test_unknown_sheet_raises(self):
        with pytest.raises(BindingError, match='Cannot find data section'):
            apply_edit(MULTI_SAMPLE, cell='B2@nope', value='1')

    def test_default_picks_first_sheet(self):
        # When neither ref nor param give a sheet, the first data section wins.
        out = apply_edit(MULTI_SAMPLE, cell='B2', value='999')
        assert 'North,999,120' in out  # revenue is first


class TestLocateDataSection:

    def test_finds_default(self):
        lines = SAMPLE.split('\n')
        result = _locate_data_section(lines, None)
        assert result is not None
        body_start, body_end = result
        body = '\n'.join(lines[body_start:body_end])
        assert 'Region,Revenue,Profit' in body
        assert 'def transform' not in body

    def test_finds_named(self):
        lines = MULTI_SAMPLE.split('\n')
        result = _locate_data_section(lines, 'costs')
        assert result is not None
        body_start, body_end = result
        body = '\n'.join(lines[body_start:body_end])
        assert 'North,50,60' in body
        # Revenue body should not be in the slice
        assert 'North,100,120' not in body

    def test_unknown_returns_none(self):
        lines = MULTI_SAMPLE.split('\n')
        assert _locate_data_section(lines, 'nope') is None


# ─── bind: DSL preprocessing ────────────────────────────────────────────────

class TestPreprocessBindBlocks:

    def test_no_blocks_passthrough(self):
        src = '<h1>Hello</h1>\n<p>World</p>'
        result = preprocess(src)
        assert result.template == src
        assert result.bindings == []

    def test_single_input_block(self):
        src = (
            'bind: input\n'
            '  cell: B2\n'
            '  label: "Unit Price"\n'
            '  type: number\n'
        )
        result = preprocess(src)
        assert len(result.bindings) == 1
        d = result.bindings[0]
        assert d.kind == 'input'
        assert d.cell == 'B2'
        assert d.label == 'Unit Price'
        assert d.input_type == 'number'
        # Rendered HTML contains the right hooks
        assert 'data-grid-bind="B2"' in result.template
        assert 'type="number"' in result.template

    def test_select_block_with_options(self):
        src = (
            'bind: select\n'
            '  cell: A2\n'
            '  label: "Region"\n'
            '  options: North, South, East, West\n'
        )
        result = preprocess(src)
        assert result.bindings[0].kind == 'select'
        assert result.bindings[0].options == ['North', 'South', 'East', 'West']
        assert '<select' in result.template
        assert '<option value="North">North</option>' in result.template

    def test_checkbox_block(self):
        src = 'bind: checkbox\n  cell: D2\n'
        result = preprocess(src)
        assert result.bindings[0].kind == 'checkbox'
        assert 'type="checkbox"' in result.template

    def test_textarea_block(self):
        src = 'bind: textarea\n  cell: E2\n  placeholder: "Notes..."\n'
        result = preprocess(src)
        assert result.bindings[0].kind == 'textarea'
        assert '<textarea' in result.template

    def test_min_max_step(self):
        src = (
            'bind: input\n'
            '  cell: B2\n'
            '  type: number\n'
            '  min: 0\n'
            '  max: 100\n'
            '  step: 5\n'
        )
        result = preprocess(src)
        assert 'min="0"' in result.template
        assert 'max="100"' in result.template
        assert 'step="5"' in result.template

    def test_a1_ref_validated_eagerly(self):
        src = 'bind: input\n  cell: NotARef\n'
        with pytest.raises(BindingError):
            preprocess(src)

    def test_missing_cell_rejected(self):
        src = 'bind: input\n  label: "x"\n'
        with pytest.raises(BindingError, match='requires a `cell:`'):
            preprocess(src)

    def test_unknown_kind_rejected(self):
        src = 'bind: blender\n  cell: B2\n'
        with pytest.raises(BindingError, match='Unknown bind kind'):
            preprocess(src)

    def test_block_terminates_on_dedent(self):
        src = (
            'bind: input\n'
            '  cell: B2\n'
            '<p>after</p>\n'
        )
        result = preprocess(src)
        assert len(result.bindings) == 1
        assert '<p>after</p>' in result.template

    def test_multiple_blocks(self):
        src = (
            'bind: input\n'
            '  cell: B2\n'
            '\n'
            'bind: input\n'
            '  cell: C2\n'
        )
        result = preprocess(src)
        assert len(result.bindings) == 2
        assert result.bindings[0].cell == 'B2'
        assert result.bindings[1].cell == 'C2'

    def test_emits_data_grid_bind_current_for_initial_value(self):
        src = 'bind: input\n  cell: B2\n'
        result = preprocess(src)
        # The wrapper carries a Jinja expression that renderer.py will resolve.
        assert 'data-grid-bind-current="{{ cell(' in result.template


# ─── make_cell_helper / inline cell() ──────────────────────────────────────

class TestCellHelper:

    def setup_method(self):
        self.df = pd.DataFrame({
            'Region': ['North', 'South', 'West'],
            'Revenue': [100, 150, 80],
            'Profit': [30, 45, 20],
        })
        self.helper = make_cell_helper({'default': self.df})

    def test_reads_cell_value(self):
        out = self.helper('B2')
        assert '100' in out
        assert 'data-grid-cell="B2"' in out

    def test_emits_contenteditable_by_default(self):
        out = self.helper('B2')
        assert 'contenteditable="true"' in out

    def test_can_disable_editable(self):
        out = self.helper('B2', editable=False)
        assert 'contenteditable' not in out

    def test_header_is_readonly(self):
        out = self.helper('A1')
        assert 'Region' in out
        # Header cells never get contenteditable
        assert 'contenteditable' not in out

    def test_unknown_sheet_returns_ref_error(self):
        out = self.helper('B2@nope')
        assert '#REF!' in out

    def test_out_of_range_returns_ref_error(self):
        out = self.helper('Z99')
        assert '#REF!' in out

    def test_format_spec_applied(self):
        out = self.helper('B2', fmt=',.2f')
        assert '100.00' in out

    def test_html_escapes_value(self):
        df = pd.DataFrame({'A': ['<script>alert(1)</script>']})
        helper = make_cell_helper({'default': df})
        out = helper('A2')
        assert '<script>' not in out  # would re-render; must be escaped
        assert '&lt;script&gt;' in out


# ─── render() integration ──────────────────────────────────────────────────

class TestRendererIntegration:

    def test_inline_cell_renders(self):
        from gridlang.renderer import render
        df = pd.DataFrame({'A': ['x', 'y'], 'B': [1, 2]})
        html = render(
            template_content='<p>{{ cell("B2") }}</p>',
            df=df,
            aggregates={},
            meta={'name': 'T'},
            raw_df=df,
            standalone=True,
        )
        assert 'data-grid-cell="B2"' in html
        assert 'contenteditable="true"' in html
        # Binding styles should be auto-injected
        assert '.grid-cell' in html

    def test_bind_block_renders(self):
        from gridlang.renderer import render
        df = pd.DataFrame({'A': ['x', 'y'], 'B': [1, 2]})
        template = (
            'bind: input\n'
            '  cell: B2\n'
            '  label: "Value"\n'
            '  type: number\n'
        )
        html = render(
            template_content=template,
            df=df,
            aggregates={},
            meta={'name': 'T'},
            raw_df=df,
            standalone=True,
        )
        assert 'data-grid-bind="B2"' in html
        assert 'type="number"' in html
        assert '.grid-bind' in html  # styles injected

    def test_no_binding_no_styles(self):
        # Backward compat: templates without cell()/bind: get the same output.
        from gridlang.renderer import render
        df = pd.DataFrame({'A': [1, 2]})
        html = render(
            template_content='<h1>{{ meta.name }}</h1>',
            df=df,
            aggregates={},
            meta={'name': 'T'},
            raw_df=df,
            standalone=True,
        )
        assert 'grid-cell' not in html
        assert 'grid-bind' not in html


# ─── client_js ─────────────────────────────────────────────────────────────

class TestClientJs:

    def test_default_api_url(self):
        js = client_js()
        assert '/api/cell-edit' in js
        assert '<script>' in js
        assert 'data-grid-cell' in js
        assert 'data-grid-bind' in js

    def test_custom_api_url(self):
        js = client_js('/custom/endpoint')
        assert '/custom/endpoint' in js
        assert '/api/cell-edit' not in js


# ─── server endpoint integration ───────────────────────────────────────────

class TestServerEndpoint:
    """Test the /api/cell-edit endpoint via a real HTTP request."""

    def setup_method(self):
        import threading, tempfile, os
        from http.server import HTTPServer
        from gridlang.server import GridLangHandler

        # Write a temp .grid file the handler will edit.
        self.tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.grid', delete=False, encoding='utf-8',
        )
        self.tmp.write(SAMPLE)
        self.tmp.close()
        self.path = self.tmp.name

        GridLangHandler.grid_path = type(self.tmp).__mro__[0].__name__  # placeholder
        from pathlib import Path
        GridLangHandler.grid_path = Path(self.path)
        GridLangHandler.edit_mode = True
        GridLangHandler.allow_remote = False

        # Bind to an ephemeral port (0 lets the OS pick).
        self.server = HTTPServer(('127.0.0.1', 0), GridLangHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def teardown_method(self):
        import os
        self.server.shutdown()
        self.thread.join(timeout=2)
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _post(self, payload: dict) -> tuple[int, dict]:
        import http.client
        body = json.dumps(payload)
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request(
            'POST', '/api/cell-edit',
            body=body,
            headers={'Content-Type': 'application/json'},
        )
        resp = conn.getresponse()
        data = json.loads(resp.read().decode('utf-8'))
        conn.close()
        return resp.status, data

    def test_a1_edit_roundtrip(self):
        status, data = self._post({'cell': 'B2', 'value': '999', 'save': True})
        assert status == 200, data
        assert 'North,999,30' in data['content']
        # On-disk file is updated when save=true
        with open(self.path, encoding='utf-8') as f:
            assert 'North,999,30' in f.read()

    def test_a1_edit_no_save_keeps_disk_clean(self):
        original = open(self.path, encoding='utf-8').read()
        status, data = self._post({'cell': 'B2', 'value': '999', 'save': False})
        assert status == 200
        assert 'North,999,30' in data['content']
        # Disk untouched
        assert open(self.path, encoding='utf-8').read() == original

    def test_a1_edit_returns_rendered_html(self):
        status, data = self._post({'cell': 'B2', 'value': '999', 'save': False})
        assert status == 200
        assert data.get('html')  # non-empty HTML fragment

    def test_a1_edit_invalid_cell_rejected(self):
        status, data = self._post({'cell': 'NotACell', 'value': '1'})
        assert status == 400
        assert 'error' in data

    def test_a1_edit_header_row_rejected(self):
        status, data = self._post({'cell': 'B1', 'value': 'hi'})
        assert status == 400
        assert 'header' in data['error'].lower()

    def test_legacy_edit_still_works(self):
        # The original {content,row,col,value} shape must still function
        # so the existing editor UI doesn't break.
        original = open(self.path, encoding='utf-8').read()
        status, data = self._post({
            'content': original,
            'row': 0,            # zero-indexed data row → North
            'col': 'Revenue',
            'value': '777',
        })
        assert status == 200, data
        assert 'North,777,30' in data['content']

    def test_invalid_json_rejected(self):
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request('POST', '/api/cell-edit', body='not json',
                     headers={'Content-Type': 'application/json'})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode('utf-8'))
        conn.close()
        assert resp.status == 400
        assert 'JSON' in data['error']
