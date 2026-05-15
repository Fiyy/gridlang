"""
GridLang Charts — Extended SVG chart generation library.

Provides 8 chart types as pure SVG output for embedding in HTML templates.
No external JavaScript dependencies — everything renders server-side.
"""

from __future__ import annotations

import math
from typing import Sequence, Optional, Union

import pandas as pd
import numpy as np


# Default color palette (accessible, distinct)
DEFAULT_COLORS = [
    '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6',
    '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
]


def sparkline(series, width: int = 80, height: int = 20, color: str = '#2563eb') -> str:
    """
    Inline sparkline chart (mini line chart).

    Args:
        series: Sequence of numeric values.
        width: SVG width in pixels.
        height: SVG height in pixels.
        color: Line color.
    """
    values = _to_float_list(series)
    if len(values) < 2:
        return ""

    min_val, max_val = min(values), max(values)
    val_range = max_val - min_val or 1

    points = []
    step = width / (len(values) - 1)
    for i, v in enumerate(values):
        x = i * step
        y = height - ((v - min_val) / val_range * (height - 2)) - 1
        points.append(f"{x:.1f},{y:.1f}")

    polyline = " ".join(points)
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'style="display:inline-block;vertical-align:middle;">'
        f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{points[-1].split(",")[0]}" cy="{points[-1].split(",")[1]}" '
        f'r="2" fill="{color}"/>'
        f'</svg>'
    )


def bar_chart(
    labels: Sequence,
    values: Sequence,
    width: int = 500,
    height: int = 300,
    title: str = "",
    color: str = '#3b82f6',
    show_values: bool = True,
) -> str:
    """
    Vertical bar chart.

    Args:
        labels: Category labels.
        values: Numeric values.
        width: SVG width.
        height: SVG height.
        title: Chart title.
        color: Bar color.
        show_values: Show value labels above bars.
    """
    vals = _to_float_list(values)
    labs = [str(l) for l in labels]
    if not vals:
        return ""

    margin = {'top': 40 if title else 20, 'right': 20, 'bottom': 50, 'left': 60}
    chart_w = width - margin['left'] - margin['right']
    chart_h = height - margin['top'] - margin['bottom']

    max_val = max(vals) or 1
    bar_width = chart_w / len(vals) * 0.7
    gap = chart_w / len(vals) * 0.15

    elements = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
                f'xmlns="http://www.w3.org/2000/svg">']

    # Title
    if title:
        elements.append(f'<text x="{width/2}" y="20" text-anchor="middle" '
                       f'font-size="14" font-weight="600" fill="#1f2937">{title}</text>')

    # Y-axis gridlines
    for i in range(5):
        y = margin['top'] + chart_h * i / 4
        val = max_val * (4 - i) / 4
        elements.append(f'<line x1="{margin["left"]}" y1="{y}" x2="{width - margin["right"]}" '
                       f'y2="{y}" stroke="#e5e7eb" stroke-width="1"/>')
        elements.append(f'<text x="{margin["left"] - 8}" y="{y + 4}" text-anchor="end" '
                       f'font-size="10" fill="#6b7280">{_format_axis_val(val)}</text>')

    # Bars
    for i, (label, value) in enumerate(zip(labs, vals)):
        bar_h = (value / max_val) * chart_h
        x = margin['left'] + i * (bar_width + gap * 2) + gap
        y = margin['top'] + chart_h - bar_h

        elements.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
                       f'height="{bar_h:.1f}" fill="{color}" rx="3" opacity="0.85"/>')

        # Value label
        if show_values:
            elements.append(f'<text x="{x + bar_width/2:.1f}" y="{y - 5:.1f}" '
                           f'text-anchor="middle" font-size="10" fill="#374151">'
                           f'{_format_axis_val(value)}</text>')

        # X-axis label
        elements.append(f'<text x="{x + bar_width/2:.1f}" y="{height - margin["bottom"] + 18}" '
                       f'text-anchor="middle" font-size="10" fill="#6b7280">{label}</text>')

    elements.append('</svg>')
    return "\n".join(elements)


def line_chart(
    x: Sequence,
    y: Union[Sequence, dict[str, Sequence]],
    width: int = 500,
    height: int = 300,
    title: str = "",
    colors: list[str] = None,
    show_dots: bool = True,
    show_legend: bool = True,
) -> str:
    """
    Line chart with one or multiple series.

    Args:
        x: X-axis values (labels or numbers).
        y: Single series (list) or multiple series (dict of name→values).
        width: SVG width.
        height: SVG height.
        title: Chart title.
        colors: Custom color list.
        show_dots: Show data point dots.
        show_legend: Show legend for multi-series.
    """
    colors = colors or DEFAULT_COLORS
    margin = {'top': 40 if title else 20, 'right': 20, 'bottom': 50, 'left': 60}
    chart_w = width - margin['left'] - margin['right']
    chart_h = height - margin['top'] - margin['bottom']

    # Normalize to dict of series
    if isinstance(y, dict):
        series_dict = {k: _to_float_list(v) for k, v in y.items()}
    else:
        series_dict = {'': _to_float_list(y)}

    x_labels = [str(v) for v in x]
    all_vals = [v for vals in series_dict.values() for v in vals]
    if not all_vals:
        return ""

    min_val = min(all_vals)
    max_val = max(all_vals)
    val_range = max_val - min_val or 1

    elements = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
                f'xmlns="http://www.w3.org/2000/svg">']

    if title:
        elements.append(f'<text x="{width/2}" y="20" text-anchor="middle" '
                       f'font-size="14" font-weight="600" fill="#1f2937">{title}</text>')

    # Gridlines
    for i in range(5):
        gy = margin['top'] + chart_h * i / 4
        val = max_val - (max_val - min_val) * i / 4
        elements.append(f'<line x1="{margin["left"]}" y1="{gy}" x2="{width - margin["right"]}" '
                       f'y2="{gy}" stroke="#f1f5f9" stroke-width="1"/>')
        elements.append(f'<text x="{margin["left"] - 8}" y="{gy + 4}" text-anchor="end" '
                       f'font-size="10" fill="#6b7280">{_format_axis_val(val)}</text>')

    # X-axis labels
    n_points = len(x_labels)
    step = chart_w / max(n_points - 1, 1)
    label_skip = max(1, n_points // 10)
    for i, label in enumerate(x_labels):
        if i % label_skip == 0 or i == n_points - 1:
            lx = margin['left'] + i * step
            elements.append(f'<text x="{lx}" y="{height - margin["bottom"] + 18}" '
                           f'text-anchor="middle" font-size="10" fill="#6b7280">{label}</text>')

    # Draw series
    for si, (name, vals) in enumerate(series_dict.items()):
        color = colors[si % len(colors)]
        points = []
        for i, v in enumerate(vals):
            px = margin['left'] + i * step
            py = margin['top'] + chart_h - ((v - min_val) / val_range * chart_h)
            points.append(f"{px:.1f},{py:.1f}")

        polyline = " ".join(points)
        elements.append(f'<polyline points="{polyline}" fill="none" stroke="{color}" '
                       f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')

        if show_dots:
            for pt in points:
                cx, cy = pt.split(',')
                elements.append(f'<circle cx="{cx}" cy="{cy}" r="3" fill="{color}"/>')

    # Legend
    if show_legend and len(series_dict) > 1:
        legend_y = margin['top'] - 5
        legend_x = margin['left'] + 10
        for si, name in enumerate(series_dict.keys()):
            color = colors[si % len(colors)]
            lx = legend_x + si * 100
            elements.append(f'<rect x="{lx}" y="{legend_y - 8}" width="12" height="12" '
                           f'fill="{color}" rx="2"/>')
            elements.append(f'<text x="{lx + 16}" y="{legend_y + 2}" '
                           f'font-size="11" fill="#374151">{name}</text>')

    elements.append('</svg>')
    return "\n".join(elements)


def pie_chart(
    labels: Sequence,
    values: Sequence,
    width: int = 350,
    height: int = 350,
    title: str = "",
    colors: list[str] = None,
    show_pct: bool = True,
) -> str:
    """
    Pie/donut chart.

    Args:
        labels: Segment labels.
        values: Numeric values.
        width: SVG width.
        height: SVG height.
        title: Chart title.
        colors: Custom color list.
        show_pct: Show percentage labels.
    """
    colors = colors or DEFAULT_COLORS
    vals = _to_float_list(values)
    labs = [str(l) for l in labels]
    if not vals or sum(vals) == 0:
        return ""

    total = sum(vals)
    cx, cy = width / 2, height / 2 + (15 if title else 0)
    radius = min(width, height) / 2 - 50
    inner_radius = radius * 0.55  # Donut hole

    elements = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
                f'xmlns="http://www.w3.org/2000/svg">']

    if title:
        elements.append(f'<text x="{width/2}" y="22" text-anchor="middle" '
                       f'font-size="14" font-weight="600" fill="#1f2937">{title}</text>')

    angle = -90  # Start from top
    for i, (label, value) in enumerate(zip(labs, vals)):
        if value <= 0:
            continue
        color = colors[i % len(colors)]
        pct = value / total
        sweep = pct * 360

        start_rad = math.radians(angle)
        end_rad = math.radians(angle + sweep)

        # Outer arc
        x1 = cx + radius * math.cos(start_rad)
        y1 = cy + radius * math.sin(start_rad)
        x2 = cx + radius * math.cos(end_rad)
        y2 = cy + radius * math.sin(end_rad)

        # Inner arc
        ix1 = cx + inner_radius * math.cos(start_rad)
        iy1 = cy + inner_radius * math.sin(start_rad)
        ix2 = cx + inner_radius * math.cos(end_rad)
        iy2 = cy + inner_radius * math.sin(end_rad)

        large_arc = 1 if sweep > 180 else 0

        path = (f'M {ix1:.1f},{iy1:.1f} '
                f'L {x1:.1f},{y1:.1f} '
                f'A {radius:.1f},{radius:.1f} 0 {large_arc} 1 {x2:.1f},{y2:.1f} '
                f'L {ix2:.1f},{iy2:.1f} '
                f'A {inner_radius:.1f},{inner_radius:.1f} 0 {large_arc} 0 {ix1:.1f},{iy1:.1f} Z')

        elements.append(f'<path d="{path}" fill="{color}" stroke="white" stroke-width="2"/>')

        # Label
        if show_pct and pct > 0.04:  # Skip tiny slices
            mid_angle = math.radians(angle + sweep / 2)
            label_r = radius + 18
            lx = cx + label_r * math.cos(mid_angle)
            ly = cy + label_r * math.sin(mid_angle)
            anchor = "start" if lx > cx else "end"
            elements.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
                           f'font-size="10" fill="#374151">{label} ({pct:.0%})</text>')

        angle += sweep

    # Center total
    elements.append(f'<text x="{cx}" y="{cy - 5}" text-anchor="middle" '
                   f'font-size="11" fill="#6b7280">Total</text>')
    elements.append(f'<text x="{cx}" y="{cy + 15}" text-anchor="middle" '
                   f'font-size="16" font-weight="700" fill="#1f2937">'
                   f'{_format_axis_val(total)}</text>')

    elements.append('</svg>')
    return "\n".join(elements)


def scatter_chart(
    x: Sequence,
    y: Sequence,
    width: int = 500,
    height: int = 350,
    title: str = "",
    color: str = '#3b82f6',
    x_label: str = "",
    y_label: str = "",
    size: Union[Sequence, int] = 5,
) -> str:
    """
    Scatter plot.

    Args:
        x: X-axis values.
        y: Y-axis values.
        width: SVG width.
        height: SVG height.
        title: Chart title.
        color: Dot color.
        x_label: X-axis label.
        y_label: Y-axis label.
        size: Dot radius (constant or per-point).
    """
    x_vals = _to_float_list(x)
    y_vals = _to_float_list(y)
    if not x_vals or not y_vals or len(x_vals) != len(y_vals):
        return ""

    sizes = _to_float_list(size) if not isinstance(size, (int, float)) else [float(size)] * len(x_vals)

    margin = {'top': 40 if title else 20, 'right': 20, 'bottom': 50, 'left': 60}
    chart_w = width - margin['left'] - margin['right']
    chart_h = height - margin['top'] - margin['bottom']

    x_min, x_max = min(x_vals), max(x_vals)
    y_min, y_max = min(y_vals), max(y_vals)
    x_range = x_max - x_min or 1
    y_range = y_max - y_min or 1

    elements = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
                f'xmlns="http://www.w3.org/2000/svg">']

    if title:
        elements.append(f'<text x="{width/2}" y="20" text-anchor="middle" '
                       f'font-size="14" font-weight="600" fill="#1f2937">{title}</text>')

    # Gridlines
    for i in range(5):
        gy = margin['top'] + chart_h * i / 4
        elements.append(f'<line x1="{margin["left"]}" y1="{gy}" x2="{width - margin["right"]}" '
                       f'y2="{gy}" stroke="#f1f5f9" stroke-width="1"/>')
        gx = margin['left'] + chart_w * i / 4
        elements.append(f'<line x1="{gx}" y1="{margin["top"]}" x2="{gx}" '
                       f'y2="{margin["top"] + chart_h}" stroke="#f1f5f9" stroke-width="1"/>')

    # Dots
    for i, (xv, yv) in enumerate(zip(x_vals, y_vals)):
        px = margin['left'] + (xv - x_min) / x_range * chart_w
        py = margin['top'] + chart_h - (yv - y_min) / y_range * chart_h
        r = sizes[i] if i < len(sizes) else 5
        elements.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{r}" '
                       f'fill="{color}" opacity="0.7"/>')

    # Axis labels
    if x_label:
        elements.append(f'<text x="{width/2}" y="{height - 5}" text-anchor="middle" '
                       f'font-size="11" fill="#6b7280">{x_label}</text>')
    if y_label:
        elements.append(f'<text x="12" y="{height/2}" text-anchor="middle" '
                       f'font-size="11" fill="#6b7280" transform="rotate(-90 12 {height/2})">'
                       f'{y_label}</text>')

    elements.append('</svg>')
    return "\n".join(elements)


def area_chart(
    x: Sequence,
    y: Union[Sequence, dict[str, Sequence]],
    width: int = 500,
    height: int = 300,
    title: str = "",
    colors: list[str] = None,
    stacked: bool = False,
) -> str:
    """
    Area chart (filled line chart).

    Args:
        x: X-axis values.
        y: Single series or dict of name→values.
        width: SVG width.
        height: SVG height.
        title: Chart title.
        colors: Custom colors.
        stacked: If True, stack areas.
    """
    colors = colors or DEFAULT_COLORS
    margin = {'top': 40 if title else 20, 'right': 20, 'bottom': 50, 'left': 60}
    chart_w = width - margin['left'] - margin['right']
    chart_h = height - margin['top'] - margin['bottom']

    if isinstance(y, dict):
        series_dict = {k: _to_float_list(v) for k, v in y.items()}
    else:
        series_dict = {'': _to_float_list(y)}

    all_vals = [v for vals in series_dict.values() for v in vals]
    if not all_vals:
        return ""

    if stacked:
        max_val = max(sum(vals[i] for vals in series_dict.values())
                     for i in range(len(list(series_dict.values())[0])))
    else:
        max_val = max(all_vals)
    max_val = max_val or 1

    n_points = len(list(series_dict.values())[0])
    step = chart_w / max(n_points - 1, 1)

    elements = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
                f'xmlns="http://www.w3.org/2000/svg">']

    if title:
        elements.append(f'<text x="{width/2}" y="20" text-anchor="middle" '
                       f'font-size="14" font-weight="600" fill="#1f2937">{title}</text>')

    baseline_y = margin['top'] + chart_h

    # Draw areas (reverse order for stacking visibility)
    prev_points = None
    for si, (name, vals) in enumerate(reversed(list(series_dict.items()))):
        color = colors[(len(series_dict) - 1 - si) % len(colors)]
        points_top = []
        for i, v in enumerate(vals):
            px = margin['left'] + i * step
            py = baseline_y - (v / max_val * chart_h)
            points_top.append((px, py))

        # Build path
        path_parts = [f'M {points_top[0][0]:.1f},{points_top[0][1]:.1f}']
        for px, py in points_top[1:]:
            path_parts.append(f'L {px:.1f},{py:.1f}')
        # Close to baseline
        path_parts.append(f'L {points_top[-1][0]:.1f},{baseline_y:.1f}')
        path_parts.append(f'L {points_top[0][0]:.1f},{baseline_y:.1f} Z')

        elements.append(f'<path d="{" ".join(path_parts)}" fill="{color}" opacity="0.3"/>')
        # Line on top
        line_points = " ".join(f"{px:.1f},{py:.1f}" for px, py in points_top)
        elements.append(f'<polyline points="{line_points}" fill="none" stroke="{color}" '
                       f'stroke-width="2"/>')

    elements.append('</svg>')
    return "\n".join(elements)


def stacked_bar_chart(
    labels: Sequence,
    series_dict: dict[str, Sequence],
    width: int = 500,
    height: int = 300,
    title: str = "",
    colors: list[str] = None,
    show_legend: bool = True,
) -> str:
    """
    Stacked bar chart.

    Args:
        labels: Category labels.
        series_dict: Dict of series_name → values.
        width: SVG width.
        height: SVG height.
        title: Chart title.
        colors: Custom colors.
        show_legend: Show legend.
    """
    colors = colors or DEFAULT_COLORS
    margin = {'top': 40 if title else 20, 'right': 20, 'bottom': 50, 'left': 60}
    if show_legend:
        margin['top'] += 25
    chart_w = width - margin['left'] - margin['right']
    chart_h = height - margin['top'] - margin['bottom']

    labs = [str(l) for l in labels]
    n_bars = len(labs)
    if n_bars == 0:
        return ""

    # Calculate max stacked value
    series_vals = {k: _to_float_list(v) for k, v in series_dict.items()}
    max_stack = max(sum(series_vals[k][i] for k in series_vals)
                   for i in range(n_bars))
    max_stack = max_stack or 1

    bar_width = chart_w / n_bars * 0.7
    gap = chart_w / n_bars * 0.15

    elements = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
                f'xmlns="http://www.w3.org/2000/svg">']

    if title:
        elements.append(f'<text x="{width/2}" y="20" text-anchor="middle" '
                       f'font-size="14" font-weight="600" fill="#1f2937">{title}</text>')

    # Legend
    if show_legend:
        legend_y = margin['top'] - 20
        for si, name in enumerate(series_vals.keys()):
            color = colors[si % len(colors)]
            lx = margin['left'] + si * 90
            elements.append(f'<rect x="{lx}" y="{legend_y - 8}" width="10" height="10" '
                           f'fill="{color}" rx="2"/>')
            elements.append(f'<text x="{lx + 14}" y="{legend_y + 1}" '
                           f'font-size="10" fill="#374151">{name}</text>')

    baseline_y = margin['top'] + chart_h

    # Draw stacked bars
    for i, label in enumerate(labs):
        x = margin['left'] + i * (bar_width + gap * 2) + gap
        y_offset = 0

        for si, (series_name, vals) in enumerate(series_vals.items()):
            color = colors[si % len(colors)]
            value = vals[i] if i < len(vals) else 0
            bar_h = (value / max_stack) * chart_h

            y = baseline_y - y_offset - bar_h
            elements.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
                           f'height="{bar_h:.1f}" fill="{color}" rx="2"/>')
            y_offset += bar_h

        # X-axis label
        elements.append(f'<text x="{x + bar_width/2:.1f}" y="{height - margin["bottom"] + 18}" '
                       f'text-anchor="middle" font-size="10" fill="#6b7280">{label}</text>')

    elements.append('</svg>')
    return "\n".join(elements)


def heatmap(
    df: pd.DataFrame,
    width: int = 500,
    height: int = 400,
    title: str = "",
    color_low: str = '#dbeafe',
    color_high: str = '#1d4ed8',
) -> str:
    """
    Heatmap visualization of a DataFrame.

    Args:
        df: DataFrame (numeric columns only; first column used as row labels).
        width: SVG width.
        height: SVG height.
        title: Chart title.
        color_low: Color for lowest values.
        color_high: Color for highest values.
    """
    margin = {'top': 40 if title else 20, 'right': 20, 'bottom': 40, 'left': 80}
    chart_w = width - margin['left'] - margin['right']
    chart_h = height - margin['top'] - margin['bottom']

    # Use first column as labels if it's string type
    row_labels = df.iloc[:, 0].tolist() if df.iloc[:, 0].dtype == object else df.index.tolist()
    numeric_df = df.select_dtypes(include=[np.number])
    if numeric_df.empty:
        return ""

    col_labels = numeric_df.columns.tolist()
    n_rows = len(row_labels)
    n_cols = len(col_labels)

    cell_w = chart_w / n_cols
    cell_h = chart_h / n_rows

    all_vals = numeric_df.values.flatten()
    val_min = np.nanmin(all_vals)
    val_max = np.nanmax(all_vals)
    val_range = val_max - val_min or 1

    # Parse colors
    r_low, g_low, b_low = _hex_to_rgb(color_low)
    r_high, g_high, b_high = _hex_to_rgb(color_high)

    elements = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
                f'xmlns="http://www.w3.org/2000/svg">']

    if title:
        elements.append(f'<text x="{width/2}" y="20" text-anchor="middle" '
                       f'font-size="14" font-weight="600" fill="#1f2937">{title}</text>')

    # Column headers
    for j, col in enumerate(col_labels):
        cx = margin['left'] + j * cell_w + cell_w / 2
        elements.append(f'<text x="{cx}" y="{margin["top"] - 5}" text-anchor="middle" '
                       f'font-size="10" fill="#374151">{col}</text>')

    # Cells
    for i in range(n_rows):
        # Row label
        ry = margin['top'] + i * cell_h + cell_h / 2 + 4
        elements.append(f'<text x="{margin["left"] - 5}" y="{ry}" text-anchor="end" '
                       f'font-size="10" fill="#374151">{row_labels[i]}</text>')

        for j in range(n_cols):
            val = numeric_df.iloc[i, j]
            if pd.isna(val):
                color = '#f3f4f6'
            else:
                ratio = (val - val_min) / val_range
                r = int(r_low + (r_high - r_low) * ratio)
                g = int(g_low + (g_high - g_low) * ratio)
                b = int(b_low + (b_high - b_low) * ratio)
                color = f'#{r:02x}{g:02x}{b:02x}'

            x = margin['left'] + j * cell_w
            y = margin['top'] + i * cell_h
            elements.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w:.1f}" '
                           f'height="{cell_h:.1f}" fill="{color}" stroke="white" stroke-width="1"/>')

            # Value text
            text_color = '#ffffff' if not pd.isna(val) and (val - val_min) / val_range > 0.5 else '#1f2937'
            elements.append(f'<text x="{x + cell_w/2:.1f}" y="{y + cell_h/2 + 4:.1f}" '
                           f'text-anchor="middle" font-size="10" fill="{text_color}">'
                           f'{_format_axis_val(val) if not pd.isna(val) else ""}</text>')

    elements.append('</svg>')
    return "\n".join(elements)


def color_scale(value, min_val: float = 0, max_val: float = 100) -> str:
    """Return CSS color string based on value position (red → green)."""
    try:
        v = float(value)
    except (ValueError, TypeError):
        return "#9ca3af"

    ratio = max(0.0, min(1.0, (v - min_val) / (max_val - min_val or 1)))

    if ratio < 0.5:
        r = 239
        g = int(68 + ratio * 2 * (163 - 68))
        b = 68
    else:
        r = int(239 - (ratio - 0.5) * 2 * (239 - 34))
        g = int(163 + (ratio - 0.5) * 2 * (197 - 163))
        b = int(68 + (ratio - 0.5) * 2 * (94 - 68))

    return f"#{r:02x}{g:02x}{b:02x}"


# =============================================================================
# Helpers
# =============================================================================

def _to_float_list(series) -> list[float]:
    """Convert various inputs to list of floats."""
    if isinstance(series, pd.Series):
        return series.dropna().astype(float).tolist()
    if isinstance(series, np.ndarray):
        return [float(v) for v in series if not np.isnan(v)]
    if isinstance(series, (list, tuple)):
        result = []
        for v in series:
            try:
                if v is not None and not (isinstance(v, float) and math.isnan(v)):
                    result.append(float(v))
            except (ValueError, TypeError):
                pass
        return result
    return []


def _format_axis_val(val) -> str:
    """Format axis value for display."""
    if abs(val) >= 1_000_000:
        return f"{val/1_000_000:.1f}M"
    elif abs(val) >= 1_000:
        return f"{val/1_000:.1f}K"
    elif isinstance(val, float) and val != int(val):
        return f"{val:.1f}"
    else:
        return str(int(val))


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def get_all_charts() -> dict:
    """Return all chart functions for template injection."""
    return {
        'sparkline': sparkline,
        'bar_chart': bar_chart,
        'line_chart': line_chart,
        'pie_chart': pie_chart,
        'scatter_chart': scatter_chart,
        'area_chart': area_chart,
        'stacked_bar_chart': stacked_bar_chart,
        'heatmap': heatmap,
        'color_scale': color_scale,
    }
