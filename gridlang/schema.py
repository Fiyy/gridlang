"""
GridLang Schema — Data layer parsing and validation.

Handles CSV parsing into pandas DataFrames with type inference
and optional schema constraint validation.
"""

from __future__ import annotations

import io
from typing import Optional

import pandas as pd
import numpy as np


class SchemaError(Exception):
    """Raised when data fails schema validation."""

    def __init__(self, violations: list[str]):
        self.violations = violations
        message = f"Schema validation failed ({len(violations)} violation(s)):\n"
        message += "\n".join(f"  - {v}" for v in violations)
        super().__init__(message)


def parse_data(csv_content: str) -> pd.DataFrame:
    """
    Parse the data section CSV into a pandas DataFrame.

    Performs automatic type inference:
      - Integer patterns → int64
      - Float patterns → float64
      - ISO dates → datetime64
      - Boolean (true/false, yes/no) → bool
      - Everything else → string (object)

    Lines starting with `#` are skipped as comments.

    Args:
        csv_content: Raw CSV string from the data section.

    Returns:
        DataFrame with inferred types.
    """
    if not csv_content.strip():
        return pd.DataFrame()

    # Strip lines that start with `#` so inline notes / fallback markers don't
    # become rogue rows. Quoted strings inside cells are unaffected — only
    # whole lines whose first non-whitespace character is `#` are dropped.
    cleaned_lines = [
        line for line in csv_content.splitlines()
        if not line.lstrip().startswith('#')
    ]
    if not cleaned_lines:
        return pd.DataFrame()
    cleaned = '\n'.join(cleaned_lines)

    # Parse CSV
    df = pd.read_csv(
        io.StringIO(cleaned),
        skipinitialspace=True,
        na_values=['', 'NA', 'N/A', 'null', 'None'],
    )

    # Additional type inference for columns that pandas might miss
    for col in df.columns:
        df[col] = _infer_column_type(df[col])

    return df


def _infer_column_type(series: pd.Series) -> pd.Series:
    """Try to infer better types for a column."""
    if series.dtype != object:
        # Already typed by pandas
        return series

    # Skip if all NaN
    non_null = series.dropna()
    if non_null.empty:
        return series

    # Try boolean
    bool_map = {
        'true': True, 'false': False,
        'yes': True, 'no': False,
        'True': True, 'False': False,
        'Yes': True, 'No': False,
    }
    if all(str(v) in bool_map for v in non_null):
        return series.map(lambda x: bool_map.get(str(x), x) if pd.notna(x) else x).astype('boolean')

    # Try datetime (ISO format)
    try:
        result = pd.to_datetime(non_null, format='ISO8601')
        # If successful, convert the whole series
        return pd.to_datetime(series, format='ISO8601', errors='coerce')
    except (ValueError, TypeError):
        pass

    # Try numeric (in case pandas missed it)
    try:
        numeric = pd.to_numeric(non_null)
        return pd.to_numeric(series, errors='coerce')
    except (ValueError, TypeError):
        pass

    return series


def validate_schema(df: pd.DataFrame, schema: dict) -> list[str]:
    """
    Validate DataFrame against schema constraints defined in meta.

    Schema format:
    ```yaml
    schema:
      columns:
        ColumnName:
          type: string|int|float|date|bool
          required: true|false
          min: number
          max: number
          enum: [list, of, values]
    ```

    Args:
        df: DataFrame to validate.
        schema: Schema definition dict from meta section.

    Returns:
        List of violation messages (empty = valid).
    """
    violations = []

    if not schema:
        return violations

    columns_spec = schema.get('columns', {})

    for col_name, constraints in columns_spec.items():
        # Check column exists
        if col_name not in df.columns:
            if constraints.get('required', False):
                violations.append(f"Required column '{col_name}' not found")
            continue

        col = df[col_name]

        # Type check
        expected_type = constraints.get('type')
        if expected_type:
            violations.extend(_check_type(col, col_name, expected_type))

        # Required (no nulls)
        if constraints.get('required', False):
            null_count = col.isna().sum()
            if null_count > 0:
                violations.append(
                    f"Column '{col_name}': {null_count} null value(s) in required column"
                )

        # Min/Max bounds
        if 'min' in constraints:
            min_val = constraints['min']
            below = col.dropna() < min_val
            if below.any():
                violations.append(
                    f"Column '{col_name}': {below.sum()} value(s) below minimum {min_val}"
                )

        if 'max' in constraints:
            max_val = constraints['max']
            above = col.dropna() > max_val
            if above.any():
                violations.append(
                    f"Column '{col_name}': {above.sum()} value(s) above maximum {max_val}"
                )

        # Enum constraint
        if 'enum' in constraints:
            allowed = set(constraints['enum'])
            invalid = col.dropna()[~col.dropna().isin(allowed)]
            if not invalid.empty:
                unique_invalid = invalid.unique()[:5]  # Show first 5
                violations.append(
                    f"Column '{col_name}': invalid value(s): {list(unique_invalid)}. "
                    f"Allowed: {sorted(allowed)}"
                )

    return violations


def _check_type(col: pd.Series, col_name: str, expected_type: str) -> list[str]:
    """Check if column matches expected type."""
    violations = []
    type_checks = {
        'int': lambda s: pd.api.types.is_integer_dtype(s),
        'float': lambda s: pd.api.types.is_float_dtype(s) or pd.api.types.is_integer_dtype(s),
        'string': lambda s: pd.api.types.is_string_dtype(s) or pd.api.types.is_object_dtype(s),
        'date': lambda s: pd.api.types.is_datetime64_any_dtype(s),
        'bool': lambda s: pd.api.types.is_bool_dtype(s),
    }

    checker = type_checks.get(expected_type)
    if checker and not checker(col):
        violations.append(
            f"Column '{col_name}': expected type '{expected_type}', got '{col.dtype}'"
        )

    return violations


def check_schema(df: pd.DataFrame, schema: Optional[dict]) -> None:
    """
    Validate and raise if violations found.

    Args:
        df: DataFrame to validate.
        schema: Schema dict (None = skip validation).

    Raises:
        SchemaError: If validation fails.
    """
    if schema is None:
        return

    violations = validate_schema(df, schema)
    if violations:
        raise SchemaError(violations)
