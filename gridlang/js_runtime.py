"""
GridLang JavaScript Runtime — alternative compute engine for ``.grid`` files
with ``engine: javascript`` in the meta section.

The Python compute engine remains the default. JavaScript is opt-in and runs
in a Node subprocess via a JSON-over-stdio bridge: there is no in-process JS
interpreter, no shared memory, and no path from user code into the host
Python.

## Wire contract

The Python side sends a request on stdin::

    {
      "code":   "<user JS source>",
      "df":     [ {col: val, ...}, ... ],
      "sheets": { "name": [ {col: val}, ... ], ... },
      "is_multi_sheet": bool
    }

The Node bridge replies on stdout with a single JSON object::

    {
      "df":                  [ {col: val, ...}, ... ],
      "sheets":              { "name": [...], ... },
      "aggregates":          { ... },
      "conditional_formats": [ {column, rule, ...}, ... ],
      "validation_messages": [...],
      "found_functions":     ["transform", "aggregates", ...],
      "error":               null | "<message>"
    }

User code can define these top-level functions, all optional:

  * ``validate(df) -> string[]``  — non-empty list halts execution
  * ``transform(df) -> df``       (single-sheet)
  * ``transform(sheets) -> sheets`` (multi-sheet — chosen by parameter name)
  * ``aggregates(df) -> object``
  * ``conditional_formats() -> object[]``

The df helper API is loaded from ``gridlang/js/df_helpers.js`` and is shared
verbatim with the Node and browser bundles produced by ``gridlang js-bundle``.

## Sandbox

The bridge uses ``vm.runInNewContext`` with a curated globals object — no
``require``, ``process``, ``fs``, ``child_process``, ``Buffer``, or
``setImmediate``. Wall-clock timeout via ``vm.Script.runInContext({timeout})``.
We also constrain Node's heap with ``--max-old-space-size=256`` so a runaway
allocation cannot starve the host.

When ``node`` isn't on ``$PATH``, ``execute_js()`` raises ``RuntimeError_``
with an actionable message — callers can detect this via
``JsRuntimeUnavailable`` and fall back gracefully.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import numpy as np

from gridlang.runtime import ConditionalFormat, ExecutionResult, RuntimeError_


# ─── Public errors ─────────────────────────────────────────────────────────

class JsRuntimeUnavailable(RuntimeError_):
    """Raised when no JS interpreter (node) is available on the host."""


# ─── JS source loading ────────────────────────────────────────────────────

# Both files live next to this Python module so they ship inside the wheel.
_JS_DIR = Path(__file__).parent / 'js'
_HELPERS_PATH = _JS_DIR / 'df_helpers.js'
_BRIDGE_NODE_PATH = _JS_DIR / 'bridge_node.js'


def _read_js(path: Path) -> str:
    """Read a JS source file, raising a helpful error if it's missing."""
    try:
        return path.read_text(encoding='utf-8')
    except OSError as e:
        raise RuntimeError_(
            f"GridLang JS source missing at {path}: {e}. "
            f"Reinstall the gridlang package."
        )


def get_helpers_source() -> str:
    """Return the contents of df_helpers.js (the makeDF prelude).

    Public so other modules (notably js_bundle) can embed it in standalone
    bundles.
    """
    return _read_js(_HELPERS_PATH)


def get_bridge_source() -> str:
    """Return the contents of bridge_node.js with the helper prelude inlined.

    The bridge file contains a literal placeholder ``__HELPERS_PRELUDE__``
    which we substitute with a JSON-encoded copy of the helpers source so
    ``vm.runInContext`` can execute it inside the sandbox.
    """
    helpers = get_helpers_source()
    bridge = _read_js(_BRIDGE_NODE_PATH)
    return bridge.replace('__HELPERS_PRELUDE__', json.dumps(helpers))




# ─── Public entry point ────────────────────────────────────────────────────

# Default 5-second wall-clock per stage. Override via env var for long compute.
_DEFAULT_TIMEOUT_MS = int(os.environ.get('GRIDLANG_JS_TIMEOUT_MS', '5000'))
_PROCESS_TIMEOUT_S = float(os.environ.get('GRIDLANG_JS_PROCESS_TIMEOUT_S', '15'))


def is_node_available() -> bool:
    """True iff a usable Node interpreter is on PATH."""
    return shutil.which('node') is not None


def execute_js(
    compute_code: str,
    df: pd.DataFrame,
    sheets: Optional[dict[str, pd.DataFrame]] = None,
    *,
    timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    node_path: Optional[str] = None,
) -> ExecutionResult:
    """
    Execute the compute layer using the JavaScript engine.

    Mirrors :func:`gridlang.runtime.execute` semantics — same hooks, same
    ``ExecutionResult`` shape — but the user code is JavaScript run inside
    a Node subprocess sandbox.

    Args:
        compute_code: User JavaScript source.
        df:           Primary DataFrame.
        sheets:       Multi-sheet dict (optional).
        timeout_ms:   Per-stage VM timeout for user code.
        node_path:    Override the auto-detected node binary (mostly for tests).

    Raises:
        JsRuntimeUnavailable: If Node is not on PATH.
        RuntimeError_:        If user code errors, validation fails, or the
                              bridge times out / crashes.
    """
    node = node_path or shutil.which('node')
    if not node:
        raise JsRuntimeUnavailable(
            "engine: javascript requires 'node' on PATH. "
            "Install Node 18+ from https://nodejs.org/ or change to engine: python."
        )

    # Empty compute: short-circuit, identical to the Python engine.
    if not compute_code.strip():
        result_sheets = {k: v.copy() for k, v in (sheets or {'default': df}).items()}
        return ExecutionResult(
            df=df.copy(), sheets=result_sheets, compute_functions=[],
        )

    payload = {
        'code': compute_code,
        'df': _df_to_records(df),
        'sheets': {k: _df_to_records(v) for k, v in (sheets or {}).items()},
        'is_multi_sheet': bool(sheets) and len(sheets) > 1,
        'timeout_ms': int(timeout_ms),
    }
    request = json.dumps(payload, default=_json_default)

    # Persist the bridge to a tempfile to avoid command-line length limits and
    # to keep the JS source debuggable when something goes wrong.
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.js', delete=False, encoding='utf-8',
    ) as bridge_file:
        bridge_file.write(get_bridge_source())
        bridge_path = bridge_file.name

    try:
        proc = subprocess.run(
            [node, '--max-old-space-size=256', bridge_path],
            input=request,
            capture_output=True,
            text=True,
            timeout=_PROCESS_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError_(
            f"JavaScript runtime timed out after {_PROCESS_TIMEOUT_S}s "
            f"(set GRIDLANG_JS_PROCESS_TIMEOUT_S to override)."
        )
    except FileNotFoundError as e:
        raise RuntimeError_(
            f"Could not launch node interpreter at {node!r}: {e}"
        )
    finally:
        try:
            os.unlink(bridge_path)
        except OSError:
            pass

    if proc.returncode != 0 and not proc.stdout.strip():
        # Node itself crashed before the bridge could respond.
        raise RuntimeError_(
            f"JavaScript runtime crashed (exit {proc.returncode}):\n{proc.stderr.strip()}"
        )

    try:
        response = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError_(
            f"JavaScript runtime returned invalid JSON: {e}\n"
            f"stdout: {proc.stdout[:200]!r}\nstderr: {proc.stderr[:200]!r}"
        )

    if response.get('error'):
        raise RuntimeError_(response['error'])

    return _build_result(response, df, sheets)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame to JSON-friendly list-of-records."""
    if df is None or df.empty:
        return []
    return [_clean_record(r) for r in df.to_dict(orient='records')]


def _clean_record(record: dict) -> dict:
    """Replace NaN/Timestamp with JSON-safe equivalents."""
    out = {}
    for k, v in record.items():
        out[str(k)] = _clean_scalar(v)
    return out


def _clean_scalar(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v


def _json_default(o: Any) -> Any:
    """JSON encoder fallback for stray numpy/pandas scalars."""
    cleaned = _clean_scalar(o)
    if cleaned is not o:
        return cleaned
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _records_to_df(records: list[dict], original: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    Convert array-of-objects back to a DataFrame.

    When the records are empty but we have an `original` reference, preserve
    the original column order — otherwise a transform that wipes all rows
    would also wipe the schema.
    """
    if not records:
        if original is not None and not original.empty:
            return pd.DataFrame(columns=list(original.columns))
        return pd.DataFrame()
    df = pd.DataFrame(records)
    # If JS code added columns, df.columns will reflect that — keep insertion order.
    return df


def _build_result(
    response: dict,
    original_df: pd.DataFrame,
    original_sheets: Optional[dict[str, pd.DataFrame]],
) -> ExecutionResult:
    """Marshall the bridge JSON back into an ExecutionResult."""
    df_records = response.get('df') or []
    sheet_records = response.get('sheets') or {}

    df_out = _records_to_df(df_records, original=original_df)
    sheets_out: dict[str, pd.DataFrame] = {}
    for name, recs in sheet_records.items():
        original = (original_sheets or {}).get(name)
        sheets_out[name] = _records_to_df(recs, original=original)
    if not sheets_out:
        sheets_out = {'default': df_out}

    cf_list = []
    for rule in response.get('conditional_formats') or []:
        if not isinstance(rule, dict):
            continue
        cf_list.append(ConditionalFormat(
            column=rule.get('column', ''),
            rule=rule.get('rule', ''),
            value=rule.get('value'),
            value2=rule.get('value2'),
            style=rule.get('style', ''),
            min_color=rule.get('min_color', '#ef4444'),
            max_color=rule.get('max_color', '#10b981'),
        ))

    return ExecutionResult(
        df=df_out,
        sheets=sheets_out,
        aggregates=response.get('aggregates') or {},
        conditional_formats=cf_list,
        validation_messages=response.get('validation_messages') or [],
        compute_functions=response.get('found_functions') or [],
    )
