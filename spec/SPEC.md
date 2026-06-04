# GridLang Format Specification v1.0

## 1. Overview

GridLang (`.grid`) is an AI-native file format for structured data, designed to replace binary spreadsheet formats in AI Agent workflows. A `.grid` file is a **plain-text, human-readable** document containing four sections that separate concerns:

1. **Meta** — metadata and configuration
2. **Data** — raw data matrix
3. **Compute** — transformation logic as executable code
4. **Present** — visual presentation template

## 2. File Structure

### 2.1 Section Delimiters

Sections are separated by delimiter lines matching the pattern:

```
--- <section_name> ---
```

Rules:
- Exactly three hyphens, a space, the section name, a space, and three hyphens
- Section names are lowercase: `meta`, `data`, `compute`, `present`
- Leading/trailing whitespace on the delimiter line is ignored
- Section order MUST be: `meta` → `data` → `compute` → `present`
- All sections are REQUIRED (but may be empty)

### 2.2 Encoding

- File encoding: UTF-8 (no BOM)
- Line endings: LF (`\n`) preferred; CRLF (`\r\n`) accepted
- File extension: `.grid`
- MIME type: `text/x-gridlang`

## 3. Meta Section

The meta section contains YAML-formatted metadata.

### 3.1 Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Human-readable document name |
| `engine` | string | Compute engine identifier (`python`) |
| `version` | string | GridLang spec version (`"1.0"`) |

### 3.2 Optional Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `author` | string | — | Document author |
| `description` | string | — | Document description |
| `created` | datetime | — | Creation timestamp (ISO 8601) |
| `modified` | datetime | — | Last modification timestamp |
| `tags` | list[string] | [] | Categorization tags |
| `schema` | object | — | Data schema constraints (see §4.3) |
| `dependencies` | list[string] | [] | Additional Python packages required |

### 3.3 Example

```yaml
name: "Q1 Sales Report"
engine: python
version: "1.0"
author: "Sales Team"
description: "Quarterly sales analysis with growth projections"
tags: ["sales", "quarterly", "2024"]
dependencies: ["scipy"]
```

## 4. Data Section

The data section contains the raw data matrix in CSV format.

### 4.1 Format Rules

- Standard CSV (RFC 4180 compliant)
- First row MUST be column headers
- Column headers must be valid Python identifiers (letters, digits, underscores; no spaces)
- Empty cells are permitted (parsed as NaN for numeric columns, empty string for text)
- Quoting: use double quotes for fields containing commas, quotes, or newlines

### 4.2 Type Inference

The runtime performs automatic type inference:

| Pattern | Inferred Type |
|---------|--------------|
| Integer (e.g., `42`, `-7`) | int64 |
| Float (e.g., `3.14`, `1e-5`) | float64 |
| ISO date (e.g., `2024-01-15`) | datetime64 |
| Boolean (`true`/`false`, `yes`/`no`) | bool |
| Everything else | string (object) |

### 4.3 Optional Schema Constraints

Defined in the meta section:

```yaml
schema:
  columns:
    Revenue:
      type: float
      min: 0
      required: true
    Region:
      type: string
      enum: ["North", "South", "East", "West"]
    Date:
      type: date
      format: "%Y-%m-%d"
```

### 4.4 Example

```csv
Product,Q1,Q2,Q3,Q4,Region
Widget_A,15000,18000,22000,28000,North
Widget_B,12000,14000,16000,19000,South
Gadget_X,8000,9500,11000,14000,East
Tool_Y,20000,21000,19000,23000,West
```

### 4.5 Multi-Sheet Syntax (v0.2)

A `.grid` file may contain multiple data sections using the `data:name` syntax:

```
--- data:sheet_name ---
```

Rules:
- A single `--- data ---` is equivalent to `--- data:default ---`
- Multiple `data:name` sections are permitted; each name must be unique
- Sheet names follow the same rules as column headers (valid Python identifiers)
- Section order remains: `meta` → `data:*` → `compute` → `present`

When multiple sheets are present, the compute layer receives a `sheets` dict instead of a single DataFrame:

```python
def transform(sheets: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """
    Transform multiple data sheets.
    
    Args:
        sheets: Dictionary mapping sheet names to DataFrames.
    Returns:
        Dictionary of transformed DataFrames.
    """
    main = sheets['sales']
    targets = sheets['targets']
    main['vs_target'] = main['revenue'] - targets.set_index('region')['target']
    return {'sales': main, 'targets': targets}
```

Example multi-sheet file:

```
--- data:sales ---
Product,Revenue,Region
Widget_A,15000,North
Widget_B,12000,South

--- data:targets ---
Region,Target
North,14000
South,13000
```

## 5. Compute Section

The compute section contains Python code that defines data transformation logic.

### 5.1 Interface Contract

The compute layer communicates with the runtime through **well-defined function signatures**:

#### Required Function: `transform(df) -> DataFrame`

```python
def transform(df: pd.DataFrame) -> pd.DataFrame:
    """
    Transform the raw data.
    
    Args:
        df: The raw DataFrame from the data section.
    Returns:
        Modified DataFrame (may add/remove/modify columns).
    """
    ...
```

#### Optional Function: `aggregates(df) -> dict`

```python
def aggregates(df: pd.DataFrame) -> dict:
    """
    Compute summary statistics.
    
    Args:
        df: The transformed DataFrame (output of transform).
    Returns:
        Dictionary of named aggregate values.
    """
    ...
```

#### Optional Function: `validate(df) -> list[str]`

```python
def validate(df: pd.DataFrame) -> list[str]:
    """
    Custom validation rules.
    
    Args:
        df: The raw DataFrame before transformation.
    Returns:
        List of warning/error messages (empty = valid).
    """
    ...
```

### 5.2 Available Modules

The compute layer runs in a sandboxed environment with access to:

**Always available (no declaration needed):**
- `pandas` (as `pd`)
- `numpy` (as `np`)
- `math`
- `statistics`
- `datetime`
- `decimal`
- `collections`
- `itertools`
- `functools`
- `re`

**Available via `dependencies` declaration in meta:**
- `scipy`
- `sklearn` (scikit-learn)
- Any package listed in `meta.dependencies`

### 5.3 Restrictions

The following are **prohibited** in the compute layer:
- File I/O (`open()`, `pathlib`, file operations)
- Network access (`urllib`, `requests`, `socket`)
- System operations (`os.system`, `subprocess`, `shutil`)
- Dynamic imports (`importlib`)
- Code execution (`exec()`, `eval()`, `compile()`)

### 5.4 Example

```python
def transform(df):
    # Calculate annual totals
    quarters = ['Q1', 'Q2', 'Q3', 'Q4']
    df['Annual_Total'] = df[quarters].sum(axis=1)
    
    # Growth rate (Q4 vs Q1)
    df['Growth_Rate'] = ((df['Q4'] - df['Q1']) / df['Q1'] * 100).round(1)
    
    # Rank by total revenue
    df['Rank'] = df['Annual_Total'].rank(ascending=False).astype(int)
    
    # Performance category
    df['Category'] = df['Growth_Rate'].apply(
        lambda x: 'Star' if x > 50 else 'Stable' if x > 0 else 'Declining'
    )
    
    return df

def aggregates(df):
    return {
        'total_revenue': df['Annual_Total'].sum(),
        'avg_growth': df['Growth_Rate'].mean().round(1),
        'top_product': df.loc[df['Annual_Total'].idxmax(), 'Product'],
        'star_count': (df['Category'] == 'Star').sum(),
        'regions': df['Region'].nunique()
    }
```

## 6. Present Section

The present section is an HTML template with Jinja2 syntax for rendering the computed results.

### 6.1 Template Context

The following variables are injected into the template:

| Variable | Type | Description |
|----------|------|-------------|
| `df` | DataFrame | The transformed DataFrame |
| `agg` | dict | Output of `aggregates()` (empty dict if not defined) |
| `meta` | dict | The meta section fields |
| `raw_df` | DataFrame | The original untransformed DataFrame |

### 6.2 Template Syntax

Standard Jinja2 syntax:
- `{{ expression }}` — output a value
- `{% statement %}` — control flow (for, if, etc.)
- `{# comment #}` — template comments

### 6.3 Built-in Helpers

Available template functions:

| Function | Description |
|----------|-------------|
| `format_number(n, decimals=2)` | Locale-aware number formatting |
| `format_pct(n, decimals=1)` | Percentage formatting (e.g., "45.2%") |
| `format_currency(n, symbol="$")` | Currency formatting |
| `sparkline(series)` | Inline SVG sparkline chart |
| `bar_chart(labels, values)` | Simple SVG bar chart |
| `color_scale(value, min, max)` | CSS color based on value position |

### 6.4 Default Styling

If the present section contains no `<style>` tag, a default stylesheet is applied providing:
- Clean table styling with alternating row colors
- Responsive layout
- Print-friendly formatting
- Automatic number alignment (right-aligned)

### 6.5 Example

```html
<style>
  .dashboard { font-family: system-ui, sans-serif; max-width: 900px; margin: 0 auto; }
  .kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin: 1rem 0; }
  .kpi { background: #f8f9fa; padding: 1rem; border-radius: 8px; text-align: center; }
  .kpi-value { font-size: 2rem; font-weight: bold; color: #2563eb; }
  table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
  th, td { padding: 0.5rem; border-bottom: 1px solid #e5e7eb; text-align: left; }
  th { background: #f1f5f9; font-weight: 600; }
  .star { color: #f59e0b; }
  .declining { color: #ef4444; }
</style>

<div class="dashboard">
  <h1>{{ meta.name }}</h1>
  
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-value">{{ format_currency(agg.total_revenue) }}</div>
      <div>Total Revenue</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{{ format_pct(agg.avg_growth) }}</div>
      <div>Avg Growth</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{{ agg.top_product }}</div>
      <div>Top Product</div>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th>#</th><th>Product</th><th>Annual Total</th>
        <th>Growth</th><th>Category</th>
      </tr>
    </thead>
    <tbody>
      {% for _, row in df.iterrows() %}
      <tr>
        <td>{{ row.Rank }}</td>
        <td>{{ row.Product }}</td>
        <td>{{ format_currency(row.Annual_Total) }}</td>
        <td>{{ format_pct(row.Growth_Rate) }}</td>
        <td class="{{ row.Category|lower }}">{{ row.Category }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

## 7. Execution Model

### 7.1 Pipeline

```
┌──────────┐     ┌───────────┐     ┌──────────────┐     ┌──────────┐
│  Parse   │ ──▶ │  Validate │ ──▶ │   Compute    │ ──▶ │  Render  │
│ .grid    │     │  Schema   │     │  transform() │     │  HTML    │
└──────────┘     └───────────┘     │  aggregates()│     └──────────┘
                                   └──────────────┘
```

### 7.2 Execution Order

1. Parse the `.grid` file into four sections
2. Parse meta (YAML) and validate required fields
3. Parse data (CSV) into DataFrame with type inference
4. If `meta.schema` exists, validate data against schema
5. If `validate()` exists in compute, run it; halt on errors
6. Execute `transform(df)` → get transformed DataFrame
7. If `aggregates()` exists, execute with transformed df
8. Render present template with context variables

### 7.3 Error Handling

| Error Type | Behavior |
|-----------|----------|
| Parse error (malformed sections) | Halt, report line number |
| Schema validation failure | Halt, report violations |
| Custom validation (`validate()`) | Halt, report messages |
| Compute runtime error | Halt, report exception with traceback |
| Render template error | Halt, report template error location |

## 8. Design Principles

1. **Plain-text first** — Every layer is human-readable without tools
2. **AI-native** — Each layer uses formats AI models generate naturally
3. **Separation of concerns** — Data, logic, and presentation are independent
4. **Testable** — Compute functions can be unit-tested in isolation
5. **Version-control friendly** — Meaningful diffs in git
6. **Safe by default** — Sandboxed compute prevents malicious code
7. **Progressive complexity** — Simple files are simple; complex files are possible

## 9. MIME Type & File Association

- File extension: `.grid`
- MIME type: `text/x-gridlang`
- Magic bytes: Files starting with `--- meta ---` (after optional BOM/whitespace)

## 10. Conditional Formatting (v0.2)

The compute layer may define an optional `conditional_formats()` function that specifies cell-level formatting rules.

### 10.1 Interface

```python
def conditional_formats() -> list[dict]:
    """
    Define conditional formatting rules.
    
    Returns:
        List of rule dictionaries.
    """
    return [
        {
            "column": "Growth_Rate",
            "rule": "greater_than",
            "value": 50,
            "style": {"background": "#d4edda", "color": "#155724", "bold": True}
        },
        {
            "column": "Growth_Rate",
            "rule": "less_than",
            "value": 0,
            "style": {"background": "#f8d7da", "color": "#721c24"}
        }
    ]
```

### 10.2 Supported Rules

| Rule | Value Type | Description |
|------|-----------|-------------|
| `greater_than` | number | Cell value > value |
| `less_than` | number | Cell value < value |
| `equals` | any | Cell value == value |
| `between` | [min, max] | min <= cell value <= max |
| `color_scale` | [min_color, max_color] | Gradient based on value range |
| `data_bar` | color string | Bar width proportional to value |

### 10.3 Rule Dictionary Keys

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `column` | string | Yes | Target column name |
| `rule` | string | Yes | Rule type (see §10.2) |
| `value` | varies | Yes | Threshold value(s) |
| `style` | dict | Yes | CSS-like style properties |

### 10.4 Style Properties

Style dictionaries support the following properties:

- `background` — Background color (CSS color string)
- `color` — Text color (CSS color string)
- `bold` — Bold text (boolean)
- `italic` — Italic text (boolean)
- `border` — Border style (CSS border string)

## 11. Built-in Formula Library (v0.2)

GridLang provides 59 Excel-compatible functions that are automatically injected into the compute sandbox namespace. These functions operate on pandas Series/DataFrames and mirror the behavior of their Excel counterparts.

### 11.1 Statistical Functions

| Function | Description |
|----------|-------------|
| `SUMIF(range, criteria, sum_range)` | Sum cells matching criteria |
| `SUMIFS(sum_range, *criteria_pairs)` | Sum cells matching multiple criteria |
| `COUNTIF(range, criteria)` | Count cells matching criteria |
| `COUNTIFS(range, *criteria_pairs)` | Count cells matching multiple criteria |
| `AVERAGEIF(range, criteria, avg_range)` | Average of cells matching criteria |
| `AVERAGEIFS(avg_range, *criteria_pairs)` | Average matching multiple criteria |
| `MAXIFS(range, *criteria_pairs)` | Maximum matching criteria |
| `MINIFS(range, *criteria_pairs)` | Minimum matching criteria |
| `MEDIAN(range)` | Median value |
| `STDEV(range)` | Standard deviation (sample) |
| `VAR(range)` | Variance (sample) |
| `PERCENTILE(range, k)` | k-th percentile |
| `RANK(value, range, order)` | Rank of value in range |

### 11.2 Lookup Functions

| Function | Description |
|----------|-------------|
| `VLOOKUP(value, table, col_index, approx)` | Vertical lookup |
| `HLOOKUP(value, table, row_index, approx)` | Horizontal lookup |
| `XLOOKUP(lookup, lookup_range, return_range, not_found, match_mode)` | Modern lookup (exact/wildcard/binary) |
| `INDEX(range, row, col)` | Value at row/col intersection |
| `MATCH(value, range, match_type)` | Position of value in range |
| `OFFSET(ref, rows, cols, height, width)` | Reference offset by rows/cols |
| `INDIRECT(ref_string)` | Evaluate text as reference |

### 11.3 Text Functions

| Function | Description |
|----------|-------------|
| `LEFT(text, n)` | First n characters |
| `RIGHT(text, n)` | Last n characters |
| `MID(text, start, n)` | Substring from position |
| `LEN(text)` | String length |
| `TRIM(text)` | Remove leading/trailing spaces |
| `UPPER(text)` | Convert to uppercase |
| `LOWER(text)` | Convert to lowercase |
| `PROPER(text)` | Title case |
| `SUBSTITUTE(text, old, new, instance)` | Replace text |
| `CONCATENATE(*args)` | Join strings |
| `TEXT(value, format_str)` | Format value as text |
| `FIND(find_text, within_text, start)` | Find position (case-sensitive) |
| `SEARCH(find_text, within_text, start)` | Find position (case-insensitive) |

### 11.4 Date Functions

| Function | Description |
|----------|-------------|
| `YEAR(date)` | Extract year |
| `MONTH(date)` | Extract month |
| `DAY(date)` | Extract day |
| `TODAY()` | Current date |
| `NOW()` | Current datetime |
| `DATEDIF(start, end, unit)` | Difference between dates |
| `EOMONTH(start, months)` | End of month offset |
| `NETWORKDAYS(start, end, holidays)` | Working days between dates |
| `WEEKDAY(date, return_type)` | Day of week |

### 11.5 Logic Functions

| Function | Description |
|----------|-------------|
| `IF(condition, true_val, false_val)` | Conditional value |
| `IFS(*condition_value_pairs)` | Multiple conditions |
| `SWITCH(expr, *case_value_pairs, default)` | Match expression to cases |
| `AND(*conditions)` | All conditions true |
| `OR(*conditions)` | Any condition true |
| `NOT(condition)` | Negate condition |
| `IFERROR(value, error_val)` | Handle errors |
| `IFNA(value, na_val)` | Handle NA values |

### 11.6 Math Functions

| Function | Description |
|----------|-------------|
| `ROUND(value, decimals)` | Round to n decimal places |
| `ROUNDUP(value, decimals)` | Round up (away from zero) |
| `ROUNDDOWN(value, decimals)` | Round down (toward zero) |
| `CEILING(value, significance)` | Round up to nearest multiple |
| `FLOOR(value, significance)` | Round down to nearest multiple |
| `MOD(value, divisor)` | Modulo remainder |
| `ABS(value)` | Absolute value |
| `POWER(base, exponent)` | Exponentiation |

### 11.7 Data Analysis Functions

| Function | Description |
|----------|-------------|
| `PIVOT(df, values, index, columns, aggfunc)` | Create pivot table |
| `SORT(range, col, order)` | Sort data by column |
| `FILTER(range, criteria)` | Filter rows by criteria |
| `UNIQUE(range)` | Unique values |
| `GROUPBY(df, by, agg)` | Group and aggregate |
| `TRANSPOSE(range)` | Transpose rows/columns |

### 11.8 Usage

All functions are auto-injected into the compute namespace. No imports are required:

```python
def transform(df):
    df['High_Revenue'] = COUNTIF(df['Revenue'], '>10000')
    df['Region_Lookup'] = VLOOKUP(df['Code'], ref_table, 2, False)
    df['Year'] = YEAR(df['Date'])
    df['Label'] = IF(df['Growth'] > 0, 'Growing', 'Declining')
    df['Rounded'] = CEILING(df['Revenue'], 1000)
    
    summary = PIVOT(df, values='Revenue', index='Region', 
                    columns='Quarter', aggfunc='sum')
    return df
```

## 12. Charts (v0.2)

GridLang provides 9 SVG chart types available as template functions in the present section. All charts render as inline SVG elements and are fully responsive.

### 12.1 Available Chart Types

| Function | Description |
|----------|-------------|
| `sparkline(series, width, height, color)` | Inline mini line chart |
| `bar_chart(labels, values, **opts)` | Vertical bar chart |
| `line_chart(x, y, **opts)` | Line chart with optional markers |
| `pie_chart(labels, values, **opts)` | Pie/donut chart |
| `scatter_chart(x, y, **opts)` | Scatter plot |
| `area_chart(x, y, **opts)` | Filled area chart |
| `stacked_bar_chart(labels, datasets, **opts)` | Stacked bar chart |
| `heatmap(matrix, x_labels, y_labels, **opts)` | Color-coded matrix |
| `color_scale(value, min, max, **opts)` | Single-cell color indicator |

### 12.2 Common Options

All chart functions accept the following keyword arguments:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `width` | int | 400 | Chart width in pixels |
| `height` | int | 300 | Chart height in pixels |
| `title` | string | None | Chart title |
| `colors` | list[string] | theme | Color palette |
| `legend` | bool | True | Show legend |
| `animate` | bool | False | Enable CSS animations |

### 12.3 Example Usage

```html
<div class="charts">
  <!-- Sparklines in table cells -->
  {% for _, row in df.iterrows() %}
  <tr>
    <td>{{ row.Product }}</td>
    <td>{{ sparkline([row.Q1, row.Q2, row.Q3, row.Q4], width=80, height=20) }}</td>
  </tr>
  {% endfor %}

  <!-- Full bar chart -->
  {{ bar_chart(df['Product'].tolist(), df['Annual_Total'].tolist(),
               title="Revenue by Product", colors=["#2563eb"]) }}

  <!-- Pie chart -->
  {{ pie_chart(df['Region'].tolist(), df['Revenue'].tolist(),
               title="Revenue by Region") }}

  <!-- Heatmap -->
  {{ heatmap(df[['Q1','Q2','Q3','Q4']].values,
             x_labels=['Q1','Q2','Q3','Q4'],
             y_labels=df['Product'].tolist(),
             title="Quarterly Performance") }}
</div>
```

## 13. Import/Export (v0.2)

GridLang supports importing from and exporting to common spreadsheet formats.

### 13.1 CSV Import/Export

```bash
# Import CSV to .grid (creates minimal file with data section populated)
gridlang import data.csv -o output.grid

# Export data section to CSV
gridlang export report.grid -f csv -o output.csv
```

### 13.2 Excel Import

```bash
gridlang import workbook.xlsx -o output.grid
```

Import capabilities:
- **Formula conversion** — Excel formulas are translated to equivalent Python compute code
- **Style extraction** — Cell formatting is converted to present layer CSS and conditional_formats()
- **Multi-sheet** — Each worksheet becomes a `data:sheet_name` section
- **Named ranges** — Preserved as comments in the compute layer

### 13.3 Excel Export

```bash
gridlang export report.grid -f xlsx -o output.xlsx
```

Export capabilities:
- **Native charts** — SVG charts are converted to native Excel chart objects
- **Conditional formatting** — `conditional_formats()` rules become Excel conditional formatting
- **Frozen headers** — First row is automatically frozen as header
- **Styled output** — Present layer styles mapped to Excel cell formats where applicable

### 13.4 Round-Trip Support

GridLang supports round-trip conversion:

```bash
# Excel → GridLang → Excel (lossless for supported features)
gridlang import original.xlsx -o working.grid
# ... edit the .grid file ...
gridlang export working.grid -f xlsx -o updated.xlsx
```

Round-trip guarantees:
- Data values are preserved exactly
- Column types are maintained
- Conditional formatting rules survive the round-trip
- Chart types are mapped to closest equivalent

## 14. Live Preview Server (v0.2)

GridLang includes a built-in development server for live preview during editing.

### 14.1 Usage

```bash
gridlang serve file.grid --port 8080
```

### 14.2 Features

- **Auto-reload** — Browser automatically refreshes when the `.grid` file changes on disk
- **File watching** — Uses filesystem events (inotify/FSEvents/kqueue) for instant detection
- **Error overlay** — Parse/compute/render errors displayed as overlay without losing last good state
- **Hot recompute** — Only re-executes changed layers (e.g., editing present layer skips recompute)

### 14.3 Options

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | 8080 | Server port |
| `--host` | localhost | Bind address |
| `--no-open` | false | Don't auto-open browser |
| `--watch-dir` | file's dir | Additional directories to watch |
| `--debounce` | 300ms | Debounce delay for rapid changes |

### 14.4 Example Workflow

```bash
# Start the preview server
gridlang serve dashboard.grid --port 3000

# In another terminal, edit the file
# Browser updates automatically on every save
```

## 15. Future Extensions (v2.0 Roadmap)

- ~~**Multi-sheet** — Multiple data sections in one file (`--- data:sheet_name ---`)~~ ✓ DONE (v0.2, see §4.5)
- ~~**Chart DSL** — Simplified chart declaration syntax within present layer~~ ✓ DONE (v0.3, see §16)
- ~~**Remote data sources** — `@source` directive at top of data sections~~ ✓ DONE (v0.4, see §17)
- ~~**Reactive bindings** — Two-way binding between present layer edits and data layer~~ ✓ DONE (v0.5, see §18)
- ~~**JavaScript engine** — Alternative compute engine for browser-native execution~~ ✓ DONE (v0.6, see §19)
- ~~**Collaborative editing** — CRDT-based real-time collaboration protocol~~ ✓ DONE (v0.8, see §21)

## 16. Chart & Format DSL (v0.3)

The present layer supports a declarative block syntax for charts and conditional
formatting that compiles to the corresponding Jinja2 helper calls. This is the
preferred way for AI agents to author visualizations because it does not require
constructing template expressions.

### 16.1 Block Syntax

A block begins with a top-level line `chart: TYPE` or `format: TYPE` and is
followed by one or more indented `key: value` lines. The block ends at the first
line that is non-blank and indented at or below the head:

```
chart: bar
  data: agg.region_revenue
  labels: agg.region_labels
  title: "Revenue by Region"
  width: 500
  height: 280
```

Two consecutive blank lines also end a block. Continuation of a long value is
allowed by indenting subsequent lines further than the head.

### 16.2 Supported `chart:` Types

| Type           | Compiles to            | Required keys     |
|----------------|-----------------------|-------------------|
| `bar`          | `bar_chart`           | `data`, `labels`  |
| `line`         | `line_chart`          | `data`, `labels`  |
| `pie`          | `pie_chart`           | `data`, `labels`  |
| `scatter`      | `scatter_chart`       | `x`, `y`          |
| `area`         | `area_chart`          | `data`, `labels`  |
| `stacked_bar`  | `stacked_bar_chart`   | `data` (multi-col), `labels` |
| `heatmap`      | `heatmap`             | `data` (DataFrame slice / A1) |
| `sparkline`    | `sparkline`           | `data`            |
| `color_scale`  | `color_scale`         | `value`           |

All other keys (e.g. `title`, `width`, `height`, `color`, `colors`, `show_values`)
become keyword arguments to the underlying chart function. Quoted strings are
forwarded as-is.

### 16.3 Reference Resolution

Values inside a block are resolved against the runtime template context:

| Form             | Resolves to                                    |
|------------------|------------------------------------------------|
| `42` / `3.14`    | numeric literal                                |
| `"hello"`        | string literal                                 |
| `[a, b, c]`      | list literal (each element re-resolved)        |
| `agg.foo`        | `agg['foo']` from compute aggregates           |
| `meta.foo`       | `meta['foo']` from meta section                |
| `Q1,Q2,Q3,Q4`    | dict-of-series (line/area/stacked) or concat   |
| `Revenue`        | `df['Revenue'].tolist()` (single column)       |
| `sales!Revenue`  | `sheets['sales']['Revenue'].tolist()`          |
| `B2:D4`          | A1 range as flat list (or DataFrame for heatmap) |
| `B2:D4@sales`    | A1 range scoped to a named sheet               |
| `B2@sales`       | single A1 cell, must be sheet-qualified        |

A1 row indices follow Excel convention: row 1 is the header, row 2 is the first
data row. Column letters follow Excel: A=0, B=1, …, Z=25, AA=26.

Bare A1 cell references like `B2` are NOT auto-resolved — they would shadow real
column names. Cells must be qualified with `@sheet` to disambiguate.

### 16.4 Supported `format:` Types

| Type            | Behavior                                                          |
|-----------------|-------------------------------------------------------------------|
| `color_scale`   | Gradient color between `min_color` and `max_color`                |
| `data_bar`      | Bar width proportional to value                                   |
| `greater_than`  | Apply style when cell > value                                     |
| `less_than`     | Apply style when cell < value                                     |
| `equals`        | Apply style when cell == value                                    |
| `between`       | Apply style when value ≤ cell ≤ value2                            |
| `rules`         | Multiple inline rules: `rule: ">90 -> bold green"`                |

Common keys: `column`, `range`, `value`, `value2`, `style`, `min_color`,
`max_color`. The shorthand `min:`/`max:` selects between numeric bound and color
based on whether the value parses as a hex/quoted string.

The `format: rules` form accepts any number of `rule:` lines with the inline
syntax `<op> <value> -> <style words>`. Recognized color words (`red`, `green`,
`yellow`) map to the renderer's `highlight-*` CSS classes.

### 16.5 Interaction with Runtime Conditional Formats

DSL `format:` blocks are merged with the `conditional_formats()` function from
the compute layer; both contribute `ConditionalFormat` objects to the same list.
Style classes and inline styles are emitted via `cond_class(col, value)` and
`cond_style(col, value)` template helpers.

### 16.6 Example

```
--- present ---
chart: bar
  data: agg.region_revenue
  labels: agg.region_labels
  title: "Q1 Sales by Region"

chart: heatmap
  data: B2:D10
  title: "Correlation Matrix"

format: color_scale
  range: B2:B100
  min_color: "#ffffff"
  max_color: "#e74c3c"

format: rules
  range: D2:D50
  rule: ">90 -> bold green"
  rule: "<60 -> italic red"
```

### 16.7 Backward Compatibility

Files written before v0.3 that use explicit Jinja2 chart calls
(`{{ bar_chart(...) }}`) continue to work unchanged — the DSL preprocessor only
acts on lines that match the `chart:` / `format:` head pattern.

## 17. Remote Data Sources (v0.4)

A data section may begin with one or more `@directive: value` lines that load the
data from an external location instead of (or in addition to) the inline CSV. The
inline CSV becomes a fallback used when the remote source is unavailable or
disabled.

### 17.1 Syntax

```
--- data ---
@source: https://api.example.com/sales.csv
@format: csv               # csv | tsv | json | xlsx | auto (default)
@cache: 1h                 # 30s | 5m | 1h | 7d | 0 / off
@timeout: 10               # seconds, default 15
@header: Authorization: Bearer xyz   # repeatable
@select: data.records      # JSON dot-path drilldown
@encoding: utf-8           # default utf-8
@sheet: Sheet1             # for xlsx sources

# Inline fallback (used when remote denied / failed)
Region,Total
North,100
South,95
```

A line counts as part of the directive zone if it is a `@key: value` line, blank,
or a `#` comment. The first non-directive content line (and everything after it)
becomes the inline CSV body.

A pure-CSV data section with no `@` directives behaves identically to v0.3.

### 17.2 Directive Reference

| Directive    | Type       | Default | Notes                                              |
|--------------|-----------|---------|---------------------------------------------------|
| `@source`    | URL       | —       | Required to enable remote loading. See §17.4 for schemes. |
| `@format`    | enum      | `auto`  | `csv` / `tsv` / `json` / `xlsx` / `auto` (sniff from URL) |
| `@cache`     | duration  | `1h`    | TTL of the on-disk cache. `0` / `off` disables. |
| `@timeout`   | float (s) | `15`    | Network timeout per request.                      |
| `@header`    | string    | —       | `Name: Value` header sent with the request. Repeatable. |
| `@select`    | dot-path  | —       | JSON-only: drill into a sub-path before parsing. Supports `a.b.c[0].d`. |
| `@encoding`  | string    | `utf-8` | Text decoding charset.                            |
| `@sheet`     | string    | first   | XLSX-only: which worksheet to read.               |

### 17.3 Format Detection (`auto`)

The `@source` URL extension determines the format:

| Extension                 | Format |
|---------------------------|--------|
| `.csv`                    | csv    |
| `.tsv`                    | tsv    |
| `.json`                   | json   |
| `.xlsx` / `.xls`          | xlsx   |
| (anything else / no ext)  | csv    |

### 17.4 Allowed Schemes & Safety Model

| Scheme       | Always allowed? | Notes                                            |
|--------------|----------------|---------------------------------------------------|
| `file://`    | yes             | Local file. Useful for fixtures and tests.       |
| `http://`    | only with opt-in | Requires `--allow-remote` (CLI) or `allow_remote=True` (API). |
| `https://`   | only with opt-in | Same as above.                                   |
| (other)      | rejected        | Raises a `DataSourceError` at parse time.        |

When `http(s)` access is denied:
* If an inline CSV fallback is present, it is used and labeled `fallback:<url>`.
* Otherwise a `DataSourceError` is raised demanding `--allow-remote`.

When `http(s)` access is enabled but the fetch fails:
* If an inline fallback is present, it is used and labeled `fallback:<url>` with
  the underlying error in parentheses.
* Otherwise the original error is propagated.

This model ensures that a `.grid` file never silently issues network requests
unless the user has explicitly opted in.

### 17.5 Caching

Successful `http(s)` fetches are written to a content-addressed cache keyed by
SHA1(`source` + sorted `headers` + `format` + `select`). The cache directory
defaults to `~/.cache/gridlang/sources/` and may be overridden by the
`GRIDLANG_CACHE_DIR` environment variable. Cache entries older than the
`@cache` TTL are ignored. `file://` fetches are not cached — the file system is
already the cache.

The CLI flag `--no-cache` (on `run` / `render`) bypasses the persistent cache
for a single invocation.

### 17.6 JSON `@select`

For JSON sources, `@select` walks a dot-path before passing the result to
`pd.DataFrame`:

| Selector form | Operation                                |
|---------------|------------------------------------------|
| `a.b.c`       | `obj['a']['b']['c']` (object keys)       |
| `items[2]`    | `obj['items'][2]` (array index)          |
| `data.records[0].fields` | mixed traversal              |

The selected value should be:
* a list of dicts → becomes a multi-row DataFrame (typical case),
* a single dict → becomes a one-row DataFrame,
* a list of scalars → becomes a one-column `value` DataFrame.

### 17.7 Multi-Sheet Support

Each `--- data:name ---` section has its own directive block:

```
--- data:sales ---
@source: https://api/sales.csv
Region,Total
Inline,0

--- data:targets ---
@source: file://./targets.csv
```

`load_dataframes(doc, allow_remote=...)` resolves all sheets in one pass.

### 17.8 CLI Integration

```bash
# Fetch enabled
gridlang run report.grid --allow-remote

# Force cache miss
gridlang render dashboard.grid --allow-remote --no-cache -o out.html

# Live preview with remote fetch
gridlang serve dashboard.grid --allow-remote
```

`gridlang validate` and `gridlang info` print declared `@source` URLs without
fetching them, so static checks remain network-free.

### 17.9 Programmatic API

```python
from gridlang.parser import parse_file
from gridlang.data_sources import load_dataframes

doc = parse_file('report.grid')
sheets, source_labels = load_dataframes(doc, allow_remote=True)
print(source_labels)  # {'default': 'remote:https://api/x.csv'}
```

## 18. Reactive Bindings (v0.5)

The reactive bindings layer makes individual cells in the data section editable
from the rendered preview. Edits made in the browser are POSTed back to the
local server, applied in-place to the `.grid` source, and trigger a re-render.

### 18.1 Two binding forms

#### Inline `cell()` helper

```html
<td>{{ cell("B2") }}</td>
<td>{{ cell("B2@sales") }}</td>          <!-- cross-sheet -->
<td>{{ cell("B2", fmt=",.2f") }}</td>     <!-- formatted -->
<td>{{ cell("B2", editable=False) }}</td> <!-- read-only span -->
```

The helper resolves the cell value from the **raw** data section (not the
post-compute DataFrame), so edits round-trip predictably even when `transform()`
is non-trivial.  It emits:

```html
<span data-grid-cell="B2" class="grid-cell"
      contenteditable="true" role="textbox" spellcheck="false">100</span>
```

The header row (row 1) is always rendered read-only, regardless of `editable=`.
Out-of-range or unknown-sheet references render as `<span class="grid-cell-error">#REF!</span>`.

#### `bind:` DSL block

Form-style binding, parallel to `chart:` and `format:`:

```
bind: input
  cell: B2
  label: "Unit Price"
  type: number
  min: 0
  step: 0.10

bind: select
  cell: A2
  label: "Region"
  options: North, South, East, West

bind: checkbox
  cell: D2

bind: textarea
  cell: E2
  placeholder: "Notes..."
```

Supported kinds: `input`, `select`, `checkbox`, `textarea`. The `cell:` key is
mandatory; everything else is optional. Validation happens at preprocess time
— a malformed A1 reference fails fast, never at edit time.

### 18.2 A1 cell reference grammar

```
ref       := col_letters digit+ ("@" sheet_name)?
col_letters := [A-Z]+         (case-insensitive; A=1, B=2, ..., AA=27)
digit     := [0-9]            (1-based; row 1 is the header)
sheet_name := [A-Za-z][\w]*    (Python identifier)
```

Cell ranges (`B2:D4`) are reserved for chart DSL and **not** accepted in
bindings — only single cells.

### 18.3 Wire protocol

**Endpoint:** `POST /api/cell-edit`

**Request (A1 form):**
```json
{
  "cell": "B2",
  "value": "120",
  "sheet": null,
  "save": true
}
```

| Field   | Type    | Required | Description                                         |
|---------|---------|----------|-----------------------------------------------------|
| `cell`  | string  | yes      | A1 cell reference (`B2` or `B2@sales`)              |
| `value` | any     | yes      | New cell value; converted to string via `str()`     |
| `sheet` | string  | no       | Explicit sheet override (wins over `@sheet` in ref) |
| `save`  | boolean | no       | If true, write the updated source back to disk      |
| `content` | string | no      | Edit against this content instead of the disk file  |

**Response:**
```json
{
  "content": "<full updated .grid source>",
  "html":    "<re-rendered HTML fragment>",
  "error":   null
}
```

**Legacy form** (still supported for the existing editor UI):
```json
{
  "content": "...",
  "row":     0,
  "col":     "Revenue",
  "value":   "999"
}
```

### 18.4 Server-side edit semantics

`apply_edit()` rewrites **only the target CSV row**. Everything else is
preserved byte-for-byte:

* meta / compute / present sections — untouched
* `@directive` blocks (`@source`, `@cache`, etc.) — untouched
* `#`-prefixed comment lines — untouched
* blank lines inside the data section — untouched
* other CSV rows — untouched
* formulas in other cells of the same row — untouched

The target value is CSV-escaped per RFC 4180: only quoted when it contains
`,`, `"`, leading/trailing whitespace, or `\n`.

The header row (row 1) is **never** editable; attempts to write to row 1
raise `BindingError` and return HTTP 400.

### 18.5 Security model

* `gridlang serve` exposes `/api/cell-edit` only when bound to `127.0.0.1`.
* `save: true` writes the file. The server runs with the user's privileges; do
  not expose `gridlang serve` to untrusted networks.
* For preview-only mode (no file writes), pass `save: false` from the client —
  edits still update the in-memory source for the live preview but never touch
  disk.
* The legacy `{content, row, col}` shape lets clients pre-stage an edit chain
  in memory; the server uses the supplied `content` rather than re-reading
  disk.

### 18.6 Client-side JS

`gridlang serve --edit` injects `bindings.client_js()` into the preview page.
The script wires every `[data-grid-cell]` and `[data-grid-bind]` element to
the API:

* `data-grid-cell` (inline cells) — commit on `blur` or `Enter`; cancel on
  `Esc`.  Reloads the page after a successful save so the rest of the
  document re-renders against the new value.
* `data-grid-bind` (form widgets) — commit on `change`. The wrapper carries
  `data-grid-bind-current` so the widget initializes to the current value.

### 18.7 Programmatic API

```python
from gridlang.bindings import apply_edit, parse_a1_ref, preprocess

src = open('report.grid').read()

# Apply a single edit
new_src = apply_edit(src, cell='B2', value='999')
new_src = apply_edit(src, cell='B2@sales', value='999')

# Parse an A1 reference
row, col, sheet = parse_a1_ref('AB10@costs')   # (10, 28, 'costs')

# Pre-scan a present layer for bind: blocks
result = preprocess(template_text)
for d in result.bindings:
    print(d.cell, d.kind, d.label)
```

### 18.8 Backward compatibility

A `.grid` file that uses neither `cell()` nor `bind:` renders byte-identically
to v0.4. The `client_js()` script is only injected when the preview HTML
contains `data-grid-cell` or `data-grid-bind` markers.

## 19. JavaScript Engine (v0.6)

The compute layer can be authored in JavaScript instead of Python by setting
``engine: javascript`` in the ``meta`` section. The two engines are
behaviorally equivalent — same hooks, same ``ExecutionResult`` — but JS code
is executed in a Node subprocess via JSON IPC, isolated from the Python host.

### 19.1 Selecting the engine

```yaml
--- meta ---
name: "Quarterly Sales"
engine: javascript    # or: python (default)
version: "1.0"
```

The parser accepts both ``python`` and ``javascript``. Anything else raises
``ParseError``. Multi-engine documents are not supported — one engine per file.

### 19.2 User-defined hooks

The same four hook names as the Python engine, but written as top-level
JavaScript functions:

| Hook                      | Signature                  | Purpose                      |
|---------------------------|----------------------------|------------------------------|
| `validate(df)`            | returns `string[]`         | non-empty result halts run   |
| `transform(df)`           | returns `df` (records)     | single-sheet transform       |
| `transform(sheets)`       | returns `sheets` object    | multi-sheet (param-name dispatch) |
| `aggregates(df)`          | returns `object`           | KPIs / summary dict          |
| `conditional_formats()`   | returns `rule[]`            | declarative styling          |

Multi-vs-single-sheet detection mirrors the Python engine: name the first
parameter ``sheets`` (or ``dfs``) to receive the multi-sheet dict.

### 19.3 Data shape

The DataFrame is exposed as **array-of-records** (one object per row):

```js
df === [
  { Region: 'North', Revenue: 100, Profit: 30 },
  { Region: 'South', Revenue: 200, Profit: 60 }
]
```

A small helper API is auto-attached (non-enumerable, so `JSON.stringify(df)`
remains clean):

| Method                 | Description                                               |
|------------------------|-----------------------------------------------------------|
| `df.col(name)`         | array of values for one column                            |
| `df.sum(name)`         | sum of a numeric column                                   |
| `df.mean(name)`        | mean of a numeric column                                  |
| `df.max(name)`         | max of a numeric column                                   |
| `df.min(name)`         | min of a numeric column                                   |
| `df.row(i)`            | the i-th record                                           |
| `df.where(predicate)`  | new df filtered by a row predicate                        |
| `df.addColumn(n, fn)`  | mutate-in-place; `fn(row, index)` returns the cell value  |
| `df.columns`           | array of column names                                     |
| `df.shape`             | `[rows, cols]`                                            |

Returning a plain array of objects is sufficient — the runtime re-wraps it on
the way out.

### 19.4 Sandbox

The bridge runs user code with ``vm.runInNewContext`` against a curated globals
object:

* **Available**: `Math`, `JSON`, `Number`, `String`, `Boolean`, `Array`,
  `Object`, `Date`, `isFinite`, `isNaN`, `parseFloat`, `parseInt`, `Map`, `Set`,
  `WeakMap`, `WeakSet`, `Symbol`, `Error`/`TypeError`/`RangeError`,
  `SyntaxError`, `Promise`, plus a no-op `console.{log,warn,error}`.
* **Blocked**: `require`, `process`, `Buffer`, `setTimeout`, `setImmediate`,
  `setInterval`, `fs`, `child_process`, `globalThis` (replaced by the sandbox),
  filesystem and network access.
* **Memory**: Node is launched with `--max-old-space-size=256` so a runaway
  allocation cannot starve the host.
* **CPU**: `vm.runInContext({timeout})` cuts user code at the per-stage
  timeout (default 5000 ms; override via `GRIDLANG_JS_TIMEOUT_MS`).
* **Wall clock**: the whole subprocess is killed after
  `GRIDLANG_JS_PROCESS_TIMEOUT_S` seconds (default 15).

Attempting to use a blocked global raises a ReferenceError, which the bridge
re-raises as a Python `RuntimeError_` with the original message preserved.

### 19.5 Wire protocol

Internal — implementations may rely on it but `.grid` authors should not. See
`gridlang/js_runtime.py`. Briefly:

```json
// stdin
{ "code": "...", "df": [...], "sheets": {...},
  "is_multi_sheet": bool, "timeout_ms": 5000 }

// stdout (one line)
{ "df": [...], "sheets": {...}, "aggregates": {...},
  "conditional_formats": [...], "validation_messages": [...],
  "found_functions": [...], "error": null | "<message>" }
```

### 19.6 Conditional formats

The `conditional_formats()` hook returns a list of plain objects:

```js
function conditional_formats() {
  return [
    { column: 'Growth', rule: 'greater_than', value: 30, style: 'highlight-green' },
    { column: 'Growth', rule: 'less_than',    value: 0,  style: 'highlight-red'   },
  ];
}
```

Same rule grammar as the Python engine — see §10. The objects are converted
to runtime `ConditionalFormat` records by the bridge.

### 19.7 When Node is missing

If `node` is not on `$PATH`, the runtime raises
`gridlang.js_runtime.JsRuntimeUnavailable` (a subclass of `RuntimeError_`).
The error message points to https://nodejs.org/. Tools that want a graceful
degradation can catch this exception and fall back to a static rendering.

### 19.8 Programmatic API

```python
from gridlang.runtime import execute             # engine-aware dispatcher
from gridlang.js_runtime import execute_js, is_node_available

if is_node_available():
    result = execute_js(js_code, df)             # direct call
    # or, route through the dispatcher:
    result = execute(js_code, df, engine='javascript')
```

### 19.9 Choosing an engine

| Reason                              | Pick     |
|-------------------------------------|----------|
| Default behaviour, full pandas API  | python   |
| You're sharing with frontend devs   | javascript |
| You want pure-text dependencies     | javascript (`engine: javascript` requires only Node) |
| You need numpy / scipy / statsmodels| python   |
| You'll later run the same code in the browser | javascript |

The two engines are **not** code-compatible — `transform()` in one is not
runnable by the other. Pick at authoring time and stick with it for the
lifetime of the file.

## 20. JS Engine — Extended API & Bundling (v0.7)

v0.7 expands the JavaScript engine in three directions: a richer pandas-ish
helper API on `df`, a CLI command that produces self-contained JS bundles,
and a clean separation of the JS source files so external consumers (browser
bundles, custom hosts) can pick up the same runtime.

### 20.1 Expanded df helper API

The helper prelude (loaded from `gridlang/js/df_helpers.js`) now exposes ~25
methods grouped by purpose. All are non-enumerable so `JSON.stringify(df)`
remains clean, and chainable where it makes sense.

**Column / row access**

| Method                     | Description                                         |
|----------------------------|-----------------------------------------------------|
| `df.col(name)`             | array of values for one column                      |
| `df.row(i)`                | i-th record                                         |
| `df.pluck(...names)`       | new df with only the named columns                  |
| `df.drop(...names)`        | new df without the named columns                    |
| `df.rename({old: new, …})` | new df with columns renamed                         |

**Aggregations**

| Method                  | Description                                            |
|-------------------------|--------------------------------------------------------|
| `df.count()`            | row count                                              |
| `df.sum(name)`          | sum of a numeric column (skips NaN)                    |
| `df.mean(name)`         | arithmetic mean                                        |
| `df.max(name)`          | maximum value, or `null` for empty / all-NaN           |
| `df.min(name)`          | minimum value, or `null`                               |
| `df.variance(name)`     | sample variance (n-1 denominator)                      |
| `df.std(name)`          | sample standard deviation                              |
| `df.median(name)`       | 50th percentile                                        |
| `df.quantile(name, q)`  | linear-interpolated quantile, q∈[0,1]                  |
| `df.describe()`         | per-column `{count, mean, std, min, q25, q50, q75, max}` |

**Filtering / slicing**

| Method                  | Description                                            |
|-------------------------|--------------------------------------------------------|
| `df.where(predicate)`   | new df filtered by `(row) => bool`                     |
| `df.head(n=5)`          | first n rows                                           |
| `df.tail(n=5)`          | last n rows                                            |
| `df.slice(start, end)`  | slice by index (Array.prototype.slice semantics)       |
| `df.distinct(name?)`    | rows unique by one column or by full record           |
| `df.find(pred)`         | first matching record (or `undefined`)                 |
| `df.some(pred)`         | true if any row matches                                |
| `df.every(pred)`        | true if all rows match                                 |
| `df.none(pred)`         | true if no rows match                                  |

**Sorting / grouping**

| Method                              | Description                                |
|-------------------------------------|--------------------------------------------|
| `df.sortBy(name, {desc?})`          | numeric-aware, falls back to string compare |
| `df.groupBy(name)`                  | object: `{ groupKey: df, … }`              |
| `df.countBy(name)`                  | object: `{ groupKey: count, … }`           |

**Mutations**

| Method                              | Description                                |
|-------------------------------------|--------------------------------------------|
| `df.addColumn(name, fn)`            | add a single column in place               |
| `df.assign({col: fn, col2: …})`     | add multiple columns at once               |

**Joins**

| Method                                  | Description                                |
|-----------------------------------------|--------------------------------------------|
| `df.join(right, key, {rightKey?})`      | inner join on shared key                   |
| `df.leftJoin(right, key, {rightKey?})`  | left join (missing rights are dropped from row) |
| `df.concat(other)`                      | row-wise concat                            |

**Conversion / metadata**

| Property / method     | Description                                      |
|-----------------------|--------------------------------------------------|
| `df.columns`          | array of column names                            |
| `df.shape`            | `[rows, cols]`                                   |
| `df.empty`            | `true` if length is 0                            |
| `df.toRecords()`      | plain `Array.from(df)`                           |
| `df.toCSV()`          | RFC-4180-quoted CSV string                       |

### 20.2 The `gridlang js-bundle` command

Bundle a `.grid` file (whose meta declares `engine: javascript`) into a
self-contained JS file that runs anywhere a JS engine does — without
gridlang, without Python, without npm.

```bash
# Default: produce a Node bundle
gridlang js-bundle report.grid -o bundle.js
node bundle.js                   # prints pipeline result as JSON

# Browser / Web-Worker bundle
gridlang js-bundle report.grid --browser -o worker.js

# Smaller (no JSON pretty-printing)
gridlang js-bundle report.grid --minify -o tight.js

# Stream to stdout (e.g. piped into another tool)
gridlang js-bundle report.grid | node
```

#### Node bundle (`--browser` not set)

Embeds:

* the data section (post-`@source` resolution) as `const REQUEST = {...}`
* `df_helpers.js` (the makeDF prelude)
* `runtime_pipeline.js` (the engine-agnostic runner)
* the user's compute layer wrapped in an IIFE that re-exports
  `validate / transform / aggregates / conditional_formats` to a scope object
* a `process.stdout.write(JSON.stringify(...))` tail

Running it produces the same JSON that the in-process bridge would produce.

#### Browser bundle (`--browser`)

Same payload, different glue. The IIFE attaches itself to the host (`self`,
`globalThis`, or `this`), exposes `runGridLangPipeline()` as a callable, and
listens on `self.addEventListener('message', …)` so the file works as-is when
loaded in a Worker:

```js
const blob = new Blob([bundleSource], {type: 'application/javascript'});
const w = new Worker(URL.createObjectURL(blob));
w.onmessage = e => render(e.data);
w.postMessage({});      // run the embedded pipeline
```

Or as a `<script>` tag for in-page execution:

```html
<script src="report.bundle.js"></script>
<script>
  const result = runGridLangPipeline();
  console.log(result.aggregates);
</script>
```

### 20.3 Source-file layout

The JS runtime sources are no longer inlined as Python strings; they ship
as plain `.js` files alongside the package:

```
gridlang/js/
├── df_helpers.js        — makeDF helper prelude (≈ 200 lines)
├── runtime_pipeline.js  — engine-agnostic pipeline runner
└── bridge_node.js       — Node stdin/stdout bridge for in-process calls
```

These are picked up automatically via package data. External consumers can
read them via:

```python
from gridlang.js_runtime import get_helpers_source, get_bridge_source
from gridlang.js_bundle  import get_pipeline_source
```

### 20.4 Programmatic API

```python
from gridlang.js_bundle import bundle_file, bundle_doc

# From disk
result = bundle_file('report.grid', target='node')
open('bundle.js', 'w').write(result.source)

# From a parsed document — useful when post-processing the data.
from gridlang.parser import parse_string
doc = parse_string(grid_text)
result = bundle_doc(doc, target='browser', pretty=False)
```

The returned `BundleResult` carries `source`, `target`, `bytes`, and
`sheet_count`. `target='browser'` requires the document to declare
`engine: javascript`; otherwise `bundle_doc` raises `ValueError`.

### 20.5 Determinism

A bundle is deterministic for a given (`.grid` source, gridlang version)
pair. Two identical `.grid` files produce byte-identical bundles, which means
bundle artifacts are diff-friendly and content-addressable (e.g. cache them
by SHA256).

### 20.6 Use cases

* **Frontend hand-off** — give a frontend dev a single `.js` they can drop
  into their build pipeline; the data and transformation are baked in.
* **CI without gridlang** — run pipeline checks inside a Node-only image.
* **Edge deployment** — push the bundle to Cloudflare Workers / Lambda@Edge
  / Deno Deploy for serverless rendering.
* **Notebook-free analysis** — compile a `.grid` file once, re-run the
  bundle with different inputs by overriding `REQUEST` or via
  `runGridLangPipeline({df: [...]})` in the browser.

## 21. Collaborative Editing (v0.8)

GridLang ships with a CRDT-based collaboration layer that lets multiple
clients edit cells of the same `.grid` file concurrently. Convergence is
guaranteed — every replica that has seen the same set of operations ends
up with the same per-cell value, regardless of arrival order.

The implementation lives in three modules:

| Module                       | Responsibility                                  |
|------------------------------|-------------------------------------------------|
| `gridlang.crdt`              | HLC clocks + LWW per-cell document + version vectors |
| `gridlang.collab`            | Server-side `CollabSession` (peers, persistence, sync) |
| `gridlang.collab_client`     | Self-contained browser JS (`/api/collab/client.js`) |

### 21.1 Data model

Cells form a logical map keyed by `(sheet, row, col)`. Insertions and
deletions of rows/columns are **out of scope** for v0.8 — GridLang's data
layer has fixed headers, so the only operation we converge on is "edit
cell X to value Y".

A single edit is a `CellOp`:

```python
CellOp(
    key=CellKey(sheet="", row=2, col=2),    # B2 in the default sheet
    value=120,
    hlc=HLC(wall_ms=1700000000000, logical=0, site_id="srv-abc"),
)
```

The replica state (`gridlang.crdt.Document`) holds:

* `cells: dict[CellKey, CellOp]` — the current LWW winner per cell.
* `journal: list[CellOp]` — every op accepted (used for `ops_since`).
* `clock: HLC` — the local HLC, advanced on every local edit and merged
  on every remote op.

### 21.2 Hybrid Logical Clock (HLC)

Each `CellOp` carries an HLC tuple `(wall_ms, logical, site_id)` that:

1. **Advances monotonically** on the local replica — two events on the
   same site are always strictly ordered.
2. **Survives clock skew** — if a remote op carries a wall timestamp
   ahead of ours, our clock jumps to match it (and bumps `logical`),
   so subsequent local edits remain causally after everything we've seen.
3. **Has a deterministic total order** — when `wall_ms` and `logical`
   tie, `site_id` breaks the tie. Two replicas given the same op set
   pick the same winner per cell.

Algorithm follows Kulkarni et al., *Logical Physical Clocks*, 2014.

### 21.3 Wire protocol

All endpoints are JSON over HTTP. The server is started with `--collab`:

```bash
gridlang serve report.grid --collab --edit
```

| Endpoint                         | Method | Body                                           | Returns |
|----------------------------------|--------|------------------------------------------------|---------|
| `/api/collab/join`               | POST   | `{}` or `{peer_id?}`                            | `{peer_id, site_id, ops, version}` |
| `/api/collab/leave`              | POST   | `{peer_id}`                                     | `{left: true}` |
| `/api/collab/op`                 | POST   | `{peer_id, cell, value, sheet?}`                | `{op, version}` |
| `/api/collab/poll`               | POST   | `{peer_id, since: vv}`                          | `{ops, version, peer_count}` |
| `/api/collab/snapshot`           | GET    | —                                              | `{site_id, ops, version}` |
| `/api/collab/stats`              | GET    | —                                              | `{cells, journal, peers, version}` |
| `/api/collab/client.js`          | GET    | —                                              | JS source |

The version vector `vv` is `{site_id: [wall_ms, logical], ...}`, so each
peer summarizes "what I've seen from each replica" in O(sites) space.

### 21.4 Op submission

```
POST /api/collab/op
{
  "peer_id": "peer-abcd1234",
  "cell":    "B2",
  "value":   120,
  "sheet":   null
}
```

The server:

1. Validates the cell ref via `gridlang.bindings.parse_a1_ref` — header rows
   (`row == 1`) are rejected.
2. Generates a new `CellOp` with the server's HLC bumped via `tick(wall_ms)`.
3. Calls `gridlang.bindings.apply_edit(grid_source, cell, value, sheet)` to
   update the on-disk `.grid` file. The file remains the source of truth —
   `gridlang run`/`render` after a collab session see the merged values.
4. Records the op in the session's journal and replies with the op +
   the new version vector.

If the disk write fails (e.g. sheet not found), the in-memory op is rolled
back so the session stays consistent with disk.

### 21.5 Polling

```
POST /api/collab/poll
{
  "peer_id": "peer-abcd1234",
  "since":   {"srv-xyz": [1700000001000, 7], "peer-foo": [1700000000999, 0]}
}
```

The server returns ops whose `(wall_ms, logical)` is greater than the
peer's recorded mark for that op's `site_id`. The peer merges the response
into its local state, advances its HLC, and includes the new vector in
the next poll.

The default cadence is **700 ms**, controlled by `state.pollMs` in the
client. Long-poll/SSE may be added in a future minor release; the JSON-poll
shape will remain backward-compatible.

### 21.6 Browser client

`/api/collab/client.js` is a single-file IIFE. It:

* Joins on page load, applies the snapshot to all `[data-grid-cell]`
  elements found in the DOM.
* Replaces the v0.5 `client_js()` blur handlers — edits route through
  `/api/collab/op` instead of `/api/cell-edit`.
* Polls in the background, applies remote ops to the DOM (skipping cells
  the user is currently typing into), flashes a brief blue tint on remote
  edits.
* Sends `/api/collab/leave` via `fetch(..., {keepalive: true})` on
  `beforeunload` so the server can drop the peer cleanly.

The whole client is ~250 lines of vanilla JS, no dependencies.

### 21.7 Programmatic API

```python
from gridlang.collab import CollabSession

sess = CollabSession("/path/to/file.grid")
peer_a = sess.register_peer()
peer_b = sess.register_peer()

# A submits an edit; the .grid file is rewritten on disk.
op = sess.submit_local("B2", 999, peer_id=peer_a)

# B asks for what's new.
result = sess.poll(peer_id=peer_b, since={})
# result == {"ops": [op], "version": {...}, "peer_count": 2}
```

The lower-level `gridlang.crdt` API can also be used standalone — e.g. in
a Python notebook driving multiple `Document` replicas to verify
convergence properties.

### 21.8 Convergence proof sketch

The CRDT is a **per-key LWW register** with HLC timestamps and `site_id`
tiebreakers. This satisfies the standard CRDT axioms:

* **Commutativity** — `apply(a) ∘ apply(b) == apply(b) ∘ apply(a)` because
  the result depends only on `max((a.hlc, a.site_id), (b.hlc, b.site_id))`.
* **Associativity** — same total-order argument extends to any sequence.
* **Idempotence** — `apply(a) ∘ apply(a) == apply(a)` because the LWW
  comparison `cur >= candidate` rejects duplicates.

Property tests in `tests/test_crdt.py` exercise these on random op sets
(see `TestDocumentConvergence.test_random_permutations_converge`).

### 21.9 Backward compatibility

* When `--collab` is **off** (default), all `/api/collab/*` endpoints
  return 404 and the client JS is not served. Existing single-user
  workflows (`gridlang serve --edit`) are unchanged.
* When `--collab` is **on**, the v0.5 contenteditable cells and
  `bind:` widgets keep working — they just commit through the CRDT
  layer instead of the direct `/api/cell-edit` endpoint. The server
  still updates the on-disk `.grid` file on every commit, so other
  tools (CLI, editor preview, exports) see the latest values.
* Out of scope for v0.8 (planned for v0.9+):
  * Inserting/deleting rows or columns concurrently — would need an
    RGA or Yjs-style sequence CRDT layered under cell ops.
  * Federation between independent servers — the on-disk file plus the
    in-memory journal is the source of truth for one server only.
  * Auth — the protocol assumes peers on the same trusted network.
