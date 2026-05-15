"""
GridLang CLI — Command-line interface for working with .grid files.

Commands:
  gridlang run <file>         — Execute compute and display results
  gridlang render <file>      — Render to HTML
  gridlang validate <file>    — Validate file format
  gridlang info <file>        — Show file structure summary
  gridlang import <xlsx>      — Convert Excel to .grid
  gridlang export <grid>      — Convert .grid to Excel
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from gridlang.parser import parse_file, ParseError, GridDocument
from gridlang.schema import parse_data, validate_schema, SchemaError, check_schema
from gridlang.runtime import execute, RuntimeError_, ExecutionResult
from gridlang.renderer import render, RenderError


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog='gridlang',
        description='GridLang — AI-native spreadsheet format toolkit',
    )
    parser.add_argument('--version', action='version', version='gridlang 0.2.0')

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # run command
    run_parser = subparsers.add_parser('run', help='Execute compute and display results')
    run_parser.add_argument('file', type=str, help='Path to .grid file')
    run_parser.add_argument('--json', action='store_true', help='Output as JSON')
    run_parser.add_argument('--sheet', type=str, default=None, help='Specific sheet to display')

    # render command
    render_parser = subparsers.add_parser('render', help='Render to HTML file')
    render_parser.add_argument('file', type=str, help='Path to .grid file')
    render_parser.add_argument('-o', '--output', type=str, default=None,
                               help='Output HTML file path (default: stdout)')
    render_parser.add_argument('--fragment', action='store_true',
                               help='Output HTML fragment without full document wrapper')

    # validate command
    validate_parser = subparsers.add_parser('validate', help='Validate .grid file format')
    validate_parser.add_argument('file', type=str, help='Path to .grid file')
    validate_parser.add_argument('--strict', action='store_true',
                                  help='Treat warnings as errors')

    # info command
    info_parser = subparsers.add_parser('info', help='Show file structure summary')
    info_parser.add_argument('file', type=str, help='Path to .grid file')

    # import command
    import_parser = subparsers.add_parser('import', help='Convert Excel (.xlsx) or CSV to .grid')
    import_parser.add_argument('file', type=str, help='Path to .xlsx or .csv file')
    import_parser.add_argument('-o', '--output', type=str, default=None,
                               help='Output .grid file path')
    import_parser.add_argument('--sheet', type=str, nargs='*', default=None,
                               help='Specific sheet(s) to import (xlsx only)')
    import_parser.add_argument('--no-formulas', action='store_true',
                               help='Skip formula conversion')
    import_parser.add_argument('--no-styles', action='store_true',
                               help='Skip style extraction')

    # export command
    export_parser = subparsers.add_parser('export', help='Convert .grid to Excel (.xlsx) or CSV')
    export_parser.add_argument('file', type=str, help='Path to .grid file')
    export_parser.add_argument('-o', '--output', type=str, default=None,
                               help='Output file path (.xlsx or .csv)')
    export_parser.add_argument('--format', choices=['xlsx', 'csv'], default=None,
                               help='Output format (auto-detected from extension)')
    export_parser.add_argument('--sheet', type=str, default=None,
                               help='Specific sheet to export')
    export_parser.add_argument('--raw', action='store_true',
                               help='Export raw data without running compute')
    export_parser.add_argument('--no-summary', action='store_true',
                               help='Skip aggregates summary sheet (xlsx only)')
    export_parser.add_argument('--engine', choices=['openpyxl', 'xlsxwriter'],
                               default='openpyxl', help='Excel write engine')

    # serve command
    serve_parser = subparsers.add_parser('serve', help='Live preview server for .grid files')
    serve_parser.add_argument('file', type=str, help='Path to .grid file')
    serve_parser.add_argument('--port', type=int, default=8080, help='HTTP port (default: 8080)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Dispatch
    try:
        if args.command == 'run':
            cmd_run(args)
        elif args.command == 'render':
            cmd_render(args)
        elif args.command == 'validate':
            cmd_validate(args)
        elif args.command == 'info':
            cmd_info(args)
        elif args.command == 'import':
            cmd_import(args)
        elif args.command == 'export':
            cmd_export(args)
        elif args.command == 'serve':
            cmd_serve(args)
    except (ParseError, SchemaError, RuntimeError_, RenderError) as e:
        _error(str(e))
    except FileNotFoundError as e:
        _error(str(e))
    except KeyboardInterrupt:
        sys.exit(130)


def cmd_run(args):
    """Execute the .grid file and show results."""
    doc = parse_file(args.file)

    # Parse data (multi-sheet aware)
    if doc.is_multi_sheet:
        sheets = {name: parse_data(raw) for name, raw in doc.sheets_raw.items()}
        primary_df = list(sheets.values())[0]
    else:
        primary_df = parse_data(doc.data_raw)
        sheets = {'default': primary_df}

    # Validate schema if defined
    schema = doc.meta.get('schema')
    check_schema(primary_df, schema)

    # Execute compute
    result = execute(doc.compute_raw, primary_df, sheets=sheets)

    # Output
    if args.json:
        import json
        output = {
            'data': result.df.to_dict(orient='records'),
            'aggregates': _serialize_aggregates(result.aggregates),
            'functions': result.compute_functions,
        }
        if result.is_multi_sheet:
            output['sheets'] = {
                name: df.to_dict(orient='records')
                for name, df in result.sheets.items()
            }
        print(json.dumps(output, indent=2, default=str))
    else:
        _print_header(doc)
        print()

        # Show specific sheet or primary
        if args.sheet and args.sheet in result.sheets:
            display_df = result.sheets[args.sheet]
            print(f"━━━ Sheet: {args.sheet} ━━━")
        else:
            display_df = result.df
            if result.is_multi_sheet:
                print(f"━━━ Sheets: {', '.join(result.sheets.keys())} ━━━")
                print(f"━━━ Showing: {list(result.sheets.keys())[0]} ━━━")
            else:
                print("━━━ Data (transformed) ━━━")

        print(display_df.to_string(index=False))
        print()

        # Print aggregates
        if result.aggregates:
            print("━━━ Aggregates ━━━")
            for key, value in result.aggregates.items():
                label = key.replace('_', ' ').title()
                if isinstance(value, float):
                    print(f"  {label}: {value:,.2f}")
                else:
                    print(f"  {label}: {value}")
            print()

        # Print conditional formats if any
        if result.conditional_formats:
            print(f"━━━ Conditional Formats ({len(result.conditional_formats)} rule(s)) ━━━")
            for cf in result.conditional_formats:
                print(f"  • {cf.column}: {cf.rule} → {cf.style or cf.rule}")
            print()

        print(f"✓ Executed successfully ({len(result.compute_functions)} function(s): "
              f"{', '.join(result.compute_functions)})")


def cmd_render(args):
    """Render .grid file to HTML."""
    doc = parse_file(args.file)

    # Parse data
    if doc.is_multi_sheet:
        sheets = {name: parse_data(raw) for name, raw in doc.sheets_raw.items()}
        primary_df = list(sheets.values())[0]
    else:
        primary_df = parse_data(doc.data_raw)
        sheets = None

    # Schema validation
    schema = doc.meta.get('schema')
    check_schema(primary_df, schema)

    # Execute compute
    result = execute(doc.compute_raw, primary_df, sheets=sheets)

    # Render
    html = render(
        template_content=doc.present_raw,
        df=result.df,
        aggregates=result.aggregates,
        meta=doc.meta,
        raw_df=primary_df,
        sheets=result.sheets if result.is_multi_sheet else None,
        conditional_formats=result.conditional_formats,
        standalone=not args.fragment,
    )

    # Output
    if args.output:
        output_path = Path(args.output)
        output_path.write_text(html, encoding='utf-8')
        print(f"✓ Rendered to {output_path} ({len(html):,} bytes)")
    else:
        print(html)


def cmd_validate(args):
    """Validate a .grid file."""
    errors = []
    warnings = []

    # Step 1: Parse
    try:
        doc = parse_file(args.file)
        sheet_info = f" ({len(doc.sheets_raw)} sheet(s))" if doc.is_multi_sheet else ""
        print(f"  ✓ File structure: valid{sheet_info}")
    except ParseError as e:
        errors.append(f"Parse error: {e}")
        _print_validation_result(args.file, errors, warnings)
        return

    # Step 2: Data parsing
    try:
        df = parse_data(doc.data_raw)
        rows, cols = df.shape
        print(f"  ✓ Data layer: {rows} rows × {cols} columns")
    except Exception as e:
        errors.append(f"Data parse error: {e}")
        df = None

    # Step 3: Schema validation
    if df is not None:
        schema = doc.meta.get('schema')
        if schema:
            violations = validate_schema(df, schema)
            if violations:
                for v in violations:
                    if args.strict:
                        errors.append(v)
                    else:
                        warnings.append(v)
                if not violations:
                    print("  ✓ Schema validation: passed")
            else:
                print("  ✓ Schema validation: passed")
        else:
            print("  ○ Schema validation: skipped (no schema defined)")

    # Step 4: Compute syntax check
    if doc.compute_raw.strip():
        try:
            compile(doc.compute_raw, '<compute>', 'exec')
            print("  ✓ Compute layer: valid Python syntax")
        except SyntaxError as e:
            errors.append(f"Compute syntax error (line {e.lineno}): {e.msg}")

        # Check for dangerous patterns
        dangerous_patterns = ['os.system', 'subprocess', 'open(', '__import__',
                             'exec(', 'eval(', 'shutil']
        for pattern in dangerous_patterns:
            if pattern in doc.compute_raw:
                warnings.append(f"Potentially unsafe pattern in compute: '{pattern}'")
    else:
        print("  ○ Compute layer: empty (pass-through)")

    # Step 5: Template syntax check
    if doc.present_raw.strip():
        try:
            from jinja2 import Environment, BaseLoader
            env = Environment(loader=BaseLoader())
            env.parse(doc.present_raw)
            print("  ✓ Present layer: valid Jinja2 template")
        except Exception as e:
            errors.append(f"Template error: {e}")
    else:
        print("  ○ Present layer: empty (will use default template)")

    _print_validation_result(args.file, errors, warnings)


def cmd_info(args):
    """Show file structure summary."""
    doc = parse_file(args.file)
    summary = doc.summary()

    print(f"\n{'─' * 50}")
    print(f"  📄 {summary['name']}")
    print(f"{'─' * 50}")
    print(f"  Engine:  {summary['engine']}")
    print(f"  Version: {summary['version']}")
    if summary['sheet_count'] > 1:
        print(f"  Sheets:  {summary['sheet_count']} ({', '.join(summary['sheet_names'])})")
    print()
    print(f"  ┌─ meta ────── {len(doc.meta_raw.splitlines()):>3} lines  (YAML config)")

    # Data info
    if summary['data_lines'] > 0:
        try:
            df = parse_data(doc.data_raw)
            print(f"  ├─ data ────── {summary['data_lines']:>3} lines  "
                  f"({df.shape[0]} rows × {df.shape[1]} cols)")
            print(f"  │   columns: {', '.join(df.columns[:6])}"
                  f"{'...' if len(df.columns) > 6 else ''}")
        except Exception:
            print(f"  ├─ data ────── {summary['data_lines']:>3} lines")
    else:
        print(f"  ├─ data ────── (empty)")

    # Compute info
    if summary['has_compute']:
        functions = []
        for line in doc.compute_raw.splitlines():
            if line.strip().startswith('def '):
                fname = line.strip().split('(')[0].replace('def ', '')
                functions.append(fname)
        print(f"  ├─ compute ─── {summary['compute_lines']:>3} lines  "
              f"(Python: {', '.join(functions) or 'no functions'})")
    else:
        print(f"  ├─ compute ─── (empty)")

    # Present info
    if summary['has_present']:
        print(f"  └─ present ─── {summary['present_lines']:>3} lines  (HTML/Jinja2)")
    else:
        print(f"  └─ present ─── (empty, will use default)")

    print(f"{'─' * 50}\n")


def cmd_import(args):
    """Import Excel or CSV file to .grid format."""
    file_path = Path(args.file)
    suffix = file_path.suffix.lower()

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = file_path.with_suffix('.grid')

    print(f"  📥 Importing: {file_path.name}")

    if suffix == '.csv':
        from gridlang.csv_io import import_csv
        grid_content = import_csv(file_path)
    elif suffix in ('.xlsx', '.xls'):
        from gridlang.excel_import import import_excel
        grid_content = import_excel(
            file_path,
            sheet_names=args.sheet,
            include_formulas=not args.no_formulas,
            include_styles=not args.no_styles,
        )
    else:
        _error(f"Unsupported file format '{suffix}'. Supported: .xlsx, .csv")
        return

    # Write
    output_path.write_text(grid_content, encoding='utf-8')
    lines = grid_content.count('\n')
    print(f"  ✓ Converted to: {output_path}")
    print(f"    {lines} lines | source: {suffix}")


def cmd_export(args):
    """Export .grid file to Excel or CSV format."""
    grid_path = Path(args.file)

    # Determine format and output path
    if args.output:
        output_path = Path(args.output)
        fmt = args.format or output_path.suffix.lstrip('.')
    else:
        fmt = args.format or 'xlsx'
        output_path = grid_path.with_suffix(f'.{fmt}')

    print(f"  📤 Exporting: {grid_path.name} → .{fmt}")

    if fmt == 'csv':
        from gridlang.csv_io import export_csv
        result_path = export_csv(
            grid_path, output_path,
            sheet=args.sheet, raw=args.raw,
        )
        file_size = result_path.stat().st_size
        print(f"  ✓ Exported to: {result_path} ({file_size:,} bytes)")
    else:
        from gridlang.excel_export import export_excel
        result_path = export_excel(
            grid_path, output_path,
            include_aggregates=not args.no_summary,
            engine=args.engine,
        )
        file_size = result_path.stat().st_size
        print(f"  ✓ Exported to: {result_path} ({file_size:,} bytes)")
        print(f"    Engine: {args.engine} | Summary sheet: {'yes' if not args.no_summary else 'no'}")


def cmd_serve(args):
    """Start live preview server."""
    from gridlang.server import serve
    serve(args.file, port=args.port)


def _print_header(doc: GridDocument):
    """Print document header."""
    print(f"\n{'━' * 50}")
    print(f"  📊 {doc.name}")
    print(f"{'━' * 50}")


def _print_validation_result(filepath: str, errors: list[str], warnings: list[str]):
    """Print final validation summary."""
    print()
    if errors:
        print(f"✗ INVALID — {len(errors)} error(s):")
        for e in errors:
            print(f"    ✗ {e}")
        sys.exit(1)
    elif warnings:
        print(f"⚠ VALID with {len(warnings)} warning(s):")
        for w in warnings:
            print(f"    ⚠ {w}")
    else:
        print(f"✓ {filepath}: ALL CHECKS PASSED")


def _serialize_aggregates(agg: dict) -> dict:
    """Convert aggregates to JSON-serializable format."""
    result = {}
    for key, value in agg.items():
        if isinstance(value, (pd.Timestamp,)):
            result[key] = value.isoformat()
        elif isinstance(value, (float,)) and pd.isna(value):
            result[key] = None
        else:
            result[key] = value
    return result


def _error(message: str):
    """Print error and exit."""
    print(f"\n✗ Error: {message}", file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
    main()
