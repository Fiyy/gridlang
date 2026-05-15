"""
GridLang Formulas — Excel-compatible built-in function library.

Provides familiar Excel-style functions that can be used directly
in the compute layer, bridging the gap for users transitioning from Excel.

All functions operate on pandas Series/DataFrames and are vectorized where possible.
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any, Union

import numpy as np
import pandas as pd


# =============================================================================
# Statistical / Aggregation Functions
# =============================================================================

def SUMIF(series: pd.Series, condition, sum_series: pd.Series = None) -> float:
    """
    Sum values where condition is met.

    Args:
        series: Series to evaluate condition against.
        condition: A callable (lambda), a value (equals), or a string like ">10".
        sum_series: Series to sum (defaults to same series if not provided).
    """
    mask = _parse_condition(series, condition)
    target = sum_series if sum_series is not None else series
    return float(target[mask].sum())


def COUNTIF(series: pd.Series, condition) -> int:
    """Count values where condition is met."""
    mask = _parse_condition(series, condition)
    return int(mask.sum())


def AVERAGEIF(series: pd.Series, condition, avg_series: pd.Series = None) -> float:
    """Average values where condition is met."""
    mask = _parse_condition(series, condition)
    target = avg_series if avg_series is not None else series
    filtered = target[mask]
    return float(filtered.mean()) if len(filtered) > 0 else 0.0


def SUMIFS(sum_series: pd.Series, *criteria_pairs) -> float:
    """
    Sum with multiple conditions.
    Usage: SUMIFS(df['Revenue'], df['Region'], 'North', df['Year'], 2024)
    """
    mask = pd.Series(True, index=sum_series.index)
    for i in range(0, len(criteria_pairs), 2):
        criteria_series = criteria_pairs[i]
        condition = criteria_pairs[i + 1]
        mask = mask & _parse_condition(criteria_series, condition)
    return float(sum_series[mask].sum())


def COUNTIFS(*criteria_pairs) -> int:
    """Count with multiple conditions."""
    if len(criteria_pairs) < 2:
        return 0
    mask = pd.Series(True, index=criteria_pairs[0].index)
    for i in range(0, len(criteria_pairs), 2):
        criteria_series = criteria_pairs[i]
        condition = criteria_pairs[i + 1]
        mask = mask & _parse_condition(criteria_series, condition)
    return int(mask.sum())


# =============================================================================
# Lookup Functions
# =============================================================================

def VLOOKUP(lookup_value, table: pd.DataFrame, col_index: int, exact: bool = True) -> Any:
    """
    Vertical lookup — search first column of table for value, return col_index column.

    Args:
        lookup_value: Value to find in first column.
        table: DataFrame to search.
        col_index: 1-based column index to return.
        exact: If True, require exact match. If False, approximate (sorted data).
    """
    first_col = table.iloc[:, 0]
    if exact:
        matches = table[first_col == lookup_value]
        if matches.empty:
            return None
        return matches.iloc[0, col_index - 1]
    else:
        # Approximate match (assumes sorted)
        sorted_table = table.sort_values(table.columns[0])
        first_col_sorted = sorted_table.iloc[:, 0]
        valid = sorted_table[first_col_sorted <= lookup_value]
        if valid.empty:
            return None
        return valid.iloc[-1, col_index - 1]


def HLOOKUP(lookup_value, table: pd.DataFrame, row_index: int, exact: bool = True) -> Any:
    """
    Horizontal lookup — search first row (columns) for value, return row_index row.
    """
    cols = table.columns.tolist()
    if exact:
        if lookup_value not in cols:
            return None
        col_pos = cols.index(lookup_value)
        return table.iloc[row_index - 1, col_pos]
    else:
        matching = [c for c in cols if c <= lookup_value]
        if not matching:
            return None
        col_pos = cols.index(matching[-1])
        return table.iloc[row_index - 1, col_pos]


def INDEX(table: pd.DataFrame, row: int, col: int = None) -> Any:
    """
    Return value at specified row/col position (1-based).
    """
    if col is None:
        return table.iloc[row - 1]
    return table.iloc[row - 1, col - 1]


def MATCH(lookup_value, series: pd.Series, match_type: int = 0) -> int:
    """
    Find position of value in series (1-based).

    match_type: 0=exact, 1=largest<=value, -1=smallest>=value
    """
    if match_type == 0:
        matches = series[series == lookup_value]
        if matches.empty:
            return 0
        return int(matches.index[0]) + 1
    elif match_type == 1:
        valid = series[series <= lookup_value]
        if valid.empty:
            return 0
        return int(valid.idxmax()) + 1
    else:
        valid = series[series >= lookup_value]
        if valid.empty:
            return 0
        return int(valid.idxmin()) + 1


def XLOOKUP(lookup_value, lookup_series: pd.Series, return_series: pd.Series,
            if_not_found=None) -> Any:
    """Modern lookup — more flexible than VLOOKUP."""
    matches = return_series[lookup_series == lookup_value]
    if matches.empty:
        return if_not_found
    return matches.iloc[0]


# =============================================================================
# Text Functions
# =============================================================================

def LEFT(text, n: int = 1) -> str:
    """Return leftmost n characters."""
    if pd.isna(text):
        return ""
    return str(text)[:n]


def RIGHT(text, n: int = 1) -> str:
    """Return rightmost n characters."""
    if pd.isna(text):
        return ""
    return str(text)[-n:]


def MID(text, start: int, n: int) -> str:
    """Return n characters starting at position start (1-based)."""
    if pd.isna(text):
        return ""
    return str(text)[start - 1:start - 1 + n]


def CONCATENATE(*args) -> str:
    """Join values into a single string."""
    return "".join(str(a) if not pd.isna(a) else "" for a in args)


def TRIM(text) -> str:
    """Remove leading/trailing whitespace."""
    if pd.isna(text):
        return ""
    return str(text).strip()


def UPPER(text) -> str:
    """Convert to uppercase."""
    if pd.isna(text):
        return ""
    return str(text).upper()


def LOWER(text) -> str:
    """Convert to lowercase."""
    if pd.isna(text):
        return ""
    return str(text).lower()


def PROPER(text) -> str:
    """Capitalize first letter of each word."""
    if pd.isna(text):
        return ""
    return str(text).title()


def SUBSTITUTE(text, old: str, new: str, instance: int = None) -> str:
    """Replace occurrences of old with new."""
    if pd.isna(text):
        return ""
    s = str(text)
    if instance is None:
        return s.replace(old, new)
    # Replace nth instance only
    count = 0
    result = []
    i = 0
    while i < len(s):
        if s[i:i + len(old)] == old:
            count += 1
            if count == instance:
                result.append(new)
            else:
                result.append(old)
            i += len(old)
        else:
            result.append(s[i])
            i += 1
    return "".join(result)


def LEN(text) -> int:
    """Return length of text."""
    if pd.isna(text):
        return 0
    return len(str(text))


def TEXT(value, format_str: str = "") -> str:
    """Format a value as text (simplified)."""
    if pd.isna(value):
        return ""
    if isinstance(value, (int, float)):
        if '%' in format_str:
            return f"{value:.1%}" if isinstance(value, float) and value < 1 else f"{value}%"
        if '.' in format_str:
            decimals = len(format_str.split('.')[-1].rstrip('0#'))
            return f"{value:,.{decimals}f}"
        return f"{value:,.0f}"
    return str(value)


# =============================================================================
# Date Functions
# =============================================================================

def YEAR(d) -> int:
    """Extract year from date."""
    d = _to_datetime(d)
    return d.year if d else 0


def MONTH(d) -> int:
    """Extract month from date."""
    d = _to_datetime(d)
    return d.month if d else 0


def DAY(d) -> int:
    """Extract day from date."""
    d = _to_datetime(d)
    return d.day if d else 0


def WEEKDAY(d, return_type: int = 1) -> int:
    """Return day of week (1=Sun...7=Sat for type 1)."""
    d = _to_datetime(d)
    if not d:
        return 0
    dow = d.weekday()  # 0=Mon ... 6=Sun
    if return_type == 1:
        return (dow + 2) % 7 or 7  # 1=Sun ... 7=Sat
    elif return_type == 2:
        return dow + 1  # 1=Mon ... 7=Sun
    return dow  # 0=Mon ... 6=Sun


def DATEDIF(start, end, unit: str = "D") -> int:
    """
    Calculate difference between dates.
    unit: "D" (days), "M" (months), "Y" (years)
    """
    start = _to_datetime(start)
    end = _to_datetime(end)
    if not start or not end:
        return 0

    if unit.upper() == "D":
        return (end - start).days
    elif unit.upper() == "M":
        return (end.year - start.year) * 12 + (end.month - start.month)
    elif unit.upper() == "Y":
        return end.year - start.year
    return 0


def NETWORKDAYS(start, end, holidays=None) -> int:
    """Count working days between two dates (excluding weekends)."""
    start = _to_datetime(start)
    end = _to_datetime(end)
    if not start or not end:
        return 0

    business_days = pd.bdate_range(start, end)
    count = len(business_days)

    if holidays:
        holidays_dt = [_to_datetime(h) for h in holidays]
        for h in holidays_dt:
            if h and h in business_days:
                count -= 1

    return count


def TODAY() -> date:
    """Return current date."""
    return date.today()


def NOW() -> datetime:
    """Return current datetime."""
    return datetime.now()


def EDATE(start, months: int):
    """Return date that is N months from start."""
    start = _to_datetime(start)
    if not start:
        return None
    month = start.month + months
    year = start.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(start.day, [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30,
                           31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


# =============================================================================
# Logic Functions
# =============================================================================

def IF(condition, true_val, false_val=None):
    """
    Conditional — works on both scalars and Series.

    For Series: returns a Series with values based on condition.
    For scalars: returns true_val or false_val.
    """
    if isinstance(condition, pd.Series):
        return pd.Series(
            np.where(condition, true_val, false_val),
            index=condition.index
        )
    return true_val if condition else false_val


def IFS(*args) -> Any:
    """
    Multiple conditions — first True wins.
    Usage: IFS(cond1, val1, cond2, val2, ...)
    """
    for i in range(0, len(args), 2):
        if i + 1 < len(args) and args[i]:
            return args[i + 1]
    return None


def SWITCH(value, *pairs) -> Any:
    """
    Match value against pairs.
    Usage: SWITCH(val, match1, result1, match2, result2, ..., default)
    """
    for i in range(0, len(pairs) - 1, 2):
        if value == pairs[i]:
            return pairs[i + 1]
    # Last unpaired value is default
    if len(pairs) % 2 == 1:
        return pairs[-1]
    return None


def AND(*conditions) -> bool:
    """All conditions must be True."""
    return all(conditions)


def OR(*conditions) -> bool:
    """At least one condition must be True."""
    return any(conditions)


def NOT(condition) -> bool:
    """Negate condition."""
    if isinstance(condition, pd.Series):
        return ~condition
    return not condition


def IFERROR(value, value_if_error):
    """Return value_if_error if value raises an error or is NaN."""
    if isinstance(value, pd.Series):
        return value.fillna(value_if_error)
    try:
        if pd.isna(value):
            return value_if_error
        return value
    except Exception:
        return value_if_error


# =============================================================================
# Math Functions
# =============================================================================

def ROUND(value, decimals: int = 0):
    """Round to specified decimals."""
    if isinstance(value, pd.Series):
        return value.round(decimals)
    return round(float(value), decimals)


def ROUNDUP(value, decimals: int = 0):
    """Round up (away from zero)."""
    factor = 10 ** decimals
    if isinstance(value, pd.Series):
        return (value * factor).apply(np.ceil) / factor
    return np.ceil(float(value) * factor) / factor


def ROUNDDOWN(value, decimals: int = 0):
    """Round down (toward zero)."""
    factor = 10 ** decimals
    if isinstance(value, pd.Series):
        return (value * factor).apply(np.floor) / factor
    return np.floor(float(value) * factor) / factor


def ABS(value):
    """Absolute value."""
    if isinstance(value, pd.Series):
        return value.abs()
    return abs(value)


def MOD(number, divisor):
    """Modulo (remainder)."""
    if isinstance(number, pd.Series):
        return number % divisor
    return number % divisor


def POWER(base, exponent):
    """Raise base to power."""
    if isinstance(base, pd.Series):
        return base ** exponent
    return base ** exponent


def CEILING(number, significance: float = 1):
    """Round up to nearest multiple of significance."""
    if isinstance(number, pd.Series):
        return (number / significance).apply(np.ceil) * significance
    return np.ceil(float(number) / significance) * significance


def FLOOR(number, significance: float = 1):
    """Round down to nearest multiple of significance."""
    if isinstance(number, pd.Series):
        return (number / significance).apply(np.floor) * significance
    return np.floor(float(number) / significance) * significance


# =============================================================================
# Statistical Functions
# =============================================================================

def RANK(value, series: pd.Series, order: int = 0):
    """
    Return rank of value in series.
    order: 0=descending (largest=1), 1=ascending (smallest=1)
    """
    if isinstance(value, pd.Series):
        return value.rank(ascending=(order == 1)).astype(int)
    ascending = (order == 1)
    ranked = series.rank(ascending=ascending)
    matches = ranked[series == value]
    return int(matches.iloc[0]) if not matches.empty else 0


def PERCENTILE(series: pd.Series, pct: float) -> float:
    """Return the percentile value of series."""
    return float(series.quantile(pct))


def QUARTILE(series: pd.Series, quart: int) -> float:
    """Return quartile (0=min, 1=25%, 2=50%, 3=75%, 4=max)."""
    return float(series.quantile(quart / 4.0))


def MEDIAN(series: pd.Series) -> float:
    """Return median."""
    return float(series.median())


def STDEV(series: pd.Series) -> float:
    """Return standard deviation (sample)."""
    return float(series.std())


def VAR(series: pd.Series) -> float:
    """Return variance (sample)."""
    return float(series.var())


def LARGE(series: pd.Series, k: int) -> float:
    """Return k-th largest value."""
    sorted_vals = series.dropna().sort_values(ascending=False)
    if k > len(sorted_vals):
        return float('nan')
    return float(sorted_vals.iloc[k - 1])


def SMALL(series: pd.Series, k: int) -> float:
    """Return k-th smallest value."""
    sorted_vals = series.dropna().sort_values(ascending=True)
    if k > len(sorted_vals):
        return float('nan')
    return float(sorted_vals.iloc[k - 1])


# =============================================================================
# Data Analysis Functions (Excel Power Features)
# =============================================================================

def PIVOT(df: pd.DataFrame, index, columns=None, values=None,
          aggfunc='sum', fill_value=0) -> pd.DataFrame:
    """
    Create pivot table (equivalent to Excel Pivot Table).

    Args:
        df: Source DataFrame.
        index: Column(s) for row grouping.
        columns: Column(s) for column grouping.
        values: Column(s) to aggregate.
        aggfunc: 'sum', 'mean', 'count', 'min', 'max', or callable.
        fill_value: Fill NaN with this value.
    """
    result = pd.pivot_table(
        df,
        index=index,
        columns=columns,
        values=values,
        aggfunc=aggfunc,
        fill_value=fill_value,
    )
    # Flatten multi-level columns if needed
    if isinstance(result.columns, pd.MultiIndex):
        result.columns = ['_'.join(str(c) for c in col).strip('_')
                         for col in result.columns.values]
    return result.reset_index()


def UNIQUE(series: pd.Series) -> pd.Series:
    """Return unique values."""
    return pd.Series(series.unique())


def SORT(df: pd.DataFrame, by, ascending: bool = True) -> pd.DataFrame:
    """Sort DataFrame by column(s)."""
    return df.sort_values(by=by, ascending=ascending).reset_index(drop=True)


def FILTER(df: pd.DataFrame, condition: pd.Series) -> pd.DataFrame:
    """Filter rows by condition."""
    return df[condition].reset_index(drop=True)


def GROUPBY(df: pd.DataFrame, by, agg: dict = None) -> pd.DataFrame:
    """
    Group and aggregate (simplified pivot).
    Usage: GROUPBY(df, 'Region', {'Revenue': 'sum', 'Count': 'count'})
    """
    if agg:
        result = df.groupby(by).agg(agg).reset_index()
    else:
        result = df.groupby(by).sum(numeric_only=True).reset_index()
    return result


def TRANSPOSE(df: pd.DataFrame) -> pd.DataFrame:
    """Transpose rows and columns."""
    return df.set_index(df.columns[0]).T.reset_index()


# =============================================================================
# Helper Functions (Internal)
# =============================================================================

def _parse_condition(series: pd.Series, condition) -> pd.Series:
    """Parse condition into boolean mask."""
    if callable(condition):
        return series.apply(condition).astype(bool)
    elif isinstance(condition, str):
        # Parse string conditions like ">10", "<=5", "<>0"
        condition = condition.strip()
        if condition.startswith('>='):
            return series >= float(condition[2:])
        elif condition.startswith('<='):
            return series <= float(condition[2:])
        elif condition.startswith('<>'):
            val = condition[2:]
            try:
                return series != float(val)
            except ValueError:
                return series != val
        elif condition.startswith('>'):
            return series > float(condition[1:])
        elif condition.startswith('<'):
            return series < float(condition[1:])
        elif condition.startswith('='):
            val = condition[1:]
            try:
                return series == float(val)
            except ValueError:
                return series == val
        elif '*' in condition or '?' in condition:
            # Wildcard matching
            pattern = condition.replace('*', '.*').replace('?', '.')
            return series.astype(str).str.match(pattern, case=False)
        else:
            # Exact match
            try:
                return series == float(condition)
            except ValueError:
                return series == condition
    else:
        # Direct value comparison
        return series == condition


def _to_datetime(d) -> datetime | None:
    """Convert various date types to datetime."""
    if d is None or (isinstance(d, float) and np.isnan(d)):
        return None
    if isinstance(d, datetime):
        return d
    if isinstance(d, date):
        return datetime(d.year, d.month, d.day)
    if isinstance(d, pd.Timestamp):
        return d.to_pydatetime()
    if isinstance(d, str):
        try:
            return pd.to_datetime(d).to_pydatetime()
        except Exception:
            return None
    return None


# =============================================================================
# Export all formula functions for runtime injection
# =============================================================================

def get_all_formulas() -> dict:
    """Return all formula functions as a dict for namespace injection."""
    import inspect
    module = inspect.getmodule(get_all_formulas)
    formulas = {}
    for name, obj in inspect.getmembers(module):
        if (inspect.isfunction(obj)
                and not name.startswith('_')
                and name != 'get_all_formulas'
                and name[0].isupper()):
            formulas[name] = obj
    return formulas
