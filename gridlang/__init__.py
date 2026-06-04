"""
GridLang - AI-Native Spreadsheet Format

Data + Compute + Presentation in a single .grid file.
"""

__version__ = "1.0.0"

from gridlang.parser import parse, GridDocument
from gridlang.runtime import execute
from gridlang.renderer import render
from gridlang.excel_import import import_excel, import_excel_to_file
from gridlang.excel_export import export_excel, export_dataframe
from gridlang.chart_dsl import preprocess as preprocess_chart_dsl
from gridlang.data_sources import (
    load_dataframes, resolve as resolve_data_source,
    parse_directives, DataSourceSpec, DataSourceError,
)
from gridlang.bindings import (
    preprocess as preprocess_bindings,
    apply_edit as apply_cell_edit,
    parse_a1_ref,
    BindingError,
    BindDirective,
)
from gridlang.js_runtime import (
    execute_js, is_node_available, JsRuntimeUnavailable,
    get_helpers_source, get_bridge_source,
)
from gridlang.js_bundle import bundle_doc, bundle_file, BundleResult, get_pipeline_source
from gridlang.crdt import (
    HLC, CellKey, CellOp, Document as CrdtDocument,
    vv_from_ops, vv_max, vv_serialize, vv_deserialize,
)
from gridlang.collab import (
    CollabSession, CollabError, get_session as get_collab_session,
    reset_sessions as reset_collab_sessions,
)

__all__ = [
    "parse", "execute", "render", "GridDocument",
    "import_excel", "import_excel_to_file",
    "export_excel", "export_dataframe",
    "preprocess_chart_dsl",
    "load_dataframes", "resolve_data_source", "parse_directives",
    "DataSourceSpec", "DataSourceError",
    "preprocess_bindings", "apply_cell_edit", "parse_a1_ref",
    "BindingError", "BindDirective",
    "execute_js", "is_node_available", "JsRuntimeUnavailable",
    "get_helpers_source", "get_bridge_source", "get_pipeline_source",
    "bundle_doc", "bundle_file", "BundleResult",
    "HLC", "CellKey", "CellOp", "CrdtDocument",
    "vv_from_ops", "vv_max", "vv_serialize", "vv_deserialize",
    "CollabSession", "CollabError",
    "get_collab_session", "reset_collab_sessions",
    "__version__",
]
