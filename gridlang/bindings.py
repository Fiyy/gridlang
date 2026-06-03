"""
GridLang Bindings — Two-way reactive binding between the present layer and the data layer.

The bindings layer makes individual cells in the data section editable from the
rendered preview. It introduces three pieces:

1.  **`cell()` Jinja helper** — Inline binding. Use ``{{ cell("B2") }}`` (or
    ``{{ cell("B2@sheet") }}``) inside a template. The renderer emits a
    ``<span data-grid-cell="…" contenteditable="true">VALUE</span>`` element
    that the live-preview client wires up to ``POST /api/cell-edit``.

2.  **`bind:` DSL block** — Form-style binding, parallel to ``chart:`` /
    ``format:`` blocks::

        bind: input
          cell: B2
          label: "Unit Price"
          type: number

    Emits a labeled form input that posts the same edit protocol.

3.  **`apply_edit()`** — Server-side writer. Given the original ``.grid`` source
    and an edit ``(cell, value, sheet)``, it rewrites ONLY the target row in the
    raw CSV body, preserving comments, blank lines, ``@directive`` blocks,
    formulas in other cells, and section ordering.

A1 references are accepted in the form ``B2`` (default sheet) or ``B2@sheet``.
The column letter maps to a 1-based column index; the row number is 1-based and
must include the header row (row 1 is the header).

The module deliberately keeps zero dependencies on Jinja2/Flask/HTTP — the
``client_js()`` returns a small vanilla-JS snippet that the server can inject
into the rendered HTML when ``--edit`` mode is on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd


# ─── A1 cell references ─────────────────────────────────────────────────────

# Permits B2, AA10, B2@sheet_name. Sheet name is a Python identifier-ish token.
_A1_REF = re.compile(r'^\s*([A-Za-z]+)(\d+)(?:@([A-Za-z][\w]*))?\s*$')


class BindingError(ValueError):
    """Raised when a binding directive or A1 reference is malformed."""


def parse_a1_ref(ref: str) -> tuple[int, int, Optional[str]]:
    """
    Parse an A1 cell reference like ``B2`` or ``B2@sheet``.

    Returns:
        (row_1based, col_1based, sheet_or_None)

    Raises:
        BindingError: If the reference is not a valid A1 cell reference.
    """
    if not isinstance(ref, str):
        raise BindingError(f"Cell reference must be a string, got {type(ref).__name__}")
    m = _A1_REF.match(ref)
    if not m:
        raise BindingError(f"Invalid A1 cell reference: {ref!r}")
    col_letters, row_str, sheet = m.group(1).upper(), m.group(2), m.group(3)
    # Column letters → 1-based index (A=1, B=2, …, AA=27).
    col_idx = 0
    for ch in col_letters:
        col_idx = col_idx * 26 + (ord(ch) - ord('A') + 1)
    row_idx = int(row_str)
    if row_idx < 1:
        raise BindingError(f"Row number must be ≥ 1: {ref!r}")
    return row_idx, col_idx, sheet


# ─── Inline `cell()` helper for templates ──────────────────────────────────

def make_cell_helper(
    df_by_sheet: dict[str, pd.DataFrame],
    default_sheet: str = 'default',
    *,
    editable: bool = True,
):
    """
    Build a Jinja2 helper that reads a cell value and emits an editable HTML span.

    The helper signature is ``cell(ref, *, fmt=None, editable=None)``.

    The rendered span carries ``data-grid-cell`` (e.g. ``"B2"`` or ``"B2@sales"``)
    and, when ``editable`` is True, ``contenteditable="true"`` plus an ARIA role.
    The live-preview client picks these up and POSTs edits back.
    """

    def _cell(ref: str, fmt: Optional[str] = None, editable: Optional[bool] = None) -> str:
        row, col, sheet = parse_a1_ref(ref)
        target_sheet = sheet or default_sheet
        df = df_by_sheet.get(target_sheet)
        if df is None:
            return f'<span class="grid-cell-error" title="unknown sheet {target_sheet!r}">#REF!</span>'

        # Row 1 is the header; row 2 is the first data row → df.iloc index = row - 2.
        # Allow row == 1 (header) for read-only display; we never make headers editable.
        if row == 1:
            if col - 1 < len(df.columns):
                value = df.columns[col - 1]
                return _emit_span(ref, value, editable=False, fmt=fmt)
            return f'<span class="grid-cell-error">#REF!</span>'

        df_row = row - 2
        if df_row < 0 or df_row >= len(df) or col - 1 >= len(df.columns):
            return f'<span class="grid-cell-error" title="out of range">#REF!</span>'

        value = df.iloc[df_row, col - 1]
        is_editable = editable if editable is not None else True
        return _emit_span(ref, value, editable=is_editable, fmt=fmt)

    return _cell


def _emit_span(ref: str, value: Any, *, editable: bool, fmt: Optional[str] = None) -> str:
    """Format a single cell value as an HTML span with binding attributes."""
    display = _format_value(value, fmt)
    # Escape the displayed text minimally — the cell is shown as user content,
    # not parsed as HTML on the way out.
    safe = (str(display)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;'))
    attrs = [f'data-grid-cell="{ref}"', 'class="grid-cell"']
    if editable:
        attrs.append('contenteditable="true"')
        attrs.append('role="textbox"')
        attrs.append('spellcheck="false"')
    return f'<span {" ".join(attrs)}>{safe}</span>'


def _format_value(value: Any, fmt: Optional[str]) -> str:
    """Apply a Python-style format spec to a cell value. Falls back to str()."""
    if value is None:
        return ''
    if isinstance(value, float) and pd.isna(value):
        return ''
    if fmt:
        try:
            return format(value, fmt)
        except (ValueError, TypeError):
            return str(value)
    return str(value)


# ─── `bind:` DSL block parser ──────────────────────────────────────────────

@dataclass
class BindDirective:
    """A `bind:` block parsed from the present layer."""
    kind: str = 'input'                          # input | select | checkbox | textarea
    cell: str = ''                                # A1 ref, e.g. "B2" or "B2@sales"
    label: str = ''
    input_type: str = 'text'                      # text | number | date
    options: list[str] = field(default_factory=list)  # for select
    placeholder: str = ''
    sheet: str = ''                                # explicit sheet override
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    step: Optional[str] = None


_BIND_BLOCK_HEAD = re.compile(r'^bind\s*:\s*([A-Za-z][\w\-]*)\s*$')
_BIND_KV_LINE = re.compile(r'^(\s+)([A-Za-z][\w\-]*)\s*:\s*(.*)$')

_VALID_BIND_KINDS = {'input', 'select', 'checkbox', 'textarea'}


@dataclass
class BindingsResult:
    """Result of preprocessing the present layer for `bind:` blocks."""
    template: str
    bindings: list[BindDirective] = field(default_factory=list)


def preprocess(present_text: str) -> BindingsResult:
    """
    Scan the present text and rewrite each ``bind:`` block to an HTML form widget.

    The resulting template is still Jinja2 — it can use the renderer's `cell()`
    helper to populate current values via ``{{ cell("B2") }}``.

    Lines outside ``bind:`` blocks are passed through verbatim, so this composes
    cleanly with the chart/format DSL preprocessor (which runs first).
    """
    if not present_text:
        return BindingsResult(template=present_text)

    lines = present_text.splitlines()
    out: list[str] = []
    bindings: list[BindDirective] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        m = _BIND_BLOCK_HEAD.match(line.strip())
        if m:
            kind = m.group(1).lower()
            indent = len(line) - len(line.lstrip())
            body, consumed = _collect_bind_body(lines, i + 1, indent)
            directive = _build_directive(kind, body)
            bindings.append(directive)
            out.append(_render_bind_html(directive, indent_prefix=line[:indent]))
            i = i + 1 + consumed
            continue
        out.append(line)
        i += 1

    return BindingsResult(template='\n'.join(out), bindings=bindings)


def _collect_bind_body(lines: list[str], start: int, head_indent: int) -> tuple[dict[str, list[str]], int]:
    """Collect ``key: value`` pairs that belong to a bind block."""
    pairs: dict[str, list[str]] = {}
    consumed = 0
    j = start
    last_key: Optional[str] = None
    while j < len(lines):
        raw = lines[j]
        if raw.strip() == '':
            consumed += 1
            j += 1
            # Two consecutive blanks end a block (consistent with chart_dsl).
            if j < len(lines) and lines[j].strip() == '':
                break
            continue
        cur_indent = len(raw) - len(raw.lstrip())
        if cur_indent <= head_indent:
            break
        m = _BIND_KV_LINE.match(raw)
        if m:
            key = m.group(2).strip()
            val = m.group(3).strip()
            pairs.setdefault(key, []).append(val)
            last_key = key
        elif last_key is not None:
            # Continuation line — append to the previous value.
            pairs[last_key][-1] = (pairs[last_key][-1] + ' ' + raw.strip()).strip()
        consumed += 1
        j += 1
    return pairs, consumed


def _build_directive(kind: str, body: dict[str, list[str]]) -> BindDirective:
    """Materialize a BindDirective from raw body pairs."""
    if kind not in _VALID_BIND_KINDS:
        raise BindingError(
            f"Unknown bind kind {kind!r}. "
            f"Valid: {', '.join(sorted(_VALID_BIND_KINDS))}"
        )

    def _last(name: str, default: str = '') -> str:
        vlist = body.get(name) or []
        return vlist[-1] if vlist else default

    cell = _last('cell').strip()
    if not cell:
        raise BindingError(f"bind: {kind} requires a `cell:` reference")
    # Validate eagerly so a bad ref fails at preprocess, not at edit time.
    parse_a1_ref(cell)

    label = _strip_quotes(_last('label'))
    input_type = _last('type', 'text').strip().lower() or 'text'
    placeholder = _strip_quotes(_last('placeholder'))
    sheet = _last('sheet').strip()

    options_raw = body.get('options') or body.get('option') or []
    options: list[str] = []
    for opt in options_raw:
        # Allow comma-separated lists or repeated `option:` keys.
        for piece in _split_csv(opt):
            options.append(_strip_quotes(piece))

    return BindDirective(
        kind=kind,
        cell=cell,
        label=label,
        input_type=input_type,
        options=options,
        placeholder=placeholder,
        sheet=sheet,
        min_value=_last('min', None) or None,
        max_value=_last('max', None) or None,
        step=_last('step', None) or None,
    )


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s


def _split_csv(s: str) -> list[str]:
    """Tiny CSV-ish split that respects double-quoted commas."""
    out: list[str] = []
    cur = ''
    in_quote = False
    for ch in s:
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch == ',' and not in_quote:
            out.append(cur.strip())
            cur = ''
            continue
        cur += ch
    if cur.strip():
        out.append(cur.strip())
    return [x for x in out if x]


def _render_bind_html(directive: BindDirective, indent_prefix: str = '') -> str:
    """Render a BindDirective to an HTML form widget. Includes the current value."""
    cell = directive.cell
    label = directive.label
    label_html = f'<label class="grid-bind-label">{_html_escape(label)}</label>' if label else ''

    # Each widget reads its current value via the `cell()` Jinja helper. We embed
    # that as a Jinja expression so renderer.py resolves it during template render.
    current_expr = f'{{{{ cell({cell!r}, editable=False) }}}}'

    if directive.kind == 'select':
        options_html = ''.join(
            f'<option value="{_html_escape(o)}">{_html_escape(o)}</option>'
            for o in directive.options
        )
        widget = (f'<select class="grid-bind-input" data-grid-bind="{cell}" '
                  f'data-grid-bind-kind="select">'
                  f'{options_html}</select>')
    elif directive.kind == 'checkbox':
        widget = (f'<input type="checkbox" class="grid-bind-input" '
                  f'data-grid-bind="{cell}" data-grid-bind-kind="checkbox" />')
    elif directive.kind == 'textarea':
        widget = (f'<textarea class="grid-bind-input" data-grid-bind="{cell}" '
                  f'data-grid-bind-kind="textarea" '
                  f'placeholder="{_html_escape(directive.placeholder)}"></textarea>')
    else:  # input
        attrs = [
            f'type="{directive.input_type}"',
            f'class="grid-bind-input"',
            f'data-grid-bind="{cell}"',
            f'data-grid-bind-kind="input"',
        ]
        if directive.placeholder:
            attrs.append(f'placeholder="{_html_escape(directive.placeholder)}"')
        if directive.min_value is not None:
            attrs.append(f'min="{_html_escape(directive.min_value)}"')
        if directive.max_value is not None:
            attrs.append(f'max="{_html_escape(directive.max_value)}"')
        if directive.step is not None:
            attrs.append(f'step="{_html_escape(directive.step)}"')
        widget = f'<input {" ".join(attrs)} />'

    # Wrap in a div carrying the current value as data-* so the client-side JS
    # can populate the widget after render.
    return (
        f'{indent_prefix}<div class="grid-bind" data-grid-bind-current="{current_expr}">'
        f'{label_html}{widget}</div>'
    )


def _html_escape(s: str) -> str:
    return (str(s)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


# ─── Server-side edit applier ──────────────────────────────────────────────

def apply_edit(
    grid_source: str,
    *,
    cell: str,
    value: Any,
    sheet: Optional[str] = None,
) -> str:
    """
    Apply a single-cell edit to the raw .grid source string.

    Returns the updated .grid source. Only the target CSV row is modified —
    the rest of the file (meta, comments, blank lines, @directives, formulas,
    other rows, compute/present sections) is preserved byte-for-byte.

    Args:
        grid_source: Original .grid file content.
        cell:        A1 cell reference, e.g. ``"B2"`` or ``"B2@sales"``.
        value:       New cell value (any type — converted to string).
        sheet:       Optional sheet override. If both ``cell`` carries an
                     ``@sheet`` qualifier and ``sheet`` is given, ``sheet``
                     wins (lets the server pin the sheet without trusting
                     the client's reference).

    Raises:
        BindingError: If the cell ref is malformed, the sheet is unknown,
                      or the row/column is out of range.
    """
    row_1, col_1, ref_sheet = parse_a1_ref(cell)
    target_sheet = sheet or ref_sheet  # explicit sheet wins
    if row_1 == 1:
        raise BindingError(
            f"Cannot edit header row via {cell!r}; only data rows (row ≥ 2) are editable."
        )

    lines = grid_source.split('\n')
    section_bounds = _locate_data_section(lines, target_sheet)
    if section_bounds is None:
        raise BindingError(
            f"Cannot find data section for sheet {target_sheet or 'default'!r}"
        )
    body_start, body_end = section_bounds

    # Walk body lines: skip @directives, blank lines, and comments; the
    # remaining lines are CSV. The first CSV line is the header (row 1),
    # the next is row 2, etc.
    new_lines = list(lines)
    csv_seen = 0
    target_csv_row = row_1  # 1-based; row 1 = header

    for idx in range(body_start, body_end):
        raw = lines[idx]
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith('@'):
            continue
        if stripped.startswith('#'):
            continue
        csv_seen += 1
        if csv_seen != target_csv_row:
            continue

        # Update column col_1 (1-based) in this CSV row.
        # Preserve leading whitespace if the line was indented.
        leading = raw[:len(raw) - len(raw.lstrip())]
        cells = stripped.split(',')
        # Pad if needed so col_1 is reachable.
        while len(cells) < col_1:
            cells.append('')
        new_value = _csv_cell_repr(value)
        cells[col_1 - 1] = new_value
        new_lines[idx] = leading + ','.join(cells)
        return '\n'.join(new_lines)

    raise BindingError(
        f"Row {row_1} not found in sheet {target_sheet or 'default'!r}"
        f" (only {csv_seen} CSV rows present)"
    )


def _csv_cell_repr(value: Any) -> str:
    """Format a Python value for a CSV cell. Quotes only when needed."""
    if value is None:
        return ''
    s = str(value)
    needs_quote = (',' in s) or ('"' in s) or s.startswith(' ') or s.endswith(' ') or '\n' in s
    if needs_quote:
        return '"' + s.replace('"', '""') + '"'
    return s


# Match `--- data ---` or `--- data:sheet ---`.
_DATA_HEAD_DEFAULT = re.compile(r'^\s*---\s+data\s+---\s*$')
_DATA_HEAD_NAMED = re.compile(r'^\s*---\s+data:([A-Za-z][\w]*)\s+---\s*$')
_ANY_HEAD = re.compile(r'^\s*---\s+\w[\w:]*\s+---\s*$')


def _locate_data_section(lines: list[str], sheet: Optional[str]) -> Optional[tuple[int, int]]:
    """
    Find the [start, end) line range of the body of the requested data section.

    For ``sheet=None`` or ``sheet='default'``, picks the first data section
    encountered (matching how the parser populates ``data_specs['default']``).
    For a named sheet, requires ``--- data:sheet ---``.

    Returns:
        (body_start, body_end) line indices, or None if not found.
    """
    target_named = sheet and sheet != 'default'
    head_idx: Optional[int] = None

    for i, line in enumerate(lines):
        if target_named:
            m = _DATA_HEAD_NAMED.match(line)
            if m and m.group(1) == sheet:
                head_idx = i
                break
        else:
            # Default sheet: prefer `--- data ---` exactly, else first `data:*`.
            if _DATA_HEAD_DEFAULT.match(line):
                head_idx = i
                break
            m = _DATA_HEAD_NAMED.match(line)
            if m and head_idx is None:
                head_idx = i
                # Don't break — keep looking for an unnamed `--- data ---`.

    if head_idx is None:
        return None

    body_start = head_idx + 1
    body_end = len(lines)
    for i in range(body_start, len(lines)):
        if _ANY_HEAD.match(lines[i]):
            body_end = i
            break
    return body_start, body_end


# ─── Client-side JS for live-preview server ────────────────────────────────

def client_js(api_url: str = '/api/cell-edit') -> str:
    """
    Return a self-contained <script> tag that wires up bound cells/inputs.

    Wires up:
      * ``[data-grid-cell]``     — contenteditable cells (commit on blur or Enter)
      * ``[data-grid-bind]``     — form inputs (commit on change)
      * ``[data-grid-bind-current]`` — propagates current value into the widget

    On a successful POST the page reloads the preview iframe (or the page
    itself) so the rest of the document re-renders against the new data.
    """
    return _CLIENT_JS_TEMPLATE.replace('@@API@@', api_url)


_CLIENT_JS_TEMPLATE = """
<script>
(function() {
  const API = "@@API@@";

  function notify(msg, kind) {
    let el = document.getElementById('__grid_toast');
    if (!el) {
      el = document.createElement('div');
      el.id = '__grid_toast';
      el.style.cssText = 'position:fixed;bottom:1rem;right:1rem;padding:.5rem .9rem;'
        + 'border-radius:6px;font:13px -apple-system,sans-serif;z-index:9999;'
        + 'transition:opacity .3s';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.style.background = kind === 'err' ? '#fee2e2' : '#dcfce7';
    el.style.color = kind === 'err' ? '#991b1b' : '#166534';
    el.style.opacity = '1';
    setTimeout(() => { el.style.opacity = '0'; }, 1200);
  }

  async function commit(cell, value) {
    const resp = await fetch(API, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cell: cell, value: value, save: true}),
    });
    const d = await resp.json();
    if (!resp.ok || d.error) {
      notify(d.error || ('HTTP ' + resp.status), 'err');
      return false;
    }
    notify('saved', 'ok');
    return true;
  }

  // 1. Contenteditable cells.
  document.querySelectorAll('[data-grid-cell][contenteditable="true"]').forEach(el => {
    let original = el.textContent;
    el.addEventListener('focus', () => { original = el.textContent; });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); el.blur(); }
      if (e.key === 'Escape') { el.textContent = original; el.blur(); }
    });
    el.addEventListener('blur', async () => {
      const v = el.textContent.trim();
      if (v === original) return;
      const ok = await commit(el.dataset.gridCell, v);
      if (ok) { setTimeout(() => location.reload(), 250); }
      else el.textContent = original;
    });
  });

  // 2. Form inputs from bind: blocks. Commit on change/blur.
  document.querySelectorAll('[data-grid-bind]').forEach(el => {
    // Initialize from data-grid-bind-current on the wrapper, if present.
    const wrap = el.closest('.grid-bind');
    if (wrap && wrap.dataset.gridBindCurrent) {
      const cur = wrap.dataset.gridBindCurrent.trim();
      if (el.tagName === 'SELECT') {
        for (const opt of el.options) if (opt.value === cur) { opt.selected = true; break; }
      } else if (el.type === 'checkbox') {
        el.checked = (cur === 'true' || cur === '1' || cur === 'yes');
      } else {
        el.value = cur;
      }
    }
    el.addEventListener('change', async () => {
      const v = (el.type === 'checkbox') ? (el.checked ? 'true' : 'false') : el.value;
      const ok = await commit(el.dataset.gridBind, v);
      if (ok) setTimeout(() => location.reload(), 250);
    });
  });
})();
</script>
"""


# ─── Default styles ────────────────────────────────────────────────────────

BINDING_STYLES = """
<style>
  .grid-cell { padding: 1px 4px; border-radius: 3px; }
  .grid-cell[contenteditable="true"] { outline: 1px dashed #cbd5e1;
    cursor: text; min-width: 1.5rem; display: inline-block; }
  .grid-cell[contenteditable="true"]:hover { background: #fef9c3; outline-color: #f59e0b; }
  .grid-cell[contenteditable="true"]:focus { background: #fef3c7;
    outline: 2px solid #f59e0b; outline-offset: -1px; }
  .grid-cell-error { color: #b91c1c; font-weight: 600; }
  .grid-bind { display: inline-flex; flex-direction: column; gap: .25rem;
    margin: .5rem .75rem .5rem 0; }
  .grid-bind-label { font-size: .8rem; color: #475569; font-weight: 500; }
  .grid-bind-input { padding: .35rem .55rem; border: 1px solid #cbd5e1;
    border-radius: 6px; font: inherit; font-size: .9rem; min-width: 9rem; }
  .grid-bind-input:focus { outline: 2px solid #2563eb; outline-offset: -1px;
    border-color: #2563eb; }
</style>
"""
