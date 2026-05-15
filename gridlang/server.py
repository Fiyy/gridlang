"""
GridLang Server — Local HTTP server for live preview of .grid files.

Usage:
  gridlang serve file.grid [--port 8080]

Features:
- Renders .grid file and serves the HTML
- Auto-reloads when the .grid file changes
- Injects live-reload script for browser auto-refresh
"""

from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Optional

from gridlang.parser import parse_file
from gridlang.schema import parse_data
from gridlang.runtime import execute
from gridlang.renderer import render


class GridLangHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves rendered .grid files."""

    grid_path: Path = None
    _last_html: str = ""
    _last_mtime: float = 0

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self._serve_grid()
        elif self.path == '/poll':
            self._serve_poll()
        elif self.path == '/raw':
            self._serve_raw()
        else:
            self.send_error(404)

    def _serve_grid(self):
        """Render and serve the .grid file."""
        try:
            html = self._render_grid()
            # Inject auto-reload script
            html = html.replace('</body>', _RELOAD_SCRIPT + '</body>')

            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
        except Exception as e:
            self._serve_error(str(e))

    def _serve_poll(self):
        """Long-poll endpoint — returns when file changes."""
        try:
            mtime = os.path.getmtime(self.grid_path)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(f'{{"mtime": {mtime}}}'.encode())
        except Exception as e:
            self.send_response(500)
            self.end_headers()

    def _serve_raw(self):
        """Serve raw .grid file content."""
        try:
            content = self.grid_path.read_text(encoding='utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))
        except Exception as e:
            self._serve_error(str(e))

    def _serve_error(self, message: str):
        """Serve an error page."""
        html = f"""<!DOCTYPE html>
<html><head><title>GridLang Error</title>
<style>body {{ font-family: monospace; padding: 2rem; }}
.error {{ background: #fef2f2; border: 1px solid #fecaca; padding: 1.5rem;
          border-radius: 8px; color: #991b1b; white-space: pre-wrap; }}</style>
</head><body>
<h2>⚠ GridLang Render Error</h2>
<div class="error">{message}</div>
{_RELOAD_SCRIPT}
</body></html>"""
        self.send_response(500)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def _render_grid(self) -> str:
        """Execute the full GridLang pipeline."""
        doc = parse_file(self.grid_path)

        # Parse data
        if doc.is_multi_sheet:
            sheets = {name: parse_data(raw) for name, raw in doc.sheets_raw.items()}
            primary_df = list(sheets.values())[0]
        else:
            primary_df = parse_data(doc.data_raw)
            sheets = None

        # Execute compute
        result = execute(doc.compute_raw, primary_df, sheets=sheets)

        # Render HTML
        html = render(
            template_content=doc.present_raw,
            df=result.df,
            aggregates=result.aggregates,
            meta=doc.meta,
            raw_df=primary_df,
            sheets=result.sheets if result.is_multi_sheet else None,
            conditional_formats=result.conditional_formats,
            standalone=True,
        )

        return html

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass


# Auto-reload JavaScript (polls for file changes)
_RELOAD_SCRIPT = """
<script>
(function() {
  let lastMtime = 0;
  async function poll() {
    try {
      const resp = await fetch('/poll');
      const data = await resp.json();
      if (lastMtime && data.mtime !== lastMtime) {
        location.reload();
      }
      lastMtime = data.mtime;
    } catch(e) {}
    setTimeout(poll, 1000);
  }
  poll();
})();
</script>
"""


def serve(grid_path: str | Path, port: int = 8080, open_browser: bool = True):
    """
    Start a local HTTP server serving a rendered .grid file.

    Args:
        grid_path: Path to the .grid file to serve.
        port: HTTP port number.
        open_browser: Attempt to open browser automatically.
    """
    path = Path(grid_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    GridLangHandler.grid_path = path

    server = HTTPServer(('127.0.0.1', port), GridLangHandler)

    print(f"\n  🌐 GridLang Preview Server")
    print(f"  {'─' * 40}")
    print(f"  File:    {path.name}")
    print(f"  URL:     http://localhost:{port}")
    print(f"  Raw:     http://localhost:{port}/raw")
    print(f"  {'─' * 40}")
    print(f"  Auto-reload: enabled (watching file changes)")
    print(f"  Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  ✓ Server stopped")
        server.shutdown()
