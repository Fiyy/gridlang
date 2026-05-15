"""
GridLang Renderer — Renders the present layer as HTML.

Uses Jinja2 to process the HTML template with injected context
(transformed DataFrame, aggregates, meta info) and provides
built-in helper functions for common formatting tasks.

Supports:
- Full chart library (line, pie, scatter, area, bar, stacked, heatmap, sparkline)
- Conditional formatting (auto-applied CSS based on rules)
- Frozen headers (sticky table headers via CSS)
- Multi-sheet tab rendering
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd
import numpy as np
from jinja2 import Environment, BaseLoader, TemplateSyntaxError, UndefinedError

from gridlang.charts import get_all_charts
from gridlang.runtime import ConditionalFormat


# Default HTML wrapper and styles
DEFAULT_STYLES = """
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         line-height: 1.6; color: #1a1a1a; max-width: 1100px; margin: 0 auto; padding: 2rem; }
  h1, h2, h3 { color: #111827; }
  table { width: 100%; border-collapse: collapse; margin: 1.5rem 0; font-size: 0.9rem; }
  th { background: #f1f5f9; color: #374151; font-weight: 600; text-align: left;
       padding: 0.75rem; border-bottom: 2px solid #e2e8f0; position: sticky; top: 0; z-index: 1; }
  td { padding: 0.75rem; border-bottom: 1px solid #f1f5f9; }
  tr:hover { background: #f8fafc; }
  td:last-child, th:last-child { text-align: right; }
  .number { text-align: right; font-variant-numeric: tabular-nums; }
  .positive { color: #059669; }
  .negative { color: #dc2626; }
  .highlight-red { background-color: #fef2f2; color: #991b1b; }
  .highlight-green { background-color: #ecfdf5; color: #065f46; }
  .highlight-yellow { background-color: #fffbeb; color: #92400e; }
  .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
              gap: 1rem; margin: 1.5rem 0; }
  .kpi { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px;
         padding: 1.25rem; text-align: center; }
  .kpi-value { font-size: 1.75rem; font-weight: 700; color: #2563eb; }
  .kpi-label { font-size: 0.85rem; color: #64748b; margin-top: 0.25rem; }
  .sheet-tabs { display: flex; gap: 0; border-bottom: 2px solid #e2e8f0; margin-bottom: 1.5rem; }
  .sheet-tab { padding: 0.5rem 1.25rem; cursor: pointer; border: 1px solid #e2e8f0;
               border-bottom: none; border-radius: 6px 6px 0 0; background: #f8fafc;
               font-size: 0.85rem; margin-bottom: -2px; }
  .sheet-tab.active { background: white; border-bottom: 2px solid white; font-weight: 600; }
  .data-bar { background: linear-gradient(90deg, #3b82f6 var(--bar-width), transparent var(--bar-width));
              background-size: 100% 100%; background-repeat: no-repeat; }
  @media print { body { max-width: none; } th { position: static; } }
</style>
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ title }}</title>
  {{ styles }}
</head>
<body>
{{ content }}
</body>
</html>
"""


class RenderError(Exception):
    """Raised when template rendering fails."""
    pass


def render(
    template_content: str,
    df: pd.DataFrame,
    aggregates: dict[str, Any],
    meta: dict[str, Any],
    raw_df: Optional[pd.DataFrame] = None,
    sheets: Optional[dict[str, pd.DataFrame]] = None,
    conditional_formats: Optional[list[ConditionalFormat]] = None,
    standalone: bool = True,
) -> str:
    """
    Render the present layer template.

    Args:
        template_content: HTML/Jinja2 template string from the present section.
        df: Transformed DataFrame (primary sheet).
        aggregates: Aggregates dict from compute layer.
        meta: Meta section dict.
        raw_df: Original untransformed DataFrame (optional).
        sheets: All sheets dict for multi-sheet mode.
        conditional_formats: Conditional formatting rules.
        standalone: If True, wrap in full HTML document with default styles.

    Returns:
        Rendered HTML string.

    Raises:
        RenderError: If template rendering fails.
    """
    if not template_content.strip():
        template_content = _generate_default_template(df, aggregates, sheets)

    # Set up Jinja2 environment
    env = Environment(
        loader=BaseLoader(),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    # Register all helper functions (formatting + charts)
    env.globals.update(_get_helpers())
    env.globals.update(get_all_charts())

    # Add conditional format helper
    cf_rules = conditional_formats or []
    env.globals['cond_style'] = lambda col, value: _apply_conditional_format(col, value, cf_rules)
    env.globals['cond_class'] = lambda col, value: _apply_conditional_class(col, value, cf_rules)

    # Compile template
    try:
        template = env.from_string(template_content)
    except TemplateSyntaxError as e:
        raise RenderError(f"Template syntax error (line {e.lineno}): {e.message}")

    # Build context
    context = {
        'df': df,
        'agg': aggregates,
        'meta': meta,
        'raw_df': raw_df if raw_df is not None else df,
        'sheets': sheets or {'default': df},
    }

    # Render
    try:
        rendered = template.render(**context)
    except UndefinedError as e:
        raise RenderError(f"Template variable error: {e}")
    except Exception as e:
        raise RenderError(f"Template render error: {type(e).__name__}: {e}")

    # Wrap in full HTML document if standalone
    if standalone:
        has_custom_style = '<style' in template_content.lower()
        styles = "" if has_custom_style else DEFAULT_STYLES

        # Add conditional format CSS
        if cf_rules:
            styles += _generate_conditional_css(cf_rules, df)

        wrapper_env = Environment(loader=BaseLoader(), autoescape=False)
        wrapper = wrapper_env.from_string(HTML_TEMPLATE)
        rendered = wrapper.render(
            title=meta.get('name', 'GridLang Document'),
            styles=styles,
            content=rendered,
        )

    return rendered


def _generate_default_template(
    df: pd.DataFrame,
    aggregates: dict,
    sheets: Optional[dict[str, pd.DataFrame]] = None,
) -> str:
    """Generate a default table template when no present section is given."""
    parts = []

    # Aggregates as KPI cards
    if aggregates:
        parts.append('<div class="kpi-grid">')
        for key, value in aggregates.items():
            label = key.replace('_', ' ').title()
            if isinstance(value, float):
                display = f"{value:,.2f}"
            else:
                display = str(value)
            parts.append(f'  <div class="kpi"><div class="kpi-value">{display}</div>'
                        f'<div class="kpi-label">{label}</div></div>')
        parts.append('</div>')

    # Multi-sheet tabs
    if sheets and len(sheets) > 1:
        parts.append('<div class="sheet-tabs">')
        for i, name in enumerate(sheets.keys()):
            active = ' active' if i == 0 else ''
            parts.append(f'  <div class="sheet-tab{active}">{name}</div>')
        parts.append('</div>')

    # Table
    if not df.empty:
        parts.append('<table>')
        parts.append('<thead><tr>')
        for col in df.columns:
            parts.append(f'  <th>{col}</th>')
        parts.append('</tr></thead>')
        parts.append('<tbody>')
        parts.append('{% for _, row in df.iterrows() %}')
        parts.append('<tr>')
        for col in df.columns:
            parts.append(f'  <td>{{{{ row["{col}"] }}}}</td>')
        parts.append('</tr>')
        parts.append('{% endfor %}')
        parts.append('</tbody>')
        parts.append('</table>')

    return '\n'.join(parts)


def _get_helpers() -> dict[str, Any]:
    """Return formatting helper functions available in templates."""
    return {
        'format_number': _format_number,
        'format_pct': _format_pct,
        'format_currency': _format_currency,
        'frozen_table': _frozen_table,
        'merge_cells': _merge_cells,
    }


def _format_number(n, decimals: int = 2) -> str:
    """Format number with thousands separator."""
    if pd.isna(n):
        return "—"
    try:
        return f"{float(n):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(n)


def _format_pct(n, decimals: int = 1) -> str:
    """Format as percentage."""
    if pd.isna(n):
        return "—"
    try:
        return f"{float(n):.{decimals}f}%"
    except (ValueError, TypeError):
        return str(n)


def _format_currency(n, symbol: str = "$", decimals: int = 0) -> str:
    """Format as currency."""
    if pd.isna(n):
        return "—"
    try:
        value = float(n)
        if value < 0:
            return f"-{symbol}{abs(value):,.{decimals}f}"
        return f"{symbol}{value:,.{decimals}f}"
    except (ValueError, TypeError):
        return str(n)


def _frozen_table(df: pd.DataFrame, max_height: str = "400px", **kwargs) -> str:
    """Generate a table with frozen (sticky) headers."""
    style = f'style="max-height: {max_height}; overflow-y: auto; display: block;"'
    lines = [f'<div {style}>', '<table>']
    lines.append('<thead><tr>')
    for col in df.columns:
        lines.append(f'<th>{col}</th>')
    lines.append('</tr></thead>')
    lines.append('<tbody>')
    for _, row in df.iterrows():
        lines.append('<tr>')
        for col in df.columns:
            val = row[col]
            lines.append(f'<td>{val}</td>')
        lines.append('</tr>')
    lines.append('</tbody></table></div>')
    return '\n'.join(lines)


def _merge_cells(value, colspan: int = 1, rowspan: int = 1, **attrs) -> str:
    """Generate a table cell with merge (span) attributes."""
    attr_str = ""
    if colspan > 1:
        attr_str += f' colspan="{colspan}"'
    if rowspan > 1:
        attr_str += f' rowspan="{rowspan}"'
    for k, v in attrs.items():
        attr_str += f' {k}="{v}"'
    return f'<td{attr_str}>{value}</td>'


# =============================================================================
# Conditional Formatting
# =============================================================================

def _apply_conditional_format(column: str, value, rules: list[ConditionalFormat]) -> str:
    """Return inline CSS style string based on conditional format rules."""
    for rule in rules:
        if rule.column != column:
            continue
        if _rule_matches(rule, value):
            if rule.rule == 'color_scale':
                return f'style="background-color: {_compute_color_scale(value, rule)}"'
            elif rule.rule == 'data_bar':
                pct = _compute_data_bar_pct(value, rule)
                return f'style="--bar-width: {pct}%"'
    return ""


def _apply_conditional_class(column: str, value, rules: list[ConditionalFormat]) -> str:
    """Return CSS class based on conditional format rules."""
    classes = []
    for rule in rules:
        if rule.column != column:
            continue
        if _rule_matches(rule, value):
            if rule.style:
                classes.append(rule.style)
            if rule.rule == 'data_bar':
                classes.append('data-bar')
    return ' '.join(classes)


def _rule_matches(rule: ConditionalFormat, value) -> bool:
    """Check if a value matches a conditional format rule."""
    try:
        v = float(value) if not isinstance(value, (int, float)) else value
    except (ValueError, TypeError):
        v = value

    if rule.rule == 'greater_than':
        return v > rule.value
    elif rule.rule == 'less_than':
        return v < rule.value
    elif rule.rule == 'equals':
        return v == rule.value or str(v) == str(rule.value)
    elif rule.rule == 'not_equals':
        return v != rule.value
    elif rule.rule == 'between':
        return rule.value <= v <= rule.value2
    elif rule.rule in ('color_scale', 'data_bar'):
        return True  # Always applies
    return False


def _compute_color_scale(value, rule: ConditionalFormat) -> str:
    """Compute color for color_scale rule."""
    try:
        v = float(value)
    except (ValueError, TypeError):
        return "transparent"

    # Normalize (assume 0-100 if no bounds set, or use value/max heuristic)
    min_v = float(rule.value) if rule.value is not None else 0
    max_v = float(rule.value2) if rule.value2 is not None else 100
    val_range = max_v - min_v or 1
    ratio = max(0.0, min(1.0, (v - min_v) / val_range))

    # Interpolate between min_color and max_color
    r1, g1, b1 = _hex_to_rgb(rule.min_color)
    r2, g2, b2 = _hex_to_rgb(rule.max_color)
    r = int(r1 + (r2 - r1) * ratio)
    g = int(g1 + (g2 - g1) * ratio)
    b = int(b1 + (b2 - b1) * ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


def _compute_data_bar_pct(value, rule: ConditionalFormat) -> float:
    """Compute bar width percentage for data_bar rule."""
    try:
        v = float(value)
    except (ValueError, TypeError):
        return 0
    max_v = float(rule.value) if rule.value is not None else 100
    return max(0, min(100, v / max_v * 100))


def _generate_conditional_css(rules: list[ConditionalFormat], df: pd.DataFrame) -> str:
    """Generate additional CSS for conditional formatting."""
    css_parts = ['<style>']
    # Built-in conditional format classes
    added_classes = set()
    for rule in rules:
        if rule.style and rule.style not in added_classes:
            added_classes.add(rule.style)
            # Don't redefine classes already in DEFAULT_STYLES
            if rule.style not in ('positive', 'negative', 'highlight-red',
                                  'highlight-green', 'highlight-yellow'):
                css_parts.append(f'  .{rule.style} {{ font-weight: 600; }}')
    css_parts.append('</style>')
    return '\n'.join(css_parts) if len(css_parts) > 2 else ""


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
