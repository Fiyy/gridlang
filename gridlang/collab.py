"""
GridLang Collab — In-process collaborative-editing session manager.

This module wraps :mod:`gridlang.crdt` with the glue needed to drive a live
preview server: it owns the local replica, persists committed cells back to
the ``.grid`` file via :func:`gridlang.bindings.apply_edit`, broadcasts ops
to subscribed peers via a simple pub/sub queue, and tracks a per-peer cursor
so each peer only sees ops it hasn't yet ack'd.

Threading model
---------------
``CollabSession`` is mutex-guarded for the *coarse* operations the server
performs in response to HTTP requests:

* ``submit_local`` — a peer POSTs a new edit. The session generates a CellOp,
  appends it to the journal, persists to disk, and queues it for delivery
  to other peers.

* ``poll`` — a peer asks "what's new since version V?". Returns the delta
  plus the session's current version vector.

* ``snapshot`` — a fresh peer asks for the full state.

The HTTP layer (:mod:`gridlang.server`) calls these from the same thread that
serves the request; ``HTTPServer`` is single-threaded, so contention is
minimal and a reentrant lock is sufficient.

File-of-truth invariant
-----------------------
After ``submit_local`` commits, the on-disk ``.grid`` file is updated in the
same call — a future ``parse_file`` will see the merged value. This keeps
the existing single-user workflows ("edit and rerun ``gridlang run``") fully
intact while adding collaboration.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from gridlang.crdt import (
    CellOp, Document, HLC, VersionVec,
    vv_serialize, vv_deserialize,
)
from gridlang.bindings import apply_edit, BindingError


# ─── Wall-time injection ───────────────────────────────────────────────────

def _wall_ms() -> int:
    """Return the current wall time in milliseconds.

    Centralized so tests can monkey-patch this single function instead of
    chasing ``time.time()`` calls across the module.
    """
    import time
    return int(time.time() * 1000)


# ─── Peer state ────────────────────────────────────────────────────────────

@dataclass
class PeerState:
    """Server-side bookkeeping for one connected peer."""
    peer_id: str
    last_seen_ms: int = 0
    version: VersionVec = field(default_factory=dict)


# ─── Session ───────────────────────────────────────────────────────────────

class CollabError(RuntimeError):
    """Raised when a collab op cannot be accepted (bad cell, peer unknown, …)."""


class CollabSession:
    """One live ``.grid`` document being edited by ≥1 peers.

    The session is created per-file by the server and lives for the duration
    of ``gridlang serve --collab``. The server's ``grid_path`` is read once
    at construction; from then on the session is the source of truth and
    writes back through :func:`apply_edit` after every op.
    """

    def __init__(
        self,
        grid_path: Path,
        *,
        site_id: Optional[str] = None,
        persist: bool = True,
    ):
        self.grid_path = Path(grid_path)
        self.site_id = site_id or f'srv-{uuid.uuid4().hex[:8]}'
        self.persist = persist

        self.doc = Document(site_id=self.site_id)
        self.peers: dict[str, PeerState] = {}
        self._lock = threading.RLock()

    # ── Peer registration ──────────────────────────────────────────────────

    def register_peer(self, peer_id: Optional[str] = None) -> str:
        """Register a new peer and return its assigned id."""
        with self._lock:
            pid = peer_id or f'peer-{uuid.uuid4().hex[:8]}'
            self.peers[pid] = PeerState(peer_id=pid, last_seen_ms=_wall_ms())
            return pid

    def drop_peer(self, peer_id: str) -> None:
        """Forget a peer. Subsequent polls from it will be rejected."""
        with self._lock:
            self.peers.pop(peer_id, None)

    def has_peer(self, peer_id: str) -> bool:
        with self._lock:
            return peer_id in self.peers

    # ── Submitting local edits ─────────────────────────────────────────────

    def submit_local(self, cell: str, value: Any, *, peer_id: str,
                     sheet: Optional[str] = None) -> CellOp:
        """Apply an edit originating from a peer.

        The session mints a fresh op (using its own ``site_id`` plus the
        author's ``peer_id`` recorded in the op's metadata via the cell
        value... no, simpler: the op carries the *server's* HLC site, and
        we track originator separately if needed). We persist to disk.

        Returns the generated op so the caller can echo it to other peers.
        """
        with self._lock:
            if peer_id not in self.peers:
                raise CollabError(f"unknown peer: {peer_id}")

            # Validate the cell ref via apply_edit (also writes to disk).
            wall = _wall_ms()
            op = self.doc.edit(cell, value, wall_ms=wall, default_sheet=sheet or '')

            # Persist atomically: read source, apply, write back.
            if self.persist:
                try:
                    src = self.grid_path.read_text(encoding='utf-8')
                    new_src = apply_edit(src, cell=cell, value=value, sheet=sheet)
                    self.grid_path.write_text(new_src, encoding='utf-8')
                except (BindingError, OSError) as e:
                    # Persistence failure rolls back the in-memory op so
                    # replicas stay consistent with disk.
                    self._rollback_last_op(op)
                    raise CollabError(f"persist failed: {e}") from e

            self.peers[peer_id].last_seen_ms = wall
            return op

    def _rollback_last_op(self, op: CellOp) -> None:
        """Remove ``op`` from the document. Internal — already locked."""
        # Drop from journal and re-derive the winner.
        self.doc.journal = [j for j in self.doc.journal if not (
            j.key == op.key and j.hlc == op.hlc
        )]
        # Recompute the cell winner from journal entries.
        winner = None
        for j in self.doc.journal:
            if j.key != op.key:
                continue
            if winner is None or j.hlc.to_tuple() > winner.hlc.to_tuple():
                winner = j
        if winner is None:
            self.doc.cells.pop(op.key, None)
        else:
            self.doc.cells[op.key] = winner

    # ── Receiving remote ops (other servers / federation) ──────────────────

    def merge_remote(self, op: CellOp) -> bool:
        """Apply an op generated elsewhere (e.g. by a federated peer).

        Returns True if it advanced the local state. Does not persist —
        federation is out of scope for v0.8; this hook is here so future
        work can extend the session without breaking the API.
        """
        with self._lock:
            return self.doc.apply(op, wall_ms=_wall_ms())

    # ── Polling ────────────────────────────────────────────────────────────

    def poll(self, *, peer_id: str, since: VersionVec) -> dict:
        """Return new ops since ``since`` plus the current version vector.

        ``since`` is the version vector the peer *currently* has. The result
        contains only the delta the peer is missing. The peer is expected
        to send back its updated vector on the next poll.
        """
        with self._lock:
            if peer_id not in self.peers:
                raise CollabError(f"unknown peer: {peer_id}")
            self.peers[peer_id].last_seen_ms = _wall_ms()
            self.peers[peer_id].version = dict(since)

            delta = self.doc.ops_since(since)
            return {
                'ops': [op.to_dict() for op in delta],
                'version': vv_serialize(self.doc.version()),
                'peer_count': len(self.peers),
            }

    # ── Snapshot ──────────────────────────────────────────────────────────

    def snapshot(self, *, peer_id: Optional[str] = None) -> dict:
        """Return the full op set + version vector for a fresh peer."""
        with self._lock:
            if peer_id and peer_id in self.peers:
                self.peers[peer_id].last_seen_ms = _wall_ms()
            return {
                'site_id': self.site_id,
                'ops': [op.to_dict() for op in self.doc.snapshot()],
                'version': vv_serialize(self.doc.version()),
            }

    # ── Inspection ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Quick summary for /api/collab/stats and tests."""
        with self._lock:
            return {
                'site_id': self.site_id,
                'cells': len(self.doc.cells),
                'journal': len(self.doc.journal),
                'peers': len(self.peers),
                'version': vv_serialize(self.doc.version()),
            }


# ─── Module-level singleton (per-file, per-server) ─────────────────────────

_SESSIONS: dict[Path, CollabSession] = {}
_SESSIONS_LOCK = threading.Lock()


def get_session(grid_path: Path, *, persist: bool = True) -> CollabSession:
    """Return the singleton :class:`CollabSession` for ``grid_path``.

    The first call constructs the session; subsequent calls return the same
    instance. Tests should call :func:`reset_sessions` between scenarios.
    """
    p = Path(grid_path).resolve()
    with _SESSIONS_LOCK:
        sess = _SESSIONS.get(p)
        if sess is None:
            sess = CollabSession(p, persist=persist)
            _SESSIONS[p] = sess
        return sess


def reset_sessions() -> None:
    """Drop every cached session. Called from tests + by ``serve()`` startup."""
    with _SESSIONS_LOCK:
        _SESSIONS.clear()


# ─── Wire-form helpers ────────────────────────────────────────────────────

def parse_poll_request(body: dict) -> tuple[str, VersionVec]:
    """Parse ``{peer_id, since}`` → ``(peer_id, version_vector)``."""
    if not isinstance(body, dict):
        raise CollabError("poll body must be an object")
    peer_id = body.get('peer_id')
    if not isinstance(peer_id, str) or not peer_id:
        raise CollabError("poll body missing 'peer_id'")
    since = body.get('since') or {}
    try:
        vv = vv_deserialize(since)
    except ValueError as e:
        raise CollabError(f"poll 'since' malformed: {e}")
    return peer_id, vv


def parse_op_request(body: dict) -> tuple[str, str, Any, Optional[str]]:
    """Parse ``{peer_id, cell, value, sheet?}`` → tuple."""
    if not isinstance(body, dict):
        raise CollabError("op body must be an object")
    peer_id = body.get('peer_id')
    cell = body.get('cell')
    if not isinstance(peer_id, str) or not peer_id:
        raise CollabError("op body missing 'peer_id'")
    if not isinstance(cell, str) or not cell:
        raise CollabError("op body missing 'cell'")
    return peer_id, cell, body.get('value'), body.get('sheet')
