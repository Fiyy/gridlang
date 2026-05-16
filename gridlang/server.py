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
import traceback
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Optional

from gridlang.parser import parse_string, parse_file, ParseError
from gridlang.schema import parse_data
from gridlang.runtime import execute
from gridlang.renderer import render


class GridLangHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves rendered .grid files and editor UI."""

    grid_path: Path = None
    edit_mode: bool = False

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
            html = html.replace('</body>', _RELOAD_SCRIPT + '</body>')
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
        """POST /api/render — render .grid content from request body, return HTML."""
        try:
            body = self._read_body()
            data = json.loads(body)
            content = data.get('content', '')
            html = self._render_from_string(content)
            self._send_json(200, {'html': html, 'error': None})
        except Exception as e:
            self._send_json(200, {'html': None, 'error': str(e)})

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
        if doc.is_multi_sheet:
            sheets = {name: parse_data(raw) for name, raw in doc.sheets_raw.items()}
            primary_df = list(sheets.values())[0]
        else:
            primary_df = parse_data(doc.data_raw)
            sheets = None

        result = execute(doc.compute_raw, primary_df, sheets=sheets)

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
# Editor HTML — Full single-page editor with Monaco + live preview
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
    --bg: #0f172a; --bg-panel: #1e293b; --border: #334155;
    --text: #e2e8f0; --text-dim: #94a3b8; --accent: #3b82f6;
    --accent-hover: #2563eb; --success: #10b981; --error: #ef4444;
    --toolbar-h: 44px;
  }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); height: 100vh; overflow: hidden; }

  /* Toolbar */
  .toolbar {
    height: var(--toolbar-h); background: var(--bg-panel); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; padding: 0 16px; gap: 12px; z-index: 100;
  }
  .toolbar .logo { font-weight: 700; font-size: 14px; color: var(--accent); letter-spacing: -0.5px; }
  .toolbar .filename { font-size: 13px; color: var(--text-dim); }
  .toolbar .spacer { flex: 1; }
  .toolbar button {
    padding: 5px 14px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); font-size: 12px; cursor: pointer;
    transition: all 0.15s;
  }
  .toolbar button:hover { background: var(--border); }
  .toolbar button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .toolbar button.primary:hover { background: var(--accent-hover); }
  .toolbar .status { font-size: 11px; padding: 3px 10px; border-radius: 4px; }
  .toolbar .status.ok { background: #064e3b; color: #6ee7b7; }
  .toolbar .status.err { background: #7f1d1d; color: #fca5a5; }
  .toolbar .status.saving { background: #1e3a5f; color: #93c5fd; }

  /* Main layout */
  .main { display: flex; height: calc(100vh - var(--toolbar-h)); }

  /* Editor pane */
  .editor-pane {
    width: 50%; min-width: 300px; display: flex; flex-direction: column;
    border-right: 2px solid var(--border);
  }
  .editor-tabs {
    height: 32px; background: var(--bg); display: flex; align-items: center;
    border-bottom: 1px solid var(--border); padding: 0 8px; gap: 4px;
  }
  .editor-tab {
    padding: 4px 12px; font-size: 11px; border-radius: 4px 4px 0 0; cursor: pointer;
    color: var(--text-dim); border: 1px solid transparent; border-bottom: none;
  }
  .editor-tab.active { background: var(--bg-panel); color: var(--text); border-color: var(--border); }
  #editor-container { flex: 1; }

  /* Preview pane */
  .preview-pane { flex: 1; display: flex; flex-direction: column; background: #fff; }
  .preview-header {
    height: 32px; background: #f8fafc; border-bottom: 1px solid #e2e8f0;
    display: flex; align-items: center; padding: 0 12px; gap: 8px;
  }
  .preview-header span { font-size: 11px; color: #64748b; }
  .preview-header .dot { width: 8px; height: 8px; border-radius: 50%; }
  .preview-header .dot.green { background: #10b981; }
  .preview-header .dot.red { background: #ef4444; }
  .preview-header .dot.yellow { background: #f59e0b; }
  #preview-frame { flex: 1; border: none; width: 100%; }

  /* Error overlay */
  .error-panel {
    display: none; background: #1c1917; color: #fca5a5; padding: 12px 16px;
    font-family: monospace; font-size: 12px; max-height: 120px; overflow-y: auto;
    border-top: 2px solid var(--error);
  }
  .error-panel.visible { display: block; }

  /* Resize handle */
  .resize-handle {
    width: 4px; cursor: col-resize; background: transparent; transition: background 0.15s;
    position: relative; z-index: 10;
  }
  .resize-handle:hover, .resize-handle.dragging { background: var(--accent); }
</style>
</head>
<body>

<!-- Toolbar -->
<div class="toolbar">
  <span class="logo">GridLang</span>
  <span class="filename" id="filename">loading...</span>
  <div class="spacer"></div>
  <span class="status ok" id="status">Ready</span>
  <button onclick="formatCode()">Format</button>
  <button class="primary" onclick="saveFile()">Save</button>
</div>

<!-- Main -->
<div class="main">
  <!-- Editor -->
  <div class="editor-pane" id="editor-pane">
    <div class="editor-tabs">
      <div class="editor-tab active">Source</div>
    </div>
    <div id="editor-container"></div>
    <div class="error-panel" id="error-panel"></div>
  </div>

  <!-- Resize handle -->
  <div class="resize-handle" id="resize-handle"></div>

  <!-- Preview -->
  <div class="preview-pane">
    <div class="preview-header">
      <div class="dot green" id="preview-dot"></div>
      <span id="preview-status">Preview</span>
    </div>
    <iframe id="preview-frame" sandbox="allow-scripts allow-same-origin"></iframe>
  </div>
</div>

<!-- Monaco Editor (from CDN) -->
<script src="https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs/loader.js"></script>
<script>
// ==========================================================================
// State
// ==========================================================================
let editor = null;
let renderTimeout = null;
let lastSavedContent = '';
const RENDER_DELAY = 600; // ms debounce

// ==========================================================================
// Initialize Monaco Editor
// ==========================================================================
require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs' } });

require(['vs/editor/editor.main'], function () {
  // Register .grid language
  monaco.languages.register({ id: 'gridlang' });
  monaco.languages.setMonarchTokensProvider('gridlang', {
    tokenizer: {
      root: [
        [/^---\s+\w+[\w:]*\s+---\s*$/, 'keyword'],           // section delimiters
        [/^(name|engine|version|description|author|tags|schema|dependencies)\s*:/, 'type'],  // meta keys
        [/"[^"]*"/, 'string'],
        [/'[^']*'/, 'string'],
        [/\b(def|return|if|elif|else|for|in|import|from|as|not|and|or|True|False|None|pass|lambda)\b/, 'keyword'],
        [/\b(transform|aggregates|validate|conditional_formats)\b/, 'type.identifier'],
        [/\b(SUMIF|COUNTIF|VLOOKUP|XLOOKUP|HLOOKUP|PIVOT|GROUPBY|SORT|FILTER|IF|IFS|SWITCH|ROUND|ABS|LEFT|RIGHT|MID|UPPER|LOWER|TRIM|YEAR|MONTH|DAY|CONCATENATE|RANK|PERCENTILE|MEDIAN|STDEV|UNIQUE|TRANSPOSE|IFERROR|AVERAGEIF|SUMIFS|COUNTIFS|INDEX|MATCH|TODAY|NOW|LEN|SUBSTITUTE|PROPER|TEXT|DATEDIF|NETWORKDAYS|EDATE|WEEKDAY|MOD|POWER|CEILING|FLOOR|ROUNDUP|ROUNDDOWN|AND|OR|NOT|SMALL|LARGE|QUARTILE|VAR)\b/, 'support.function'],
        [/\b(pd|np|df|sheets|agg|meta|raw_df)\b/, 'variable'],
        [/#.*$/, 'comment'],
        [/\b\d+\.?\d*\b/, 'number'],
        [/<\/?[\w-]+/, 'tag'],                                   // HTML tags
        [/\{\{.*?\}\}/, 'string.escape'],                        // Jinja2 expressions
        [/\{%.*?%\}/, 'keyword.control'],                        // Jinja2 blocks
      ]
    }
  });

  // Define editor theme
  monaco.editor.defineTheme('gridlang-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: 'keyword', foreground: 'c084fc' },
      { token: 'type', foreground: '38bdf8' },
      { token: 'type.identifier', foreground: '22d3ee' },
      { token: 'support.function', foreground: 'fbbf24' },
      { token: 'string', foreground: '86efac' },
      { token: 'string.escape', foreground: 'fb923c' },
      { token: 'keyword.control', foreground: 'f472b6' },
      { token: 'comment', foreground: '64748b' },
      { token: 'number', foreground: 'f97316' },
      { token: 'variable', foreground: '67e8f9' },
      { token: 'tag', foreground: '818cf8' },
    ],
    colors: {
      'editor.background': '#0f172a',
      'editor.lineHighlightBackground': '#1e293b',
      'editorGutter.background': '#0f172a',
      'editorLineNumber.foreground': '#475569',
    }
  });

  // Create editor
  editor = monaco.editor.create(document.getElementById('editor-container'), {
    language: 'gridlang',
    theme: 'gridlang-dark',
    fontSize: 13,
    lineHeight: 20,
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    padding: { top: 8, bottom: 8 },
    automaticLayout: true,
    tabSize: 4,
    wordWrap: 'on',
    renderLineHighlight: 'line',
    smoothScrolling: true,
    cursorSmoothCaretAnimation: 'on',
    bracketPairColorization: { enabled: true },
  });

  // Live preview on content change
  editor.onDidChangeModelContent(() => {
    clearTimeout(renderTimeout);
    renderTimeout = setTimeout(renderPreview, RENDER_DELAY);
    updateModifiedState();
  });

  // Ctrl+S to save
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveFile);

  // Load file content
  loadFile();
});

// ==========================================================================
// API calls
// ==========================================================================

async function loadFile() {
  try {
    const resp = await fetch('/api/source');
    const data = await resp.json();
    document.getElementById('filename').textContent = data.filename;
    lastSavedContent = data.content;
    if (editor) {
      editor.setValue(data.content);
      // Initial render
      setTimeout(renderPreview, 200);
    }
  } catch (e) {
    setStatus('err', 'Load failed');
  }
}

async function renderPreview() {
  const content = editor.getValue();
  const dot = document.getElementById('preview-dot');
  const previewStatus = document.getElementById('preview-status');
  const errorPanel = document.getElementById('error-panel');

  dot.className = 'dot yellow';
  previewStatus.textContent = 'Rendering...';

  try {
    const resp = await fetch('/api/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    const data = await resp.json();

    if (data.error) {
      dot.className = 'dot red';
      previewStatus.textContent = 'Error';
      errorPanel.textContent = data.error;
      errorPanel.classList.add('visible');
    } else {
      dot.className = 'dot green';
      previewStatus.textContent = 'Preview';
      errorPanel.classList.remove('visible');

      // Wrap in full HTML with base styles
      const fullHtml = `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       line-height: 1.6; color: #1a1a1a; max-width: 1000px; margin: 0 auto; padding: 1.5rem; }
h1, h2, h3 { color: #111827; }
table { width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.85rem; }
th { background: #f1f5f9; color: #374151; font-weight: 600; text-align: left;
     padding: 0.6rem; border-bottom: 2px solid #e2e8f0; position: sticky; top: 0; }
td { padding: 0.6rem; border-bottom: 1px solid #f1f5f9; }
tr:hover { background: #f8fafc; }
.number { text-align: right; font-variant-numeric: tabular-nums; }
.positive { color: #059669; } .negative { color: #dc2626; }
.highlight-red { background-color: #fef2f2; color: #991b1b; }
.highlight-green { background-color: #ecfdf5; color: #065f46; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.8rem; margin: 1rem 0; }
.kpi { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 1rem; text-align: center; }
.kpi-value { font-size: 1.5rem; font-weight: 700; color: #2563eb; }
.kpi-label { font-size: 0.8rem; color: #64748b; margin-top: 0.2rem; }
</style></head><body>${data.html}</body></html>`;

      const frame = document.getElementById('preview-frame');
      frame.srcdoc = fullHtml;
    }
  } catch (e) {
    dot.className = 'dot red';
    previewStatus.textContent = 'Connection error';
  }
}

async function saveFile() {
  const content = editor.getValue();
  setStatus('saving', 'Saving...');

  try {
    const resp = await fetch('/api/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    const data = await resp.json();

    if (data.saved) {
      lastSavedContent = content;
      setStatus('ok', 'Saved');
      updateModifiedState();
    } else {
      setStatus('err', data.error || 'Save failed');
    }
  } catch (e) {
    setStatus('err', 'Save failed');
  }
}

function formatCode() {
  if (!editor) return;
  // Simple auto-format: ensure sections have blank lines around them
  let content = editor.getValue();
  content = content.replace(/\n*(---\s+\w[\w:]*\s+---)\n*/g, '\n\n$1\n');
  content = content.trim() + '\n';
  editor.setValue(content);
  setStatus('ok', 'Formatted');
}

// ==========================================================================
// UI Helpers
// ==========================================================================

function setStatus(type, text) {
  const el = document.getElementById('status');
  el.className = 'status ' + type;
  el.textContent = text;
  if (type === 'ok' || type === 'saving') {
    setTimeout(() => {
      if (el.textContent === text) {
        el.className = 'status ok';
        el.textContent = 'Ready';
      }
    }, 2000);
  }
}

function updateModifiedState() {
  const filename = document.getElementById('filename');
  const isModified = editor && editor.getValue() !== lastSavedContent;
  const baseName = filename.textContent.replace(/ •$/, '');
  filename.textContent = isModified ? baseName + ' •' : baseName;
}

// ==========================================================================
// Resize Handle
// ==========================================================================
(function () {
  const handle = document.getElementById('resize-handle');
  const editorPane = document.getElementById('editor-pane');
  let isDragging = false;

  handle.addEventListener('mousedown', (e) => {
    isDragging = true;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });

  document.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    const pct = (e.clientX / window.innerWidth) * 100;
    const clamped = Math.max(20, Math.min(80, pct));
    editorPane.style.width = clamped + '%';
  });

  document.addEventListener('mouseup', () => {
    if (!isDragging) return;
    isDragging = false;
    handle.classList.remove('dragging');
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

def serve(grid_path: str | Path, port: int = 8080, edit: bool = False):
    """
    Start a local HTTP server.

    Args:
        grid_path: Path to the .grid file.
        port: HTTP port number.
        edit: If True, serve the editor UI. If False, preview only.
    """
    path = Path(grid_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    GridLangHandler.grid_path = path
    GridLangHandler.edit_mode = edit

    server = HTTPServer(('127.0.0.1', port), GridLangHandler)

    mode = "Editor" if edit else "Preview"
    print(f"\n  {'─' * 44}")
    print(f"  GridLang {mode}")
    print(f"  {'─' * 44}")
    print(f"  File:      {path.name}")
    print(f"  URL:       http://localhost:{port}")
    if edit:
        print(f"  Preview:   http://localhost:{port}/preview")
    print(f"  {'─' * 44}")
    print(f"  Auto-reload: enabled")
    print(f"  Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped")
        server.shutdown()
