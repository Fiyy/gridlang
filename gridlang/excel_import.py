"""
GridLang Excel Import — Convert .xlsx files to .grid format.

Handles:
- Data extraction (all sheets → CSV)
- Formula detection and conversion to Python (best-effort)
- Format extraction (fonts, colors, conditional formats) → HTML/CSS
- Multi-sheet workbooks → multi data sections
"""

from __future__ import annotations

import re
import io
from pathlib import Path
from typing import Optional
from datetime import datetime

import pandas as pd
import numpy as np
from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils import get_column_letter
try:
    from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, DataBarRule
except ImportError:
    CellIsRule = ColorScaleRule = DataBarRule = None


class ImportError_(Exception):
    """Raised when Excel import fails."""
    pass


def import_excel(
    xlsx_path: str | Path,
    sheet_names: Optional[list[str]] = None,
    include_formulas: bool = True,
    include_styles: bool = True,
) -> str:
    """
    Convert an Excel file to .grid format.

    Args:
        xlsx_path: Path to .xlsx file.
        sheet_names: Specific sheets to import (None = all).
        include_formulas: Attempt to convert formulas to Python.
        include_styles: Extract formatting to HTML/CSS.

    Returns:
        String content of the resulting .grid file.
    """
    path = Path(xlsx_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {path}")

    # Load workbook with data values and formulas
    wb_data = load_workbook(path, data_only=True)
    wb_formulas = load_workbook(path, data_only=False) if include_formulas else None

    # Determine which sheets to process
    available_sheets = wb_data.sheetnames
    if sheet_names:
        for name in sheet_names:
            if name not in available_sheets:
                raise ImportError_(f"Sheet '{name}' not found. Available: {available_sheets}")
        target_sheets = sheet_names
    else:
        target_sheets = available_sheets

    # Build .grid file content
    parts = []

    # --- meta ---
    parts.append("--- meta ---")
    parts.append(f'name: "{path.stem}"')
    parts.append('engine: python')
    parts.append('version: "1.0"')
    parts.append(f'description: "Imported from {path.name}"')
    parts.append(f'imported_from: "{path.name}"')
    parts.append(f'import_date: "{datetime.now().strftime("%Y-%m-%d %H:%M")}"')
    if len(target_sheets) > 1:
        sheets_str = ', '.join(f'"{s}"' for s in target_sheets)
        parts.append(f'sheets: [{sheets_str}]')
    parts.append("")

    # --- data (per sheet) ---
    all_formulas = {}  # sheet_name → list of formula info
    all_styles = {}    # sheet_name → style info

    for sheet_name in target_sheets:
        ws_data = wb_data[sheet_name]
        ws_formula = wb_formulas[sheet_name] if wb_formulas else None

        # Extract data as CSV
        if len(target_sheets) == 1:
            parts.append("--- data ---")
        else:
            safe_name = _safe_sheet_name(sheet_name)
            parts.append(f"--- data:{safe_name} ---")

        csv_content = _extract_sheet_data(ws_data)
        parts.append(csv_content)
        parts.append("")

        # Collect formulas
        if ws_formula and include_formulas:
            formulas = _extract_formulas(ws_formula)
            if formulas:
                all_formulas[sheet_name] = formulas

        # Collect styles
        if include_styles:
            styles = _extract_styles(ws_data)
            if styles:
                all_styles[sheet_name] = styles

    # --- compute ---
    parts.append("--- compute ---")
    compute_code = _generate_compute_section(all_formulas, target_sheets)
    parts.append(compute_code)
    parts.append("")

    # --- present ---
    parts.append("--- present ---")
    present_code = _generate_present_section(all_styles, target_sheets)
    parts.append(present_code)

    wb_data.close()
    if wb_formulas:
        wb_formulas.close()

    return "\n".join(parts)


def import_excel_to_file(
    xlsx_path: str | Path,
    output_path: str | Path,
    **kwargs,
) -> Path:
    """Import Excel and write to .grid file."""
    content = import_excel(xlsx_path, **kwargs)
    out = Path(output_path)
    out.write_text(content, encoding='utf-8')
    return out


# =============================================================================
# Data Extraction
# =============================================================================

def _extract_sheet_data(ws) -> str:
    """Extract sheet data as CSV string."""
    rows = []
    for row in ws.iter_rows(values_only=True):
        # Skip completely empty rows
        if all(cell is None for cell in row):
            continue
        csv_row = []
        for cell in row:
            if cell is None:
                csv_row.append("")
            elif isinstance(cell, datetime):
                csv_row.append(cell.strftime("%Y-%m-%d"))
            elif isinstance(cell, float) and cell == int(cell):
                csv_row.append(str(int(cell)))
            else:
                val = str(cell)
                # Quote if contains comma or quotes
                if ',' in val or '"' in val or '\n' in val:
                    val = '"' + val.replace('"', '""') + '"'
                csv_row.append(val)
        rows.append(','.join(csv_row))

    if not rows:
        return ""

    # Clean column headers (make valid Python identifiers)
    if rows:
        headers = rows[0].split(',')
        clean_headers = [_clean_column_name(h) for h in headers]
        rows[0] = ','.join(clean_headers)

    return '\n'.join(rows)


def _clean_column_name(name: str) -> str:
    """Convert column header to valid Python identifier."""
    name = name.strip().strip('"')
    # Replace spaces and special chars with underscore
    name = re.sub(r'[^\w]', '_', name)
    # Remove leading digits
    name = re.sub(r'^(\d)', r'_\1', name)
    # Remove multiple underscores
    name = re.sub(r'_+', '_', name).strip('_')
    return name or 'Column'


# =============================================================================
# Formula Extraction & Conversion
# =============================================================================

# Excel formula → Python conversion patterns
FORMULA_PATTERNS = [
    # Basic aggregations
    (r'=SUM\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)', r"df['{col}'].sum()"),
    (r'=AVERAGE\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)', r"df['{col}'].mean()"),
    (r'=COUNT\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)', r"df['{col}'].count()"),
    (r'=MAX\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)', r"df['{col}'].max()"),
    (r'=MIN\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)', r"df['{col}'].min()"),
    # IF
    (r'=IF\(([^,]+),([^,]+),([^)]+)\)', r"IF({0}, {1}, {2})"),
    # VLOOKUP
    (r'=VLOOKUP\(', 'VLOOKUP('),
    # SUMIF
    (r'=SUMIF\(', 'SUMIF('),
    (r'=COUNTIF\(', 'COUNTIF('),
]


def _extract_formulas(ws) -> list[dict]:
    """Extract formulas from worksheet."""
    formulas = []
    for row in ws.iter_rows():
        for cell in row:
            if cell.value and isinstance(cell.value, str) and cell.value.startswith('='):
                formulas.append({
                    'cell': cell.coordinate,
                    'column': get_column_letter(cell.column),
                    'col_idx': cell.column - 1,
                    'row': cell.row,
                    'formula': cell.value,
                })
    return formulas


def _convert_formula_to_python(formula: str, headers: list[str] = None) -> str:
    """
    Best-effort conversion of Excel formula to Python expression.
    Returns Python code string, or a comment if conversion isn't possible.

    Supports:
    - Aggregations: SUM, AVERAGE, COUNT, MAX, MIN, STDEV, MEDIAN
    - Arithmetic: A1+B1, A1*2, A1/B1
    - IF: =IF(A1>0, "Yes", "No")
    - VLOOKUP: =VLOOKUP(A1, B:D, 2, FALSE)
    - SUMIF/COUNTIF: =SUMIF(A:A, ">10", B:B)
    - Text: CONCATENATE, LEFT, RIGHT, LEN, TRIM, UPPER, LOWER
    - Math: ROUND, ABS, MOD, POWER, CEILING, FLOOR
    - Date: YEAR, MONTH, DAY, TODAY, NOW
    - Nested formulas (basic level)
    """
    f = formula.strip()
    if not f.startswith('='):
        return f"# Not a formula: {formula}"

    f = f[1:]  # Remove leading '='

    def col_ref(letter):
        """Convert column letter to column name."""
        if headers:
            idx = ord(letter.upper()) - ord('A')
            if idx < len(headers):
                return headers[idx]
        return f"col_{letter}"

    # --- Simple column range aggregations: SUM(B2:B100) ---
    range_match = re.match(r'(\w+)\(([A-Z]+)\d+:([A-Z]+)\d+\)', f)
    if range_match:
        func = range_match.group(1).upper()
        col_letter = range_match.group(2)
        func_map = {
            'SUM': 'sum()', 'AVERAGE': 'mean()', 'COUNT': 'count()',
            'MAX': 'max()', 'MIN': 'min()', 'STDEV': 'std()',
            'MEDIAN': 'median()', 'VAR': 'var()',
            'COUNTA': 'count()', 'COUNTBLANK': 'isna().sum()',
        }
        if func in func_map:
            cn = col_ref(col_letter)
            return f"df['{cn}'].{func_map[func]}"

    # --- IF(condition, true_val, false_val) ---
    if_match = re.match(r'IF\((.+)\)', f, re.IGNORECASE)
    if if_match:
        inner = if_match.group(1)
        parts = _split_formula_args(inner)
        if len(parts) == 3:
            cond = _convert_condition(parts[0].strip(), headers)
            true_val = _convert_value(parts[1].strip(), headers)
            false_val = _convert_value(parts[2].strip(), headers)
            return f"IF({cond}, {true_val}, {false_val})"

    # --- VLOOKUP(value, table, col_idx, exact) ---
    vlookup_match = re.match(r'VLOOKUP\((.+)\)', f, re.IGNORECASE)
    if vlookup_match:
        parts = _split_formula_args(vlookup_match.group(1))
        if len(parts) >= 3:
            lookup_val = _convert_value(parts[0].strip(), headers)
            col_idx = parts[2].strip()
            return f"VLOOKUP({lookup_val}, df, {col_idx})"

    # --- SUMIF(range, criteria, [sum_range]) ---
    sumif_match = re.match(r'SUMIF\((.+)\)', f, re.IGNORECASE)
    if sumif_match:
        parts = _split_formula_args(sumif_match.group(1))
        if len(parts) >= 2:
            col = _convert_range_to_col(parts[0].strip(), headers)
            criteria = parts[1].strip()
            if len(parts) >= 3:
                sum_col = _convert_range_to_col(parts[2].strip(), headers)
                return f"SUMIF(df['{col}'], {criteria}, df['{sum_col}'])"
            return f"SUMIF(df['{col}'], {criteria})"

    # --- COUNTIF(range, criteria) ---
    countif_match = re.match(r'COUNTIF\((.+)\)', f, re.IGNORECASE)
    if countif_match:
        parts = _split_formula_args(countif_match.group(1))
        if len(parts) >= 2:
            col = _convert_range_to_col(parts[0].strip(), headers)
            criteria = parts[1].strip()
            return f"COUNTIF(df['{col}'], {criteria})"

    # --- CONCATENATE(...) ---
    concat_match = re.match(r'CONCATENATE\((.+)\)', f, re.IGNORECASE)
    if concat_match:
        parts = _split_formula_args(concat_match.group(1))
        converted = [_convert_value(p.strip(), headers) for p in parts]
        return f"CONCATENATE({', '.join(converted)})"

    # --- Text functions: LEFT, RIGHT, MID, LEN, TRIM, UPPER, LOWER ---
    text_funcs = {'LEFT': 'LEFT', 'RIGHT': 'RIGHT', 'MID': 'MID',
                  'LEN': 'LEN', 'TRIM': 'TRIM', 'UPPER': 'UPPER', 'LOWER': 'LOWER'}
    for excel_fn, py_fn in text_funcs.items():
        match = re.match(rf'{excel_fn}\((.+)\)', f, re.IGNORECASE)
        if match:
            args = _split_formula_args(match.group(1))
            converted_args = [_convert_value(a.strip(), headers) for a in args]
            return f"{py_fn}({', '.join(converted_args)})"

    # --- ROUND, ABS, MOD, POWER ---
    math_funcs = {'ROUND': 'ROUND', 'ROUNDUP': 'ROUNDUP', 'ROUNDDOWN': 'ROUNDDOWN',
                  'ABS': 'ABS', 'MOD': 'MOD', 'POWER': 'POWER',
                  'CEILING': 'CEILING', 'FLOOR': 'FLOOR'}
    for excel_fn, py_fn in math_funcs.items():
        match = re.match(rf'{excel_fn}\((.+)\)', f, re.IGNORECASE)
        if match:
            args = _split_formula_args(match.group(1))
            converted_args = [_convert_value(a.strip(), headers) for a in args]
            return f"{py_fn}({', '.join(converted_args)})"

    # --- Date functions: YEAR, MONTH, DAY ---
    date_funcs = {'YEAR': 'YEAR', 'MONTH': 'MONTH', 'DAY': 'DAY',
                  'TODAY': 'TODAY', 'NOW': 'NOW'}
    for excel_fn, py_fn in date_funcs.items():
        match = re.match(rf'{excel_fn}\((.+)?\)', f, re.IGNORECASE)
        if match:
            inner = match.group(1)
            if inner:
                arg = _convert_value(inner.strip(), headers)
                return f"{py_fn}({arg})"
            return f"{py_fn}()"

    # --- Simple arithmetic: A1+B1, A1*2, (A1-B1)/C1 ---
    arith_match = re.match(r'([A-Z]+)(\d+)\s*([+\-*/])\s*([A-Z]+)(\d+)', f)
    if arith_match:
        col1 = col_ref(arith_match.group(1))
        op = arith_match.group(3)
        col2 = col_ref(arith_match.group(4))
        return f"df['{col1}'] {op} df['{col2}']"

    # Column * constant: =A1*100, =B2/12
    arith_const = re.match(r'([A-Z]+)(\d+)\s*([+\-*/])\s*([\d.]+)', f)
    if arith_const:
        col1 = col_ref(arith_const.group(1))
        op = arith_const.group(3)
        num = arith_const.group(4)
        return f"df['{col1}'] {op} {num}"

    # Constant * column: =100*A1
    arith_const2 = re.match(r'([\d.]+)\s*([+\-*/])\s*([A-Z]+)(\d+)', f)
    if arith_const2:
        num = arith_const2.group(1)
        op = arith_const2.group(2)
        col1 = col_ref(arith_const2.group(3))
        return f"{num} {op} df['{col1}']"

    # --- Cannot convert — return as comment ---
    return f"# Excel: ={f}"


def _split_formula_args(s: str) -> list[str]:
    """Split formula arguments respecting nested parentheses and quotes."""
    parts = []
    depth = 0
    in_string = False
    current = []

    for ch in s:
        if ch == '"' and depth == 0:
            in_string = not in_string
            current.append(ch)
        elif ch == '(' and not in_string:
            depth += 1
            current.append(ch)
        elif ch == ')' and not in_string:
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0 and not in_string:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)

    if current:
        parts.append(''.join(current))

    return parts


def _convert_condition(cond: str, headers: list[str] = None) -> str:
    """Convert an Excel condition to Python."""
    # Cell reference comparisons: A1>0, B2="Yes"
    match = re.match(r'([A-Z]+)(\d+)\s*(>=|<=|<>|>|<|=)\s*(.+)', cond)
    if match:
        col_letter = match.group(1)
        op = match.group(3)
        value = match.group(4).strip()

        col_name = col_letter
        if headers:
            idx = ord(col_letter.upper()) - ord('A')
            if idx < len(headers):
                col_name = headers[idx]

        # Convert operator
        op_map = {'=': '==', '<>': '!=', '>': '>', '<': '<', '>=': '>=', '<=': '<='}
        py_op = op_map.get(op, op)

        return f"df['{col_name}'] {py_op} {value}"

    return cond


def _convert_value(val: str, headers: list[str] = None) -> str:
    """Convert a value reference to Python."""
    # Cell reference: A1, B2
    match = re.match(r'^([A-Z]+)(\d+)$', val)
    if match:
        col_letter = match.group(1)
        if headers:
            idx = ord(col_letter.upper()) - ord('A')
            if idx < len(headers):
                return f"df['{headers[idx]}']"
        return f"df['col_{col_letter}']"

    # Already a string literal or number
    return val


def _convert_range_to_col(range_str: str, headers: list[str] = None) -> str:
    """Convert A:A or A1:A100 to column name."""
    match = re.match(r'([A-Z]+)(?::\1|\d*:[A-Z]+\d*)', range_str)
    if match:
        col_letter = match.group(1)
        if headers:
            idx = ord(col_letter.upper()) - ord('A')
            if idx < len(headers):
                return headers[idx]
        return f"col_{col_letter}"
    return range_str


def _generate_compute_section(all_formulas: dict, sheet_names: list[str]) -> str:
    """Generate compute section from extracted formulas."""
    lines = []
    lines.append("def transform(df):")

    has_formulas = any(formulas for formulas in all_formulas.values())

    if has_formulas:
        lines.append("    # Auto-converted from Excel formulas")
        lines.append("    # Review and adjust as needed")
        lines.append("")

        for sheet_name, formulas in all_formulas.items():
            if len(all_formulas) > 1:
                lines.append(f"    # --- Sheet: {sheet_name} ---")

            # Group formulas by type
            seen_patterns = set()
            for f_info in formulas:
                python_code = _convert_formula_to_python(f_info['formula'])
                # Avoid duplicates (same formula applied to many rows)
                pattern_key = re.sub(r'\d+', 'N', f_info['formula'])
                if pattern_key not in seen_patterns:
                    seen_patterns.add(pattern_key)
                    if python_code.startswith('#'):
                        lines.append(f"    {python_code}")
                    else:
                        lines.append(f"    # Cell {f_info['cell']}: {f_info['formula']}")
                        lines.append(f"    # → {python_code}")
        lines.append("")
    else:
        lines.append("    # No formulas detected — add your transformations here")
        lines.append("    pass")
        lines.append("")

    lines.append("    return df")

    return "\n".join(lines)


# =============================================================================
# Style Extraction
# =============================================================================

def _extract_styles(ws) -> dict:
    """Extract basic styling information from worksheet."""
    styles = {
        'has_bold_header': False,
        'has_colors': False,
        'number_formats': {},
        'conditional_formats': [],
        'column_widths': {},
        'merged_cells': [],
    }

    # Check header row styling
    first_row = list(ws.iter_rows(min_row=1, max_row=1))[0] if ws.max_row else []
    for cell in first_row:
        if cell.font and cell.font.bold:
            styles['has_bold_header'] = True
            break

    # Check for number formats
    for row in ws.iter_rows(min_row=2, max_row=min(10, ws.max_row or 1)):
        for cell in row:
            if cell.number_format and cell.number_format != 'General':
                col_letter = get_column_letter(cell.column)
                styles['number_formats'][col_letter] = cell.number_format

    # Extract conditional formatting rules
    try:
        for cf_rule in ws.conditional_formatting:
            for rule in cf_rule.rules:
                cf_info = {'range': str(cf_rule)}
                rule_type = type(rule).__name__
                if rule_type == 'CellIsRule':
                    cf_info['type'] = 'cell_is'
                    cf_info['operator'] = getattr(rule, 'operator', '')
                    cf_info['formula'] = rule.formula if hasattr(rule, 'formula') and rule.formula else []
                elif rule_type == 'ColorScaleRule':
                    cf_info['type'] = 'color_scale'
                elif rule_type == 'DataBarRule':
                    cf_info['type'] = 'data_bar'
                else:
                    cf_info['type'] = rule_type
                styles['conditional_formats'].append(cf_info)
    except Exception:
        pass  # Skip conditional format extraction on error

    # Column widths
    for col_dim in ws.column_dimensions.values():
        if col_dim.width:
            styles['column_widths'][col_dim.index] = col_dim.width

    # Merged cells
    for merged_range in ws.merged_cells.ranges:
        styles['merged_cells'].append({
            'range': str(merged_range),
            'min_row': merged_range.min_row,
            'max_row': merged_range.max_row,
            'min_col': merged_range.min_col,
            'max_col': merged_range.max_col,
            'colspan': merged_range.max_col - merged_range.min_col + 1,
            'rowspan': merged_range.max_row - merged_range.min_row + 1,
        })

    return styles


def _generate_present_section(all_styles: dict, sheet_names: list[str]) -> str:
    """Generate present section from extracted styles."""
    lines = []

    # Generate style block
    has_special_styles = any(
        s.get('has_colors') or s.get('conditional_formats')
        for s in all_styles.values()
    )

    lines.append('<style>')
    lines.append('  .imported-table { width: 100%; border-collapse: collapse; }')
    lines.append('  .imported-table th { background: #1e40af; color: white; padding: 0.6rem; '
                 'text-align: left; font-weight: 600; }')
    lines.append('  .imported-table td { padding: 0.5rem; border-bottom: 1px solid #e5e7eb; }')
    lines.append('  .imported-table tr:nth-child(even) { background: #f9fafb; }')
    lines.append('  .imported-table tr:hover { background: #eff6ff; }')
    lines.append('  .number { text-align: right; font-variant-numeric: tabular-nums; }')
    lines.append('</style>')
    lines.append('')
    lines.append('<h1>{{ meta.name }}</h1>')
    lines.append('<p><em>Imported from {{ meta.imported_from }}</em></p>')
    lines.append('')

    # Multi-sheet tabs
    if len(sheet_names) > 1:
        lines.append('<div class="sheet-tabs">')
        for i, name in enumerate(sheet_names):
            active = ' active' if i == 0 else ''
            lines.append(f'  <div class="sheet-tab{active}">{name}</div>')
        lines.append('</div>')
        lines.append('')

    # Table
    lines.append('<table class="imported-table">')
    lines.append('  <thead><tr>')
    lines.append('    {% for col in df.columns %}<th>{{ col }}</th>{% endfor %}')
    lines.append('  </tr></thead>')
    lines.append('  <tbody>')
    lines.append('    {% for _, row in df.iterrows() %}')
    lines.append('    <tr>')
    lines.append('      {% for col in df.columns %}')
    lines.append('      <td>{{ row[col] }}</td>')
    lines.append('      {% endfor %}')
    lines.append('    </tr>')
    lines.append('    {% endfor %}')
    lines.append('  </tbody>')
    lines.append('</table>')

    return '\n'.join(lines)


def _safe_sheet_name(name: str) -> str:
    """Convert sheet name to safe section identifier."""
    safe = re.sub(r'[^\w]', '_', name.strip())
    safe = re.sub(r'_+', '_', safe).strip('_').lower()
    return safe or 'sheet'
