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
- **Reactive bindings** — Two-way binding between present layer edits and data layer
- **Remote data sources** — `--- data:url ---` to fetch from APIs
- **Chart DSL** — Simplified chart declaration syntax within present layer
- **JavaScript engine** — Alternative compute engine for browser-native execution
- **Collaborative editing** — CRDT-based real-time collaboration protocol
