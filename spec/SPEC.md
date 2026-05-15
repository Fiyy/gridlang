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

## 10. Future Extensions (v2.0 Roadmap)

- **Multi-sheet** — Multiple data sections in one file (`--- data:sheet_name ---`)
- **Reactive bindings** — Two-way binding between present layer edits and data layer
- **Remote data sources** — `--- data:url ---` to fetch from APIs
- **Chart DSL** — Simplified chart declaration syntax within present layer
- **JavaScript engine** — Alternative compute engine for browser-native execution
- **Collaborative editing** — CRDT-based real-time collaboration protocol
