"""
GridLang Server — Local HTTP server for live preview and editing of .grid files.

Usage:
  gridlang serve file.grid [--port 8080]           # Preview only
  gridlang serve file.grid [--port 8080] --edit     # Editor + Preview

Features:
- Preview mode: renders .grid file, auto-reloads on file changes
- Editor mode: Monaco Editor on left, live preview on right
- API endpoints for reading/writing .grid content and rendering
"""

from __future__ import annotations

import os
import json
import base64
import tempfile
import traceback
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Optional

from gridlang.parser import parse_string, parse_file, ParseError
from gridlang.schema import parse_data
from gridlang.runtime import execute
from gridlang.renderer import render
from gridlang.data_sources import load_dataframes
from gridlang.bindings import apply_edit, BindingError, client_js
import pandas as pd


class GridLangHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves rendered .grid files and editor UI."""

    grid_path: Path = None
    edit_mode: bool = False
    allow_remote: bool = False

    def do_GET(self):
        path = urlparse(self.path).path

        if path == '/' or path == '/index.html':
            if self.edit_mode:
                self._serve_editor()
            else:
                self._serve_preview()
        elif path == '/preview':
            self._serve_preview()
        elif path == '/api/render':
            self._api_render_get()
        elif path == '/api/source':
            self._api_source()
        elif path == '/api/poll':
            self._api_poll()
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == '/api/render':
            self._api_render_post()
        elif path == '/api/save':
            self._api_save()
        elif path == '/api/import':
            self._api_import()
        elif path == '/api/export/xlsx':
            self._api_export_xlsx()
        elif path == '/api/export/csv':
            self._api_export_csv()
        elif path == '/api/cell-edit':
            self._api_cell_edit()
        else:
            self.send_error(404)

    # =========================================================================
    # Pages
    # =========================================================================

    def _serve_editor(self):
        """Serve the full editor UI."""
        self._send_html(200, EDITOR_HTML)

    def _serve_preview(self):
        """Render and serve the .grid file as standalone HTML."""
        try:
            html = self._render_from_file()
            # Inject auto-reload + (in --edit mode) bindings client JS so
            # contenteditable cells and `bind:` widgets can post back to /api/cell-edit.
            extra = _RELOAD_SCRIPT
            if self.edit_mode and ('data-grid-cell' in html or 'data-grid-bind' in html):
                extra = extra + client_js()
            if '</body>' in html:
                html = html.replace('</body>', extra + '</body>')
            else:
                html = html + extra
            self._send_html(200, html)
        except Exception as e:
            self._send_html(500, _error_page(str(e)))

    # =========================================================================
    # API Endpoints
    # =========================================================================

    def _api_source(self):
        """GET /api/source — return raw .grid file content."""
        try:
            content = self.grid_path.read_text(encoding='utf-8')
            self._send_json(200, {'content': content, 'filename': self.grid_path.name})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _api_poll(self):
        """GET /api/poll — return file mtime for change detection."""
        try:
            mtime = os.path.getmtime(self.grid_path)
            self._send_json(200, {'mtime': mtime})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _api_render_get(self):
        """GET /api/render — render the current file and return HTML."""
        try:
            html = self._render_from_file()
            self._send_json(200, {'html': html, 'error': None})
        except Exception as e:
            self._send_json(200, {'html': None, 'error': str(e)})

    def _api_render_post(self):
        """POST /api/render — render .grid content from request body, return HTML + data info."""
        try:
            body = self._read_body()
            data = json.loads(body)
            content = data.get('content', '')

            doc = parse_string(content)
            # Get raw data for editable table
            raw_df = parse_data(doc.data_raw)
            columns = raw_df.columns.tolist()
            raw_rows = []
            for _, row in raw_df.iterrows():
                raw_rows.append({col: (None if pd.isna(row[col]) else row[col]) for col in columns})

            html = self._render_doc(doc)
            self._send_json(200, {
                'html': html,
                'error': None,
                'columns': columns,
                'raw_data': raw_rows,
            })
        except Exception as e:
            self._send_json(200, {'html': None, 'error': str(e), 'columns': [], 'raw_data': []})

    def _api_save(self):
        """POST /api/save — save .grid content to file."""
        try:
            body = self._read_body()
            data = json.loads(body)
            content = data.get('content', '')

            # Validate before saving
            parse_string(content)

            self.grid_path.write_text(content, encoding='utf-8')
            self._send_json(200, {'saved': True, 'filename': self.grid_path.name})
        except ParseError as e:
            self._send_json(400, {'saved': False, 'error': f'Invalid .grid format: {e}'})
        except Exception as e:
            self._send_json(500, {'saved': False, 'error': str(e)})

    def _api_import(self):
        """POST /api/import — upload xlsx/csv file, return .grid content."""
        try:
            body = self._read_body()
            data = json.loads(body)
            filename = data.get('filename', 'upload.xlsx')
            file_b64 = data.get('data', '')  # base64-encoded file content

            file_bytes = base64.b64decode(file_b64)
            suffix = Path(filename).suffix.lower()

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name

            try:
                if suffix == '.csv':
                    from gridlang.csv_io import import_csv
                    grid_content = import_csv(tmp_path)
                elif suffix in ('.xlsx', '.xls'):
                    from gridlang.excel_import import import_excel
                    grid_content = import_excel(tmp_path)
                else:
                    self._send_json(400, {'error': f'Unsupported format: {suffix}'})
                    return
            finally:
                os.unlink(tmp_path)

            self._send_json(200, {'content': grid_content, 'filename': filename})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _api_export_xlsx(self):
        """POST /api/export/xlsx — render current .grid content and return xlsx as base64."""
        try:
            body = self._read_body()
            data = json.loads(body)
            content = data.get('content', '')

            # Save to temp .grid file, then export
            with tempfile.NamedTemporaryFile(suffix='.grid', mode='w', delete=False, encoding='utf-8') as tmp:
                tmp.write(content)
                grid_path = tmp.name

            xlsx_path = grid_path + '.xlsx'
            try:
                from gridlang.excel_export import export_excel
                export_excel(grid_path, xlsx_path)
                with open(xlsx_path, 'rb') as f:
                    xlsx_b64 = base64.b64encode(f.read()).decode('ascii')
            finally:
                os.unlink(grid_path)
                if os.path.exists(xlsx_path):
                    os.unlink(xlsx_path)

            self._send_json(200, {'data': xlsx_b64, 'filename': 'export.xlsx'})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _api_export_csv(self):
        """POST /api/export/csv — render current .grid content and return csv string."""
        try:
            body = self._read_body()
            data = json.loads(body)
            content = data.get('content', '')

            # Parse and execute
            doc = parse_string(content)
            if doc.is_multi_sheet:
                sheets = {name: parse_data(raw) for name, raw in doc.sheets_raw.items()}
                primary_df = list(sheets.values())[0]
                result = execute(doc.compute_raw, primary_df, sheets=sheets, engine=doc.engine)
            else:
                primary_df = parse_data(doc.data_raw)
                result = execute(doc.compute_raw, primary_df, engine=doc.engine)

            csv_str = result.df.to_csv(index=False)
            self._send_json(200, {'data': csv_str, 'filename': 'export.csv'})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _api_cell_edit(self):
        """POST /api/cell-edit — apply a cell edit to the .grid source.

        Accepts two payload shapes:

          New (A1) form, used by reactive bindings:
              {"cell": "B2", "value": "120", "sheet": null, "save": true}

          Legacy (row + column-name) form, used by the editor UI:
              {"content": "...", "row": 0, "col": "Region", "value": "North"}

        For the new form, ``save: true`` writes the result back to disk
        (only honored when the server was started with ``--edit``). For
        either shape, the response includes the updated ``content`` plus
        a freshly-rendered HTML fragment so callers can refresh the
        preview without a second round-trip.
        """
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self._send_json(400, {'error': f'Invalid JSON: {e}'})
            return

        try:
            # Dispatch on payload shape.
            if 'cell' in data:
                new_content, html = self._apply_a1_edit(data)
            else:
                new_content, html = self._apply_legacy_edit(data)
        except BindingError as e:
            self._send_json(400, {'error': str(e)})
            return
        except (ParseError, ValueError) as e:
            self._send_json(400, {'error': str(e)})
            return
        except Exception as e:
            self._send_json(500, {'error': f'{type(e).__name__}: {e}'})
            return

        self._send_json(200, {'content': new_content, 'html': html, 'error': None})

    def _apply_a1_edit(self, data: dict) -> tuple[str, str]:
        """Handle ``{cell, value, sheet?, save?, content?}`` payloads."""
        cell = data.get('cell')
        if not cell:
            raise BindingError("'cell' is required")
        value = data.get('value', '')
        sheet = data.get('sheet')
        save = bool(data.get('save', False))

        # Source: explicit content beats on-disk file.
        content = data.get('content')
        if content is None:
            content = self.grid_path.read_text(encoding='utf-8')

        new_content = apply_edit(content, cell=cell, value=value, sheet=sheet)

        # Render so the client can refresh the preview without a second hop.
        try:
            html = self._render_from_string(new_content)
        except Exception as e:
            html = ''  # render failure isn't fatal for the edit itself

        if save:
            self.grid_path.write_text(new_content, encoding='utf-8')

        return new_content, html

    def _apply_legacy_edit(self, data: dict) -> tuple[str, str]:
        """Handle the original ``{content, row, col, value, sheet}`` payloads."""
        content = data.get('content', '')
        row = data.get('row')
        col = data.get('col')
        value = data.get('value', '')
        sheet = data.get('sheet', None)

        doc = parse_string(content)

        # Determine which raw data section to edit.
        if sheet and sheet in doc.sheets_raw:
            raw_csv = doc.sheets_raw[sheet]
        else:
            raw_csv = doc.data_raw

        csv_lines = raw_csv.strip().split('\n')
        if len(csv_lines) < 2:
            raise ValueError('No data rows')

        headers = [h.strip() for h in csv_lines[0].split(',')]
        if col not in headers:
            raise ValueError(f'Column "{col}" not found')

        col_idx = headers.index(col)
        data_row_idx = row + 1  # +1 for header

        if data_row_idx >= len(csv_lines):
            raise ValueError(f'Row {row} out of range')

        row_parts = csv_lines[data_row_idx].split(',')
        while len(row_parts) <= col_idx:
            row_parts.append('')
        row_parts[col_idx] = str(value)
        csv_lines[data_row_idx] = ','.join(row_parts)

        new_csv = '\n'.join(csv_lines)
        new_content = self._replace_data_section(content, new_csv, sheet)

        try:
            html = self._render_from_string(new_content)
        except Exception:
            html = ''
        return new_content, html


    @staticmethod
    def _replace_data_section(content: str, new_csv: str, sheet: str = None) -> str:
        """Replace the data section in .grid source with new CSV content."""
        import re
        lines = content.split('\n')

        # Find the target data section
        if sheet and sheet != 'default':
            target = f'data:{sheet}'
        else:
            target = 'data'

        # Find section boundaries
        section_start = None
        section_end = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if re.match(r'^---\s+' + re.escape(target) + r'\s+---$', stripped) or \
               (target == 'data' and re.match(r'^---\s+data\s+---$', stripped)):
                section_start = i + 1
            elif section_start is not None and re.match(r'^---\s+\w', stripped):
                section_end = i
                break

        if section_start is None:
            return content

        if section_end is None:
            section_end = len(lines)

        # Replace
        new_lines = lines[:section_start] + [new_csv, ''] + lines[section_end:]
        return '\n'.join(new_lines)

    # =========================================================================
    # Render Helpers
    # =========================================================================

    def _render_from_file(self) -> str:
        """Render the .grid file from disk."""
        doc = parse_file(self.grid_path)
        return self._render_doc(doc)

    def _render_from_string(self, content: str) -> str:
        """Render .grid content from a string."""
        doc = parse_string(content)
        return self._render_doc(doc)

    def _render_doc(self, doc) -> str:
        """Execute compute + render HTML for a parsed GridDocument."""
        sheets, _ = load_dataframes(doc, allow_remote=self.allow_remote)
        primary_df = list(sheets.values())[0] if sheets else pd.DataFrame()
        sheets_for_compute = sheets if doc.is_multi_sheet else None

        result = execute(doc.compute_raw, primary_df, sheets=sheets_for_compute, engine=doc.engine)

        html = render(
            template_content=doc.present_raw,
            df=result.df,
            aggregates=result.aggregates,
            meta=doc.meta,
            raw_df=primary_df,
            sheets=result.sheets if result.is_multi_sheet else None,
            conditional_formats=result.conditional_formats,
            standalone=False,
        )
        return html

    # =========================================================================
    # HTTP Helpers
    # =========================================================================

    def _send_html(self, code: int, html: str):
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def _send_json(self, code: int, data: dict):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode('utf-8'))

    def _read_body(self) -> str:
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length).decode('utf-8')

    def log_message(self, format, *args):
        pass  # Suppress request logging


# =============================================================================
# Auto-reload script (for preview-only mode)
# =============================================================================

_RELOAD_SCRIPT = """
<script>
(function() {
  let lastMtime = 0;
  async function poll() {
    try {
      const resp = await fetch('/api/poll');
      const data = await resp.json();
      if (lastMtime && data.mtime !== lastMtime) { location.reload(); }
      lastMtime = data.mtime;
    } catch(e) {}
    setTimeout(poll, 1000);
  }
  poll();
})();
</script>
"""


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><title>GridLang Error</title>
<style>body {{ font-family: monospace; padding: 2rem; }}
.error {{ background: #fef2f2; border: 1px solid #fecaca; padding: 1.5rem;
          border-radius: 8px; color: #991b1b; white-space: pre-wrap; }}</style>
</head><body>
<h2>GridLang Render Error</h2>
<div class="error">{message}</div>
{_RELOAD_SCRIPT}
</body></html>"""


# =============================================================================
# Editor HTML — Fully self-contained, zero external dependencies
# =============================================================================

EDITOR_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GridLang Editor</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --bg: #0f172a; --bg2: #1e293b; --bg3: #283548; --border: #334155;
    --text: #e2e8f0; --dim: #94a3b8; --accent: #3b82f6;
    --green: #10b981; --red: #ef4444; --yellow: #f59e0b;
    --bar-h: 42px; --tab-h: 30px;
  }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
         background: var(--bg); color: var(--text); height: 100vh; overflow: hidden; }

  /* ── Toolbar ── */
  .bar {
    height: var(--bar-h); background: var(--bg2); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; padding: 0 14px; gap: 10px;
  }
  .bar .logo { font-weight: 700; font-size: 14px; color: var(--accent); }
  .bar .fname { font-size: 12px; color: var(--dim); }
  .bar .sep { flex: 1; }
  .bar .st {
    font-size: 11px; padding: 2px 10px; border-radius: 3px; font-weight: 500;
  }
  .bar .st.ok { background: #064e3b; color: #6ee7b7; }
  .bar .st.err { background: #7f1d1d; color: #fca5a5; }
  .bar .st.busy { background: #1e3a5f; color: #93c5fd; }
  .bar button {
    padding: 4px 12px; border-radius: 5px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); font-size: 12px; cursor: pointer;
  }
  .bar button:hover { background: var(--bg3); }
  .bar button.pri { background: var(--accent); border-color: var(--accent); color: #fff; }
  .bar button.pri:hover { background: #2563eb; }

  /* ── Layout ── */
  .main { display: flex; height: calc(100vh - var(--bar-h)); }

  /* ── Editor Pane ── */
  .ed-pane {
    width: 50%; min-width: 250px; display: flex; flex-direction: column;
    border-right: 2px solid var(--border); position: relative;
  }
  .ed-tabs {
    height: var(--tab-h); background: var(--bg); display: flex; align-items: center;
    padding: 0 8px; border-bottom: 1px solid var(--border);
  }
  .ed-tab {
    padding: 3px 10px; font-size: 11px; background: var(--bg2); color: var(--dim);
    border-radius: 3px 3px 0 0; border: 1px solid var(--border); border-bottom: none;
  }
  .ed-tab.active { color: var(--text); }

  /* Editor wrapper with line numbers */
  .ed-wrap { flex: 1; display: flex; overflow: hidden; }
  .ed-lines {
    width: 44px; background: var(--bg); color: #475569; font: 13px/20px 'SF Mono', 'Fira Code', 'Consolas', monospace;
    text-align: right; padding: 10px 6px 10px 0; overflow: hidden; user-select: none;
    border-right: 1px solid var(--border);
  }
  .ed-input {
    flex: 1; background: var(--bg); color: #cbd5e1; border: none; outline: none; resize: none;
    font: 13px/20px 'SF Mono', 'Fira Code', 'Consolas', monospace;
    padding: 10px 12px; tab-size: 4; white-space: pre; overflow: auto;
    caret-color: #3b82f6;
  }
  .ed-input::selection { background: rgba(59,130,246,0.3); }

  /* Error panel */
  .err-box {
    display: none; background: #1c1917; color: #fca5a5; padding: 8px 14px;
    font: 12px/18px monospace; max-height: 100px; overflow-y: auto;
    border-top: 2px solid var(--red);
  }
  .err-box.vis { display: block; }

  /* ── Resize Handle ── */
  .resizer {
    width: 5px; cursor: col-resize; background: transparent; z-index: 10;
  }
  .resizer:hover, .resizer.on { background: var(--accent); }

  /* ── Preview Pane ── */
  .pv-pane { flex: 1; display: flex; flex-direction: column; background: #fff; }
  .pv-bar {
    height: var(--tab-h); background: #f8fafc; border-bottom: 1px solid #e2e8f0;
    display: flex; align-items: center; padding: 0 10px; gap: 6px;
  }
  .pv-bar span { font-size: 11px; color: #64748b; }
  .pv-dot { width: 8px; height: 8px; border-radius: 50%; }
  .pv-dot.g { background: var(--green); }
  .pv-dot.r { background: var(--red); }
  .pv-dot.y { background: var(--yellow); }
  .pv-tabs { display: flex; gap: 0; margin-left: auto; }
  .pv-tab {
    padding: 3px 12px; font-size: 11px; cursor: pointer; color: #64748b;
    border-bottom: 2px solid transparent;
  }
  .pv-tab.active { color: #2563eb; border-bottom-color: #2563eb; font-weight: 600; }
  .pv-tab:hover { color: #1e40af; }
  #pv-frame { flex: 1; border: none; width: 100%; }
  #pv-data { flex: 1; overflow: auto; display: none; padding: 0; }
  #pv-data table { width: 100%; border-collapse: collapse; font-size: 13px; }
  #pv-data th {
    background: #1e40af; color: #fff; padding: 6px 10px; font-weight: 600;
    text-align: left; font-size: 11px; position: sticky; top: 0; z-index: 1;
  }
  #pv-data td {
    padding: 2px 4px; border: 1px solid #e2e8f0; cursor: cell; min-width: 70px;
  }
  #pv-data td:focus {
    outline: 2px solid #3b82f6; outline-offset: -2px; background: #eff6ff;
  }
  #pv-data tr:hover td { background: #f8fafc; }
  #pv-data td.edited { background: #fefce8; }

  /* ── Keyboard shortcut hint ── */
  .hint { position: fixed; bottom: 8px; right: 12px; font-size: 10px; color: #475569; }
  .hint kbd { background: #1e293b; padding: 1px 5px; border-radius: 3px; border: 1px solid #334155; }
</style>
</head>
<body>

<!-- Toolbar -->
<div class="bar">
  <span class="logo">GridLang</span>
  <span class="fname" id="fname">loading...</span>
  <div class="sep"></div>
  <span class="st ok" id="status">Ready</span>
  <button onclick="doImport()">Import</button>
  <button onclick="showExportMenu()">Export ▾</button>
  <button onclick="doFormat()">Format</button>
  <button class="pri" onclick="doSave()">Save</button>
</div>

<!-- Hidden file input for import -->
<input type="file" id="file-input" accept=".xlsx,.xls,.csv" style="display:none" onchange="handleFileSelect(event)">

<!-- Export dropdown -->
<div id="export-menu" style="display:none; position:fixed; top:42px; right:80px; background:var(--bg2); border:1px solid var(--border); border-radius:6px; padding:4px 0; z-index:999; box-shadow:0 4px 12px rgba(0,0,0,0.3);">
  <div style="padding:6px 16px; font-size:12px; cursor:pointer; color:var(--text);" onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''" onclick="doExport('xlsx')">Export as Excel (.xlsx)</div>
  <div style="padding:6px 16px; font-size:12px; cursor:pointer; color:var(--text);" onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''" onclick="doExport('csv')">Export as CSV (.csv)</div>
</div>

<!-- Main -->
<div class="main">
  <!-- Editor -->
  <div class="ed-pane" id="ed-pane">
    <div class="ed-tabs"><div class="ed-tab active">Source</div></div>
    <div class="ed-wrap">
      <div class="ed-lines" id="lines">1</div>
      <textarea class="ed-input" id="editor" spellcheck="false" autocomplete="off" autocorrect="off" autocapitalize="off"></textarea>
    </div>
    <div class="err-box" id="errbox"></div>
  </div>

  <!-- Resize -->
  <div class="resizer" id="resizer"></div>

  <!-- Preview -->
  <div class="pv-pane">
    <div class="pv-bar">
      <div class="pv-dot g" id="dot"></div>
      <span id="pv-st">Preview</span>
      <div class="pv-tabs">
        <div class="pv-tab active" onclick="switchPvTab('render')">Render</div>
        <div class="pv-tab" onclick="switchPvTab('data')">Data</div>
      </div>
    </div>
    <iframe id="pv-frame" sandbox="allow-scripts"></iframe>
    <div id="pv-data"></div>
  </div>
</div>

<div class="hint"><kbd>Ctrl</kbd>+<kbd>S</kbd> save &nbsp; Edits auto-preview in 600ms</div>

<script>
// State
const ed = document.getElementById('editor');
const lines = document.getElementById('lines');
const errbox = document.getElementById('errbox');
const dot = document.getElementById('dot');
const pvSt = document.getElementById('pv-st');
let saved = '';
let timer = null;

// ── Load file ──
fetch('/api/source').then(r=>r.json()).then(d => {
  document.getElementById('fname').textContent = d.filename;
  saved = d.content;
  ed.value = d.content;
  syncLines();
  doRender();
});

// ── Editor events ──
ed.addEventListener('input', () => {
  syncLines();
  clearTimeout(timer);
  timer = setTimeout(doRender, 600);
  updMod();
});
ed.addEventListener('scroll', () => { lines.scrollTop = ed.scrollTop; });
ed.addEventListener('keydown', handleTab);

function syncLines() {
  const n = ed.value.split('\n').length;
  const arr = [];
  for (let i = 1; i <= n; i++) arr.push(i);
  lines.textContent = arr.join('\n');
}

function handleTab(e) {
  if (e.key === 'Tab') {
    e.preventDefault();
    const s = ed.selectionStart, en = ed.selectionEnd;
    ed.value = ed.value.substring(0, s) + '    ' + ed.value.substring(en);
    ed.selectionStart = ed.selectionEnd = s + 4;
    syncLines();
  }
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    doSave();
  }
}

// ── Render preview ──
async function doRender() {
  dot.className = 'pv-dot y';
  pvSt.textContent = 'Rendering...';
  try {
    const r = await fetch('/api/render', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({content: ed.value}),
    });
    const d = await r.json();
    if (d.error) {
      dot.className = 'pv-dot r'; pvSt.textContent = 'Error';
      errbox.textContent = d.error; errbox.classList.add('vis');
    } else {
      dot.className = 'pv-dot g'; pvSt.textContent = 'Preview';
      errbox.classList.remove('vis');

      // Store raw data for editable Data tab
      lastColumns = d.columns || [];
      lastRawData = d.raw_data || [];
      if (currentPvTab === 'data') buildDataTable();

      const full = `<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.6;color:#1a1a1a;max-width:980px;margin:0 auto;padding:1.2rem}
h1,h2,h3{color:#111827}
table{width:100%;border-collapse:collapse;margin:1rem 0;font-size:.85rem}
th{background:#f1f5f9;color:#374151;font-weight:600;text-align:left;padding:.55rem;border-bottom:2px solid #e2e8f0;position:sticky;top:0}
td{padding:.55rem;border-bottom:1px solid #f1f5f9}
tr:hover{background:#f8fafc}
.number{text-align:right;font-variant-numeric:tabular-nums}
.positive{color:#059669}.negative{color:#dc2626}
.highlight-red{background:#fef2f2;color:#991b1b}
.highlight-green{background:#ecfdf5;color:#065f46}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.7rem;margin:1rem 0}
.kpi{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:.9rem;text-align:center}
.kpi-value{font-size:1.4rem;font-weight:700;color:#2563eb}
.kpi-label{font-size:.78rem;color:#64748b;margin-top:.15rem}
</style></head><body>${d.html}</body></html>`;
      document.getElementById('pv-frame').srcdoc = full;
    }
  } catch(e) {
    dot.className = 'pv-dot r'; pvSt.textContent = 'Connection error';
  }
}

// ── Save ──
async function doSave() {
  setSt('busy','Saving...');
  try {
    const r = await fetch('/api/save', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({content: ed.value}),
    });
    const d = await r.json();
    if (d.saved) { saved = ed.value; setSt('ok','Saved'); updMod(); }
    else { setSt('err', d.error || 'Failed'); }
  } catch(e) { setSt('err','Save failed'); }
}

// ── Format ──
function doFormat() {
  let v = ed.value;
  v = v.replace(/\n*(---\s+[\w:]+\s+---)\n*/g, '\n\n$1\n');
  v = v.trim() + '\n';
  ed.value = v;
  syncLines();
  doRender();
  setSt('ok','Formatted');
}

// ── Import ──
function doImport() {
  document.getElementById('file-input').click();
}

async function handleFileSelect(e) {
  const file = e.target.files[0];
  if (!file) return;
  setSt('busy', 'Importing ' + file.name + '...');

  const reader = new FileReader();
  reader.onload = async function(ev) {
    const b64 = btoa(new Uint8Array(ev.target.result).reduce((s, b) => s + String.fromCharCode(b), ''));
    try {
      const r = await fetch('/api/import', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ filename: file.name, data: b64 }),
      });
      const d = await r.json();
      if (d.error) {
        setSt('err', d.error);
      } else {
        ed.value = d.content;
        syncLines();
        doRender();
        setSt('ok', 'Imported ' + file.name);
      }
    } catch(err) {
      setSt('err', 'Import failed');
    }
  };
  reader.readAsArrayBuffer(file);
  e.target.value = ''; // Reset so same file can be re-imported
}

// ── Export ──
let exportMenuVisible = false;
function showExportMenu() {
  const menu = document.getElementById('export-menu');
  exportMenuVisible = !exportMenuVisible;
  menu.style.display = exportMenuVisible ? 'block' : 'none';
}
// Close menu on outside click
document.addEventListener('click', (e) => {
  if (!e.target.closest('#export-menu') && !e.target.closest('[onclick*="showExportMenu"]')) {
    document.getElementById('export-menu').style.display = 'none';
    exportMenuVisible = false;
  }
});

async function doExport(fmt) {
  document.getElementById('export-menu').style.display = 'none';
  exportMenuVisible = false;
  setSt('busy', 'Exporting as ' + fmt + '...');

  try {
    const r = await fetch('/api/export/' + fmt, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ content: ed.value }),
    });
    const d = await r.json();
    if (d.error) {
      setSt('err', d.error);
      return;
    }

    // Trigger download
    if (fmt === 'csv') {
      const blob = new Blob([d.data], { type: 'text/csv' });
      downloadBlob(blob, d.filename);
    } else {
      const bytes = Uint8Array.from(atob(d.data), c => c.charCodeAt(0));
      const blob = new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
      downloadBlob(blob, d.filename);
    }
    setSt('ok', 'Exported ' + d.filename);
  } catch(err) {
    setSt('err', 'Export failed');
  }
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── UI helpers ──
function setSt(t, msg) {
  const el = document.getElementById('status');
  el.className = 'st ' + t;
  el.textContent = msg;
  if (t !== 'err') setTimeout(() => { if(el.textContent===msg){el.className='st ok';el.textContent='Ready';} }, 2000);
}

function updMod() {
  const el = document.getElementById('fname');
  const base = el.textContent.replace(/ •$/, '');
  el.textContent = (ed.value !== saved) ? base + ' •' : base;
}

// ── Preview Tabs (Render / Data) ──
let currentPvTab = 'render';
let lastRawData = [];
let lastColumns = [];

function switchPvTab(tab) {
  currentPvTab = tab;
  document.querySelectorAll('.pv-tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`.pv-tab[onclick*="${tab}"]`).classList.add('active');
  document.getElementById('pv-frame').style.display = tab === 'render' ? 'block' : 'none';
  document.getElementById('pv-data').style.display = tab === 'data' ? 'block' : 'none';
  if (tab === 'data') buildDataTable();
}

function buildDataTable() {
  const container = document.getElementById('pv-data');
  if (!lastColumns.length) {
    container.innerHTML = '<div style="padding:2rem;color:#94a3b8;text-align:center;">No data</div>';
    return;
  }
  let html = '<table><thead><tr><th style="width:36px;text-align:center;background:#374151;">#</th>';
  lastColumns.forEach(c => { html += `<th>${c}</th>`; });
  html += '</tr></thead><tbody>';
  lastRawData.forEach((row, ri) => {
    html += `<tr><td style="text-align:center;color:#94a3b8;background:#f9fafb;font-size:11px;cursor:default;">${ri+1}</td>`;
    lastColumns.forEach(col => {
      const val = row[col] !== null && row[col] !== undefined ? row[col] : '';
      html += `<td contenteditable="true" data-row="${ri}" data-col="${col}" `
            + `onblur="onCellBlur(this)" onkeydown="onCellKey(event,this)">${val}</td>`;
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
}

function onCellKey(e, td) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    td.blur();
  }
  if (e.key === 'Tab') {
    e.preventDefault();
    const next = e.shiftKey ? td.previousElementSibling : td.nextElementSibling;
    if (next && next.contentEditable === 'true') next.focus();
  }
  if (e.key === 'Escape') {
    td.blur();
  }
}

async function onCellBlur(td) {
  const row = parseInt(td.dataset.row);
  const col = td.dataset.col;
  const newVal = td.textContent.trim();

  // Check if value changed
  const oldVal = lastRawData[row] ? String(lastRawData[row][col] ?? '') : '';
  if (newVal === oldVal) return;

  td.classList.add('edited');
  setSt('busy', 'Updating...');

  try {
    const r = await fetch('/api/cell-edit', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ content: ed.value, row, col, value: newVal }),
    });
    const d = await r.json();
    if (d.error) {
      setSt('err', d.error);
      return;
    }
    // Update source editor
    ed.value = d.content;
    syncLines();
    updMod();
    // Re-render preview
    doRender();
    setSt('ok', `Cell ${col}[${row+1}] updated`);
  } catch(err) {
    setSt('err', 'Cell update failed');
  }
}

// ── Resize ──
(function(){
  const h = document.getElementById('resizer');
  const p = document.getElementById('ed-pane');
  let on = false;
  h.addEventListener('mousedown', e => {
    on = true; h.classList.add('on');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('mousemove', e => {
    if(!on) return;
    const pct = Math.max(20, Math.min(80, e.clientX / window.innerWidth * 100));
    p.style.width = pct + '%';
  });
  document.addEventListener('mouseup', () => {
    if(!on) return;
    on = false; h.classList.remove('on');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
})();
</script>
</body>
</html>
"""


# =============================================================================
# Server Entry Point
# =============================================================================

def serve(grid_path: str | Path, port: int = 8080, edit: bool = False,
          allow_remote: bool = False):
    """
    Start a local HTTP server.

    Args:
        grid_path: Path to the .grid file.
        port: HTTP port number.
        edit: If True, serve the editor UI. If False, preview only.
        allow_remote: If True, http(s) @source URLs in data sections are fetched.
    """
    path = Path(grid_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    GridLangHandler.grid_path = path
    GridLangHandler.edit_mode = edit
    GridLangHandler.allow_remote = allow_remote

    server = HTTPServer(('127.0.0.1', port), GridLangHandler)

    mode = "Editor" if edit else "Preview"
    print(f"\n  {'─' * 44}")
    print(f"  GridLang {mode}")
    print(f"  {'─' * 44}")
    print(f"  File:      {path.name}")
    print(f"  URL:       http://localhost:{port}")
    if edit:
        print(f"  Preview:   http://localhost:{port}/preview")
    if allow_remote:
        print(f"  Remote sources: ENABLED (--allow-remote)")
    print(f"  {'─' * 44}")
    print(f"  Auto-reload: enabled")
    print(f"  Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped")
        server.shutdown()
