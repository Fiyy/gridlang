"""
GridLang Chart DSL — declarative chart and conditional-format syntax.

Lets the present layer use a YAML-like block instead of explicit Jinja2 calls:

    chart: bar
      data: agg.region_revenue
      labels: agg.region_labels
      title: "Revenue by Region"
      width: 500
      height: 300

    format: color_scale
      column: Margin_Pct
      min: 0
      max: 100
      min_color: "#fecaca"
      max_color: "#bbf7d0"

Blocks are detected by a leading line `chart: <type>` or `format: <type>` followed
by indented `key: value` pairs. The DSL preprocessor:

  * `chart:` blocks → rewritten to `{{ <chart_func>(args, kwargs) }}` Jinja2 calls
  * `format:` blocks → collected as `ConditionalFormat` objects merged with runtime ones

References inside `data:`, `labels:`, etc. are resolved against the runtime context:

  | Form              | Resolves to                                       |
  |-------------------|---------------------------------------------------|
  | `42`, `3.14`      | numeric literal                                   |
  | `"hello"` / 'x'   | string literal                                    |
  | `[a, b, c]`       | list literal (each element re-resolved)           |
  | `agg.foo`         | `agg['foo']` from compute aggregates              |
  | `meta.foo`        | `meta['foo']` from meta section                   |
  | `Q1,Q2,Q3`        | dict-of-series for multi-series charts            |
  | `Revenue`         | `df['Revenue'].tolist()` (single column)          |
  | `sales!Revenue`   | `sheets['sales']['Revenue'].tolist()`             |
  | `B2:D4`           | Excel A1 range as flat list                       |
  | `B2:D4@sales`     | Excel A1 range scoped to a named sheet            |
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


# ─── Public API ─────────────────────────────────────────────────────────────

# Mapping from DSL chart type → chart function name in the Jinja2 globals.
CHART_TYPE_TO_FUNC = {
    'bar': 'bar_chart',
    'line': 'line_chart',
    'pie': 'pie_chart',
    'scatter': 'scatter_chart',
    'area': 'area_chart',
    'stacked_bar': 'stacked_bar_chart',
    'stacked-bar': 'stacked_bar_chart',
    'heatmap': 'heatmap',
    'sparkline': 'sparkline',
    'color_scale': 'color_scale',
    'color-scale': 'color_scale',
}

# Set of supported `format:` rule types (mirrors runtime ConditionalFormat rules).
FORMAT_RULE_TYPES = {
    'color_scale', 'color-scale', 'data_bar', 'data-bar',
    'greater_than', 'greater-than', 'less_than', 'less-than',
    'equals', 'between', 'rules',
}


@dataclass
class FormatDirective:
    """A `format:` block parsed from the present layer."""
    rule: str                       # canonical rule name (color_scale, data_bar, ...)
    column: str = ""                # target column, or "" for whole range
    range: str = ""                 # optional A1 range alternative to column
    value: Any = None
    value2: Any = None
    style: str = ""
    min_color: str = "#ef4444"
    max_color: str = "#10b981"
    raw_rules: list[str] = field(default_factory=list)  # for `rule: ">90 -> bold green"` lines

    def to_runtime_dict(self) -> dict:
        """Convert to a dict matching the runtime ConditionalFormat constructor kwargs."""
        return {
            'column': self.column,
            'rule': self.rule,
            'value': self.value,
            'value2': self.value2,
            'style': self.style,
            'min_color': self.min_color,
            'max_color': self.max_color,
        }


@dataclass
class DSLResult:
    """Result of preprocessing the present layer for DSL blocks."""
    template: str                                 # rewritten template (DSL → Jinja2 calls)
    formats: list[FormatDirective] = field(default_factory=list)


def preprocess(present_text: str) -> DSLResult:
    """
    Scan the present layer text and rewrite DSL blocks.

    Returns the rewritten template plus any `format:` directives the caller
    should merge with runtime conditional formats.
    """
    if not present_text:
        return DSLResult(template=present_text)

    lines = present_text.splitlines()
    out_lines: list[str] = []
    formats: list[FormatDirective] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        m = _BLOCK_HEAD.match(stripped)
        if m:
            kind = m.group(1)            # 'chart' or 'format'
            type_name = m.group(2)
            indent = len(line) - len(line.lstrip())

            # Collect indented body lines
            body, consumed = _collect_block_body(lines, i + 1, indent)
            block = _parse_block_body(body)

            if kind == 'chart':
                jinja_expr = _render_chart_call(type_name, block, indent_prefix=line[:indent])
                out_lines.append(jinja_expr)
            else:  # format
                directives = _build_format_directives(type_name, block)
                formats.extend(directives)
                # format blocks emit nothing visible
                # (an HTML comment makes round-trip diffing easier)
                out_lines.append(f"{line[:indent]}<!-- format: {type_name} -->")

            i = i + 1 + consumed
            continue

        out_lines.append(line)
        i += 1

    return DSLResult(template='\n'.join(out_lines), formats=formats)


# ─── Block detection / parsing ──────────────────────────────────────────────

# A block starts with "chart: TYPE" or "format: TYPE" optionally followed by trailing whitespace.
# Trailing characters that look like Jinja or HTML are rejected to avoid hijacking expressions.
_BLOCK_HEAD = re.compile(r'^(chart|format)\s*:\s*([A-Za-z][\w\-]*)\s*$')

# A body line is "  key: value" with at least one space of indentation greater than the head.
_KV_LINE = re.compile(r'^(\s+)([A-Za-z][\w\-]*)\s*:\s*(.*)$')


def _collect_block_body(lines: list[str], start: int, head_indent: int) -> tuple[list[tuple[str, str]], int]:
    """
    Collect indented `key: value` lines belonging to a block.

    Returns:
        (key_value_pairs, lines_consumed)

    Stops at the first line that is non-blank and not indented past head_indent,
    or at end-of-file.
    """
    pairs: list[tuple[str, str]] = []
    consumed = 0
    j = start
    last_key: Optional[str] = None

    while j < len(lines):
        raw = lines[j]
        if raw.strip() == '':
            # Blank line is permitted inside a block, but two consecutive blanks end it.
            if j + 1 < len(lines) and lines[j + 1].strip() == '':
                break
            consumed += 1
            j += 1
            continue

        cur_indent = len(raw) - len(raw.lstrip())
        if cur_indent <= head_indent:
            break

        m = _KV_LINE.match(raw)
        if m:
            key = m.group(2).strip()
            val = m.group(3).strip()
            pairs.append((key, val))
            last_key = key
        else:
            # Continuation line: append to last value with a leading space.
            if last_key is not None and pairs:
                k, v = pairs[-1]
                pairs[-1] = (k, (v + ' ' + raw.strip()).strip())
        consumed += 1
        j += 1

    return pairs, consumed


def _parse_block_body(pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    """
    Convert raw key/value pairs into a dict where values are lists of strings,
    so a key like `rule:` can repeat.
    """
    out: dict[str, list[str]] = {}
    for k, v in pairs:
        out.setdefault(k, []).append(v)
    return out


# ─── chart: → Jinja2 call ──────────────────────────────────────────────────

# Keys that are used positionally rather than as kwargs, per chart type.
# Order matters: matches the chart function signatures in charts.py.
_CHART_POSITIONAL = {
    'bar_chart':         ('labels', 'data'),
    'line_chart':        ('labels', 'data'),
    'pie_chart':         ('labels', 'data'),
    'scatter_chart':     ('x', 'y'),
    'area_chart':        ('labels', 'data'),
    'stacked_bar_chart': ('labels', 'data'),  # data must be a dict here
    'heatmap':           ('data',),           # DataFrame
    'sparkline':         ('data',),
    'color_scale':       ('value',),
}

# Common kwargs that need string quoting in the emitted Jinja expression.
_QUOTE_KEYWORDS = {
    'title', 'color', 'min_color', 'max_color', 'color_low', 'color_high',
    'x_label', 'y_label',
}

# Synonyms accepted in the DSL.
_KEY_ALIASES = {
    'min': 'min_color',           # only when value is a hex string; resolved at emit time
    'max': 'max_color',
    'series': 'data',             # bar/line use `series:` interchangeably with `data:`
    'values': 'data',
    'label': 'labels',
}


def _render_chart_call(type_name: str, block: dict[str, list[str]], indent_prefix: str = "") -> str:
    """Translate a `chart:` block into a Jinja2 expression `{{ func(args) }}`."""
    canonical = type_name.replace('-', '_').lower()
    func = CHART_TYPE_TO_FUNC.get(canonical) or CHART_TYPE_TO_FUNC.get(type_name)
    if func is None:
        return f"{indent_prefix}<!-- unknown chart type: {type_name} -->"

    # Apply key aliases — but only if it doesn't shadow a real key already supplied.
    normalized: dict[str, str] = {}
    for k, vlist in block.items():
        # Use only the last value if a key repeats (chart: kwargs are not multi-valued).
        v = vlist[-1] if vlist else ''
        target = _KEY_ALIASES.get(k, k)
        # Don't overwrite an explicit key with an aliased one.
        if target not in normalized or k == target:
            normalized[target] = v

    # Special case: `min`/`max` aliases for color_scale should map to value/value2,
    # not min_color/max_color, because the latter are already named.
    # We disambiguate: if value looks like a #hex / quoted string → keep as min_color/max_color.
    # Otherwise → it is a numeric bound → remap to value/value2.
    for src_key, alias_key, num_key in [('min', 'min_color', 'value'),
                                        ('max', 'max_color', 'value2')]:
        if src_key in block and alias_key in normalized:
            raw = block[src_key][-1]
            if not _looks_like_color(raw):
                normalized.pop(alias_key, None)
                normalized[num_key] = raw

    # Build positional args.
    pos_keys = _CHART_POSITIONAL.get(func, ())
    args: list[str] = []
    used: set[str] = set()
    for pk in pos_keys:
        if pk in normalized:
            args.append(_emit_value(normalized[pk], for_chart_type=canonical, key=pk))
            used.add(pk)
        else:
            # Required positional missing → emit None to keep the call syntactically valid;
            # the chart function will return "" for empty data.
            args.append('None')

    # Build keyword args.
    kwargs: list[str] = []
    for k, v in normalized.items():
        if k in used:
            continue
        emitted = _emit_value(v, for_chart_type=canonical, key=k)
        kwargs.append(f"{k}={emitted}")

    call = f"{func}({', '.join(args + kwargs)})"
    return f"{indent_prefix}{{{{ {call} }}}}"


def _looks_like_color(raw: str) -> bool:
    """True if the raw value is a quoted string or starts with a #."""
    s = raw.strip()
    if not s:
        return False
    if s.startswith('#'):
        return True
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        # treat any string as color-ish (e.g. CSS named color)
        return True
    return False


# ─── format: → ConditionalFormat directives ───────────────────────────────

def _build_format_directives(type_name: str, block: dict[str, list[str]]) -> list[FormatDirective]:
    """Translate a `format:` block into one or more FormatDirective objects."""
    rule_canonical = type_name.replace('-', '_').lower()
    directives: list[FormatDirective] = []

    # Common keys
    column = (block.get('column') or [''])[-1].strip().strip('"').strip("'")
    rng    = (block.get('range')  or [''])[-1].strip()
    style  = (block.get('style')  or [''])[-1].strip().strip('"').strip("'")

    if rule_canonical == 'rules':
        # `format: rules` allows multiple `rule: ">90 -> bold green"` lines.
        for rule_text in block.get('rule', []):
            d = _parse_inline_rule(rule_text, column=column, range_=rng)
            if d:
                directives.append(d)
        return directives

    # color_scale / data_bar / threshold rules.
    d = FormatDirective(rule=rule_canonical, column=column, range=rng, style=style)

    # value / value2
    if 'value' in block:
        d.value = _coerce_scalar(block['value'][-1])
    if 'value2' in block:
        d.value2 = _coerce_scalar(block['value2'][-1])

    # color_scale-specific
    if 'min_color' in block:
        d.min_color = _strip_quotes(block['min_color'][-1])
    if 'max_color' in block:
        d.max_color = _strip_quotes(block['max_color'][-1])

    # `min`/`max` shorthand for color_scale: numeric bounds (value/value2)
    # but reuse aliases for color hexes too.
    for src_key, num_attr, color_attr in [('min', 'value', 'min_color'),
                                          ('max', 'value2', 'max_color')]:
        if src_key in block:
            raw = block[src_key][-1]
            if _looks_like_color(raw):
                setattr(d, color_attr, _strip_quotes(raw))
            else:
                setattr(d, num_attr, _coerce_scalar(raw))

    directives.append(d)
    return directives


# Pattern for inline rule strings: ">90 -> bold green", "<60 -> italic red", "= Star -> star"
_INLINE_RULE = re.compile(r'^\s*"?([<>=!]+)\s*([^->]+?)"?\s*->\s*(.+?)"?\s*$')


def _parse_inline_rule(rule_text: str, column: str = "", range_: str = "") -> Optional[FormatDirective]:
    """Parse a string like '>90 -> bold green' into a FormatDirective."""
    text = rule_text.strip().strip('"').strip("'")
    m = _INLINE_RULE.match(text)
    if not m:
        return None
    op, val_raw, style = m.group(1), m.group(2).strip(), m.group(3).strip().strip('"').strip("'")

    rule = {
        '>':  'greater_than',
        '>=': 'greater_than',
        '<':  'less_than',
        '<=': 'less_than',
        '=':  'equals',
        '==': 'equals',
        '!=': 'not_equals',
    }.get(op, 'equals')

    # Style words like "bold green" become a CSS class name (no real CSS injection here —
    # the renderer's _generate_conditional_css() bolds anything with a non-default class.
    # We also map common color words so they hit the default highlight-* classes.
    style_class = _style_words_to_class(style)

    return FormatDirective(
        rule=rule,
        column=column,
        range=range_,
        value=_coerce_scalar(val_raw),
        style=style_class,
    )


_COLOR_WORD_TO_CLASS = {
    'red':    'highlight-red',
    'green':  'highlight-green',
    'yellow': 'highlight-yellow',
}


def _style_words_to_class(words: str) -> str:
    """Pick the first recognized color word; fall back to a sluggified class."""
    for w in words.lower().split():
        if w in _COLOR_WORD_TO_CLASS:
            return _COLOR_WORD_TO_CLASS[w]
    # Sluggify: keep alnum, replace spaces with -.
    slug = re.sub(r'[^a-z0-9]+', '-', words.lower()).strip('-')
    return slug or 'highlight-yellow'


# ─── Value resolution ─────────────────────────────────────────────────────

# Excel-style A1 range: e.g. B2:D4. Letters → column index, digits → row index.
_A1_RANGE = re.compile(r'^([A-Z]+)(\d+):([A-Z]+)(\d+)(?:@([A-Za-z][\w]*))?$')
# Single A1 cell: e.g. B2 — but ONLY if @sheet is given. Without an explicit sheet,
# tokens like "Q4" or "A1" would shadow real column names; the user should write the
# column name directly or scope the cell with @sheet (e.g. `B2@default`).
_A1_CELL  = re.compile(r'^([A-Z]+)(\d+)@([A-Za-z][\w]*)$')

# Single column ref (Python identifier) optionally with sheet prefix `sales!Col`.
_COL_REF  = re.compile(r'^(?:([A-Za-z][\w]*)!)?([A-Za-z][\w]*)$')

# Multi-column comma list: Q1,Q2,Q3 — each element matches _COL_REF.
_COL_LIST = re.compile(r'^[A-Za-z][\w!]*(?:\s*,\s*[A-Za-z][\w!]*)+$')


def _emit_value(raw: str, for_chart_type: str = "", key: str = "") -> str:
    """
    Translate a DSL value to a Jinja2-safe Python expression.

    The output is meant to be embedded inside `{{ ... }}` so it must evaluate
    against the renderer's template context (df, sheets, agg, meta).
    """
    s = raw.strip()
    if not s:
        return "''"

    # 1. Quoted string literal — pass through.
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s

    # 2. Numeric / bool / None literal.
    if s.lower() in ('true', 'false', 'none', 'null'):
        return {'true': 'True', 'false': 'False', 'none': 'None', 'null': 'None'}[s.lower()]
    if _is_number(s):
        return s

    # 3. List literal: [a, b, c] — recursively resolve each element.
    if s.startswith('[') and s.endswith(']'):
        inner = s[1:-1].strip()
        if not inner:
            return '[]'
        parts = _split_top_level_commas(inner)
        emitted = [_emit_value(p, for_chart_type, key) for p in parts]
        return '[' + ', '.join(emitted) + ']'

    # 4. agg.* / meta.* — direct dict access.
    if s.startswith('agg.'):
        attr = s[4:]
        return f"agg[{attr!r}]" if not _is_identifier(attr) else f"agg.{attr}"
    if s.startswith('meta.'):
        attr = s[5:]
        return f"meta[{attr!r}]" if not _is_identifier(attr) else f"meta.{attr}"

    # 5. Excel A1 range: B2:D4 (optional @sheet).
    m = _A1_RANGE.match(s)
    if m:
        c1, r1, c2, r2, sheet = m.groups()
        col_a = _col_letters_to_index(c1)
        col_b = _col_letters_to_index(c2)
        # Convert 1-based incl. data row (row 1 = header) to DataFrame iloc:
        #   B2:D4 → cols 1..3, data rows 0..2 (since row 1 = header).
        row_a = max(int(r1) - 2, 0)         # -1 for 1-based, -1 for header row
        row_b = max(int(r2) - 1, row_a + 1) # exclusive end on data rows
        target = f"sheets[{sheet!r}]" if sheet else "df"
        # Heatmap wants a DataFrame slice; everything else wants a flat list.
        # Defer the dispatch to a baked-in helper.
        if for_chart_type == 'heatmap' and key == 'data':
            return f"_a1_range_df({target}, {row_a}, {col_a}, {row_b}, {col_b + 1})"
        return f"_a1_range({target}, {row_a}, {col_a}, {row_b}, {col_b + 1})"

    # 6. Single A1 cell (must be qualified with @sheet to disambiguate from column names).
    m = _A1_CELL.match(s)
    if m:
        c1, r1, sheet = m.groups()
        col_a = _col_letters_to_index(c1)
        row_a = max(int(r1) - 2, 0)
        target = f"sheets[{sheet!r}]"
        return f"_a1_cell({target}, {row_a}, {col_a})"

    # 7. Multi-column list: Q1,Q2,Q3,Q4 (or sales!Q1,sales!Q2 …).
    if _COL_LIST.match(s):
        parts = [p.strip() for p in s.split(',')]
        # For stacked_bar's `data:` we want a dict {col_name: series}; otherwise a flat list.
        if for_chart_type in ('stacked_bar', 'stacked-bar') and key == 'data':
            entries = []
            for p in parts:
                expr, label = _column_ref_expr(p)
                entries.append(f"{label!r}: {expr}")
            return '{' + ', '.join(entries) + '}'
        # Default: produce a list-of-lists for multi-series, otherwise flatten.
        # For line_chart/area_chart `data` we want a dict so multiple series render distinctly.
        if for_chart_type in ('line', 'area') and key == 'data':
            entries = []
            for p in parts:
                expr, label = _column_ref_expr(p)
                entries.append(f"{label!r}: {expr}")
            return '{' + ', '.join(entries) + '}'
        # Otherwise flatten into one list.
        col_exprs = [_column_ref_expr(p)[0] for p in parts]
        return '(' + ' + '.join(col_exprs) + ')'

    # 8. Single column ref (with optional sheet prefix).
    m = _COL_REF.match(s)
    if m:
        sheet, col = m.group(1), m.group(2)
        if sheet:
            return f"sheets[{sheet!r}][{col!r}].tolist()"
        # The reference might also be a plain identifier the user expects to resolve
        # against the template namespace (e.g. an agg key). Default to df column lookup.
        return f"df[{col!r}].tolist() if {col!r} in df.columns else {col!r}"

    # 9. Fallback: emit as literal string.
    return repr(s)


def _column_ref_expr(ref: str) -> tuple[str, str]:
    """Resolve `Col` or `sheet!Col` to (jinja_expr, label)."""
    m = _COL_REF.match(ref.strip())
    if not m:
        return (repr(ref), ref)
    sheet, col = m.group(1), m.group(2)
    if sheet:
        return (f"sheets[{sheet!r}][{col!r}].tolist()", col)
    return (f"df[{col!r}].tolist()", col)


# ─── Misc helpers ─────────────────────────────────────────────────────────

def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _is_identifier(s: str) -> bool:
    return s.isidentifier()


def _coerce_scalar(raw: str) -> Any:
    """Convert a DSL scalar to a Python value (number / bool / string)."""
    s = raw.strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s.lower() in ('true', 'false'):
        return s.lower() == 'true'
    if _is_number(s):
        return float(s) if '.' in s or 'e' in s.lower() else int(s)
    return s


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


def _split_top_level_commas(s: str) -> list[str]:
    """Split on commas that are not inside [], (), or quoted strings."""
    parts: list[str] = []
    depth = 0
    quote: Optional[str] = None
    buf: list[str] = []
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch in '([{':
            depth += 1
            buf.append(ch)
            continue
        if ch in ')]}':
            depth -= 1
            buf.append(ch)
            continue
        if ch == ',' and depth == 0:
            parts.append(''.join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append(''.join(buf).strip())
    return parts


def _col_letters_to_index(letters: str) -> int:
    """Excel column letters (A=0, B=1, ..., AA=26) to 0-based index."""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord('A') + 1)
    return n - 1
