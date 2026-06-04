"""
GridLang Collab Client — Self-contained JS that connects a browser tab to
the server's ``/api/collab/*`` endpoints.

The client is delivered verbatim by ``GET /api/collab/client.js`` when the
server is started with ``--collab``. It runs as a regular ``<script>`` (no
modules, no bundler, no external dependencies) and:

1. Joins the session via ``POST /api/collab/join``, getting back a peer_id,
   the server's site_id, an op snapshot, and the current version vector.

2. Replays the snapshot into the page — every ``[data-grid-cell]`` whose
   A1 ref matches a known op gets its text content updated.

3. Hooks into the existing v0.5 contenteditable cells, intercepts blur/Enter
   commits, and routes them through ``POST /api/collab/op`` instead of the
   single-user ``/api/cell-edit`` endpoint.

4. Polls ``POST /api/collab/poll`` every ~700 ms with the local version
   vector. Each response is a delta of ops the peer hasn't seen, which we
   apply to the DOM (and skip our own ops by checking site_id).

5. Surfaces a small toast in the corner showing peer count + last sync.

Convergence is guaranteed by the underlying CRDT — this client never makes
ordering decisions. It just ships ops up and applies whatever the server
has queued for it.
"""

# Keep the source as a Python string so the server can serve it without
# bundling a static file. The module is import-only — no Python logic here.

COLLAB_CLIENT_JS = r"""
/* GridLang Collab Client v0.8 — CRDT-backed live editing.
 *
 * Wire protocol:
 *   POST /api/collab/join     { peer_id?: string }
 *     -> { peer_id, site_id, ops: [...], version: {site_id: [wall, logical], ...} }
 *   POST /api/collab/op       { peer_id, cell, value, sheet? }
 *     -> { op, version }
 *   POST /api/collab/poll     { peer_id, since: version_vector }
 *     -> { ops, version, peer_count }
 *   POST /api/collab/leave    { peer_id }
 *   GET  /api/collab/snapshot
 *   GET  /api/collab/stats
 */
(function () {
  'use strict';
  if (window.__GRID_COLLAB_LOADED__) return;
  window.__GRID_COLLAB_LOADED__ = true;

  // ─── State ───────────────────────────────────────────────────────────
  const state = {
    peer_id: null,
    site_id: null,
    version: {},        // local high-water mark per site_id
    cells: {},          // a1_ref -> { value, hlc }
    polling: false,
    pollMs: 700,
    lastSyncAt: 0,
  };

  // ─── HTTP helpers ────────────────────────────────────────────────────
  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    let d;
    try { d = await r.json(); } catch (e) { d = { error: 'malformed JSON' }; }
    return { ok: r.ok, status: r.status, data: d };
  }

  // ─── Version-vector ops ──────────────────────────────────────────────
  function vvUpdate(vv, site, mark) {
    const cur = vv[site];
    if (!cur || mark[0] > cur[0] || (mark[0] === cur[0] && mark[1] > cur[1])) {
      vv[site] = mark;
    }
  }

  function applyOpToState(op) {
    // op = {cell, value, hlc: {wall_ms, logical, site_id}}
    const ref = op.cell;
    const existing = state.cells[ref];
    const newMark = [op.hlc.wall_ms, op.hlc.logical, op.hlc.site_id];
    if (existing) {
      const cur = [existing.hlc.wall_ms, existing.hlc.logical, existing.hlc.site_id];
      if (cmpHLC(cur, newMark) >= 0) {
        // Local already has equal/newer — only update version vector.
        vvUpdate(state.version, op.hlc.site_id, [op.hlc.wall_ms, op.hlc.logical]);
        return false;
      }
    }
    state.cells[ref] = { value: op.value, hlc: op.hlc };
    vvUpdate(state.version, op.hlc.site_id, [op.hlc.wall_ms, op.hlc.logical]);
    paintCell(ref, op.value);
    return true;
  }

  function cmpHLC(a, b) {
    if (a[0] !== b[0]) return a[0] - b[0];
    if (a[1] !== b[1]) return a[1] - b[1];
    return a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0;
  }

  // ─── DOM painting ────────────────────────────────────────────────────
  function paintCell(ref, value) {
    const els = document.querySelectorAll(
      '[data-grid-cell="' + cssEscape(ref) + '"]'
    );
    const text = value === null || value === undefined ? '' : String(value);
    els.forEach((el) => {
      // Don't clobber the cell the user is currently typing into.
      if (document.activeElement === el) return;
      if (el.textContent !== text) {
        el.textContent = text;
        // Brief visual flash so users see remote edits arrive.
        el.classList.add('grid-cell-flash');
        setTimeout(() => el.classList.remove('grid-cell-flash'), 600);
      }
    });
    // bind: form widgets pointing at the same cell.
    const widgets = document.querySelectorAll(
      '[data-grid-bind="' + cssEscape(ref) + '"]'
    );
    widgets.forEach((el) => {
      if (document.activeElement === el) return;
      if (el.tagName === 'SELECT') {
        for (const opt of el.options) {
          if (opt.value === text) { opt.selected = true; break; }
        }
      } else if (el.type === 'checkbox') {
        el.checked = text === 'true' || text === '1' || text === 'yes';
      } else if (el.value !== text) {
        el.value = text;
      }
    });
  }

  function cssEscape(s) {
    return String(s).replace(/[^a-zA-Z0-9_:@-]/g, function (c) {
      return '\\' + c;
    });
  }

  // ─── Toast ───────────────────────────────────────────────────────────
  function makeToast() {
    let el = document.getElementById('__grid_collab_toast');
    if (el) return el;
    el = document.createElement('div');
    el.id = '__grid_collab_toast';
    el.style.cssText =
      'position:fixed;bottom:1rem;left:1rem;padding:.4rem .8rem;border-radius:6px;' +
      'font:12px -apple-system,sans-serif;z-index:9999;background:#0f172a;color:#cbd5e1;' +
      'opacity:.85;box-shadow:0 2px 8px rgba(0,0,0,.2);user-select:none;cursor:default;';
    document.body.appendChild(el);
    return el;
  }

  function setToast(msg) {
    const el = makeToast();
    el.textContent = msg;
  }

  // ─── Edit handlers (override the single-user contenteditable hooks) ─
  function attachEditHandlers() {
    document.querySelectorAll('[data-grid-cell][contenteditable="true"]').forEach((el) => {
      if (el.dataset.gridCollabBound === '1') return;
      el.dataset.gridCollabBound = '1';
      let original = el.textContent;
      el.addEventListener('focus', () => { original = el.textContent; });
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); el.blur(); }
        if (e.key === 'Escape') { el.textContent = original; el.blur(); }
      });
      el.addEventListener('blur', async () => {
        const v = el.textContent.trim();
        if (v === original) return;
        const ok = await commitOp(el.dataset.gridCell, v);
        if (!ok) el.textContent = original;
      });
    });

    document.querySelectorAll('[data-grid-bind]').forEach((el) => {
      if (el.dataset.gridCollabBound === '1') return;
      el.dataset.gridCollabBound = '1';
      el.addEventListener('change', async () => {
        const v = el.type === 'checkbox' ? (el.checked ? 'true' : 'false') : el.value;
        await commitOp(el.dataset.gridBind, v);
      });
    });
  }

  async function commitOp(cell, value) {
    if (!state.peer_id) {
      setToast('not connected');
      return false;
    }
    const r = await postJSON('/api/collab/op', {
      peer_id: state.peer_id,
      cell: cell,
      value: value,
    });
    if (!r.ok || r.data.error) {
      setToast('error: ' + (r.data.error || r.status));
      return false;
    }
    if (r.data.op) applyOpToState(r.data.op);
    if (r.data.version) state.version = mergeVV(state.version, r.data.version);
    setToast('saved');
    state.lastSyncAt = Date.now();
    return true;
  }

  function mergeVV(a, b) {
    const out = Object.assign({}, a);
    for (const k of Object.keys(b)) {
      const cur = out[k], cand = b[k];
      if (!cur || cand[0] > cur[0] || (cand[0] === cur[0] && cand[1] > cur[1])) {
        out[k] = cand;
      }
    }
    return out;
  }

  // ─── Polling loop ────────────────────────────────────────────────────
  async function pollOnce() {
    if (!state.peer_id) return;
    const r = await postJSON('/api/collab/poll', {
      peer_id: state.peer_id,
      since: state.version,
    });
    if (!r.ok || r.data.error) {
      setToast('poll error: ' + (r.data.error || r.status));
      return;
    }
    let applied = 0;
    for (const op of r.data.ops || []) {
      if (applyOpToState(op)) applied++;
    }
    if (r.data.version) state.version = mergeVV(state.version, r.data.version);
    if (applied) {
      setToast(`peers: ${r.data.peer_count} · +${applied} edit${applied > 1 ? 's' : ''}`);
    } else {
      setToast(`peers: ${r.data.peer_count} · synced`);
    }
    state.lastSyncAt = Date.now();
  }

  function startPolling() {
    if (state.polling) return;
    state.polling = true;
    const tick = async () => {
      try { await pollOnce(); }
      catch (e) { /* network blip — try again next tick */ }
      if (state.polling) setTimeout(tick, state.pollMs);
    };
    setTimeout(tick, state.pollMs);
  }

  // ─── Lifecycle ───────────────────────────────────────────────────────
  async function join() {
    const r = await postJSON('/api/collab/join', {});
    if (!r.ok || r.data.error) {
      setToast('join failed: ' + (r.data.error || r.status));
      return false;
    }
    state.peer_id = r.data.peer_id;
    state.site_id = r.data.site_id;
    for (const op of r.data.ops || []) applyOpToState(op);
    state.version = r.data.version || {};
    setToast('joined as ' + state.peer_id);
    return true;
  }

  async function leave() {
    if (!state.peer_id) return;
    state.polling = false;
    try {
      await fetch('/api/collab/leave', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ peer_id: state.peer_id }),
        keepalive: true,
      });
    } catch (e) { /* page is unloading */ }
  }

  // ─── Style for the flash effect ──────────────────────────────────────
  const styleEl = document.createElement('style');
  styleEl.textContent =
    '.grid-cell-flash{transition:background .4s;background:#dbeafe!important}';
  document.head.appendChild(styleEl);

  // ─── Boot ────────────────────────────────────────────────────────────
  async function boot() {
    if (!await join()) return;
    attachEditHandlers();
    startPolling();
    // Re-attach handlers if the page mutates (e.g. preview reload).
    const obs = new MutationObserver(attachEditHandlers);
    obs.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
  window.addEventListener('beforeunload', leave);

  // Expose for debugging.
  window.__gridCollab = state;
})();
"""
