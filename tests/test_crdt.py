"""Tests for the gridlang.crdt module — property-style.

Key properties we assert:

* HLC ordering — local ticks are monotonic; remote merges advance the clock;
  comparisons are deterministic.

* Cell convergence — any permutation of the same op set on two replicas
  produces identical state.

* Idempotence — applying the same op twice is a no-op.

* Causal preservation — ops_since correctly excludes ops the peer already saw.

* Wire round-trip — ``CellOp.from_dict(op.to_dict())`` reproduces the op.
"""

from __future__ import annotations

import itertools
import random

import pytest

from gridlang.crdt import (
    HLC, CellKey, CellOp, Document,
    vv_from_ops, vv_max, vv_serialize, vv_deserialize, sort_ops,
)


# ─── HLC ───────────────────────────────────────────────────────────────────

class TestHLC:

    def test_origin_zero(self):
        h = HLC.origin('A')
        assert h.wall_ms == 0 and h.logical == 0 and h.site_id == 'A'

    def test_tick_advances_wall(self):
        h = HLC.origin('A')
        h2 = h.tick(100)
        assert h2 == HLC(100, 0, 'A')
        # If wall is unchanged, only logical increments.
        h3 = h2.tick(100)
        assert h3 == HLC(100, 1, 'A')
        h4 = h3.tick(100)
        assert h4 == HLC(100, 2, 'A')

    def test_tick_resets_logical_when_wall_advances(self):
        h = HLC(100, 5, 'A')
        h2 = h.tick(101)
        assert h2 == HLC(101, 0, 'A')

    def test_tick_handles_clock_skew_backwards(self):
        # If local "now" is before our last HLC's wall (skew), we keep the
        # higher wall and just bump logical — the spec is to never go backwards.
        h = HLC(100, 3, 'A')
        h2 = h.tick(80)
        assert h2 == HLC(100, 4, 'A')

    def test_merge_picks_max_wall(self):
        local = HLC(100, 0, 'A')
        remote = HLC(200, 5, 'B')
        merged = local.merge(remote, wall_ms=150)
        # Max wall is remote's 200, so logical = remote.logical + 1.
        assert merged.wall_ms == 200
        assert merged.logical == 6
        assert merged.site_id == 'A'

    def test_merge_handles_wall_tie(self):
        local = HLC(100, 3, 'A')
        remote = HLC(100, 7, 'B')
        merged = local.merge(remote, wall_ms=100)
        # Both walls are 100, logical = max(3, 7) + 1.
        assert merged == HLC(100, 8, 'A')

    def test_merge_local_now_in_future(self):
        local = HLC(100, 3, 'A')
        remote = HLC(100, 1, 'B')
        merged = local.merge(remote, wall_ms=200)
        # New wall jumps to 200, logical resets.
        assert merged == HLC(200, 0, 'A')

    def test_total_order(self):
        # Lex-order on (wall, logical, site).
        a = HLC(100, 0, 'A')
        b = HLC(100, 0, 'B')
        c = HLC(100, 1, 'A')
        d = HLC(101, 0, 'A')
        assert a < b < c < d

    def test_to_tuple(self):
        h = HLC(42, 7, 'site')
        assert h.to_tuple() == (42, 7, 'site')


# ─── CellKey ───────────────────────────────────────────────────────────────

class TestCellKey:

    def test_a1_round_trip_simple(self):
        k = CellKey.from_a1('B2')
        assert k == CellKey(sheet='', row=2, col=2)
        assert k.to_a1() == 'B2'

    def test_a1_round_trip_with_sheet(self):
        k = CellKey.from_a1('AA10@sales')
        assert k == CellKey(sheet='sales', row=10, col=27)
        assert k.to_a1() == 'AA10@sales'

    def test_default_sheet(self):
        k = CellKey.from_a1('B2', default_sheet='main')
        assert k.sheet == 'main'
        assert k.to_a1() == 'B2@main'

    def test_invalid_ref(self):
        for bad in ('', '2B', '@x', 'B', '1', None):
            with pytest.raises((ValueError, TypeError)):
                CellKey.from_a1(bad)  # type: ignore

    def test_letters_two_chars(self):
        # AA = 27, ZZ = 702
        assert CellKey.from_a1('AA1').col == 27
        assert CellKey.from_a1('ZZ1').col == 702
        assert CellKey(sheet='', row=1, col=702).to_a1() == 'ZZ1'


# ─── CellOp wire form ──────────────────────────────────────────────────────

class TestCellOpWire:

    def test_round_trip(self):
        op = CellOp(
            key=CellKey('main', 5, 3),
            value='hello',
            hlc=HLC(123, 4, 'A'),
        )
        d = op.to_dict()
        op2 = CellOp.from_dict(d)
        assert op2 == op

    def test_round_trip_numeric_value(self):
        op = CellOp(key=CellKey('', 2, 2), value=42, hlc=HLC(1, 0, 'A'))
        d = op.to_dict()
        op2 = CellOp.from_dict(d)
        assert op2.value == 42

    def test_malformed(self):
        with pytest.raises(ValueError):
            CellOp.from_dict({})
        with pytest.raises(ValueError):
            CellOp.from_dict({'cell': 'B2'})  # missing hlc
        with pytest.raises(ValueError):
            CellOp.from_dict({'cell': 'B2', 'hlc': {}})


# ─── Document — local edits ────────────────────────────────────────────────

class TestDocumentLocalEdits:

    def test_edit_creates_op_with_advancing_clock(self):
        doc = Document(site_id='A')
        op1 = doc.edit('B2', 100, wall_ms=1000)
        op2 = doc.edit('B3', 200, wall_ms=1000)
        op3 = doc.edit('B4', 300, wall_ms=1001)
        assert op1.hlc < op2.hlc < op3.hlc

    def test_get_after_edit(self):
        doc = Document(site_id='A')
        doc.edit('B2', 100, wall_ms=1000)
        assert doc.get('B2') == 100

    def test_overwrite_same_cell(self):
        doc = Document(site_id='A')
        doc.edit('B2', 100, wall_ms=1000)
        doc.edit('B2', 200, wall_ms=1001)
        assert doc.get('B2') == 200

    def test_journal_contains_all_ops(self):
        doc = Document(site_id='A')
        doc.edit('B2', 100, wall_ms=1000)
        doc.edit('B2', 200, wall_ms=1001)
        # Both writes are journaled; the cells map only holds the winner.
        assert len(doc.journal) == 2
        assert len(doc.cells) == 1


# ─── Document — remote merge convergence ───────────────────────────────────

class TestDocumentConvergence:

    def _emit_ops(self, n: int) -> list[CellOp]:
        """Generate a mixed bag of ops from two simulated peers."""
        a = Document(site_id='A')
        b = Document(site_id='B')
        ops: list[CellOp] = []
        for i in range(n):
            wall = 1000 + i
            target = f'B{(i % 5) + 2}'
            if i % 2 == 0:
                ops.append(a.edit(target, f'A-{i}', wall_ms=wall))
            else:
                ops.append(b.edit(target, f'B-{i}', wall_ms=wall))
        return ops

    def test_two_replicas_converge(self):
        ops = self._emit_ops(20)
        # Replay in different orders on two fresh replicas.
        r1 = Document(site_id='X')
        r2 = Document(site_id='Y')
        r1.apply_many(ops)
        for op in reversed(ops):
            r2.apply(op)
        assert r1.cells == r2.cells

    def test_random_permutations_converge(self):
        ops = self._emit_ops(15)
        rng = random.Random(42)
        baseline = Document(site_id='X')
        baseline.apply_many(ops)
        for _ in range(10):
            shuffled = list(ops)
            rng.shuffle(shuffled)
            r = Document(site_id='X')
            r.apply_many(shuffled)
            assert r.cells == baseline.cells

    def test_idempotent_application(self):
        ops = self._emit_ops(8)
        r = Document(site_id='X')
        r.apply_many(ops)
        before = dict(r.cells)
        r.apply_many(ops)  # apply again
        r.apply_many(ops)  # and again
        assert r.cells == before

    def test_lww_winner(self):
        # Two ops on the same cell — later HLC wins regardless of order.
        op_old = CellOp(CellKey('', 2, 2), 'old', HLC(100, 0, 'A'))
        op_new = CellOp(CellKey('', 2, 2), 'new', HLC(200, 0, 'B'))
        for order in itertools.permutations([op_old, op_new]):
            r = Document(site_id='X')
            r.apply_many(order)
            assert r.get('B2') == 'new'

    def test_concurrent_same_wall_site_tiebreak(self):
        # Same wall + logical, different site_id — site_id is the tiebreaker.
        op_a = CellOp(CellKey('', 2, 2), 'A-says', HLC(100, 0, 'A'))
        op_b = CellOp(CellKey('', 2, 2), 'B-says', HLC(100, 0, 'B'))
        r1, r2 = Document('X'), Document('Y')
        r1.apply_many([op_a, op_b])
        r2.apply_many([op_b, op_a])
        # B > A by site_id, so B wins on both replicas.
        assert r1.get('B2') == 'B-says'
        assert r2.get('B2') == 'B-says'

    def test_clock_advances_after_merge(self):
        # Local clock should never go backwards after observing a remote op.
        doc = Document(site_id='X')
        remote = CellOp(CellKey('', 2, 2), 'r', HLC(500, 9, 'R'))
        doc.apply(remote, wall_ms=100)
        # Local edit now must be > remote's HLC.
        op = doc.edit('B3', 'local', wall_ms=100)
        assert op.hlc.to_tuple() > remote.hlc.to_tuple()


# ─── Sync via version vectors ──────────────────────────────────────────────

class TestVersionVectors:

    def test_vv_from_ops(self):
        ops = [
            CellOp(CellKey('', 2, 2), 'a', HLC(100, 0, 'A')),
            CellOp(CellKey('', 2, 3), 'b', HLC(100, 1, 'A')),
            CellOp(CellKey('', 2, 4), 'c', HLC(150, 0, 'B')),
        ]
        vv = vv_from_ops(ops)
        assert vv == {'A': (100, 1), 'B': (150, 0)}

    def test_vv_max(self):
        a = {'A': (100, 0), 'B': (50, 0)}
        b = {'A': (90, 5), 'C': (10, 10)}
        merged = vv_max(a, b)
        # A wins by wall (100 vs 90); B kept; C added.
        assert merged == {'A': (100, 0), 'B': (50, 0), 'C': (10, 10)}

    def test_vv_round_trip(self):
        vv = {'A': (100, 5), 'B': (200, 0)}
        assert vv_deserialize(vv_serialize(vv)) == vv

    def test_vv_deserialize_empty(self):
        assert vv_deserialize(None) == {}
        assert vv_deserialize({}) == {}

    def test_ops_since_returns_only_new(self):
        a = Document(site_id='A')
        b = Document(site_id='B')
        opA1 = a.edit('B2', 1, wall_ms=1000)
        opA2 = a.edit('B3', 2, wall_ms=1001)

        # B has seen A1 but not A2.
        b.apply(opA1)
        peer_vv = b.version()
        delta = a.ops_since(peer_vv)
        assert delta == [opA2]

    def test_ops_since_after_full_sync_is_empty(self):
        a = Document(site_id='A')
        b = Document(site_id='B')
        a.edit('B2', 1, wall_ms=1000)
        b.apply_many(a.snapshot())
        assert a.ops_since(b.version()) == []

    def test_ops_since_includes_concurrent_writes(self):
        a = Document(site_id='A')
        b = Document(site_id='B')
        a.edit('B2', 'a', wall_ms=1000)
        op_b = b.edit('B3', 'b', wall_ms=1000)

        # B asks A for ops it doesn't have. A doesn't have op_b's site mark,
        # so A reports its full journal.
        delta = a.ops_since(b.version())
        # B's version vector covers site 'B', so A's reply only contains A's op.
        assert all(op.hlc.site_id == 'A' for op in delta)


# ─── Snapshot ──────────────────────────────────────────────────────────────

class TestSnapshot:

    def test_snapshot_is_hlc_sorted(self):
        a = Document(site_id='A')
        a.edit('B2', 1, wall_ms=1002)
        a.edit('B3', 2, wall_ms=1000)
        a.edit('B4', 3, wall_ms=1001)
        snap = a.snapshot()
        assert [op.hlc.to_tuple() for op in snap] == sorted(
            op.hlc.to_tuple() for op in snap
        )

    def test_snapshot_only_winners(self):
        a = Document(site_id='A')
        a.edit('B2', 'first', wall_ms=1000)
        a.edit('B2', 'second', wall_ms=1001)
        snap = a.snapshot()
        assert len(snap) == 1
        assert snap[0].value == 'second'


# ─── Clone ────────────────────────────────────────────────────────────────

class TestClone:

    def test_clone_independent(self):
        a = Document(site_id='A')
        a.edit('B2', 1, wall_ms=1000)
        b = a.clone(site_id='B')
        # Mutating b doesn't affect a.
        b.edit('B3', 2, wall_ms=2000)
        assert 'B3' not in [op.key.to_a1() for op in a.snapshot()]

    def test_clone_preserves_state(self):
        a = Document(site_id='A')
        a.edit('B2', 'x', wall_ms=1000)
        b = a.clone()
        assert b.get('B2') == 'x'


# ─── sort_ops helper ─────────────────────────────────────────────────────

def test_sort_ops_stable_by_hlc():
    o1 = CellOp(CellKey('', 2, 2), 'a', HLC(100, 0, 'A'))
    o2 = CellOp(CellKey('', 2, 3), 'b', HLC(100, 0, 'B'))
    o3 = CellOp(CellKey('', 2, 4), 'c', HLC(99, 9, 'A'))
    out = sort_ops([o1, o2, o3])
    assert out == [o3, o1, o2]
