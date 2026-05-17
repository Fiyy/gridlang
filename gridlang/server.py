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
  .ed-wrap { flex: 1; display: flex; overflow: hidden; position: relative; }
  .ed-lines {
    width: 44px; background: var(--bg); color: #475569; font: 13px/20px 'SF Mono', 'Fira Code', 'Consolas', monospace;
    text-align: right; padding: 10px 6px 10px 0; overflow: hidden; user-select: none;
    border-right: 1px solid var(--border);
  }
  .ed-input {
    flex: 1; background: var(--bg); color: var(--text); border: none; outline: none; resize: none;
    font: 13px/20px 'SF Mono', 'Fira Code', 'Consolas', monospace;
    padding: 10px 12px; tab-size: 4; white-space: pre; overflow: auto;
  }
  .ed-input::selection { background: rgba(59,130,246,0.3); }

  /* Syntax-highlighted overlay */
  .ed-highlight {
    position: absolute; top: 0; left: 45px; right: 0; bottom: 0;
    pointer-events: none; overflow: hidden;
    font: 13px/20px 'SF Mono', 'Fira Code', 'Consolas', monospace;
    padding: 10px 12px; white-space: pre; tab-size: 4; color: transparent;
  }
  .ed-highlight .sec { color: #c084fc; font-weight: 700; }
  .ed-highlight .kw { color: #c084fc; }
  .ed-highlight .fn { color: #22d3ee; }
  .ed-highlight .xl { color: #fbbf24; }
  .ed-highlight .str { color: #86efac; }
  .ed-highlight .num { color: #f97316; }
  .ed-highlight .cmt { color: #64748b; font-style: italic; }
  .ed-highlight .tag { color: #818cf8; }
  .ed-highlight .j2e { color: #fb923c; }
  .ed-highlight .j2b { color: #f472b6; }
  .ed-highlight .var { color: #67e8f9; }
  .ed-highlight .meta-key { color: #38bdf8; }

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
  #pv-frame { flex: 1; border: none; width: 100%; }

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
  <button onclick="doFormat()">Format</button>
  <button class="pri" onclick="doSave()">Save</button>
</div>

<!-- Main -->
<div class="main">
  <!-- Editor -->
  <div class="ed-pane" id="ed-pane">
    <div class="ed-tabs"><div class="ed-tab active">Source</div></div>
    <div class="ed-wrap">
      <div class="ed-lines" id="lines">1</div>
      <div class="ed-highlight" id="highlight"></div>
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
    </div>
    <iframe id="pv-frame" sandbox="allow-scripts"></iframe>
  </div>
</div>

<div class="hint"><kbd>Ctrl</kbd>+<kbd>S</kbd> save &nbsp; Edits auto-preview in 600ms</div>

<script>
// State
const ed = document.getElementById('editor');
const hl = document.getElementById('highlight');
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
  syncAll();
  doRender();
});

// ── Editor events ──
ed.addEventListener('input', () => {
  syncAll();
  clearTimeout(timer);
  timer = setTimeout(doRender, 600);
  updMod();
});
ed.addEventListener('scroll', syncScroll);
ed.addEventListener('keydown', handleTab);

function syncAll() { syncLines(); syncHighlight(); syncScroll(); }

function syncScroll() {
  hl.scrollTop = ed.scrollTop;
  hl.scrollLeft = ed.scrollLeft;
  lines.scrollTop = ed.scrollTop;
}

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
    syncAll();
  }
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    doSave();
  }
}

// ── Syntax highlighting ──
function syncHighlight() {
  const text = ed.value;
  let html = escHtml(text);

  // Section delimiters
  html = html.replace(/^(---\s+[\w:]+\s+---)$/gm, '<span class="sec">$1</span>');

  // Meta keys
  html = html.replace(/^((?:name|engine|version|description|author|tags|dependencies|schema|import_date|imported_from|sheets)\s*:)/gm, '<span class="meta-key">$1</span>');

  // Python keywords
  html = html.replace(/\b(def|return|if|elif|else|for|in|import|from|as|not|and|or|True|False|None|pass|lambda|class|try|except|raise|with|yield|while|break|continue)\b/g, '<span class="kw">$1</span>');

  // GridLang function names
  html = html.replace(/\b(transform|aggregates|validate|conditional_formats)\b/g, '<span class="fn">$1</span>');

  // Excel formula names
  html = html.replace(/\b(SUMIF|COUNTIF|VLOOKUP|XLOOKUP|HLOOKUP|PIVOT|GROUPBY|SORT|FILTER|IF|IFS|SWITCH|ROUND|ABS|LEFT|RIGHT|MID|UPPER|LOWER|TRIM|YEAR|MONTH|DAY|CONCATENATE|RANK|PERCENTILE|MEDIAN|STDEV|UNIQUE|TRANSPOSE|IFERROR|AVERAGEIF|SUMIFS|COUNTIFS|INDEX|MATCH|TODAY|NOW|LEN|SUBSTITUTE|PROPER|TEXT|DATEDIF|NETWORKDAYS|MOD|POWER|CEILING|FLOOR|ROUNDUP|ROUNDDOWN|AND|OR|NOT|SMALL|LARGE|QUARTILE|VAR|EDATE|WEEKDAY)\b/g, '<span class="xl">$1</span>');

  // Built-in variables
  html = html.replace(/\b(pd|np|df|sheets|agg|meta|raw_df)\b/g, '<span class="var">$1</span>');

  // Strings (simple — double and single quoted)
  html = html.replace(/(&quot;[^&]*?&quot;|&#x27;[^&]*?&#x27;)/g, '<span class="str">$1</span>');

  // Numbers
  html = html.replace(/\b(\d+\.?\d*)\b/g, '<span class="num">$1</span>');

  // Comments
  html = html.replace(/(#[^\n]*)/g, '<span class="cmt">$1</span>');

  // Jinja2
  html = html.replace(/(\{\{.*?\}\})/g, '<span class="j2e">$1</span>');
  html = html.replace(/(\{%.*?%\})/g, '<span class="j2b">$1</span>');

  // HTML tags
  html = html.replace(/(&lt;\/?[\w-]+)/g, '<span class="tag">$1</span>');

  hl.innerHTML = html + '\n';
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#x27;');
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
  syncAll();
  doRender();
  setSt('ok','Formatted');
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
