"""Tests for gridlang.js_bundle (Node + browser bundle generation)."""

from __future__ import annotations

import json
import os
import subprocess
import shutil
import tempfile
from pathlib import Path

import pytest
import pandas as pd

from gridlang.parser import parse_string
from gridlang.js_bundle import (
    bundle_doc,
    bundle_file,
    BundleResult,
    get_pipeline_source,
)


_NODE = shutil.which('node')


def _skip_if_no_node():
    if not _NODE:
        pytest.skip("node not on PATH; bundle tests skipped")


SIMPLE_GRID = """\
--- meta ---
name: "Bundle Test"
engine: javascript
version: "1.0"

--- data ---
A,B
10,2
20,4
30,6

--- compute ---
function transform(df) {
  df.addColumn('C', r => r.A * r.B);
  return df;
}
function aggregates(df) {
  return { sum_a: df.sum('A'), sum_c: df.sum('C') };
}

--- present ---
"""


# ─── Pure Python: bundle assembly ─────────────────────────────────────────

class TestBundleAssembly:

    def test_returns_bundle_result(self):
        doc = parse_string(SIMPLE_GRID)
        result = bundle_doc(doc, target='node')
        assert isinstance(result, BundleResult)
        assert result.target == 'node'
        assert result.bytes > 0
        assert result.bytes == len(result.source)
        assert result.sheet_count == 1

    def test_node_bundle_contains_expected_pieces(self):
        doc = parse_string(SIMPLE_GRID)
        result = bundle_doc(doc, target='node')
        s = result.source
        # User code embedded
        assert 'transform(df)' in s
        assert "df.addColumn('C'" in s
        # Helpers prelude
        assert 'function makeDF' in s
        # Pipeline runner
        assert 'function runPipeline' in s
        # Node-specific glue
        assert 'process.stdout' in s
        # Request payload
        assert '"A": 10' in s or '"A":10' in s
        # No leftover format placeholders
        assert '__HELPERS__' not in s
        assert '__USER_CODE__' not in s
        assert '__REQUEST__' not in s
        assert '__PIPELINE__' not in s

    def test_browser_bundle_contains_worker_glue(self):
        doc = parse_string(SIMPLE_GRID)
        result = bundle_doc(doc, target='browser')
        s = result.source
        assert 'self.addEventListener' in s
        assert 'runGridLangPipeline' in s
        assert 'process.stdout' not in s     # no Node IO in the browser bundle

    def test_minify_smaller_than_pretty(self):
        doc = parse_string(SIMPLE_GRID)
        pretty = bundle_doc(doc, target='node', pretty=True)
        mini = bundle_doc(doc, target='node', pretty=False)
        assert mini.bytes < pretty.bytes

    def test_unknown_target_rejected(self):
        doc = parse_string(SIMPLE_GRID)
        with pytest.raises(ValueError, match='target must be'):
            bundle_doc(doc, target='deno')

    def test_python_engine_rejected(self):
        py_grid = SIMPLE_GRID.replace('engine: javascript', 'engine: python')
        py_grid = py_grid.replace(
            'function transform(df) {\n  df.addColumn(\'C\', r => r.A * r.B);\n  return df;\n}\n'
            'function aggregates(df) {\n  return { sum_a: df.sum(\'A\'), sum_c: df.sum(\'C\') };\n}',
            'def transform(df):\n    return df'
        )
        doc = parse_string(py_grid)
        with pytest.raises(ValueError, match='only engine: javascript'):
            bundle_doc(doc, target='node')

    def test_get_pipeline_source(self):
        s = get_pipeline_source()
        assert 'function runPipeline' in s
        assert 'transform' in s


# ─── End-to-end: run the generated bundle with node ─────────────────────────

class TestNodeBundleExecution:

    def setup_method(self):
        _skip_if_no_node()

    def _run_bundle(self, source: str) -> dict:
        """Write the bundle to a temp file, invoke node, parse JSON."""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.js', delete=False, encoding='utf-8',
        ) as f:
            f.write(source)
            path = f.name
        try:
            proc = subprocess.run(
                [_NODE, path], capture_output=True, text=True, timeout=10,
            )
            assert proc.returncode == 0, f"node failed: stderr={proc.stderr!r}"
            return json.loads(proc.stdout)
        finally:
            os.unlink(path)

    def test_node_bundle_runs(self):
        doc = parse_string(SIMPLE_GRID)
        result = bundle_doc(doc, target='node')
        out = self._run_bundle(result.source)
        assert out['error'] is None
        assert out['aggregates'] == {'sum_a': 60, 'sum_c': 280}
        assert len(out['df']) == 3
        # C column was added
        assert all('C' in r for r in out['df'])

    def test_node_bundle_multi_sheet(self):
        src = '''--- meta ---
name: "Multi"
engine: javascript
version: "1.0"

--- data:left ---
k,x
a,1
b,2

--- data:right ---
k,y
a,10
b,20

--- compute ---
function transform(sheets) {
  return { joined: sheets.left.join(sheets.right, 'k'), left: sheets.left, right: sheets.right };
}

--- present ---
'''
        doc = parse_string(src)
        result = bundle_doc(doc, target='node')
        out = self._run_bundle(result.source)
        assert out['error'] is None
        assert 'joined' in out['sheets']
        joined = out['sheets']['joined']
        assert len(joined) == 2
        assert joined[0]['x'] == 1 and joined[0]['y'] == 10

    def test_node_bundle_validate_halt(self):
        src = SIMPLE_GRID.replace(
            'function transform(df) {',
            'function validate(df) { return ["bad"]; }\nfunction transform(df) {',
        )
        doc = parse_string(src)
        result = bundle_doc(doc, target='node')
        out = self._run_bundle(result.source)
        assert out.get('error', '').startswith('Validation failed')

    def test_node_bundle_advanced_helpers(self):
        # Use a handful of v0.7-expanded helpers in the user code.
        src = '''--- meta ---
name: "Helpers"
engine: javascript
version: "1.0"

--- data ---
Region,Sales
N,100
S,200
N,300
E,150
S,250

--- compute ---
function aggregates(df) {
  const sorted = df.sortBy('Sales', { desc: true });
  const grouped = df.groupBy('Region');
  const sums = {};
  for (const k of Object.keys(grouped)) sums[k] = grouped[k].sum('Sales');
  return {
    median: df.median('Sales'),
    p90: df.quantile('Sales', 0.9),
    distinct: df.distinct('Region').count(),
    top: sorted.head(2).col('Sales'),
    by_region: sums,
  };
}

--- present ---
'''
        doc = parse_string(src)
        result = bundle_doc(doc, target='node')
        out = self._run_bundle(result.source)
        agg = out['aggregates']
        assert agg['median'] == 200
        assert agg['distinct'] == 3
        assert agg['top'] == [300, 250]
        assert agg['by_region'] == {'N': 400, 'S': 450, 'E': 150}


# ─── Browser bundle smoke test (simulated globals) ──────────────────────────

class TestBrowserBundleSmoke:
    """Run the browser bundle under Node with a faked `self` object."""

    def setup_method(self):
        _skip_if_no_node()

    def test_browser_bundle_runs_via_global(self):
        doc = parse_string(SIMPLE_GRID)
        result = bundle_doc(doc, target='browser')
        # Inject a minimal `self` so the IIFE sees a Worker-like object.
        harness = (
            "var self = {addEventListener: function(){}};\n"
            + result.source +
            "\nconsole.log(JSON.stringify(self.runGridLangPipeline()));\n"
        )
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.js', delete=False, encoding='utf-8',
        ) as f:
            f.write(harness)
            path = f.name
        try:
            proc = subprocess.run([_NODE, path], capture_output=True, text=True, timeout=10)
            assert proc.returncode == 0, f"node failed: {proc.stderr!r}"
            out = json.loads(proc.stdout.strip().split('\n')[-1])
            assert out['aggregates'] == {'sum_a': 60, 'sum_c': 280}
        finally:
            os.unlink(path)

    def test_worker_postMessage_protocol(self):
        # Simulate a worker: addEventListener registers our handler, postMessage
        # captures the response.
        doc = parse_string(SIMPLE_GRID)
        result = bundle_doc(doc, target='browser')
        harness = (
            "var captured = null;\n"
            "var listeners = [];\n"
            "var self = {\n"
            "  addEventListener: function(name, fn) { if(name==='message') listeners.push(fn); },\n"
            "  postMessage: function(r) { captured = r; },\n"
            "};\n"
            + result.source +
            "\nlisteners[0]({data: {}});\n"
            "console.log(JSON.stringify(captured));\n"
        )
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.js', delete=False, encoding='utf-8',
        ) as f:
            f.write(harness)
            path = f.name
        try:
            proc = subprocess.run([_NODE, path], capture_output=True, text=True, timeout=10)
            assert proc.returncode == 0, f"node failed: {proc.stderr!r}"
            out = json.loads(proc.stdout.strip())
            assert out['aggregates'] == {'sum_a': 60, 'sum_c': 280}
        finally:
            os.unlink(path)


# ─── bundle_file convenience wrapper ────────────────────────────────────────

class TestBundleFile:

    def test_bundle_file_reads_disk(self, tmp_path):
        path = tmp_path / 'test.grid'
        path.write_text(SIMPLE_GRID, encoding='utf-8')
        result = bundle_file(path, target='node')
        assert result.target == 'node'
        assert 'function makeDF' in result.source

    def test_bundle_file_browser(self, tmp_path):
        path = tmp_path / 'test.grid'
        path.write_text(SIMPLE_GRID, encoding='utf-8')
        result = bundle_file(path, target='browser')
        assert result.target == 'browser'
        assert 'addEventListener' in result.source


# ─── CLI integration ────────────────────────────────────────────────────────

class TestCliJsBundle:

    def setup_method(self):
        _skip_if_no_node()

    def test_cli_writes_node_bundle(self, tmp_path):
        grid_path = tmp_path / 'in.grid'
        grid_path.write_text(SIMPLE_GRID, encoding='utf-8')
        out_path = tmp_path / 'out.js'
        proc = subprocess.run(
            ['python3', '-m', 'gridlang.cli', 'js-bundle',
             str(grid_path), '-o', str(out_path)],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 0, proc.stderr
        assert out_path.exists()
        # Run it
        run = subprocess.run(
            [_NODE, str(out_path)], capture_output=True, text=True, timeout=10,
        )
        assert run.returncode == 0
        result = json.loads(run.stdout)
        assert result['aggregates']['sum_a'] == 60

    def test_cli_browser_flag(self, tmp_path):
        grid_path = tmp_path / 'in.grid'
        grid_path.write_text(SIMPLE_GRID, encoding='utf-8')
        out_path = tmp_path / 'out.js'
        proc = subprocess.run(
            ['python3', '-m', 'gridlang.cli', 'js-bundle',
             str(grid_path), '-o', str(out_path), '--browser'],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 0, proc.stderr
        text = out_path.read_text()
        assert 'addEventListener' in text
        assert 'runGridLangPipeline' in text

    def test_cli_stdout_when_no_output(self, tmp_path):
        grid_path = tmp_path / 'in.grid'
        grid_path.write_text(SIMPLE_GRID, encoding='utf-8')
        proc = subprocess.run(
            ['python3', '-m', 'gridlang.cli', 'js-bundle', str(grid_path)],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 0
        # stdout contains the bundle text
        assert 'function makeDF' in proc.stdout
        assert 'process.stdout' in proc.stdout

    def test_cli_minify_flag(self, tmp_path):
        grid_path = tmp_path / 'in.grid'
        grid_path.write_text(SIMPLE_GRID, encoding='utf-8')
        out_pretty = tmp_path / 'pretty.js'
        out_mini = tmp_path / 'mini.js'
        subprocess.run(
            ['python3', '-m', 'gridlang.cli', 'js-bundle', str(grid_path), '-o', str(out_pretty)],
            capture_output=True, text=True, timeout=15,
        )
        subprocess.run(
            ['python3', '-m', 'gridlang.cli', 'js-bundle', str(grid_path),
             '-o', str(out_mini), '--minify'],
            capture_output=True, text=True, timeout=15,
        )
        assert out_mini.stat().st_size < out_pretty.stat().st_size
