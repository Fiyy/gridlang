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
