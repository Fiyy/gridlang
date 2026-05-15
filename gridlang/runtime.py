"""
GridLang Runtime — Sandboxed execution engine for the compute layer.

Executes Python code from the compute section in a restricted environment,
providing only safe modules (pandas, numpy, math, etc.) and blocking
dangerous operations (file I/O, network, system commands).

Supports:
- Single sheet: transform(df) → df
- Multi-sheet: transform(sheets) → sheets (dict[str, DataFrame])
- Aggregates: aggregates(df) → dict
- Conditional formats: conditional_formats() → list[dict]
- Validation: validate(df) → list[str]
- Built-in Excel-style formulas (VLOOKUP, SUMIF, PIVOT, etc.)
"""

from __future__ import annotations

import builtins
import types
from typing import Any, Optional
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

from gridlang.formulas import get_all_formulas


# Modules available in the compute sandbox
SAFE_MODULES = {
    'pandas': pd,
    'pd': pd,
    'numpy': np,
    'np': np,
    'math': __import__('math'),
    'statistics': __import__('statistics'),
    'datetime': __import__('datetime'),
    'decimal': __import__('decimal'),
    'collections': __import__('collections'),
    'itertools': __import__('itertools'),
    'functools': __import__('functools'),
    're': __import__('re'),
}

# Builtins that are safe to expose
SAFE_BUILTINS = {
    'abs', 'all', 'any', 'bin', 'bool', 'chr', 'dict', 'divmod',
    'enumerate', 'filter', 'float', 'format', 'frozenset', 'getattr',
    'hasattr', 'hash', 'hex', 'int', 'isinstance', 'issubclass',
    'iter', 'len', 'list', 'map', 'max', 'min', 'next', 'oct',
    'ord', 'pow', 'print', 'range', 'repr', 'reversed', 'round',
    'set', 'slice', 'sorted', 'str', 'sum', 'tuple', 'type', 'zip',
    # Allow exception types for error handling in user code
    'ValueError', 'TypeError', 'KeyError', 'IndexError', 'Exception',
    'ZeroDivisionError', 'AttributeError', 'RuntimeError',
    'None', 'True', 'False', 'NotImplemented',
}

# Explicitly blocked names
BLOCKED_NAMES = {
    'open', 'exec', 'eval', 'compile', '__import__', 'globals', 'locals',
    'breakpoint', 'exit', 'quit', 'input',
}


class RuntimeError_(Exception):
    """Raised when compute layer execution fails."""

    def __init__(self, message: str, original_error: Optional[Exception] = None):
        self.original_error = original_error
        super().__init__(message)


@dataclass
class ConditionalFormat:
    """A single conditional formatting rule."""
    column: str
    rule: str                    # 'greater_than', 'less_than', 'equals', 'between', 'color_scale', 'data_bar'
    value: Any = None
    value2: Any = None           # For 'between' rule
    style: str = ""              # CSS class name
    min_color: str = "#ef4444"   # For color_scale
    max_color: str = "#10b981"   # For color_scale


@dataclass
class ExecutionResult:
    """Result of executing the compute layer."""

    # Transformed DataFrame (primary/first sheet)
    df: pd.DataFrame

    # All sheets (for multi-sheet mode)
    sheets: dict[str, pd.DataFrame] = field(default_factory=dict)

    # Aggregates dict (empty if aggregates() not defined)
    aggregates: dict = field(default_factory=dict)

    # Conditional formatting rules
    conditional_formats: list[ConditionalFormat] = field(default_factory=list)

    # Validation messages (empty if validate() not defined)
    validation_messages: list[str] = field(default_factory=list)

    # Execution metadata
    compute_functions: list[str] = field(default_factory=list)

    @property
    def is_multi_sheet(self) -> bool:
        return len(self.sheets) > 1


def execute(
    compute_code: str,
    df: pd.DataFrame,
    sheets: Optional[dict[str, pd.DataFrame]] = None,
    extra_modules: Optional[dict[str, Any]] = None,
) -> ExecutionResult:
    """
    Execute compute layer code against the data.

    Supports two modes:
    - Single sheet: transform(df) → df
    - Multi-sheet: transform(sheets) → sheets (auto-detected by function signature)

    Pipeline:
    1. Compile code in sandboxed namespace
    2. Run validate(df) if defined → halt on errors
    3. Run transform(df) or transform(sheets) → get transformed data
    4. Run aggregates(df) if defined → get summary dict
    5. Run conditional_formats() if defined → get formatting rules

    Args:
        compute_code: Python source code from the compute section.
        df: Input DataFrame from the data layer (primary sheet).
        sheets: Dict of sheet_name → DataFrame for multi-sheet mode.
        extra_modules: Additional modules to make available.

    Returns:
        ExecutionResult with transformed df, sheets, aggregates, and metadata.

    Raises:
        RuntimeError_: If execution fails at any stage.
    """
    if not compute_code.strip():
        result_sheets = sheets or {'default': df.copy()}
        return ExecutionResult(
            df=df.copy(),
            sheets=result_sheets,
            compute_functions=[],
        )

    # Build sandboxed namespace with formulas injected
    namespace = _build_namespace(extra_modules)

    # Compile and execute the code to define functions
    try:
        compiled = compile(compute_code, '<compute>', 'exec')
        exec(compiled, namespace)
    except SyntaxError as e:
        raise RuntimeError_(
            f"Syntax error in compute section (line {e.lineno}): {e.msg}",
            original_error=e
        )
    except Exception as e:
        raise RuntimeError_(
            f"Error loading compute section: {type(e).__name__}: {e}",
            original_error=e
        )

    # Discover defined functions
    found_functions = []
    for name in ('validate', 'transform', 'aggregates', 'conditional_formats'):
        if name in namespace and callable(namespace[name]):
            found_functions.append(name)

    # Step 1: validate(df) — optional
    validation_messages = []
    if 'validate' in namespace and callable(namespace['validate']):
        try:
            messages = namespace['validate'](df.copy())
            if messages:
                if isinstance(messages, list):
                    validation_messages = messages
                else:
                    validation_messages = [str(messages)]
                raise RuntimeError_(
                    f"Validation failed:\n" +
                    "\n".join(f"  - {m}" for m in validation_messages)
                )
        except RuntimeError_:
            raise
        except Exception as e:
            raise RuntimeError_(
                f"Error in validate(): {type(e).__name__}: {e}",
                original_error=e
            )

    # Step 2: transform — detect single vs multi-sheet mode
    transformed_df = df.copy()
    result_sheets = sheets.copy() if sheets else {'default': df.copy()}

    if 'transform' in namespace and callable(namespace['transform']):
        transform_fn = namespace['transform']

        # Detect if transform expects 'sheets' (multi-sheet) or 'df' (single)
        import inspect
        try:
            sig = inspect.signature(transform_fn)
            params = list(sig.parameters.keys())
            is_multi_sheet = params and params[0] in ('sheets', 'dfs')
        except (ValueError, TypeError):
            is_multi_sheet = False

        if is_multi_sheet and sheets:
            # Multi-sheet mode
            try:
                input_sheets = {k: v.copy() for k, v in sheets.items()}
                result = transform_fn(input_sheets)
                if result is None:
                    raise RuntimeError_(
                        "transform() returned None. Did you forget 'return sheets'?"
                    )
                if not isinstance(result, dict):
                    raise RuntimeError_(
                        f"transform(sheets) must return a dict, got {type(result).__name__}"
                    )
                result_sheets = result
                # Primary df is the first sheet
                transformed_df = list(result_sheets.values())[0]
            except RuntimeError_:
                raise
            except Exception as e:
                raise RuntimeError_(
                    f"Error in transform(): {type(e).__name__}: {e}",
                    original_error=e
                )
        else:
            # Single sheet mode
            try:
                result = transform_fn(df.copy())
                if result is None:
                    raise RuntimeError_(
                        "transform() returned None. Did you forget 'return df'?"
                    )
                if not isinstance(result, pd.DataFrame):
                    raise RuntimeError_(
                        f"transform() must return a DataFrame, got {type(result).__name__}"
                    )
                transformed_df = result
                result_sheets = {'default': transformed_df}
            except RuntimeError_:
                raise
            except Exception as e:
                raise RuntimeError_(
                    f"Error in transform(): {type(e).__name__}: {e}",
                    original_error=e
                )

    # Step 3: aggregates(df) — optional
    agg_result = {}
    if 'aggregates' in namespace and callable(namespace['aggregates']):
        try:
            agg = namespace['aggregates'](transformed_df.copy())
            if agg is None:
                agg = {}
            if not isinstance(agg, dict):
                raise RuntimeError_(
                    f"aggregates() must return a dict, got {type(agg).__name__}"
                )
            agg_result = agg
        except RuntimeError_:
            raise
        except Exception as e:
            raise RuntimeError_(
                f"Error in aggregates(): {type(e).__name__}: {e}",
                original_error=e
            )

    # Step 4: conditional_formats() — optional
    cond_formats = []
    if 'conditional_formats' in namespace and callable(namespace['conditional_formats']):
        try:
            rules = namespace['conditional_formats']()
            if rules and isinstance(rules, list):
                for rule in rules:
                    if isinstance(rule, dict):
                        cond_formats.append(ConditionalFormat(
                            column=rule.get('column', ''),
                            rule=rule.get('rule', ''),
                            value=rule.get('value'),
                            value2=rule.get('value2'),
                            style=rule.get('style', ''),
                            min_color=rule.get('min_color', '#ef4444'),
                            max_color=rule.get('max_color', '#10b981'),
                        ))
        except Exception as e:
            raise RuntimeError_(
                f"Error in conditional_formats(): {type(e).__name__}: {e}",
                original_error=e
            )

    return ExecutionResult(
        df=transformed_df,
        sheets=result_sheets,
        aggregates=agg_result,
        conditional_formats=cond_formats,
        validation_messages=validation_messages,
        compute_functions=found_functions,
    )


def _build_namespace(extra_modules: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Build a sandboxed namespace for code execution."""
    # Build safe builtins dict
    safe_builtins_dict = {}
    for name in SAFE_BUILTINS:
        obj = getattr(builtins, name, None)
        if obj is not None:
            safe_builtins_dict[name] = obj

    # Add a restricted __import__ that only allows safe modules
    all_allowed = set(SAFE_MODULES.keys())
    if extra_modules:
        all_allowed.update(extra_modules.keys())

    def restricted_import(name, *args, **kwargs):
        if name in SAFE_MODULES:
            return SAFE_MODULES[name]
        if extra_modules and name in extra_modules:
            return extra_modules[name]
        raise ImportError(
            f"Module '{name}' is not available in the GridLang sandbox. "
            f"Available modules: {sorted(SAFE_MODULES.keys())}"
        )

    safe_builtins_dict['__import__'] = restricted_import

    # Build namespace
    namespace: dict[str, Any] = {
        '__builtins__': safe_builtins_dict,
    }

    # Pre-inject safe modules
    namespace.update(SAFE_MODULES)

    # Inject all Excel-style formula functions
    namespace.update(get_all_formulas())

    # Add extra modules
    if extra_modules:
        namespace.update(extra_modules)

    return namespace
