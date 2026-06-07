"""Tests for gridlang.csv_io and gridlang.excel_import/export"""

import pytest
import pandas as pd
from pathlib import Path

from gridlang.csv_io import import_csv, export_csv, export_csv_string
from gridlang.excel_import import import_excel
from gridlang.excel_export import export_excel, export_dataframe
from gridlang.parser import parse_string


@pytest.fixture
def sample_csv(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("Name,Score,Grade\nAlice,90,A\nBob,80,B\nCharlie,70,C\n")
    return csv_file


@pytest.fixture
def sample_grid(tmp_path):
    grid_content = """--- meta ---
name: "Test"
engine: python
version: "1.0"

--- data ---
Product,Price,Qty
Widget,10,100
Gadget,20,50
Tool,15,75

--- compute ---
def transform(df):
    df['Total'] = df['Price'] * df['Qty']
    return df

def aggregates(df):
    return {'grand_total': df['Total'].sum()}

--- present ---
<p>Total: {{ agg.grand_total }}</p>
"""
    grid_file = tmp_path / "test.grid"
    grid_file.write_text(grid_content)
    return grid_file


@pytest.fixture
def sample_xlsx(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Name", "Value", "Category"])
    ws.append(["Alpha", 100, "A"])
    ws.append(["Beta", 200, "B"])
    ws.append(["Gamma", 150, "A"])
    xlsx_file = tmp_path / "test.xlsx"
    wb.save(xlsx_file)
    wb.close()
    return xlsx_file


class TestCSVImport:
    def test_basic_import(self, sample_csv):
        result = import_csv(sample_csv)
        assert "--- meta ---" in result
        assert "--- data ---" in result
        assert "--- compute ---" in result
        assert "--- present ---" in result
        assert "Alice" in result

    def test_name_from_filename(self, sample_csv):
        result = import_csv(sample_csv)
        assert 'name: "Test"' in result

    def test_custom_name(self, sample_csv):
        result = import_csv(sample_csv, name="My Report")
        assert 'name: "My Report"' in result

    def test_result_is_valid_grid(self, sample_csv):
        result = import_csv(sample_csv)
        doc = parse_string(result)
        assert doc.name == "Test"
        assert "Alice" in doc.data_raw

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            import_csv("/nonexistent/file.csv")


class TestCSVExport:
    def test_basic_export(self, sample_grid, tmp_path):
        output = tmp_path / "output.csv"
        result_path = export_csv(sample_grid, output)
        assert result_path.exists()
        content = result_path.read_text()
        assert "Product" in content
        assert "Total" in content  # Computed column
        assert "1000" in content   # Widget total: 10*100

    def test_export_raw(self, sample_grid, tmp_path):
        output = tmp_path / "raw.csv"
        result_path = export_csv(sample_grid, output, raw=True)
        content = result_path.read_text()
        assert "Product" in content
        assert "Total" not in content  # No compute

    def test_export_string(self, sample_grid):
        csv_str = export_csv_string(sample_grid)
        assert "Widget" in csv_str
        assert "Total" in csv_str


class TestExcelImport:
    def test_basic_import(self, sample_xlsx):
        result = import_excel(sample_xlsx)
        assert "--- meta ---" in result
        assert "--- data ---" in result
        assert "Alpha" in result
        assert "200" in result

    def test_result_is_valid_grid(self, sample_xlsx):
        result = import_excel(sample_xlsx)
        doc = parse_string(result)
        assert "Alpha" in doc.data_raw

    def test_specific_sheet(self, sample_xlsx):
        result = import_excel(sample_xlsx, sheet_names=["Data"])
        assert "Alpha" in result

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            import_excel("/nonexistent/file.xlsx")

    # ──────────────────────────────────────────────────────────────────
    # Regressions for the "测试1.xlsx"-class of import bugs:
    # numeric-only first row, gap rows, and trailing summary rows.
    # ──────────────────────────────────────────────────────────────────

    def test_no_header_row_detected_when_all_numeric(self, tmp_path):
        """Row 1 = all numbers → don't promote it to header.

        Before this fix, a pure-data sheet `1,1,...` / `2,2,...` would have
        row 1 silently consumed as a header, dropping a real data row.
        """
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        for v in range(1, 6):  # 5 rows of all-numeric data
            ws.append([v] * 4)
        f = tmp_path / "no_header.xlsx"
        wb.save(f); wb.close()

        result = import_excel(f)
        from gridlang.parser import parse_string
        from gridlang.schema import parse_data
        df = parse_data(parse_string(result).data_raw)

        # All 5 source rows must survive — none consumed as header.
        assert len(df) == 5
        # Synthetic headers must be col_A..col_D, not "1, 1.1, ...".
        assert list(df.columns) == ["col_A", "col_B", "col_C", "col_D"]
        # First row's values are 1, last row's values are 5.
        assert int(df.iloc[0, 0]) == 1
        assert int(df.iloc[-1, 0]) == 5

    def test_header_row_detected_when_text(self, tmp_path):
        """Row 1 = text labels → DO use it as header (existing behaviour)."""
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["Region", "Q1", "Q2"])
        ws.append(["North", 100, 200])
        ws.append(["South", 150, 250])
        f = tmp_path / "with_header.xlsx"
        wb.save(f); wb.close()

        result = import_excel(f)
        from gridlang.parser import parse_string
        from gridlang.schema import parse_data
        df = parse_data(parse_string(result).data_raw)

        assert list(df.columns) == ["Region", "Q1", "Q2"]
        assert len(df) == 2
        assert df.iloc[0]["Region"] == "North"

    def test_trailing_summary_row_separated(self, tmp_path):
        """Trailing `=SUM(...)` row goes into compute, not data."""
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["Name", "Score"])
        ws.append(["Alice", 90])
        ws.append(["Bob", 80])
        ws.append(["Carol", 70])
        # Trailing summary row in column B, rest empty.
        ws["A5"] = None
        ws["B5"] = "=SUM(B2:B4)"
        f = tmp_path / "with_summary.xlsx"
        wb.save(f); wb.close()

        result = import_excel(f)

        # Data section must NOT contain the SUM formula or its placeholder.
        # Pull just the data block to be specific.
        from gridlang.parser import parse_string
        doc = parse_string(result)
        assert "=SUM" not in doc.data_raw
        # The compute section should mention it explicitly.
        assert "=SUM(B2:B4)" in doc.compute_raw or "Summary cells" in doc.compute_raw

        # And the data rows survive intact.
        from gridlang.schema import parse_data
        df = parse_data(doc.data_raw)
        assert len(df) == 3
        assert set(df["Name"]) == {"Alice", "Bob", "Carol"}

    def test_array_formula_not_dropped(self, tmp_path):
        """ArrayFormula objects (dynamic arrays) get extracted, not silently dropped."""
        from openpyxl import Workbook
        from openpyxl.worksheet.formula import ArrayFormula
        wb = Workbook()
        ws = wb.active
        ws.append(["Name", "Score"])
        ws.append(["Alice", 90])
        ws.append(["Bob", 80])
        # Park an array formula at C1:C2.
        ws["C1"] = ArrayFormula("C1:C2", "=B1:B2*2")
        f = tmp_path / "array_formula.xlsx"
        wb.save(f); wb.close()

        result = import_excel(f)

        # Array formula's text should land somewhere in the compute section.
        # (Exact rendering varies by openpyxl version; just look for the body.)
        from gridlang.parser import parse_string
        doc = parse_string(result)
        compute = doc.compute_raw
        assert "B1:B2" in compute or "C1:C2" in compute, (
            "Array formula text was silently dropped: " + compute[:300]
        )


class TestExcelExport:
    def test_basic_export(self, sample_grid, tmp_path):
        output = tmp_path / "output.xlsx"
        result_path = export_excel(sample_grid, output)
        assert result_path.exists()
        assert result_path.stat().st_size > 0

    def test_export_has_data(self, sample_grid, tmp_path):
        output = tmp_path / "output.xlsx"
        export_excel(sample_grid, output)

        from openpyxl import load_workbook
        wb = load_workbook(output)
        ws = wb.active
        # Check header
        assert ws.cell(row=1, column=1).value == "Product"
        # Check data
        assert ws.cell(row=2, column=1).value == "Widget"
        wb.close()

    def test_export_with_summary(self, sample_grid, tmp_path):
        output = tmp_path / "output.xlsx"
        export_excel(sample_grid, output, include_aggregates=True)

        from openpyxl import load_workbook
        wb = load_workbook(output)
        assert "Summary" in wb.sheetnames
        wb.close()

    def test_export_dataframe(self, tmp_path):
        df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
        output = tmp_path / "df.xlsx"
        result = export_dataframe(df, output)
        assert result.exists()


class TestRoundTrip:
    """Test xlsx → .grid → xlsx preserves data."""

    def test_xlsx_roundtrip(self, sample_xlsx, tmp_path):
        # Import
        grid_content = import_excel(sample_xlsx)
        grid_file = tmp_path / "roundtrip.grid"
        grid_file.write_text(grid_content)

        # Export back
        output_xlsx = tmp_path / "roundtrip.xlsx"
        export_excel(grid_file, output_xlsx)

        # Verify
        from openpyxl import load_workbook
        wb = load_workbook(output_xlsx)
        ws = wb.active
        assert ws.cell(row=2, column=1).value == "Alpha"
        assert ws.cell(row=2, column=2).value == 100
        wb.close()

    def test_csv_roundtrip(self, sample_csv, tmp_path):
        # Import
        grid_content = import_csv(sample_csv)
        grid_file = tmp_path / "roundtrip.grid"
        grid_file.write_text(grid_content)

        # Export back to CSV
        output_csv = tmp_path / "roundtrip.csv"
        export_csv(grid_file, output_csv, raw=True)

        # Verify
        df = pd.read_csv(output_csv)
        assert list(df['Name']) == ['Alice', 'Bob', 'Charlie']
        assert list(df['Score']) == [90, 80, 70]
