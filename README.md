# GridLang

[![tests](https://github.com/Fiyy/gridlang/actions/workflows/test.yml/badge.svg)](https://github.com/Fiyy/gridlang/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Latest release](https://img.shields.io/github/v/release/Fiyy/gridlang)](https://github.com/Fiyy/gridlang/releases)

**The AI-native spreadsheet format — data, formulas, and visualization in a single plain-text file.**

GridLang is to Excel what Markdown is to Word: a human-readable, version-controllable, AI-friendly format that captures the full power of spreadsheets — 59 formulas, 9 chart types, conditional formatting, JS/Python compute engines, real-time CRDT collaboration — without the binary blob.

## Quick Install

```bash
cd /data/gridlang
pip install -e .
```

## Quick Start

Create `hello.grid`:

```
--- meta ---
name: "Q1 Sales"
version: "1.0"

--- data ---
Region,Jan,Feb,Mar,Total
North,120,135,150,=SUM(B2:D2)
South,95,110,125,=SUM(B3:D3)
West,80,90,105,=SUM(B4:D4)

--- present ---
chart: bar
  data: B2:D4
  labels: A2:A4
  title: "Q1 Sales by Region"
```

Run it:

```bash
gridlang run hello.grid
```

## CLI Commands

### `gridlang run`

Execute a `.grid` file and display results in the terminal.

```bash
gridlang run report.grid              # terminal output
gridlang run report.grid --json       # JSON output
gridlang run report.grid --sheet Q2   # run a specific sheet
```

### `gridlang render`

Render to a standalone HTML file with embedded charts.

```bash
gridlang render dashboard.grid -o dashboard.html
```

### `gridlang validate`

Check file format and formula syntax without executing.

```bash
gridlang validate report.grid
```

### `gridlang info`

Print a structural summary: sheets, cell count, formulas, charts.

```bash
gridlang info report.grid
```

### `gridlang import`

Import from Excel or CSV into `.grid` format.

```bash
gridlang import data.xlsx -o data.grid
gridlang import sales.csv -o sales.grid
```

### `gridlang export`

Export a `.grid` file to Excel (with native charts) or CSV.

```bash
gridlang export report.grid -o report.xlsx
gridlang export report.grid -o report.csv --format csv
```

### `gridlang serve`

Launch a live-preview web server with auto-reload on file changes.

```bash
gridlang serve dashboard.grid --port 8080
gridlang serve dashboard.grid --port 8080 --edit            # editor UI
gridlang serve dashboard.grid --port 8080 --edit --collab   # multi-peer (v0.8)
```

With `--collab`, multiple browser tabs can connect to the same URL and edit
cells live; changes converge via a CRDT (LWW per cell with HLC clocks).
See [Collaborative Editing](#collaborative-editing-v08).

### `gridlang js-bundle`

Bundle a JavaScript-engine `.grid` file into a self-contained `.js` that runs
anywhere a JS engine does — Node, browser, Web Worker, edge function. The
bundle embeds the data, helpers, pipeline runner, and your compute code; no
gridlang or Python at runtime.

```bash
gridlang js-bundle report.grid -o report.bundle.js   # Node bundle
gridlang js-bundle report.grid --browser -o worker.js  # Worker bundle
node report.bundle.js                                # prints JSON
```

## Excel vs GridLang

| Dimension | Excel (.xlsx) | GridLang (.grid) |
|-----------|--------------|-----------------|
| AI readability | Requires binary parsing libraries | Plain text — readable as-is |
| Version control | Binary diffs are opaque | `git diff` works perfectly |
| Formulas | 400+ proprietary functions | 59 Excel-compatible built-ins |
| Charts | GUI-only creation | Declarative, inline SVG |
| Multi-sheet | Tab-based UI | `--- data:name ---` sections |
| Conditional formatting | Dialog-heavy | Inline rules |
| Remote data | Manual import / Power Query | `@source: <url>` directive |
| Reactive editing | VBA macros / OLE | `{{ cell("B2") }}` + `bind:` form widgets |
| Live collaboration | OLE + Office365 servers | `serve --collab` over CRDT, no cloud needed |
| Compute language | VBA + 400 functions | Python or JavaScript (`engine:` selector) |
| Bundle for browser | Closed-source COM/OLE | `gridlang js-bundle --browser` produces a Web Worker |
| Testability | Nearly impossible | Standard unit tests (442 tests) |
| Interop | Locked ecosystem | Import/export Excel & CSV |

## Feature Highlights

### 59 Built-in Formulas

Excel-compatible syntax — works the way you already know.

```
=SUM(A1:A10)
=VLOOKUP(E2, A1:C100, 3, FALSE)
=SUMIF(B1:B50, ">100", C1:C50)
=IF(A1>90, "Pass", "Fail")
=PIVOT(data!A1:D100, "Region", "Revenue", "SUM")
```

### 9 SVG Chart Types

Declarative charts rendered as clean SVG. Use either the **Chart DSL** block syntax
(preferred, AI-friendly) or call the helpers directly with `{{ bar_chart(...) }}`.

```
--- present ---
chart: line
  data: B1:B12
  labels: A1:A12
  title: "Monthly Trend"

chart: heatmap
  data: B2:D10
  title: "Correlation Matrix"

chart: sparkline
  data: C2:C50
  inline: true
```

Supported types: `bar`, `line`, `pie`, `scatter`, `area`, `stacked_bar`, `heatmap`, `sparkline`, `color_scale`

References inside DSL blocks (`Revenue` → column, `agg.foo` → aggregate,
`B2:D4` → A1 range, `Q1,Q2,Q3` → multi-series, `sales!Revenue` → cross-sheet)
all resolve automatically. See [`spec/SPEC.md` §16](spec/SPEC.md) for the full grammar.

### Multi-Sheet Support

Organize data across named sheets, reference between them.

```
--- data:revenue ---
Region,Q1,Q2,Q3,Q4
North,120,135,150,160

--- data:summary ---
Total,=SUM(revenue!B2:E2)
```

### Conditional Formatting

```
--- present ---
format: color_scale
  range: B2:B100
  min_color: "#ffffff"
  max_color: "#e74c3c"

format: data_bar
  range: C2:C50

format: rules
  range: D2:D50
  rule: ">90 -> bold green"
  rule: "<60 -> italic red"
```

### Remote Data Sources

Load data from URLs or local files instead of (or in addition to) inline CSV.
Inline rows act as a fallback when the remote source is unavailable or
`--allow-remote` is not given.

```
--- data ---
@source: https://api.example.com/sales.json
@format: json
@select: data.records
@cache: 1h
@header: Authorization: Bearer xyz

# Fallback used when the remote is denied / fails
Region,Total
North,100
```

```bash
gridlang run report.grid                  # uses inline fallback
gridlang run report.grid --allow-remote   # fetches the URL
```

Supported schemes: `file://` (always allowed), `http(s)://` (opt-in via
`--allow-remote`). Format auto-detected from the URL extension; `csv`/`tsv`/
`json`/`xlsx` are supported. JSON `@select` drills into a sub-path with
`a.b.c[0]` syntax. See [`spec/SPEC.md` §17](spec/SPEC.md) for the full directive table.

### Reactive Bindings

Make individual cells editable from the rendered preview. Edits go directly
back into the `.grid` source and trigger a re-render — like a tiny
spreadsheet, but the source-of-truth stays in plain text.

**Inline cell binding** with the `cell()` Jinja helper:

```html
<td>{{ cell("B2") }}</td>            <!-- editable cell -->
<td>{{ cell("B2", fmt=",.2f") }}</td> <!-- formatted -->
<td>{{ cell("B2@sales") }}</td>      <!-- cross-sheet -->
```

**Form-style binding** with `bind:` blocks:

```
bind: input
  cell: B2
  label: "Unit Price"
  type: number
  step: 0.10

bind: select
  cell: A2
  options: North, South, East, West
```

```bash
gridlang serve dashboard.grid --port 8080 --edit
# Open http://localhost:8080 — click a cell, type, blur to commit.
```

The server's `POST /api/cell-edit` endpoint accepts `{cell: "B2", value: ...}`
and rewrites only the target row, preserving comments, blank lines,
`@directives`, and formulas in other cells. Header rows are read-only.
See [`spec/SPEC.md` §18](spec/SPEC.md) for the full grammar and protocol.

### Excel Import with Formula Conversion

Converts Excel formulas and structure to GridLang equivalents automatically.

```bash
gridlang import quarterly_report.xlsx -o report.grid
# Formulas, named ranges, and sheet references are preserved
```

### Excel Export with Native Charts

Exports produce real `.xlsx` files with native Excel chart objects (Bar, Line, Pie).

```bash
gridlang export dashboard.grid -o dashboard.xlsx
```

### Sandboxed Python Execution

The compute layer runs in a restricted sandbox — safe for AI-generated code.

```
--- compute ---
def transform(df):
    df['Growth'] = df['Revenue'].pct_change() * 100
    return df
```

### JavaScript Compute Engine

Set `engine: javascript` in the meta section to author the compute layer in JS.
Useful when you're sharing `.grid` files with frontend codebases or want the
compute layer to be runnable by both Python and Node tooling.

```
--- meta ---
name: "Q1 Sales"
engine: javascript
version: "1.0"

--- compute ---
function transform(df) {
  df.addColumn('Tax', r => r.Revenue * 0.2);
  return df;
}
function aggregates(df) {
  return { total: df.sum('Revenue'), tax: df.sum('Tax') };
}
```

The DataFrame is exposed as an array of records with a ~25-method helper API
covering aggregations (`sum`, `mean`, `std`, `quantile`, `describe`),
filtering (`where`, `head`, `distinct`, `find`), reshaping (`pluck`, `drop`,
`rename`, `assign`), sorting/grouping (`sortBy`, `groupBy`, `countBy`), joins
(`join`, `leftJoin`, `concat`), and conversion (`toCSV`, `toRecords`).

```js
function aggregates(df) {
  const top3 = df.sortBy('Revenue', { desc: true }).head(3);
  const byRegion = df.groupBy('Region');
  const sums = {};
  for (const k of Object.keys(byRegion)) sums[k] = byRegion[k].sum('Revenue');
  return {
    median:    df.median('Revenue'),
    p90:       df.quantile('Revenue', 0.9),
    distinct:  df.distinct('Region').count(),
    top:       top3.col('Product'),
    by_region: sums,
  };
}
```

User code runs in a Node `vm` sandbox — no `require`, `process`, or filesystem
access. Requires Node 18+; falls back gracefully via `JsRuntimeUnavailable`
when Node isn't on PATH. See [`spec/SPEC.md` §19](spec/SPEC.md) and
[§20](spec/SPEC.md) for the full helper table and bundle format.

### Self-Contained JS Bundles

`gridlang js-bundle` packages a `.grid` file's data + compute layer into a
single JS file that runs anywhere a JS engine does — without gridlang,
without Python, without npm.

```bash
# Node: prints pipeline result as JSON on stdout
gridlang js-bundle report.grid -o bundle.js
node bundle.js

# Browser / Web Worker: drop into a <script> or new Worker(blob)
gridlang js-bundle report.grid --browser -o worker.js
```

The bundle embeds your data (post-`@source` resolution), the compute code,
the helper API, and the pipeline runner — verbatim, no minification chain,
no CDN. Two identical `.grid` files produce byte-identical bundles, so they
diff cleanly and cache by SHA.

### Web Preview with Auto-Reload

```bash
gridlang serve report.grid --port 8080
# Open http://localhost:8080 — updates live as you edit
```

### Collaborative Editing (v0.8)

Multiple browser tabs (or separate users on the same network) can edit the
same `.grid` file at once. Changes converge via a CRDT — every replica that
has seen the same set of operations ends up with the same per-cell value,
regardless of arrival order.

```bash
gridlang serve dashboard.grid --collab --edit
# Open http://localhost:8080 in two browser tabs.
# Edit a cell in tab A → within ~700ms the value appears (and flashes blue) in tab B.
# Edit the same cell in both tabs at once → the higher-HLC write wins on every replica.
```

The implementation is three small modules:

| Module                       | Role                                            |
|------------------------------|-------------------------------------------------|
| `gridlang.crdt`              | HLC + LWW per-cell + version vectors            |
| `gridlang.collab`            | `CollabSession` — peers, persistence, sync      |
| `gridlang.collab_client`     | Self-contained browser JS (~250 lines)          |

Wire protocol is JSON-over-HTTP — `POST /api/collab/{join,op,poll,leave}`,
`GET /api/collab/{snapshot,stats}`. The server's on-disk `.grid` file
remains the source of truth; every commit is persisted, so `gridlang run`
or `render` after a collab session sees the merged values.

See [`spec/SPEC.md` §21](spec/SPEC.md) for the full protocol, convergence
proof sketch, and programmatic Python API.

## Project Structure

```
gridlang/
├── gridlang/
│   ├── parser.py          # .grid file parser
│   ├── schema.py          # data layer validation
│   ├── runtime.py         # compute engine (sandboxed)
│   ├── js_runtime.py      # alternative JavaScript compute engine (v0.6)
│   ├── js_bundle.py       # Node + Web Worker bundle generator (v0.7)
│   ├── js/                # JS source files (df_helpers, pipeline, bridge)
│   ├── crdt.py            # HLC + LWW per-cell CRDT (v0.8)
│   ├── collab.py          # CollabSession — peers, persistence, sync (v0.8)
│   ├── collab_client.py   # browser-side collab JS (v0.8)
│   ├── renderer.py        # HTML/SVG rendering
│   ├── chart_dsl.py       # chart:/format: DSL preprocessor
│   ├── data_sources.py    # @source remote data loader + cache
│   ├── bindings.py        # reactive cell + form bindings (v0.5)
│   ├── formulas.py        # 59 built-in functions
│   ├── charts.py          # 9 SVG chart types
│   ├── excel_import.py    # .xlsx → .grid conversion
│   ├── excel_export.py    # .grid → .xlsx with native charts
│   ├── csv_io.py          # CSV import/export
│   ├── server.py          # live preview server
│   └── cli.py             # CLI entry point
├── spec/SPEC.md           # format specification
├── examples/              # 12 example .grid files + sample.xlsx
└── tests/                 # 442 tests
```

## License

[MIT](LICENSE) — see also [`CHANGELOG.md`](CHANGELOG.md) for the version history.
