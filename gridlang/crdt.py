"""
GridLang CRDT — Conflict-free replicated data type for collaborative editing.

This module is the data-structure foundation for v0.8 collaborative editing.
It implements a per-cell Last-Writer-Wins (LWW) CRDT keyed on A1 references
and ordered by a Hybrid Logical Clock (HLC). Concretely:

* The *cells* in a `.grid` file's data sections form a logical 3-D map
  ``(sheet, row, col) -> value``. Inserts or deletes of rows/columns are
  out of scope here — GridLang's data layer has fixed headers, so the only
  concurrent operation we need to converge on is "edit cell X to value Y".

* Each edit is a :class:`CellOp` with a unique :class:`HLC` timestamp and
  the originating client's ``site_id``. Replicas converge by replaying ops
  in (HLC, site) order: the highest-HLC op for each cell wins, with
  ``site_id`` as a tiebreaker for true concurrent updates.

* :class:`Document` is the local replica state. ``apply()`` is idempotent
  and commutative — same set of ops in any order yields the same value.
  ``ops_since(version)`` returns the delta a peer hasn't seen yet, where
  ``version`` is a vector clock indexed by ``site_id``.

The module is dependency-free except for the standard library and exposes
no I/O — wire formats and HTTP plumbing live in :mod:`gridlang.collab`.

Determinism
-----------
``HLC.now()`` uses a monotonic per-instance counter that you must seed with
``HLC.bump(wall_ms)`` whenever you receive a remote op or learn a new wall
time. The class never calls ``time.time()`` itself, so unit tests get
reproducible orderings simply by feeding fixed wall times.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Iterator, Optional


# ─── Hybrid Logical Clock ──────────────────────────────────────────────────

@dataclass(frozen=True, order=True)
class HLC:
    """A Hybrid Logical Clock timestamp.

    HLC = (wall_ms, logical, site_id). It satisfies happens-before: if event
    *a* happens before *b* on the same replica, ``a < b`` always; across
    replicas, two events that did not see each other can still be ordered
    deterministically by comparing their tuples lexicographically.

    The class is immutable; use :meth:`HLC.tick` to advance the clock when
    the local replica generates a new event, and :meth:`HLC.merge` to absorb
    a remote timestamp.
    """
    wall_ms: int
    logical: int
    site_id: str

    def tick(self, wall_ms: int) -> 'HLC':
        """Return a new HLC for a locally-generated event at ``wall_ms``.

        Spec (Kulkarni et al., 2014): if the wall clock has advanced past
        the previous HLC's ``wall_ms``, reset ``logical`` to 0; otherwise
        keep ``wall_ms`` and increment ``logical``.
        """
        if wall_ms > self.wall_ms:
            return HLC(wall_ms=wall_ms, logical=0, site_id=self.site_id)
        return HLC(wall_ms=self.wall_ms, logical=self.logical + 1,
                   site_id=self.site_id)

    def merge(self, remote: 'HLC', wall_ms: int) -> 'HLC':
        """Return the HLC after observing ``remote`` at local time ``wall_ms``.

        Standard HLC merge:
          * pick the max wall time among (self, remote, wall_ms),
          * compute logical = (max(self.logical, remote.logical) + 1) when
            wall ties dictate so, else 0.
        """
        max_wall = max(self.wall_ms, remote.wall_ms, wall_ms)
        if max_wall == self.wall_ms == remote.wall_ms:
            new_logical = max(self.logical, remote.logical) + 1
        elif max_wall == self.wall_ms:
            new_logical = self.logical + 1
        elif max_wall == remote.wall_ms:
            new_logical = remote.logical + 1
        else:  # max_wall == wall_ms (a fresh local tick)
            new_logical = 0
        return HLC(wall_ms=max_wall, logical=new_logical, site_id=self.site_id)

    def to_tuple(self) -> tuple:
        """Comparable tuple form used for op ordering on the wire."""
        return (self.wall_ms, self.logical, self.site_id)

    @classmethod
    def origin(cls, site_id: str) -> 'HLC':
        """Return the zero clock for a fresh replica."""
        return cls(wall_ms=0, logical=0, site_id=site_id)


# ─── CellKey & CellOp ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class CellKey:
    """A normalized cell identity within a document.

    ``sheet`` is the empty string for the default (single-sheet) data
    section, otherwise the explicit sheet name. ``row`` is 1-based with
    ``row == 1`` being the header (header rows are read-only by convention,
    but the CRDT itself does not enforce that — :mod:`gridlang.collab` does).
    """
    sheet: str
    row: int
    col: int

    def to_a1(self) -> str:
        """Return the canonical A1 reference (e.g. ``B2`` or ``B2@sales``)."""
        # 1-based column → letters (1=A, 26=Z, 27=AA, …)
        col = self.col
        letters = ''
        while col > 0:
            col, rem = divmod(col - 1, 26)
            letters = chr(ord('A') + rem) + letters
        ref = f'{letters}{self.row}'
        if self.sheet:
            ref = f'{ref}@{self.sheet}'
        return ref

    @classmethod
    def from_a1(cls, ref: str, default_sheet: str = '') -> 'CellKey':
        """Parse an A1 reference into a CellKey.

        Accepts ``B2`` or ``B2@sheet``. Mirrors :func:`gridlang.bindings.parse_a1_ref`
        but lives here so the CRDT module has zero gridlang internal deps.
        """
        if not isinstance(ref, str) or not ref:
            raise ValueError(f"Invalid A1 reference: {ref!r}")
        s = ref.strip()
        sheet = default_sheet
        if '@' in s:
            s, sheet = s.split('@', 1)
            sheet = sheet.strip()
        # split letters + digits
        i = 0
        while i < len(s) and s[i].isalpha():
            i += 1
        letters, digits = s[:i].upper(), s[i:]
        if not letters or not digits.isdigit():
            raise ValueError(f"Invalid A1 reference: {ref!r}")
        col = 0
        for ch in letters:
            col = col * 26 + (ord(ch) - ord('A') + 1)
        return cls(sheet=sheet, row=int(digits), col=col)


@dataclass(frozen=True)
class CellOp:
    """A single LWW write to a cell.

    Two ops on the same cell converge to the one with the larger HLC; two
    ops with the same HLC are impossible because HLC carries a unique
    ``site_id`` and the local site always increments ``logical`` before
    minting a new HLC.
    """
    key: CellKey
    value: Any
    hlc: HLC

    def to_dict(self) -> dict:
        """Wire form: JSON-serializable dict suitable for HTTP transport."""
        return {
            'cell': self.key.to_a1(),
            'value': self.value,
            'hlc': {
                'wall_ms': self.hlc.wall_ms,
                'logical': self.hlc.logical,
                'site_id': self.hlc.site_id,
            },
        }

    @classmethod
    def from_dict(cls, d: dict, default_sheet: str = '') -> 'CellOp':
        """Parse from wire form. Raises :class:`ValueError` on malformed input."""
        if not isinstance(d, dict):
            raise ValueError(f"CellOp wire form must be a dict, got {type(d).__name__}")
        ref = d.get('cell')
        if not ref:
            raise ValueError("CellOp missing 'cell'")
        hlc_raw = d.get('hlc') or {}
        if not isinstance(hlc_raw, dict):
            raise ValueError("CellOp 'hlc' must be an object")
        try:
            hlc = HLC(
                wall_ms=int(hlc_raw['wall_ms']),
                logical=int(hlc_raw['logical']),
                site_id=str(hlc_raw['site_id']),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"CellOp malformed 'hlc': {e}")
        return cls(
            key=CellKey.from_a1(ref, default_sheet=default_sheet),
            value=d.get('value'),
            hlc=hlc,
        )


# ─── Vector clock helpers ──────────────────────────────────────────────────

# A "version vector" maps site_id -> highest logical seen from that site, but
# we use HLC tuples as the per-site marker because they carry wall + logical
# and order events globally without a single coordinator.

VersionVec = dict[str, tuple[int, int]]   # site_id -> (wall_ms, logical)


def vv_from_ops(ops: Iterable[CellOp]) -> VersionVec:
    """Build the version vector that summarizes the given op set."""
    vv: VersionVec = {}
    for op in ops:
        h = op.hlc
        cur = vv.get(h.site_id)
        cand = (h.wall_ms, h.logical)
        if cur is None or cand > cur:
            vv[h.site_id] = cand
    return vv


def vv_max(a: VersionVec, b: VersionVec) -> VersionVec:
    """Pointwise maximum of two version vectors (the "later" view)."""
    out = dict(a)
    for site, mark in b.items():
        cur = out.get(site)
        if cur is None or mark > cur:
            out[site] = mark
    return out


def vv_serialize(vv: VersionVec) -> dict:
    """Wire form: ``{site_id: [wall_ms, logical]}``."""
    return {s: [w, l] for s, (w, l) in vv.items()}


def vv_deserialize(d: Optional[dict]) -> VersionVec:
    """Parse a wire-form version vector. ``None`` → empty vector."""
    if not d:
        return {}
    out: VersionVec = {}
    for k, v in d.items():
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            raise ValueError(f"vector clock entry {k!r} malformed: {v!r}")
        out[str(k)] = (int(v[0]), int(v[1]))
    return out


# ─── Document — local CRDT replica ─────────────────────────────────────────

@dataclass
class Document:
    """A CRDT replica of one ``.grid`` file's editable cells.

    The Document stores the *winning* op per cell and a flat journal of all
    ops accepted so far — the journal is what :meth:`ops_since` filters
    against a peer's version vector.

    Replicas converge by exchanging ops; same final state across all replicas
    once they've all seen the same op set, regardless of arrival order.
    """
    site_id: str
    cells: dict[CellKey, CellOp] = field(default_factory=dict)
    journal: list[CellOp] = field(default_factory=list)
    clock: HLC = field(init=False)

    def __post_init__(self):
        # Initialize the local HLC for this site.
        self.clock = HLC.origin(self.site_id)

    # ── Local edits ────────────────────────────────────────────────────────

    def edit(self, cell: str, value: Any, *, wall_ms: int,
             default_sheet: str = '') -> CellOp:
        """Generate a local op for ``cell := value`` and apply it.

        ``wall_ms`` is supplied by the caller (server passes ``int(time.time()*1000)``;
        tests pass fixed values). The new op is appended to the journal and
        returned so the caller can ship it to peers.
        """
        self.clock = self.clock.tick(wall_ms)
        op = CellOp(
            key=CellKey.from_a1(cell, default_sheet=default_sheet),
            value=value,
            hlc=self.clock,
        )
        self._accept(op)
        return op

    # ── Remote merge ──────────────────────────────────────────────────────

    def apply(self, op: CellOp, *, wall_ms: Optional[int] = None) -> bool:
        """Apply a remote op to the replica. Returns True if it changed state.

        Idempotent: replaying the same op is a no-op. Commutative: order of
        arrival does not affect the final state.
        """
        # Advance the local clock so subsequent local edits are causally after
        # everything we've seen (HLC merge rule).
        if wall_ms is None:
            wall_ms = op.hlc.wall_ms
        self.clock = self.clock.merge(op.hlc, wall_ms)
        return self._accept(op)

    def apply_many(self, ops: Iterable[CellOp], *,
                   wall_ms: Optional[int] = None) -> int:
        """Bulk-apply remote ops. Returns the number of ops that changed state."""
        changed = 0
        for op in ops:
            if self.apply(op, wall_ms=wall_ms):
                changed += 1
        return changed

    def _accept(self, op: CellOp) -> bool:
        """Internal: store ``op`` if it wins the LWW race for its cell."""
        existing = self.cells.get(op.key)
        if existing is not None and existing.hlc.to_tuple() >= op.hlc.to_tuple():
            # Already have an equal-or-newer op; record it in the journal
            # only if we haven't seen this exact (key, hlc) before.
            if not self._journal_has(op):
                self.journal.append(op)
            return False
        self.cells[op.key] = op
        if not self._journal_has(op):
            self.journal.append(op)
        return True

    def _journal_has(self, op: CellOp) -> bool:
        """Has this exact (key, hlc) been journaled? Linear scan; journals stay short."""
        for j in self.journal:
            if j.key == op.key and j.hlc == op.hlc:
                return True
        return False

    # ── Read state ────────────────────────────────────────────────────────

    def get(self, cell: str, default_sheet: str = '') -> Optional[Any]:
        """Return the current value for a cell, or ``None`` if unset."""
        key = CellKey.from_a1(cell, default_sheet=default_sheet)
        op = self.cells.get(key)
        return op.value if op is not None else None

    def __len__(self) -> int:
        return len(self.cells)

    def __iter__(self) -> Iterator[CellOp]:
        return iter(self.cells.values())

    # ── Sync helpers ──────────────────────────────────────────────────────

    def version(self) -> VersionVec:
        """Return the version vector summarizing this replica's accepted ops."""
        return vv_from_ops(self.journal)

    def ops_since(self, peer_vv: VersionVec) -> list[CellOp]:
        """Return ops in the journal the peer hasn't seen yet.

        For each op, include it iff its HLC's (wall_ms, logical) exceeds the
        peer's high-water mark for that op's ``site_id``.
        """
        out: list[CellOp] = []
        for op in self.journal:
            mark = peer_vv.get(op.hlc.site_id)
            cand = (op.hlc.wall_ms, op.hlc.logical)
            if mark is None or cand > mark:
                out.append(op)
        # Stable order by HLC tuple — peers see ops in a deterministic order.
        out.sort(key=lambda o: o.hlc.to_tuple())
        return out

    def snapshot(self) -> list[CellOp]:
        """Return all winning ops, in HLC order. Useful for fresh peers."""
        return sorted(self.cells.values(), key=lambda o: o.hlc.to_tuple())

    # ── Cloning (used by tests and for safe peer state) ───────────────────

    def clone(self, site_id: Optional[str] = None) -> 'Document':
        """Return a deep-ish copy. ``site_id`` may be replaced for a new replica."""
        new = Document(site_id=site_id or self.site_id)
        new.cells = dict(self.cells)
        new.journal = list(self.journal)
        new.clock = replace(self.clock, site_id=new.site_id)
        return new


# ─── Convenience: deterministic op ordering for tests ──────────────────────

def sort_ops(ops: Iterable[CellOp]) -> list[CellOp]:
    """Return ``ops`` sorted by HLC tuple. Useful for assertions."""
    return sorted(ops, key=lambda o: o.hlc.to_tuple())
