"""Tests for the JavaScript compute engine (gridlang.js_runtime)."""

from __future__ import annotations

import os
import shutil
import pytest
import pandas as pd

from gridlang.js_runtime import (
    execute_js,
    is_node_available,
    JsRuntimeUnavailable,
    _df_to_records,
    _records_to_df,
    _clean_scalar,
)
from gridlang.runtime import RuntimeError_, ExecutionResult, execute


_NODE_AVAILABLE = is_node_available()


def _skip_if_no_node():
    if not _NODE_AVAILABLE:
        pytest.skip("node not on PATH; JS runtime tests skipped")


# ─── pure-Python helpers (no node needed) ──────────────────────────────────

class TestRecordConversion:

    def test_df_to_records(self):
        df = pd.DataFrame({'A': [1, 2], 'B': ['x', 'y']})
        recs = _df_to_records(df)
        assert recs == [{'A': 1, 'B': 'x'}, {'A': 2, 'B': 'y'}]

    def test_df_to_records_handles_nan(self):
        df = pd.DataFrame({'A': [1.0, float('nan'), 3.0]})
        recs = _df_to_records(df)
        assert recs[1]['A'] is None

    def test_df_to_records_handles_timestamp(self):
        df = pd.DataFrame({'when': [pd.Timestamp('2025-01-01')]})
        recs = _df_to_records(df)
        assert recs[0]['when'] == '2025-01-01T00:00:00'

    def test_df_to_records_empty(self):
        assert _df_to_records(pd.DataFrame()) == []

    def test_records_to_df_roundtrip(self):
        original = pd.DataFrame({'A': [1, 2, 3], 'B': ['x', 'y', 'z']})
        recs = _df_to_records(original)
        roundtripped = _records_to_df(recs)
        pd.testing.assert_frame_equal(roundtripped, original)

    def test_records_to_df_empty_preserves_columns(self):
        original = pd.DataFrame({'A': [1, 2], 'B': ['x', 'y']})
        result = _records_to_df([], original=original)
        assert list(result.columns) == ['A', 'B']
        assert len(result) == 0

    def test_clean_scalar_none(self):
        assert _clean_scalar(None) is None

    def test_clean_scalar_inf(self):
        assert _clean_scalar(float('inf')) is None
        assert _clean_scalar(float('-inf')) is None

    def test_clean_scalar_normal(self):
        assert _clean_scalar(42) == 42
        assert _clean_scalar('hello') == 'hello'


# ─── basic transform ───────────────────────────────────────────────────────

class TestExecuteJsBasic:

    def setup_method(self):
        _skip_if_no_node()

    def test_no_compute_returns_input(self):
        df = pd.DataFrame({'A': [1, 2, 3]})
        result = execute_js('', df)
        assert isinstance(result, ExecutionResult)
        pd.testing.assert_frame_equal(result.df, df)
        assert result.compute_functions == []

    def test_simple_transform(self):
        df = pd.DataFrame({'A': [1, 2, 3]})
        code = 'function transform(df) { df.addColumn("B", r => r.A * 10); return df; }'
        result = execute_js(code, df)
        assert list(result.df.columns) == ['A', 'B']
        assert list(result.df['B']) == [10, 20, 30]
        assert 'transform' in result.compute_functions

    def test_aggregates(self):
        df = pd.DataFrame({'Revenue': [100, 200, 300]})
        code = '''
            function aggregates(df) {
              return { total: df.sum('Revenue'), n: df.shape[0] };
            }
        '''
        result = execute_js(code, df)
        assert result.aggregates == {'total': 600, 'n': 3}

    def test_aggregates_only_no_transform(self):
        df = pd.DataFrame({'X': [1, 2, 3]})
        code = 'function aggregates(df) { return { sum: df.sum("X") }; }'
        result = execute_js(code, df)
        assert result.aggregates == {'sum': 6}
        # df should be unchanged
        pd.testing.assert_frame_equal(result.df, df)

    def test_conditional_formats(self):
        df = pd.DataFrame({'A': [1, 2, 3]})
        code = '''
            function conditional_formats() {
              return [{ column: 'A', rule: 'greater_than', value: 2, style: 'highlight-green' }];
            }
        '''
        result = execute_js(code, df)
        assert len(result.conditional_formats) == 1
        cf = result.conditional_formats[0]
        assert cf.column == 'A'
        assert cf.rule == 'greater_than'
        assert cf.value == 2
        assert cf.style == 'highlight-green'

    def test_full_pipeline(self):
        df = pd.DataFrame({'Region': ['N', 'S'], 'Revenue': [100, 200]})
        code = '''
            function transform(df) {
              df.addColumn('Tax', r => r.Revenue * 0.2);
              return df;
            }
            function aggregates(df) {
              return { total: df.sum('Revenue'), tax: df.sum('Tax') };
            }
            function conditional_formats() {
              return [{ column: 'Revenue', rule: 'greater_than', value: 150 }];
            }
        '''
        result = execute_js(code, df)
        assert list(result.df.columns) == ['Region', 'Revenue', 'Tax']
        assert list(result.df['Tax']) == [20, 40]
        assert result.aggregates == {'total': 300, 'tax': 60}
        assert len(result.conditional_formats) == 1
        assert set(result.compute_functions) == {'transform', 'aggregates', 'conditional_formats'}


# ─── df helpers in JS ──────────────────────────────────────────────────────

class TestDfHelpers:

    def setup_method(self):
        _skip_if_no_node()

    def test_col(self):
        df = pd.DataFrame({'A': [1, 2, 3]})
        code = 'function aggregates(df) { return { vals: df.col("A") }; }'
        result = execute_js(code, df)
        assert result.aggregates['vals'] == [1, 2, 3]

    def test_sum_mean(self):
        df = pd.DataFrame({'X': [10, 20, 30]})
        code = 'function aggregates(df) { return { s: df.sum("X"), m: df.mean("X") }; }'
        result = execute_js(code, df)
        assert result.aggregates == {'s': 60, 'm': 20}

    def test_max_min(self):
        df = pd.DataFrame({'X': [5, 1, 3, 9, 2]})
        code = 'function aggregates(df) { return { hi: df.max("X"), lo: df.min("X") }; }'
        result = execute_js(code, df)
        assert result.aggregates == {'hi': 9, 'lo': 1}

    def test_where(self):
        df = pd.DataFrame({'A': [1, 2, 3, 4, 5]})
        code = '''
            function transform(df) {
              return df.where(r => r.A >= 3);
            }
        '''
        result = execute_js(code, df)
        assert list(result.df['A']) == [3, 4, 5]

    def test_shape_columns(self):
        df = pd.DataFrame({'A': [1, 2], 'B': ['x', 'y']})
        code = 'function aggregates(df) { return { rows: df.shape[0], cols: df.columns }; }'
        result = execute_js(code, df)
        assert result.aggregates['rows'] == 2
        assert result.aggregates['cols'] == ['A', 'B']

    def test_addColumn_with_index(self):
        df = pd.DataFrame({'A': [10, 20, 30]})
        code = 'function transform(df) { df.addColumn("idx", (r, i) => i); return df; }'
        result = execute_js(code, df)
        assert list(result.df['idx']) == [0, 1, 2]


# ─── multi-sheet ───────────────────────────────────────────────────────────

class TestMultiSheet:

    def setup_method(self):
        _skip_if_no_node()

    def test_multi_sheet_transform(self):
        sheets = {
            'sales': pd.DataFrame({'R': ['N', 'S'], 'V': [100, 200]}),
            'costs': pd.DataFrame({'R': ['N', 'S'], 'C': [60, 110]}),
        }
        code = '''
            function transform(sheets) {
              for (const r of sheets.sales) {
                const cost = sheets.costs.find(c => c.R === r.R);
                r.profit = r.V - cost.C;
              }
              return sheets;
            }
        '''
        result = execute_js(code, sheets['sales'], sheets=sheets)
        assert result.is_multi_sheet
        assert list(result.df['profit']) == [40, 90]
        # Both sheets present in result
        assert 'sales' in result.sheets
        assert 'costs' in result.sheets

    def test_multi_sheet_aggregates_run_on_primary(self):
        sheets = {
            'a': pd.DataFrame({'X': [1, 2, 3]}),
            'b': pd.DataFrame({'Y': [10, 20]}),
        }
        code = 'function aggregates(df) { return { sum: df.sum("X") }; }'
        result = execute_js(code, sheets['a'], sheets=sheets)
        # aggregates() receives the primary sheet
        assert result.aggregates == {'sum': 6}

    def test_single_sheet_signature_falls_back_when_param_not_sheets(self):
        # If `transform(df)` rather than `transform(sheets)`, treat as single-sheet.
        sheets = {
            'a': pd.DataFrame({'X': [1, 2, 3]}),
            'b': pd.DataFrame({'Y': [10, 20]}),
        }
        code = 'function transform(df) { df.addColumn("Y", r => r.X + 1); return df; }'
        result = execute_js(code, sheets['a'], sheets=sheets)
        assert list(result.df['Y']) == [2, 3, 4]


# ─── error handling ────────────────────────────────────────────────────────

class TestErrorHandling:

    def setup_method(self):
        _skip_if_no_node()

    def test_syntax_error(self):
        df = pd.DataFrame({'A': [1]})
        with pytest.raises(RuntimeError_, match='Unexpected'):
            execute_js('function transform(df) { return ', df)

    def test_thrown_in_transform(self):
        df = pd.DataFrame({'A': [1]})
        with pytest.raises(RuntimeError_, match='boom'):
            execute_js('function transform(df) { throw new Error("boom"); }', df)

    def test_thrown_in_aggregates(self):
        df = pd.DataFrame({'A': [1]})
        with pytest.raises(RuntimeError_, match='aggregates'):
            execute_js('function aggregates(df) { throw new Error("boom"); }', df)

    def test_undefined_return_from_transform(self):
        df = pd.DataFrame({'A': [1]})
        with pytest.raises(RuntimeError_, match='undefined|return'):
            execute_js('function transform(df) { df.col("A"); }', df)

    def test_aggregates_returns_non_object(self):
        df = pd.DataFrame({'A': [1]})
        with pytest.raises(RuntimeError_, match='must return'):
            execute_js('function aggregates(df) { return 42; }', df)

    def test_validate_failure_halts(self):
        df = pd.DataFrame({'A': [1]})
        code = '''
            function validate(df) { return ["bad column"]; }
            function transform(df) { df.addColumn("B", r => r.A * 2); return df; }
        '''
        with pytest.raises(RuntimeError_, match='Validation failed'):
            execute_js(code, df)

    def test_validate_passes(self):
        df = pd.DataFrame({'A': [1, 2]})
        code = 'function validate(df) { return []; } function transform(df) { return df; }'
        result = execute_js(code, df)
        assert result.compute_functions  # no exception


# ─── sandbox ───────────────────────────────────────────────────────────────

class TestSandbox:

    def setup_method(self):
        _skip_if_no_node()

    def test_require_blocked(self):
        df = pd.DataFrame({'A': [1]})
        with pytest.raises(RuntimeError_, match='require'):
            execute_js('function transform(df) { require("fs"); return df; }', df)

    def test_process_blocked(self):
        df = pd.DataFrame({'A': [1]})
        with pytest.raises(RuntimeError_, match='process'):
            execute_js('function transform(df) { process.exit(1); }', df)

    def test_setTimeout_blocked(self):
        df = pd.DataFrame({'A': [1]})
        with pytest.raises(RuntimeError_, match='setTimeout'):
            execute_js('function transform(df) { setTimeout(() => {}, 1); return df; }', df)

    def test_buffer_blocked(self):
        df = pd.DataFrame({'A': [1]})
        with pytest.raises(RuntimeError_, match='Buffer'):
            execute_js('function transform(df) { Buffer.from("x"); return df; }', df)

    def test_console_log_doesnt_break(self):
        # A no-op console is fine; log calls should not error or produce output
        # that confuses the JSON bridge.
        df = pd.DataFrame({'A': [1, 2]})
        code = 'function transform(df) { console.log("hi"); return df; }'
        result = execute_js(code, df)
        assert list(result.df['A']) == [1, 2]

    def test_math_works(self):
        df = pd.DataFrame({'A': [4, 9, 16]})
        code = 'function transform(df) { df.addColumn("R", r => Math.sqrt(r.A)); return df; }'
        result = execute_js(code, df)
        assert list(result.df['R']) == [2, 3, 4]


# ─── runtime.execute() dispatch ────────────────────────────────────────────

class TestEngineDispatch:

    def setup_method(self):
        _skip_if_no_node()

    def test_execute_routes_javascript(self):
        df = pd.DataFrame({'A': [1, 2]})
        code = 'function transform(df) { df.addColumn("B", r => r.A * 2); return df; }'
        result = execute(code, df, engine='javascript')
        assert list(result.df['B']) == [2, 4]

    def test_execute_javascript_alias_js(self):
        df = pd.DataFrame({'A': [1, 2]})
        code = 'function transform(df) { return df; }'
        # Both 'javascript' and 'js' should work
        r1 = execute(code, df, engine='javascript')
        r2 = execute(code, df, engine='js')
        pd.testing.assert_frame_equal(r1.df, r2.df)

    def test_execute_python_unaffected(self):
        df = pd.DataFrame({'A': [1, 2]})
        code = 'def transform(df):\n    df["B"] = df["A"] * 2\n    return df'
        result = execute(code, df, engine='python')
        assert list(result.df['B']) == [2, 4]

    def test_execute_default_engine_is_python(self):
        # Without engine= kwarg, falls back to python.
        df = pd.DataFrame({'A': [1, 2]})
        code = 'def transform(df):\n    return df'
        result = execute(code, df)
        pd.testing.assert_frame_equal(result.df, df)

    def test_unsupported_engine_raises(self):
        df = pd.DataFrame({'A': [1]})
        with pytest.raises(RuntimeError_, match='Unsupported'):
            execute('whatever', df, engine='haskell')


# ─── parser integration ────────────────────────────────────────────────────

class TestParserAcceptsJavascript:

    def test_meta_engine_javascript_accepted(self):
        from gridlang.parser import parse_string
        src = '''--- meta ---
name: "JS"
engine: javascript
version: "1.0"

--- data ---
A,B
1,2

--- compute ---

--- present ---
'''
        doc = parse_string(src)
        assert doc.engine == 'javascript'

    def test_meta_engine_unknown_rejected(self):
        from gridlang.parser import parse_string, ParseError
        src = '''--- meta ---
name: "X"
engine: ruby
version: "1.0"

--- data ---
A
1

--- compute ---

--- present ---
'''
        with pytest.raises(ParseError, match='Unsupported engine'):
            parse_string(src)


# ─── unavailable node ──────────────────────────────────────────────────────

class TestNodeUnavailable:
    """Mock-style test that bogus node_path triggers JsRuntimeUnavailable."""

    def test_node_path_override_used(self):
        # When node_path is explicitly given but doesn't exist, we still expect
        # subprocess to error rather than the unavailable check.
        if not _NODE_AVAILABLE:
            pytest.skip("node required")
        df = pd.DataFrame({'A': [1]})
        with pytest.raises(RuntimeError_):
            execute_js(
                'function transform(df) { return df; }',
                df,
                node_path='/definitely/does/not/exist/node',
            )

    def test_unavailable_when_no_node(self, monkeypatch=None):
        # Simulate no node by patching the module-level shutil.which lookup.
        import gridlang.js_runtime as js_rt
        original = shutil.which
        try:
            js_rt.shutil.which = lambda name: None
            df = pd.DataFrame({'A': [1]})
            with pytest.raises(JsRuntimeUnavailable, match='requires'):
                execute_js('function transform(df) { return df; }', df)
        finally:
            js_rt.shutil.which = original


# ─── full end-to-end with .grid file ───────────────────────────────────────

class TestEndToEnd:
    """Round-trip a full .grid file through the JS engine."""

    def setup_method(self):
        _skip_if_no_node()

    def test_example_10_renders(self):
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / 'examples' / '10_javascript.grid'
        if not path.exists():
            pytest.skip(f"example missing: {path}")

        from gridlang.parser import parse_file
        from gridlang.runtime import execute
        from gridlang.renderer import render
        from gridlang.data_sources import load_dataframes

        doc = parse_file(path)
        assert doc.engine == 'javascript'
        sheets, _ = load_dataframes(doc, allow_remote=False)
        primary = list(sheets.values())[0]
        result = execute(doc.compute_raw, primary, engine=doc.engine)
        assert 'Total' in result.df.columns
        assert 'Avg' in result.df.columns
        assert 'Growth' in result.df.columns
        assert result.aggregates['grand_total'] > 0
        # Render should succeed
        html = render(
            template_content=doc.present_raw,
            df=result.df,
            aggregates=result.aggregates,
            meta=doc.meta,
            raw_df=primary,
            conditional_formats=result.conditional_formats,
            standalone=True,
        )
        assert 'Grand Total' in html
        assert '</svg>' in html  # bar chart rendered


# ─── Expanded df API (v0.7) ────────────────────────────────────────────────

class TestDfHelpersExpanded:
    """Tests for the v0.7 expansion of the df helper API."""

    def setup_method(self):
        _skip_if_no_node()

    def test_count(self):
        df = pd.DataFrame({'A': [1, 2, 3, 4]})
        result = execute_js('function aggregates(df) { return { n: df.count() }; }', df)
        assert result.aggregates == {'n': 4}

    def test_variance_std(self):
        df = pd.DataFrame({'X': [1, 2, 3, 4, 5]})
        code = 'function aggregates(df) { return { var: df.variance("X"), std: df.std("X") }; }'
        result = execute_js(code, df)
        # Sample variance of 1..5 = 2.5, std = sqrt(2.5)
        assert abs(result.aggregates['var'] - 2.5) < 1e-9
        assert abs(result.aggregates['std'] - 2.5 ** 0.5) < 1e-9

    def test_median_quantile(self):
        df = pd.DataFrame({'X': list(range(1, 11))})  # 1..10
        code = '''
            function aggregates(df) {
              return {
                med: df.median('X'),
                q25: df.quantile('X', 0.25),
                q75: df.quantile('X', 0.75),
              };
            }
        '''
        result = execute_js(code, df)
        assert result.aggregates['med'] == 5.5
        assert result.aggregates['q25'] == 3.25
        assert result.aggregates['q75'] == 7.75

    def test_describe(self):
        df = pd.DataFrame({'X': [1, 2, 3, 4, 5]})
        code = 'function aggregates(df) { return { d: df.describe() }; }'
        result = execute_js(code, df)
        d = result.aggregates['d']['X']
        assert d['count'] == 5
        assert d['mean'] == 3
        assert d['min'] == 1
        assert d['max'] == 5

    def test_head_tail(self):
        df = pd.DataFrame({'X': list(range(10))})
        code = '''
            function aggregates(df) {
              return { head: df.head(3).col('X'), tail: df.tail(3).col('X') };
            }
        '''
        result = execute_js(code, df)
        assert result.aggregates['head'] == [0, 1, 2]
        assert result.aggregates['tail'] == [7, 8, 9]

    def test_sortBy_asc_desc(self):
        df = pd.DataFrame({'X': [3, 1, 4, 1, 5, 9, 2, 6]})
        code = '''
            function aggregates(df) {
              return {
                asc: df.sortBy('X').col('X'),
                desc: df.sortBy('X', {desc: true}).col('X'),
              };
            }
        '''
        result = execute_js(code, df)
        assert result.aggregates['asc'] == [1, 1, 2, 3, 4, 5, 6, 9]
        assert result.aggregates['desc'] == [9, 6, 5, 4, 3, 2, 1, 1]

    def test_sortBy_string_column(self):
        df = pd.DataFrame({'name': ['charlie', 'alice', 'bob']})
        code = 'function aggregates(df) { return { s: df.sortBy("name").col("name") }; }'
        result = execute_js(code, df)
        assert result.aggregates['s'] == ['alice', 'bob', 'charlie']

    def test_distinct_no_arg(self):
        df = pd.DataFrame({'A': [1, 1, 2, 2, 3], 'B': ['x', 'x', 'y', 'y', 'z']})
        code = 'function aggregates(df) { return { n: df.distinct().count() }; }'
        result = execute_js(code, df)
        assert result.aggregates == {'n': 3}

    def test_distinct_by_column(self):
        df = pd.DataFrame({'Region': ['N', 'N', 'S', 'E', 'E', 'E']})
        code = 'function aggregates(df) { return { regs: df.distinct("Region").col("Region") }; }'
        result = execute_js(code, df)
        assert result.aggregates['regs'] == ['N', 'S', 'E']

    def test_groupBy(self):
        df = pd.DataFrame({'R': ['N', 'S', 'N', 'S', 'E'], 'V': [1, 2, 3, 4, 5]})
        code = '''
            function aggregates(df) {
              const g = df.groupBy('R');
              const sums = {};
              for (const k of Object.keys(g)) sums[k] = g[k].sum('V');
              return { sums };
            }
        '''
        result = execute_js(code, df)
        assert result.aggregates['sums'] == {'N': 4, 'S': 6, 'E': 5}

    def test_countBy(self):
        df = pd.DataFrame({'R': ['N', 'N', 'N', 'S', 'E', 'E']})
        code = 'function aggregates(df) { return df.countBy("R"); }'
        result = execute_js(code, df)
        assert result.aggregates == {'N': 3, 'S': 1, 'E': 2}

    def test_pluck(self):
        df = pd.DataFrame({'A': [1, 2], 'B': [3, 4], 'C': [5, 6]})
        code = '''
            function transform(df) {
              return df.pluck('A', 'C');
            }
        '''
        result = execute_js(code, df)
        assert list(result.df.columns) == ['A', 'C']

    def test_drop(self):
        df = pd.DataFrame({'A': [1, 2], 'B': [3, 4], 'C': [5, 6]})
        code = 'function transform(df) { return df.drop("B"); }'
        result = execute_js(code, df)
        assert list(result.df.columns) == ['A', 'C']

    def test_rename(self):
        df = pd.DataFrame({'A': [1, 2], 'B': [3, 4]})
        code = 'function transform(df) { return df.rename({A: "X", B: "Y"}); }'
        result = execute_js(code, df)
        assert list(result.df.columns) == ['X', 'Y']

    def test_assign_multiple_columns(self):
        df = pd.DataFrame({'A': [1, 2, 3]})
        code = '''
            function transform(df) {
              return df.assign({
                B: r => r.A * 2,
                C: r => r.A + 100,
                D: 'static',
              });
            }
        '''
        result = execute_js(code, df)
        assert list(result.df['B']) == [2, 4, 6]
        assert list(result.df['C']) == [101, 102, 103]
        assert list(result.df['D']) == ['static', 'static', 'static']

    def test_inner_join(self):
        sheets = {
            'left':  pd.DataFrame({'k': ['a', 'b', 'c'], 'x': [1, 2, 3]}),
            'right': pd.DataFrame({'k': ['a', 'b'], 'y': [10, 20]}),
        }
        code = '''
            function transform(sheets) {
              return { out: sheets.left.join(sheets.right, 'k'), left: sheets.left, right: sheets.right };
            }
        '''
        result = execute_js(code, sheets['left'], sheets=sheets)
        out = result.sheets['out']
        assert len(out) == 2
        assert list(out.columns) == ['k', 'x', 'y']
        assert list(out['y']) == [10, 20]

    def test_left_join(self):
        sheets = {
            'left':  pd.DataFrame({'k': ['a', 'b', 'c'], 'x': [1, 2, 3]}),
            'right': pd.DataFrame({'k': ['a', 'b'], 'y': [10, 20]}),
        }
        code = '''
            function transform(sheets) {
              return { out: sheets.left.leftJoin(sheets.right, 'k'), left: sheets.left, right: sheets.right };
            }
        '''
        result = execute_js(code, sheets['left'], sheets=sheets)
        out = result.sheets['out']
        assert len(out) == 3
        # Last row's y should be NaN/None
        assert pd.isna(out['y'].iloc[2])

    def test_concat(self):
        df = pd.DataFrame({'A': [1, 2]})
        code = '''
            function transform(df) {
              const more = [{A: 3}, {A: 4}];
              return df.concat(more);
            }
        '''
        result = execute_js(code, df)
        assert list(result.df['A']) == [1, 2, 3, 4]

    def test_find_some_every_none(self):
        df = pd.DataFrame({'X': [10, 20, 30, 40, 50]})
        code = '''
            function aggregates(df) {
              return {
                first_big: df.find(r => r.X > 25),
                any_huge:  df.some(r => r.X > 100),
                all_pos:   df.every(r => r.X > 0),
                no_neg:    df.none(r => r.X < 0),
              };
            }
        '''
        result = execute_js(code, df)
        assert result.aggregates['first_big'] == {'X': 30}
        assert result.aggregates['any_huge'] is False
        assert result.aggregates['all_pos'] is True
        assert result.aggregates['no_neg'] is True

    def test_toCSV(self):
        df = pd.DataFrame({'A': [1, 2], 'B': ['x', 'y, z']})
        code = 'function aggregates(df) { return { csv: df.toCSV() }; }'
        result = execute_js(code, df)
        csv = result.aggregates['csv']
        # First line = headers
        assert csv.split('\n')[0] == 'A,B'
        # Second line: 1,x
        # Third line: needs quoting because of comma
        assert '"y, z"' in csv

    def test_empty_property(self):
        df = pd.DataFrame()
        code = 'function aggregates(df) { return { e: df.empty }; }'
        result = execute_js(code, df)
        assert result.aggregates == {'e': True}

    def test_chaining_full_pipeline(self):
        df = pd.DataFrame({
            'Region': ['N', 'S', 'N', 'E', 'S', 'E', 'N'],
            'Year':   [2024, 2024, 2025, 2024, 2025, 2025, 2024],
            'Sales':  [100, 200, 150, 300, 250, 280, 120],
        })
        code = '''
            function transform(df) {
              return df
                .where(r => r.Year === 2025)
                .sortBy('Sales', {desc: true})
                .pluck('Region', 'Sales');
            }
            function aggregates(df) {
              const g = df.groupBy('Region');
              const out = {};
              for (const k of Object.keys(g)) out[k] = g[k].mean('Sales');
              return { mean_by_region: out, total: df.sum('Sales') };
            }
        '''
        result = execute_js(code, df)
        # Filtered to year=2025: N=150, S=250, E=280; sorted desc: 280, 250, 150
        assert list(result.df.columns) == ['Region', 'Sales']
        assert list(result.df['Sales']) == [280, 250, 150]
        # mean_by_region computed on the *transformed* df (post-filter)
        assert result.aggregates['mean_by_region'] == {'N': 150, 'S': 250, 'E': 280}
        assert result.aggregates['total'] == 680


# ─── JS source loader ──────────────────────────────────────────────────────

class TestJsSourceLoader:
    """Public helpers for js_bundle and other consumers."""

    def test_helpers_source_contains_makeDF(self):
        from gridlang.js_runtime import get_helpers_source
        src = get_helpers_source()
        assert 'function makeDF' in src
        assert 'def(\'sum\'' in src
        assert 'groupBy' in src
        assert 'leftJoin' in src

    def test_bridge_source_inlines_helpers(self):
        from gridlang.js_runtime import get_bridge_source, get_helpers_source
        bridge = get_bridge_source()
        # The placeholder must be substituted away
        assert '__HELPERS_PRELUDE__' not in bridge
        # And the helpers source (or its first line) must appear inline as JSON.
        assert 'makeDF' in bridge
        # And the bridge still includes its own runtime code
        assert 'readAllStdin' in bridge
        assert 'process.stdin' in bridge
