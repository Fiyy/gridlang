# GridLang

**The AI-native spreadsheet format — data, formulas, and visualization in a single plain-text file.**

GridLang is to Excel what Markdown is to Word: a human-readable, version-controllable, AI-friendly format that captures the full power of spreadsheets — 59 formulas, 9 chart types, conditional formatting — without the binary blob.

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
| Testability | Nearly impossible | Standard unit tests (147 tests) |
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

Declarative charts rendered as clean SVG.

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

### Web Preview with Auto-Reload

```bash
gridlang serve report.grid --port 8080
# Open http://localhost:8080 — updates live as you edit
```

## Project Structure

```
gridlang/
├── gridlang/
│   ├── parser.py          # .grid file parser
│   ├── schema.py          # data layer validation
│   ├── runtime.py         # compute engine (sandboxed)
│   ├── renderer.py        # HTML/SVG rendering
│   ├── formulas.py        # 59 built-in functions
│   ├── charts.py          # 9 SVG chart types
│   ├── excel_import.py    # .xlsx → .grid conversion
│   ├── excel_export.py    # .grid → .xlsx with native charts
│   ├── csv_io.py          # CSV import/export
│   ├── server.py          # live preview server
│   └── cli.py             # CLI entry point
├── spec/SPEC.md           # format specification
├── examples/              # 7 example .grid files + sample.xlsx
└── tests/                 # 147 tests
```

## License

MIT
