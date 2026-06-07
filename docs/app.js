/* GridLang Online Demo — full client-side via Pyodide.
 *
 * Architecture:
 *   1. Boot Pyodide, install pandas + jinja2 + openpyxl + xlsxwriter + pyyaml.
 *   2. Fetch every gridlang/*.py file (manifest.json lists them) and write
 *      them into Pyodide's virtual filesystem under /home/pyodide/gridlang/.
 *   3. Wire up render/import/export by calling into Python.
 *
 * The whole pipeline runs in the browser tab — no backend, no API calls
 * except the one-time CDN download of Pyodide + the static .py files.
 */

(function () {
  'use strict';

  // ─── DOM refs ────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const ed = $('editor');
  const lines = $('lines');
  const errbox = $('errbox');
  const dot = $('dot');
  const pvSt = $('pv-st');
  const pvFrame = $('pv-frame');
  const exampleSelect = $('example-select');

  // ─── State ───────────────────────────────────────────────────────────
  const state = {
    pyodide: null,
    ready: false,
    saved: '',
    timer: null,
    currentTab: 'render',
    lastColumns: [],
    lastRawData: [],
    examples: [],   // [{name, path}]
    filename: 'untitled.grid',
  };

  // ─── Loading helpers ─────────────────────────────────────────────────
  const lp = $('loading-progress');
  const lstep = $('loading-step');
  const lerr = $('loading-err');
  function setLoadingStep(pct, msg) {
    lp.value = Math.max(lp.value, pct);
    lstep.textContent = msg;
  }
  function showLoadingError(msg) {
    lerr.style.display = 'block';
    lerr.textContent = msg;
    lstep.textContent = '⚠ load failed';
  }
  function hideLoading() {
    $('loading').classList.add('hidden');
  }

  // ─── Editor wiring ───────────────────────────────────────────────────
  ed.addEventListener('input', () => {
    syncLines();
    clearTimeout(state.timer);
    state.timer = setTimeout(doRender, 500);
    updMod();
  });
  ed.addEventListener('scroll', () => { lines.scrollTop = ed.scrollTop; });
  ed.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const s = ed.selectionStart, en = ed.selectionEnd;
      ed.value = ed.value.substring(0, s) + '    ' + ed.value.substring(en);
      ed.selectionStart = ed.selectionEnd = s + 4;
      syncLines();
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      downloadGrid();
    }
  });

  function syncLines() {
    const n = ed.value.split('\n').length;
    const arr = [];
    for (let i = 1; i <= n; i++) arr.push(i);
    lines.textContent = arr.join('\n');
  }

  // ─── Status helpers ──────────────────────────────────────────────────
  function setSt(t, msg) {
    const el = $('status');
    el.className = 'st ' + t;
    el.textContent = msg;
    if (t !== 'err') {
      setTimeout(() => {
        if (el.textContent === msg) {
          el.className = 'st ok';
          el.textContent = 'Ready';
        }
      }, 2000);
    }
  }

  function updMod() {
    const meta = $('ed-meta');
    const mod = (ed.value !== state.saved) ? ' • modified' : '';
    const lc = ed.value.split('\n').length;
    meta.textContent = `${state.filename} · ${lc} lines${mod}`;
  }

  // ─── Pyodide bootstrap ───────────────────────────────────────────────
  async function boot() {
    try {
      setLoadingStep(5, 'Loading Pyodide runtime…');
      state.pyodide = await loadPyodide({
        indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.26.2/full/',
      });

      setLoadingStep(25, 'Installing pandas, jinja2, …');
      await state.pyodide.loadPackage([
        'pandas', 'numpy', 'micropip',
      ]);

      setLoadingStep(45, 'Installing openpyxl + jinja2 + pyyaml…');
      await state.pyodide.runPythonAsync(`
import micropip
await micropip.install([
    'jinja2', 'pyyaml', 'openpyxl', 'xlsxwriter',
])
`);

      setLoadingStep(65, 'Loading GridLang source…');
      // Fetch the manifest of files we need to ship into the FS.
      const manifest = await fetch('manifest.json').then(r => r.json());
      state.examples = manifest.examples || [];

      // Populate examples dropdown.
      for (const ex of state.examples) {
        const opt = document.createElement('option');
        opt.value = ex.path;
        opt.textContent = ex.name;
        exampleSelect.appendChild(opt);
      }

      // Mirror the gridlang package into Pyodide's virtual FS.
      await state.pyodide.runPythonAsync(`
import os, pathlib
pathlib.Path('/home/pyodide/gridlang').mkdir(parents=True, exist_ok=True)
pathlib.Path('/home/pyodide/gridlang/js').mkdir(parents=True, exist_ok=True)
`);

      let i = 0;
      const total = manifest.gridlang_files.length;
      for (const rel of manifest.gridlang_files) {
        i++;
        if (i % 4 === 0 || i === total) {
          setLoadingStep(65 + Math.round(20 * i / total),
            `Loading gridlang/${rel} (${i}/${total})`);
        }
        const text = await fetch(`gridlang/${rel}`).then(r => r.text());
        // Write the file into Pyodide's FS.
        state.pyodide.FS.writeFile(`/home/pyodide/gridlang/${rel}`, text);
      }

      setLoadingStep(90, 'Starting GridLang…');
      await state.pyodide.runPythonAsync(`
import sys
sys.path.insert(0, '/home/pyodide')
import gridlang
print('GridLang', gridlang.__version__, 'loaded')
`);

      // Read the version back into the UI.
      const version = await state.pyodide.runPythonAsync('gridlang.__version__');
      $('version').textContent = `v${version}`;

      // Define the helper functions we'll call from JS.
      await state.pyodide.runPythonAsync(_PY_HELPERS);

      setLoadingStep(100, '✓ ready');
      state.ready = true;

      // Load the first example by default.
      if (state.examples.length > 0) {
        await loadExample(state.examples[0].path);
      }

      hideLoading();
      setSt('ok', 'Ready');
    } catch (e) {
      console.error(e);
      showLoadingError('Failed to initialize:\n' + (e.message || e));
    }
  }

  // ─── Python helpers (loaded once into Pyodide) ───────────────────────
  const _PY_HELPERS = `
import json, base64, io, traceback
import pandas as pd
from gridlang.parser import parse_string, ParseError
from gridlang.schema import parse_data
from gridlang.runtime import execute
from gridlang.renderer import render
from gridlang.bindings import apply_edit, BindingError

def render_grid(content):
    """Parse + execute + render a .grid string. Returns dict for JS."""
    try:
        doc = parse_string(content)

        # Data layer (no remote fetching in browser — inline only).
        if doc.is_multi_sheet:
            sheets = {name: parse_data(raw) for name, raw in doc.sheets_raw.items()}
            primary_df = list(sheets.values())[0]
            sheets_for_compute = sheets
        else:
            primary_df = parse_data(doc.data_raw) if doc.data_raw.strip() else pd.DataFrame()
            sheets_for_compute = None

        # Run compute.
        result = execute(
            doc.compute_raw, primary_df,
            sheets=sheets_for_compute, engine=doc.engine,
        )

        # Render HTML.
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

        # Surface raw data + columns for the Data tab.
        cols = list(primary_df.columns) if not primary_df.empty else []
        rows = []
        for _, r in primary_df.iterrows():
            rows.append({c: (None if pd.isna(r[c]) else r[c]) for c in cols})

        return json.dumps({
            'ok': True,
            'html': html,
            'columns': cols,
            'rows': rows,
            'name': doc.name,
            'engine': doc.engine,
            'is_multi_sheet': doc.is_multi_sheet,
            'sheet_names': doc.sheet_names,
        }, default=str)
    except (ParseError, ValueError, KeyError) as e:
        return json.dumps({
            'ok': False, 'error': f'{type(e).__name__}: {e}',
        })
    except Exception as e:
        return json.dumps({
            'ok': False,
            'error': f'{type(e).__name__}: {e}',
            'traceback': traceback.format_exc(),
        })


def import_file(filename, b64data):
    """Convert an uploaded .xlsx/.csv/.grid into .grid source."""
    try:
        suffix = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
        if suffix == 'grid':
            content = base64.b64decode(b64data).decode('utf-8')
            return json.dumps({'ok': True, 'content': content, 'filename': filename})

        # Decode + write to a temp file in Pyodide's FS.
        data = base64.b64decode(b64data)
        import pathlib, tempfile
        tmp_dir = pathlib.Path('/tmp/gridlang_uploads')
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / filename
        tmp_path.write_bytes(data)

        if suffix == 'csv':
            from gridlang.csv_io import import_csv
            content = import_csv(tmp_path)
        elif suffix in ('xlsx', 'xls'):
            from gridlang.excel_import import import_excel
            content = import_excel(tmp_path)
        else:
            return json.dumps({'ok': False, 'error': f'Unsupported format: .{suffix}'})

        return json.dumps({'ok': True, 'content': content, 'filename': filename})
    except Exception as e:
        return json.dumps({'ok': False, 'error': f'{type(e).__name__}: {e}'})


def export_to_csv(content):
    """Render compute layer → CSV string."""
    try:
        doc = parse_string(content)
        if doc.is_multi_sheet:
            sheets = {n: parse_data(r) for n, r in doc.sheets_raw.items()}
            primary_df = list(sheets.values())[0]
            result = execute(doc.compute_raw, primary_df, sheets=sheets, engine=doc.engine)
        else:
            primary_df = parse_data(doc.data_raw)
            result = execute(doc.compute_raw, primary_df, engine=doc.engine)

        return json.dumps({'ok': True, 'data': result.df.to_csv(index=False)})
    except Exception as e:
        return json.dumps({'ok': False, 'error': f'{type(e).__name__}: {e}'})


def export_to_xlsx(content):
    """Render compute layer → xlsx bytes (returned as base64)."""
    try:
        # Write temp .grid + use excel_export.
        import pathlib
        tmp_grid = pathlib.Path('/tmp/_export.grid')
        tmp_xlsx = pathlib.Path('/tmp/_export.xlsx')
        tmp_grid.write_text(content, encoding='utf-8')

        from gridlang.excel_export import export_excel
        export_excel(tmp_grid, tmp_xlsx)
        b64 = base64.b64encode(tmp_xlsx.read_bytes()).decode('ascii')
        return json.dumps({'ok': True, 'data': b64})
    except Exception as e:
        return json.dumps({'ok': False, 'error': f'{type(e).__name__}: {e}'})


def edit_cell(content, cell, value, sheet=None):
    """Apply a single-cell edit to .grid source."""
    try:
        new_content = apply_edit(content, cell=cell, value=value, sheet=sheet)
        return json.dumps({'ok': True, 'content': new_content})
    except BindingError as e:
        return json.dumps({'ok': False, 'error': str(e)})
    except Exception as e:
        return json.dumps({'ok': False, 'error': f'{type(e).__name__}: {e}'})


def edit_cell_legacy(content, row, col, value, sheet=None):
    """Edit a cell by (row_index, column_name) — used by the Data tab."""
    try:
        doc = parse_string(content)
        if sheet and sheet in doc.sheets_raw:
            raw_csv = doc.sheets_raw[sheet]
        else:
            raw_csv = doc.data_raw

        csv_lines = raw_csv.strip().split('\\n')
        if len(csv_lines) < 2:
            raise ValueError('No data rows')
        headers = [h.strip() for h in csv_lines[0].split(',')]
        if col not in headers:
            raise ValueError(f'Column {col!r} not found')
        col_idx = headers.index(col)
        data_row_idx = row + 1
        if data_row_idx >= len(csv_lines):
            raise ValueError(f'Row {row} out of range')
        parts = csv_lines[data_row_idx].split(',')
        while len(parts) <= col_idx:
            parts.append('')
        parts[col_idx] = str(value)
        csv_lines[data_row_idx] = ','.join(parts)
        new_csv = '\\n'.join(csv_lines)

        # Replace the data section in the original content.
        import re
        target = f'data:{sheet}' if (sheet and sheet != 'default') else 'data'
        lines = content.split('\\n')
        sec_start = None
        sec_end = None
        for i, ln in enumerate(lines):
            stripped = ln.strip()
            if re.match(r'^---\\s+' + re.escape(target) + r'\\s+---$', stripped) or \\
               (target == 'data' and re.match(r'^---\\s+data\\s+---$', stripped)):
                sec_start = i + 1
            elif sec_start is not None and re.match(r'^---\\s+\\w', stripped):
                sec_end = i; break
        if sec_start is None:
            raise ValueError('data section not found')
        if sec_end is None:
            sec_end = len(lines)
        new_lines = lines[:sec_start] + [new_csv, ''] + lines[sec_end:]
        return json.dumps({'ok': True, 'content': '\\n'.join(new_lines)})
    except Exception as e:
        return json.dumps({'ok': False, 'error': f'{type(e).__name__}: {e}'})
`;

  // ─── JS-side wrappers around the Python helpers ──────────────────────
  function pyCall(fn, ...args) {
    if (!state.ready) throw new Error('runtime not ready');
    const result = state.pyodide.globals.get(fn)(...args);
    const json = result.toString();
    if (result.destroy) result.destroy();
    return JSON.parse(json);
  }

  // ─── Render pipeline ─────────────────────────────────────────────────
  async function doRender() {
    if (!state.ready) return;
    dot.className = 'pv-dot y';
    pvSt.textContent = 'Rendering…';
    try {
      const result = pyCall('render_grid', ed.value);
      if (!result.ok) {
        dot.className = 'pv-dot r';
        pvSt.textContent = 'Error';
        errbox.textContent = result.error +
          (result.traceback ? '\n\n' + result.traceback : '');
        errbox.classList.add('vis');
        return;
      }
      dot.className = 'pv-dot g';
      pvSt.textContent = `Preview · ${result.name}` +
        (result.is_multi_sheet ? ` (${result.sheet_names.length} sheets)` : '');
      errbox.classList.remove('vis');

      state.lastColumns = result.columns || [];
      state.lastRawData = result.rows || [];
      if (state.currentTab === 'data') buildDataTable();

      const full = `<!DOCTYPE html><html><head><meta charset="UTF-8"><base target="_blank">
<style>
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  line-height:1.6;color:#1a1a1a;max-width:980px;margin:0 auto;padding:1.2rem}
h1,h2,h3{color:#111827}
table{width:100%;border-collapse:collapse;margin:1rem 0;font-size:.85rem}
th{background:#f1f5f9;color:#374151;font-weight:600;text-align:left;padding:.55rem;
   border-bottom:2px solid #e2e8f0;position:sticky;top:0}
td{padding:.55rem;border-bottom:1px solid #f1f5f9}
tr:hover{background:#f8fafc}
.number{text-align:right;font-variant-numeric:tabular-nums}
.positive{color:#059669}.negative{color:#dc2626}
.highlight-red{background:#fef2f2;color:#991b1b}
.highlight-green{background:#ecfdf5;color:#065f46}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
   gap:.7rem;margin:1rem 0}
.kpi{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:.9rem;
   text-align:center}
.kpi-value{font-size:1.4rem;font-weight:700;color:#2563eb}
.kpi-label{font-size:.78rem;color:#64748b;margin-top:.15rem}
.grid-cell{padding:1px 4px;border-radius:3px}
.grid-cell[contenteditable="true"]{outline:1px dashed #cbd5e1;cursor:text;
   min-width:1.5rem;display:inline-block}
.grid-cell[contenteditable="true"]:hover{background:#fef9c3;outline-color:#f59e0b}
.grid-cell[contenteditable="true"]:focus{background:#fef3c7;
   outline:2px solid #f59e0b;outline-offset:-1px}
.grid-bind{display:inline-flex;flex-direction:column;gap:.25rem;
   margin:.5rem .75rem .5rem 0}
.grid-bind-label{font-size:.8rem;color:#475569;font-weight:500}
.grid-bind-input{padding:.35rem .55rem;border:1px solid #cbd5e1;border-radius:6px;
   font:inherit;font-size:.9rem;min-width:9rem}
</style></head><body>${result.html}<script>
// Cell-edit hooks: post a message back to the parent window.
document.querySelectorAll('[data-grid-cell][contenteditable="true"]').forEach(el => {
  let original = el.textContent;
  el.addEventListener('focus', () => { original = el.textContent; });
  el.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); el.blur(); }
    if (e.key === 'Escape') { el.textContent = original; el.blur(); }
  });
  el.addEventListener('blur', () => {
    const v = el.textContent.trim();
    if (v !== original) {
      window.parent.postMessage({type: 'grid-cell-edit', cell: el.dataset.gridCell, value: v}, '*');
    }
  });
});
document.querySelectorAll('[data-grid-bind]').forEach(el => {
  el.addEventListener('change', () => {
    const v = (el.type === 'checkbox') ? (el.checked ? 'true' : 'false') : el.value;
    window.parent.postMessage({type: 'grid-cell-edit', cell: el.dataset.gridBind, value: v}, '*');
  });
});
</` + `script></body></html>`;
      pvFrame.srcdoc = full;
    } catch (e) {
      console.error(e);
      dot.className = 'pv-dot r';
      pvSt.textContent = 'Error';
      errbox.textContent = String(e);
      errbox.classList.add('vis');
    }
  }

  // ─── Cell edit (from preview iframe) ─────────────────────────────────
  window.addEventListener('message', (e) => {
    if (!e.data || e.data.type !== 'grid-cell-edit') return;
    const { cell, value } = e.data;
    try {
      const result = pyCall('edit_cell', ed.value, cell, value, null);
      if (!result.ok) {
        setSt('err', result.error);
        return;
      }
      ed.value = result.content;
      syncLines();
      updMod();
      setSt('ok', `${cell} updated`);
      doRender();
    } catch (err) {
      setSt('err', String(err));
    }
  });

  // ─── Examples ────────────────────────────────────────────────────────
  exampleSelect.addEventListener('change', async () => {
    if (!exampleSelect.value) return;
    await loadExample(exampleSelect.value);
  });

  async function loadExample(path) {
    try {
      setSt('busy', 'Loading…');
      const text = await fetch(path).then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      });
      ed.value = text;
      state.saved = text;
      state.filename = path.split('/').pop();
      syncLines();
      updMod();
      setSt('ok', 'Loaded');
      doRender();
    } catch (e) {
      setSt('err', 'Load failed: ' + e.message);
    }
  }

  // ─── Import ──────────────────────────────────────────────────────────
  window.doImport = function () { $('file-input').click(); };

  window.handleFile = async function (e) {
    const file = e.target.files[0];
    if (!file) return;
    setSt('busy', 'Importing ' + file.name + '…');

    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const b64 = btoa(
          new Uint8Array(ev.target.result)
            .reduce((s, b) => s + String.fromCharCode(b), '')
        );
        const result = pyCall('import_file', file.name, b64);
        if (!result.ok) {
          setSt('err', result.error);
          return;
        }
        ed.value = result.content;
        state.saved = result.content;
        state.filename = file.name.replace(/\.(xlsx|xls|csv)$/i, '.grid');
        syncLines();
        updMod();
        setSt('ok', 'Imported ' + file.name);
        doRender();
      } catch (err) {
        setSt('err', 'Import failed: ' + err.message);
      }
    };
    reader.readAsArrayBuffer(file);
    e.target.value = '';
  };

  // ─── Export ──────────────────────────────────────────────────────────
  window.showExportMenu = function (e) {
    const menu = $('export-menu');
    if (menu.classList.contains('vis')) {
      menu.classList.remove('vis');
      return;
    }
    const r = e.target.getBoundingClientRect();
    menu.style.top = (r.bottom + 4) + 'px';
    menu.style.left = r.left + 'px';
    menu.classList.add('vis');
  };
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#export-menu') &&
        !e.target.closest('[onclick*="showExportMenu"]')) {
      $('export-menu').classList.remove('vis');
    }
  });

  window.doExport = function (fmt) {
    $('export-menu').classList.remove('vis');
    setSt('busy', 'Exporting as ' + fmt + '…');
    try {
      if (fmt === 'csv') {
        const result = pyCall('export_to_csv', ed.value);
        if (!result.ok) { setSt('err', result.error); return; }
        downloadBlob(
          new Blob([result.data], { type: 'text/csv' }),
          state.filename.replace(/\.grid$/, '.csv')
        );
      } else {
        const result = pyCall('export_to_xlsx', ed.value);
        if (!result.ok) { setSt('err', result.error); return; }
        const bytes = Uint8Array.from(atob(result.data), c => c.charCodeAt(0));
        downloadBlob(
          new Blob([bytes], {
            type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          }),
          state.filename.replace(/\.grid$/, '.xlsx')
        );
      }
      setSt('ok', 'Exported');
    } catch (err) {
      setSt('err', 'Export failed: ' + err.message);
    }
  };

  window.downloadGrid = function () {
    downloadBlob(new Blob([ed.value], { type: 'text/plain' }), state.filename);
    state.saved = ed.value;
    updMod();
    setSt('ok', 'Downloaded');
  };

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // ─── Format ──────────────────────────────────────────────────────────
  window.doFormat = function () {
    let v = ed.value;
    v = v.replace(/\n*(---\s+[\w:]+\s+---)\n*/g, '\n\n$1\n');
    v = v.trim() + '\n';
    ed.value = v;
    syncLines();
    updMod();
    doRender();
    setSt('ok', 'Formatted');
  };

  // ─── Preview tabs ────────────────────────────────────────────────────
  window.switchPvTab = function (tab) {
    state.currentTab = tab;
    document.querySelectorAll('.pv-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.tab === tab);
    });
    pvFrame.style.display = tab === 'render' ? 'block' : 'none';
    $('pv-data').style.display = tab === 'data' ? 'block' : 'none';
    if (tab === 'data') buildDataTable();
  };

  function buildDataTable() {
    const container = $('pv-data');
    if (!state.lastColumns.length) {
      container.innerHTML = '<div style="padding:2rem;color:#94a3b8;text-align:center">No data</div>';
      return;
    }
    let html = '<table><thead><tr><th class="row-num">#</th>';
    state.lastColumns.forEach(c => { html += `<th>${escapeHtml(c)}</th>`; });
    html += '</tr></thead><tbody>';
    state.lastRawData.forEach((row, ri) => {
      html += `<tr><td class="row-num">${ri + 1}</td>`;
      state.lastColumns.forEach(col => {
        const v = (row[col] !== null && row[col] !== undefined) ? row[col] : '';
        html += `<td contenteditable="true" data-row="${ri}" data-col="${escapeHtml(col)}"
                     onblur="onCellBlur(this)" onkeydown="onCellKey(event,this)">${escapeHtml(v)}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    container.innerHTML = html;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  window.onCellKey = function (e, td) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); td.blur(); }
    if (e.key === 'Escape') { td.blur(); }
    if (e.key === 'Tab') {
      e.preventDefault();
      const nxt = e.shiftKey ? td.previousElementSibling : td.nextElementSibling;
      if (nxt && nxt.contentEditable === 'true') nxt.focus();
    }
  };

  window.onCellBlur = async function (td) {
    const row = parseInt(td.dataset.row);
    const col = td.dataset.col;
    const newVal = td.textContent.trim();
    const oldVal = state.lastRawData[row]
      ? String(state.lastRawData[row][col] ?? '') : '';
    if (newVal === oldVal) return;
    td.classList.add('edited');
    setSt('busy', 'Updating…');
    try {
      const result = pyCall('edit_cell_legacy', ed.value, row, col, newVal, null);
      if (!result.ok) { setSt('err', result.error); return; }
      ed.value = result.content;
      syncLines();
      updMod();
      doRender();
      setSt('ok', `${col}[${row + 1}] updated`);
    } catch (err) {
      setSt('err', 'Update failed: ' + err.message);
    }
  };

  // ─── Resize handle ───────────────────────────────────────────────────
  (function () {
    const h = $('resizer');
    const p = $('ed-pane');
    let on = false;
    h.addEventListener('mousedown', (e) => {
      on = true; h.classList.add('on');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!on) return;
      const pct = Math.max(20, Math.min(80, e.clientX / window.innerWidth * 100));
      p.style.width = pct + '%';
    });
    document.addEventListener('mouseup', () => {
      if (!on) return;
      on = false; h.classList.remove('on');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    });
  })();

  // ─── Boot ────────────────────────────────────────────────────────────
  boot();
})();
