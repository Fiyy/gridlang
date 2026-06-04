# Changelog

All notable changes to GridLang are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-06-04

The v2.0 roadmap from [`spec/SPEC.md` §15](spec/SPEC.md) is complete. v1.0
is a stability marker — it bundles every feature shipped in v0.2 → v0.8
and commits to the public Python and HTTP APIs (the surfaces exported
from `gridlang.__init__` and the endpoints under `/api/*`) as a stable
contract through the v1.x line.

### Stabilized
- Python API: `parse`, `execute`, `render`, `import_excel`, `export_excel`,
  `apply_cell_edit`, `bundle_doc`, `CrdtDocument`, `CollabSession`, …
- HTTP API: `/api/render`, `/api/save`, `/api/cell-edit`, `/api/collab/*`
- File format: section delimiter grammar, A1 references, `@source` directives,
  `chart:` / `format:` / `bind:` DSL blocks
- CLI: `run`, `render`, `validate`, `info`, `import`, `export`, `serve`,
  `js-bundle` — all surfaced commands and flags

### Added (in this release)
- `CHANGELOG.md` (this file)
- `LICENSE` — MIT, matching the `pyproject.toml` declaration
- `.github/workflows/test.yml` — CI runs the 442-test suite on Python 3.9–3.12
  for every push and pull request

## [0.8.0] — 2026-06-04 — Collaborative editing (CRDT)

The final v2.0 roadmap item ([SPEC §21](spec/SPEC.md)). Multiple peers can
connect to `gridlang serve --collab` and edit cells of the same `.grid` file
live; operations converge via a per-cell LWW register with Hybrid Logical
Clock timestamps.

### Added
- `gridlang.crdt` — `HLC`, `CellOp`, `Document`, version vectors
- `gridlang.collab` — `CollabSession` with peer registry + on-disk persistence
- `gridlang.collab_client` — self-contained ~250-line browser IIFE
- HTTP endpoints: `POST /api/collab/{join,leave,op,poll}`,
  `GET /api/collab/{snapshot,stats,client.js}`
- `gridlang serve --collab` flag
- `examples/12_collab.grid` — multi-peer demo
- `tests/test_crdt.py` (39 tests), `tests/test_collab.py` (25 tests)

### Convergence guarantees (proven by tests)
- Commutativity, idempotence, random-permutation property
- 64 new property tests; total now 442

## [0.7.0] — 2026-06-03 — JS Bundles & Extended df API

`gridlang js-bundle` packages a `.grid` file's data + compute layer into a
self-contained JS file that runs anywhere a JS engine does — Node,
browser, Web Worker, edge function. No Python, no npm, no CDN.

### Added
- `gridlang.js_bundle` — `bundle_doc`, `bundle_file`, `BundleResult`
- `gridlang js-bundle` CLI command (`--browser` for Web Worker; `--minify`)
- Expanded df helper API (~25 methods): aggregations (`sum`, `mean`,
  `median`, `std`, `quantile`, `describe`), filtering (`where`, `head`,
  `distinct`, `find`), reshaping (`pluck`, `drop`, `rename`, `assign`),
  sorting/grouping (`sortBy`, `groupBy`, `countBy`), joins (`join`,
  `leftJoin`, `concat`), conversion (`toCSV`, `toRecords`)
- JS source files extracted from inline strings into `gridlang/js/*.js`
- `examples/11_js_extreme.grid` — uses ~15 df helpers in one pipeline
- [SPEC §20](spec/SPEC.md) — bundle protocol, source layout, determinism

## [0.6.0] — 2026-06-03 — JavaScript Compute Engine

Set `engine: javascript` in `--- meta ---` to author the compute layer in
JS instead of Python. The compute runs in a Node `vm` sandbox (no
`require`, `process`, or filesystem); useful for sharing `.grid` files
with frontend codebases.

### Added
- `gridlang.js_runtime` — Node subprocess sandbox via JSON IPC
- `engine:` meta selector (`python` default; `javascript` opt-in)
- Falls back gracefully via `JsRuntimeUnavailable` when Node isn't on PATH
- `examples/10_javascript.grid`
- [SPEC §19](spec/SPEC.md) — engine selection, hooks, sandbox, wire protocol

## [0.5.0] — 2026-06-03 — Reactive Bindings

Make individual cells editable from the rendered preview. Edits go directly
back into the `.grid` source and trigger a re-render — a tiny spreadsheet
where the source-of-truth stays in plain text.

### Added
- `gridlang.bindings` — `cell()` Jinja helper, `bind:` DSL block parser,
  `apply_edit()` server-side rewriter
- A1 reference grammar: `B2` (default sheet) or `B2@sheet`
- `POST /api/cell-edit` endpoint; `gridlang serve --edit` injects client JS
- Preserves comments, blank lines, `@directives`, formulas in other cells
  when rewriting a single row
- `examples/09_reactive.grid`
- [SPEC §18](spec/SPEC.md)

## [0.4.0] — 2026-06-03 — Remote Data Sources

`@source` directive in data sections to load from URLs or local files
instead of inline CSV. Inline rows act as a fallback.

### Added
- `gridlang.data_sources` — `@source`, `@format`, `@cache`, `@select`,
  `@header` directive parser + remote loader
- `--allow-remote` flag opts into HTTP(S) fetching; `file://` always allowed
- Auto-detect format from URL extension (`csv`, `tsv`, `json`, `xlsx`)
- JSON `@select` with `a.b.c[0]` path syntax
- TTL-based on-disk cache via `GRIDLANG_CACHE_DIR`
- `examples/08_remote_data.grid`, `examples/fixtures/q1_sales.json`
- [SPEC §17](spec/SPEC.md) — directive table, safety model, caching

## [0.3.0] — 2026-06-03 — Chart & Format DSL

Declarative `chart: TYPE` / `format: TYPE` blocks in the present layer that
compile to Jinja2 helper calls. Preferred over inline `{{ bar_chart(...) }}`
for AI-friendliness.

### Added
- `gridlang.chart_dsl` — block grammar parser
- `chart:` types: `bar`, `line`, `pie`, `scatter`, `area`, `stacked_bar`,
  `heatmap`, `sparkline`, `color_scale`
- `format:` types: `color_scale`, `data_bar`, `rules`
- Reference resolution: column names, `agg.foo`, `B2:D4` ranges,
  multi-series, `sales!Revenue` cross-sheet
- `examples/07_chart_dsl.grid`
- [SPEC §16](spec/SPEC.md)

## [0.2.0] — 2025-05-15 — v0.2 baseline

The first feature-complete release. Multi-sheet support, 59 Excel-compatible
formulas, 9 chart types, conditional formatting, Excel/CSV import & export,
live-preview server, web GUI editor.

### Highlights
- Multi-sheet syntax: `--- data:sheet_name ---`
- 59 formulas: `SUM`, `VLOOKUP`, `SUMIF`, `IF`, `PIVOT`, …
- 9 SVG chart types
- Conditional formatting via `conditional_formats()`
- Sandboxed Python compute layer
- `gridlang import` / `export` for `.xlsx` and `.csv`
- `gridlang serve` live-preview server with auto-reload
- Self-contained Monaco-free editor UI

[1.0.0]: https://github.com/Fiyy/gridlang/releases/tag/v1.0.0
[0.8.0]: https://github.com/Fiyy/gridlang/releases/tag/v0.8.0
[0.7.0]: https://github.com/Fiyy/gridlang/releases/tag/v0.7.0
[0.6.0]: https://github.com/Fiyy/gridlang/releases/tag/v0.6.0
[0.5.0]: https://github.com/Fiyy/gridlang/releases/tag/v0.5.0
[0.4.0]: https://github.com/Fiyy/gridlang/releases/tag/v0.4.0
[0.3.0]: https://github.com/Fiyy/gridlang/releases/tag/v0.3.0
[0.2.0]: https://github.com/Fiyy/gridlang/commit/060f529
