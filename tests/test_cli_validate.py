"""Tests for `gridlang validate` — covers the engine-aware syntax check.

Regression test for the v1.0.0 CI failure: the validate command was running
Python's compile() against JS source, which (correctly) rejects it as
'invalid syntax'. The fix routes JS files to ``node --check`` when Node is
on PATH, and to a graceful skip otherwise.
"""

from __future__ import annotations

import shutil
import subprocess


PYTHON_GRID = """--- meta ---
name: "Py"
engine: python
version: "1.0"

--- data ---
A,B
1,2

--- compute ---
def transform(df):
    return df

--- present ---
"""


JS_GRID = """--- meta ---
name: "JS"
engine: javascript
version: "1.0"

--- data ---
A,B
1,2

--- compute ---
function transform(df) {
  df.addColumn('C', r => r.A + r.B);
  return df;
}

--- present ---
"""


JS_GRID_BAD_SYNTAX = """--- meta ---
name: "JS-bad"
engine: javascript
version: "1.0"

--- data ---
A,B
1,2

--- compute ---
function transform(df) {
  df.addColumn('C', r =>
  // missing closing brace + arrow body
}

--- present ---
"""


def _validate(path) -> tuple[int, str, str]:
    proc = subprocess.run(
        ['python3', '-m', 'gridlang.cli', 'validate', str(path)],
        capture_output=True, text=True, timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestValidatePython:

    def test_python_engine_valid(self, tmp_path):
        path = tmp_path / 'py.grid'
        path.write_text(PYTHON_GRID, encoding='utf-8')
        rc, out, _ = _validate(path)
        assert rc == 0
        assert 'valid Python syntax' in out
        assert 'ALL CHECKS PASSED' in out


class TestValidateJavaScript:
    """The fix: JS source must NOT be fed to Python's compile()."""

    def test_js_engine_valid_with_node(self, tmp_path):
        if not shutil.which('node'):
            import pytest
            pytest.skip('node not on PATH')
        path = tmp_path / 'js.grid'
        path.write_text(JS_GRID, encoding='utf-8')
        rc, out, _ = _validate(path)
        assert rc == 0, out
        assert 'valid JavaScript syntax' in out
        assert 'ALL CHECKS PASSED' in out
        # Crucially: must NOT report a Python syntax error.
        assert 'Compute syntax error (line 1)' not in out
        assert 'invalid syntax' not in out

    def test_js_engine_bad_syntax_reported(self, tmp_path):
        if not shutil.which('node'):
            import pytest
            pytest.skip('node not on PATH')
        path = tmp_path / 'jsbad.grid'
        path.write_text(JS_GRID_BAD_SYNTAX, encoding='utf-8')
        rc, out, _ = _validate(path)
        # validate exits 1 on errors.
        assert rc == 1
        # The error mentions JavaScript explicitly so users aren't confused.
        assert 'JavaScript' in out


class TestValidateAllShippedExamples:
    """All committed examples must pass `gridlang validate`."""

    def test_every_example_validates(self):
        from pathlib import Path
        examples = sorted(Path('examples').glob('*.grid'))
        assert examples, 'no example .grid files found'
        for ex in examples:
            rc, out, err = _validate(ex)
            assert rc == 0, f'{ex.name} failed: stdout={out!r} stderr={err!r}'
