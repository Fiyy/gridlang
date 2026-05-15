"""
GridLang CSV Import/Export — Convert between .csv and .grid formats.

CSV is the most universal data exchange format. This module provides:
- CSV import: csv → .grid (with auto-generated compute/present scaffolding)
- CSV export: .grid → csv (transformed data output)
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional
from datetime import datetime

import pandas as pd
import numpy as np


def import_csv(
    csv_path: str | Path,
    name: Optional[str] = None,
    generate_compute: bool = True,
    generate_present: bool = True,
) -> str:
    """
    Convert a CSV file to .grid format.

    Args:
        csv_path: Path to .csv file.
        name: Document name (defaults to filename).
        generate_compute: Auto-generate a compute section scaffold.
        generate_present: Auto-generate a present section.

    Returns:
        String content of the resulting .grid file.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    # Read CSV to analyze it
    df = pd.read_csv(path)
    csv_content = path.read_text(encoding='utf-8').strip()

    doc_name = name or path.stem.replace('_', ' ').replace('-', ' ').title()

    parts = []

    # --- meta ---
    parts.append("--- meta ---")
    parts.append(f'name: "{doc_name}"')
    parts.append("engine: python")
    parts.append('version: "1.0"')
    parts.append(f'description: "Imported from {path.name}"')
    parts.append(f'import_date: "{datetime.now().strftime("%Y-%m-%d")}"')
    parts.append("")

    # --- data ---
    parts.append("--- data ---")
    parts.append(csv_content)
    parts.append("")

    # --- compute ---
    parts.append("--- compute ---")
    if generate_compute:
        parts.append(_generate_csv_compute(df))
    parts.append("")

    # --- present ---
    parts.append("--- present ---")
    if generate_present:
        parts.append(_generate_csv_present(df))

    return "\n".join(parts)


def import_csv_to_file(
    csv_path: str | Path,
    output_path: str | Path,
    **kwargs,
) -> Path:
    """Import CSV and write to .grid file."""
    content = import_csv(csv_path, **kwargs)
    out = Path(output_path)
    out.write_text(content, encoding='utf-8')
    return out


def export_csv(
    grid_path: str | Path,
    output_path: str | Path,
    sheet: Optional[str] = None,
    raw: bool = False,
) -> Path:
    """
    Export a .grid file to CSV.

    Args:
        grid_path: Path to .grid file.
        output_path: Output .csv path.
        sheet: Specific sheet to export (for multi-sheet files).
        raw: If True, export raw data without running compute.

    Returns:
        Path to the generated .csv file.
    """
    from gridlang.parser import parse_file
    from gridlang.schema import parse_data
    from gridlang.runtime import execute

    doc = parse_file(grid_path)

    if raw:
        # Export raw data without compute
        if sheet and doc.is_multi_sheet and sheet in doc.sheets_raw:
            df = parse_data(doc.sheets_raw[sheet])
        else:
            df = parse_data(doc.data_raw)
    else:
        # Parse and execute compute
        if doc.is_multi_sheet:
            sheets = {name: parse_data(raw_data) for name, raw_data in doc.sheets_raw.items()}
            primary_df = list(sheets.values())[0]
            result = execute(doc.compute_raw, primary_df, sheets=sheets)

            if sheet and sheet in result.sheets:
                df = result.sheets[sheet]
            else:
                df = result.df
        else:
            primary_df = parse_data(doc.data_raw)
            result = execute(doc.compute_raw, primary_df)
            df = result.df

    # Write CSV
    out = Path(output_path)
    df.to_csv(out, index=False)
    return out


def export_csv_string(
    grid_path: str | Path,
    sheet: Optional[str] = None,
    raw: bool = False,
) -> str:
    """Export .grid to CSV string (for stdout output)."""
    from gridlang.parser import parse_file
    from gridlang.schema import parse_data
    from gridlang.runtime import execute

    doc = parse_file(grid_path)

    if raw:
        if sheet and doc.is_multi_sheet and sheet in doc.sheets_raw:
            df = parse_data(doc.sheets_raw[sheet])
        else:
            df = parse_data(doc.data_raw)
    else:
        if doc.is_multi_sheet:
            sheets = {name: parse_data(raw_data) for name, raw_data in doc.sheets_raw.items()}
            primary_df = list(sheets.values())[0]
            result = execute(doc.compute_raw, primary_df, sheets=sheets)
            df = result.sheets.get(sheet, result.df) if sheet else result.df
        else:
            primary_df = parse_data(doc.data_raw)
            result = execute(doc.compute_raw, primary_df)
            df = result.df

    return df.to_csv(index=False)


# =============================================================================
# Internal Helpers
# =============================================================================

def _generate_csv_compute(df: pd.DataFrame) -> str:
    """Auto-generate compute section based on CSV structure."""
    lines = []
    lines.append("def transform(df):")

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    string_cols = df.select_dtypes(include=['object']).columns.tolist()

    if numeric_cols:
        lines.append("    # Numeric columns detected — add your calculations here")
        lines.append(f"    # Available: {', '.join(numeric_cols[:5])}")
        lines.append("    #")
        lines.append("    # Example:")
        if len(numeric_cols) >= 2:
            lines.append(f"    # df['Total'] = df[{numeric_cols[:3]}].sum(axis=1)")
        lines.append("    pass")
    else:
        lines.append("    # Add your transformations here")
        lines.append("    pass")

    lines.append("")
    lines.append("    return df")

    if numeric_cols:
        lines.append("")
        lines.append("def aggregates(df):")
        lines.append("    return {")
        for col in numeric_cols[:4]:
            safe_name = col.lower().replace(' ', '_')
            lines.append(f"        'total_{safe_name}': df['{col}'].sum(),")
        lines.append("    }")

    return "\n".join(lines)


def _generate_csv_present(df: pd.DataFrame) -> str:
    """Auto-generate present section for CSV data."""
    lines = []
    lines.append("<h1>{{ meta.name }}</h1>")
    lines.append("")

    # KPI cards for aggregates
    lines.append("{% if agg %}")
    lines.append('<div class="kpi-grid">')
    lines.append("  {% for key, value in agg.items() %}")
    lines.append('  <div class="kpi">')
    lines.append('    <div class="kpi-value">{{ value }}</div>')
    lines.append('    <div class="kpi-label">{{ key|replace("_", " ")|title }}</div>')
    lines.append("  </div>")
    lines.append("  {% endfor %}")
    lines.append("</div>")
    lines.append("{% endif %}")
    lines.append("")

    # Table
    lines.append("<table>")
    lines.append("  <thead><tr>")
    lines.append("    {% for col in df.columns %}<th>{{ col }}</th>{% endfor %}")
    lines.append("  </tr></thead>")
    lines.append("  <tbody>")
    lines.append("    {% for _, row in df.iterrows() %}")
    lines.append("    <tr>")
    lines.append("      {% for col in df.columns %}<td>{{ row[col] }}</td>{% endfor %}")
    lines.append("    </tr>")
    lines.append("    {% endfor %}")
    lines.append("  </tbody>")
    lines.append("</table>")

    return "\n".join(lines)
