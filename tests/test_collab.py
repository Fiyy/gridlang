"""Tests for gridlang.collab and the server's /api/collab/* endpoints.

Covers:

* ``CollabSession`` — single-peer round-trip, multi-peer convergence,
  persistence to the on-disk .grid file, version-vector deltas.

* HTTP endpoints — join, op, poll, snapshot, stats; rejection when collab
  is off; rejection of unknown peers.

The HTTP tests run an in-process ``HTTPServer`` on a free port and use
``urllib`` to drive it — no external client needed.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

from gridlang.collab import (
    CollabSession, CollabError, get_session, reset_sessions,
    parse_poll_request, parse_op_request,
)
from gridlang.crdt import HLC, CellOp, CellKey
from gridlang.server import GridLangHandler


SAMPLE_GRID = """\
--- meta ---
name: "Collab Sample"
version: "1.0"

--- data ---
Region,Q1,Q2
North,100,110
South,90,95
West,60,75

--- compute ---

--- present ---
"""


# ─── CollabSession unit tests ──────────────────────────────────────────────

def _write_sample(tmp_path: Path) -> Path:
    p = tmp_path / 'sample.grid'
    p.write_text(SAMPLE_GRID, encoding='utf-8')
    return p


class TestCollabSessionBasic:

    def test_register_peer(self, tmp_path):
        sess = CollabSession(_write_sample(tmp_path))
        pid = sess.register_peer()
        assert isinstance(pid, str) and pid.startswith('peer-')
        assert sess.has_peer(pid)
        sess.drop_peer(pid)
        assert not sess.has_peer(pid)

    def test_submit_local_persists(self, tmp_path):
        path = _write_sample(tmp_path)
        sess = CollabSession(path)
        pid = sess.register_peer()
        op = sess.submit_local('B2', 999, peer_id=pid)
        assert op.value == 999
        # On-disk file was rewritten.
        new_text = path.read_text(encoding='utf-8')
        assert 'North,999,110' in new_text

    def test_submit_local_unknown_peer(self, tmp_path):
        sess = CollabSession(_write_sample(tmp_path))
        with pytest.raises(CollabError, match='unknown peer'):
            sess.submit_local('B2', 1, peer_id='nope')

    def test_submit_local_bad_cell(self, tmp_path):
        sess = CollabSession(_write_sample(tmp_path))
        pid = sess.register_peer()
        # Header row is read-only at the apply_edit layer.
        with pytest.raises(CollabError):
            sess.submit_local('B1', 'oops', peer_id=pid)

    def test_persist_failure_rolls_back(self, tmp_path):
        # Point the session at a path the apply_edit layer will reject
        # (header-row edit). The op must not survive the rollback.
        sess = CollabSession(_write_sample(tmp_path))
        pid = sess.register_peer()
        before = sess.stats()['cells']
        with pytest.raises(CollabError):
            sess.submit_local('A1', 'header', peer_id=pid)
        assert sess.stats()['cells'] == before
        assert sess.stats()['journal'] == 0


class TestCollabSessionSync:

    def test_two_peers_converge_via_session(self, tmp_path):
        sess = CollabSession(_write_sample(tmp_path))
        p1 = sess.register_peer()
        p2 = sess.register_peer()

        # p1 starts with empty version vector.
        snap = sess.snapshot(peer_id=p1)
        assert snap['ops'] == []
        v1 = snap['version']

        # p1 submits an edit.
        sess.submit_local('B2', 222, peer_id=p1)

        # p2 polls — should see the new op.
        result = sess.poll(peer_id=p2, since={})
        assert len(result['ops']) == 1
        assert result['ops'][0]['value'] == 222

        # After p2 records the version, polling again returns nothing.
        v2 = result['version']
        from gridlang.crdt import vv_deserialize
        result2 = sess.poll(peer_id=p2, since=vv_deserialize(v2))
        assert result2['ops'] == []

    def test_poll_unknown_peer(self, tmp_path):
        sess = CollabSession(_write_sample(tmp_path))
        with pytest.raises(CollabError, match='unknown peer'):
            sess.poll(peer_id='ghost', since={})

    def test_stats_tracks_state(self, tmp_path):
        sess = CollabSession(_write_sample(tmp_path))
        pid = sess.register_peer()
        sess.submit_local('B2', 1, peer_id=pid)
        sess.submit_local('B3', 2, peer_id=pid)
        s = sess.stats()
        assert s['cells'] == 2
        assert s['journal'] == 2
        assert s['peers'] == 1


class TestSessionRegistry:

    def test_get_session_singleton(self, tmp_path):
        reset_sessions()
        path = _write_sample(tmp_path)
        a = get_session(path)
        b = get_session(path)
        assert a is b

    def test_reset_sessions_clears(self, tmp_path):
        path = _write_sample(tmp_path)
        a = get_session(path)
        reset_sessions()
        b = get_session(path)
        assert a is not b


class TestWireParsers:

    def test_parse_poll_request(self):
        peer, vv = parse_poll_request({'peer_id': 'p1', 'since': {'A': [100, 0]}})
        assert peer == 'p1'
        assert vv == {'A': (100, 0)}

    def test_parse_poll_no_since(self):
        peer, vv = parse_poll_request({'peer_id': 'p1'})
        assert peer == 'p1' and vv == {}

    def test_parse_poll_missing_peer(self):
        with pytest.raises(CollabError):
            parse_poll_request({})

    def test_parse_op_request(self):
        peer, cell, value, sheet = parse_op_request({
            'peer_id': 'p1', 'cell': 'B2', 'value': 42, 'sheet': 'main',
        })
        assert (peer, cell, value, sheet) == ('p1', 'B2', 42, 'main')

    def test_parse_op_missing_cell(self):
        with pytest.raises(CollabError):
            parse_op_request({'peer_id': 'p1'})


# ─── HTTP integration tests ────────────────────────────────────────────────

class _ServerFixture:
    """Spin up an in-process HTTPServer for one test."""

    def __init__(self, grid_path: Path, *, collab: bool = True):
        from gridlang.collab import reset_sessions, get_session
        reset_sessions()

        # Explicitly reset all class-level state so prior tests can't leak
        # `collab_mode=True` into a test that wants the server to be
        # collab-disabled. macOS CI exposed this as a flaky failure where the
        # previous fixture's daemon thread held the class attrs longer than
        # `close()` was willing to wait.
        GridLangHandler.grid_path = grid_path
        GridLangHandler.edit_mode = False
        GridLangHandler.allow_remote = False
        GridLangHandler.collab_mode = collab
        GridLangHandler.collab_session = get_session(grid_path) if collab else None

        # Bind to port 0 → the OS assigns a free port.
        self.httpd = HTTPServer(('127.0.0.1', 0), GridLangHandler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str) -> str:
        return f'http://127.0.0.1:{self.port}{path}'

    def request(self, method: str, path: str, body: dict = None) -> tuple[int, dict]:
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.url(path), data=data, method=method, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            status = resp.status
            payload = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                payload = json.loads(e.read().decode('utf-8'))
            except Exception:
                payload = {}
        return status, payload

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        # macOS CI hit a flaky case where shutdown took >1s with a request
        # in flight, leaking class-level state into the next test. Give it
        # more time, and explicitly null out class attrs so a slow daemon
        # thread can't re-flip `collab_mode` after the next test starts.
        self._thread.join(timeout=5)
        GridLangHandler.collab_session = None
        GridLangHandler.collab_mode = False


class TestCollabHTTP:

    def setup_method(self):
        self._fixtures = []
        # Force-reset class-level state on every test so a flaky teardown
        # in a prior test (slow daemon thread, etc.) can't leave
        # `collab_mode=True` set when this test wants it off.
        GridLangHandler.collab_mode = False
        GridLangHandler.collab_session = None

    def teardown_method(self):
        for f in self._fixtures:
            f.close()
        GridLangHandler.collab_mode = False
        GridLangHandler.collab_session = None

    def _server(self, tmp_path, **kw):
        path = tmp_path / 'sample.grid'
        path.write_text(SAMPLE_GRID, encoding='utf-8')
        f = _ServerFixture(path, **kw)
        self._fixtures.append(f)
        return f, path

    def test_collab_disabled_returns_404(self, tmp_path):
        srv, _ = self._server(tmp_path, collab=False)
        status, body = srv.request('POST', '/api/collab/join', {})
        assert status == 404
        assert 'collab' in body.get('error', '').lower()

    def test_join_returns_peer_and_snapshot(self, tmp_path):
        srv, _ = self._server(tmp_path)
        status, body = srv.request('POST', '/api/collab/join', {})
        assert status == 200
        assert 'peer_id' in body
        assert 'site_id' in body
        assert body['ops'] == []
        assert body['version'] == {}

    def test_full_two_peer_round_trip(self, tmp_path):
        srv, path = self._server(tmp_path)

        # Two peers join.
        _, j1 = srv.request('POST', '/api/collab/join', {})
        _, j2 = srv.request('POST', '/api/collab/join', {})
        p1, p2 = j1['peer_id'], j2['peer_id']

        # p1 submits a cell edit.
        status, body = srv.request('POST', '/api/collab/op', {
            'peer_id': p1, 'cell': 'B2', 'value': 555,
        })
        assert status == 200
        assert body['op']['value'] == 555

        # On-disk file was updated.
        assert 'North,555,110' in path.read_text(encoding='utf-8')

        # p2 polls with empty version → sees the op.
        status, body = srv.request('POST', '/api/collab/poll', {
            'peer_id': p2, 'since': {},
        })
        assert status == 200
        assert len(body['ops']) == 1
        assert body['ops'][0]['cell'] == 'B2'
        assert body['ops'][0]['value'] == 555

        # p2 polls again with the new version → no ops.
        status, body2 = srv.request('POST', '/api/collab/poll', {
            'peer_id': p2, 'since': body['version'],
        })
        assert status == 200
        assert body2['ops'] == []

    def test_op_unknown_peer_400(self, tmp_path):
        srv, _ = self._server(tmp_path)
        status, body = srv.request('POST', '/api/collab/op', {
            'peer_id': 'ghost', 'cell': 'B2', 'value': 1,
        })
        assert status == 400
        assert 'unknown peer' in body['error']

    def test_op_bad_cell_400(self, tmp_path):
        srv, _ = self._server(tmp_path)
        _, j = srv.request('POST', '/api/collab/join', {})
        status, body = srv.request('POST', '/api/collab/op', {
            'peer_id': j['peer_id'], 'cell': 'B1', 'value': 1,
        })
        assert status == 400
        assert 'header' in body['error'].lower() or 'editable' in body['error'].lower()

    def test_snapshot_endpoint(self, tmp_path):
        srv, _ = self._server(tmp_path)
        _, j = srv.request('POST', '/api/collab/join', {})
        srv.request('POST', '/api/collab/op', {
            'peer_id': j['peer_id'], 'cell': 'B2', 'value': 7,
        })
        status, body = srv.request('GET', '/api/collab/snapshot')
        assert status == 200
        assert len(body['ops']) == 1

    def test_stats_endpoint(self, tmp_path):
        srv, _ = self._server(tmp_path)
        _, j = srv.request('POST', '/api/collab/join', {})
        srv.request('POST', '/api/collab/op', {
            'peer_id': j['peer_id'], 'cell': 'B2', 'value': 7,
        })
        status, body = srv.request('GET', '/api/collab/stats')
        assert status == 200
        assert body['cells'] == 1
        assert body['journal'] == 1
        assert body['peers'] == 1

    def test_leave_endpoint(self, tmp_path):
        srv, _ = self._server(tmp_path)
        _, j = srv.request('POST', '/api/collab/join', {})
        peer_id = j['peer_id']
        # Leave.
        status, body = srv.request('POST', '/api/collab/leave', {'peer_id': peer_id})
        assert status == 200
        # Subsequent op from same peer is rejected.
        status2, body2 = srv.request('POST', '/api/collab/op', {
            'peer_id': peer_id, 'cell': 'B2', 'value': 1,
        })
        assert status2 == 400

    def test_client_js_endpoint(self, tmp_path):
        srv, _ = self._server(tmp_path)
        # Use raw urllib to fetch as text since this isn't JSON.
        req = urllib.request.Request(srv.url('/api/collab/client.js'), method='GET')
        resp = urllib.request.urlopen(req, timeout=5)
        assert resp.status == 200
        body = resp.read().decode('utf-8')
        assert '__GRID_COLLAB_LOADED__' in body
        assert '/api/collab/poll' in body

    def test_three_peer_concurrent_writes_converge(self, tmp_path):
        # Three peers all hammer different cells; final state should reflect
        # all writes deterministically.
        srv, path = self._server(tmp_path)
        _, jA = srv.request('POST', '/api/collab/join', {})
        _, jB = srv.request('POST', '/api/collab/join', {})
        _, jC = srv.request('POST', '/api/collab/join', {})

        ops_per_peer = [
            (jA['peer_id'], 'B2', 'A2'),
            (jA['peer_id'], 'C2', 'A3'),
            (jB['peer_id'], 'B3', 'B2'),
            (jB['peer_id'], 'C3', 'B3'),
            (jC['peer_id'], 'B4', 'C2'),
            (jC['peer_id'], 'C4', 'C3'),
        ]
        for peer, cell, val in ops_per_peer:
            srv.request('POST', '/api/collab/op',
                        {'peer_id': peer, 'cell': cell, 'value': val})

        # Each peer polls the full state from scratch.
        for j in (jA, jB, jC):
            _, body = srv.request('POST', '/api/collab/poll',
                                  {'peer_id': j['peer_id'], 'since': {}})
            cells = {op['cell']: op['value'] for op in body['ops']}
            assert cells == {
                'B2': 'A2', 'C2': 'A3',
                'B3': 'B2', 'C3': 'B3',
                'B4': 'C2', 'C4': 'C3',
            }
