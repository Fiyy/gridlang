"""
GridLang - AI-Native Spreadsheet Format

Data + Compute + Presentation in a single .grid file.
"""

__version__ = "0.2.0"

from gridlang.parser import parse, GridDocument
from gridlang.runtime import execute
from gridlang.renderer import render
from gridlang.excel_import import import_excel, import_excel_to_file
from gridlang.excel_export import export_excel, export_dataframe

__all__ = [
    "parse", "execute", "render", "GridDocument",
    "import_excel", "import_excel_to_file",
    "export_excel", "export_dataframe",
    "__version__",
]
