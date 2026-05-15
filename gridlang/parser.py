"""
GridLang Parser — Parses .grid files into structured GridDocument objects.

A .grid file contains sections separated by `--- section_name ---` delimiters:
  - meta: YAML metadata
  - data (or data:sheet_name): CSV data matrix (supports multiple sheets)
  - compute: Python code
  - present: HTML/Jinja2 template
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# Section delimiter pattern: --- section_name --- or --- data:sheet_name ---
SECTION_PATTERN = re.compile(r'^\s*---\s+([\w:]+)\s+---\s*$')

# Valid base section names
VALID_BASE_SECTIONS = ('meta', 'data', 'compute', 'present')


@dataclass
class GridDocument:
    """Parsed representation of a .grid file."""

    # Raw section content
    meta_raw: str = ""
    data_raw: str = ""           # Default/single sheet data
    compute_raw: str = ""
    present_raw: str = ""

    # Multi-sheet data: name → raw CSV content
    sheets_raw: dict[str, str] = field(default_factory=dict)

    # Parsed meta as dict
    meta: dict = field(default_factory=dict)

    # Source file path (if loaded from file)
    source_path: Optional[Path] = None

    @property
    def name(self) -> str:
        return self.meta.get('name', 'Untitled')

    @property
    def engine(self) -> str:
        return self.meta.get('engine', 'python')

    @property
    def version(self) -> str:
        return self.meta.get('version', '1.0')

    @property
    def dependencies(self) -> list[str]:
        return self.meta.get('dependencies', [])

    @property
    def is_multi_sheet(self) -> bool:
        return len(self.sheets_raw) > 1

    @property
    def sheet_names(self) -> list[str]:
        return list(self.sheets_raw.keys()) if self.sheets_raw else ['default']

    def summary(self) -> dict:
        """Return a summary of the document structure."""
        data_lines = len(self.data_raw.strip().splitlines()) if self.data_raw.strip() else 0
        return {
            'name': self.name,
            'engine': self.engine,
            'version': self.version,
            'data_lines': data_lines,
            'compute_lines': len(self.compute_raw.strip().splitlines()) if self.compute_raw.strip() else 0,
            'present_lines': len(self.present_raw.strip().splitlines()) if self.present_raw.strip() else 0,
            'has_compute': bool(self.compute_raw.strip()),
            'has_present': bool(self.present_raw.strip()),
            'sheet_count': len(self.sheets_raw) if self.sheets_raw else 1,
            'sheet_names': self.sheet_names,
        }


class ParseError(Exception):
    """Raised when a .grid file cannot be parsed."""

    def __init__(self, message: str, line_number: Optional[int] = None):
        self.line_number = line_number
        if line_number is not None:
            message = f"Line {line_number}: {message}"
        super().__init__(message)


def parse(source: str | Path) -> GridDocument:
    """
    Parse a .grid file from a string or file path.

    Args:
        source: Either a string containing .grid content, or a Path to a .grid file.

    Returns:
        GridDocument with all sections parsed.

    Raises:
        ParseError: If the file format is invalid.
        FileNotFoundError: If a file path is given but doesn't exist.
    """
    source_path = None

    if isinstance(source, Path) or (isinstance(source, str) and _looks_like_path(source)):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        content = path.read_text(encoding='utf-8')
        source_path = path
    else:
        content = source

    return _parse_content(content, source_path)


def parse_file(filepath: str | Path) -> GridDocument:
    """Parse a .grid file from a file path."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    content = path.read_text(encoding='utf-8')
    return _parse_content(content, path)


def parse_string(content: str) -> GridDocument:
    """Parse a .grid file from a string."""
    return _parse_content(content, None)


def _looks_like_path(s: str) -> bool:
    """Heuristic to distinguish a file path from .grid content."""
    if '--- meta ---' in s:
        return False
    if s.endswith('.grid') or '/' in s or '\\' in s:
        return True
    return False


def _parse_content(content: str, source_path: Optional[Path]) -> GridDocument:
    """Internal parser implementation supporting multi-sheet syntax."""
    lines = content.splitlines()

    # Find all section boundaries
    section_starts: list[tuple[str, int]] = []  # (full_name, line_index)

    for i, line in enumerate(lines):
        match = SECTION_PATTERN.match(line)
        if match:
            section_name = match.group(1).lower()
            section_starts.append((section_name, i))

    # Validate section presence
    if not section_starts:
        raise ParseError("No sections found. A .grid file must start with '--- meta ---'")

    # Classify sections
    base_sections_found = []  # Track order of base sections (meta, data, compute, present)
    data_sections = []  # Track all data sections specifically

    for full_name, line_idx in section_starts:
        base_name = full_name.split(':')[0]
        if base_name not in VALID_BASE_SECTIONS:
            raise ParseError(
                f"Unknown section '{full_name}'. "
                f"Valid sections: {', '.join(VALID_BASE_SECTIONS)} (data supports data:name syntax)",
                line_number=line_idx + 1
            )
        if base_name == 'data':
            data_sections.append((full_name, line_idx))
        if base_name not in base_sections_found:
            base_sections_found.append(base_name)
        elif base_name != 'data':
            # Only data can have multiple sections
            raise ParseError(
                f"Duplicate section '{full_name}'",
                line_number=line_idx + 1
            )

    # Validate required base sections exist
    missing = [s for s in VALID_BASE_SECTIONS if s not in base_sections_found]
    if missing:
        raise ParseError(f"Missing required section(s): {', '.join(missing)}")

    # Validate order (meta before data before compute before present)
    expected_order = [s for s in VALID_BASE_SECTIONS if s in base_sections_found]
    if base_sections_found != expected_order:
        raise ParseError(
            f"Sections must appear in order: {' → '.join(VALID_BASE_SECTIONS)}. "
            f"Found: {' → '.join(base_sections_found)}"
        )

    # Extract content between section delimiters
    sections_content: dict[str, str] = {}
    for i, (name, start_line) in enumerate(section_starts):
        content_start = start_line + 1
        if i + 1 < len(section_starts):
            content_end = section_starts[i + 1][1]
        else:
            content_end = len(lines)
        section_content = '\n'.join(lines[content_start:content_end]).strip()
        sections_content[name] = section_content

    # Parse meta as YAML
    meta_content = sections_content.get('meta', '')
    try:
        meta = yaml.safe_load(meta_content) if meta_content else {}
        if meta is None:
            meta = {}
        if not isinstance(meta, dict):
            raise ParseError("Meta section must be valid YAML mapping")
    except yaml.YAMLError as e:
        raise ParseError(f"Invalid YAML in meta section: {e}")

    # Validate required meta fields
    _validate_meta(meta)

    # Handle multi-sheet data
    sheets_raw: dict[str, str] = {}
    primary_data_raw = ""

    if len(data_sections) == 1:
        # Single data section
        full_name = data_sections[0][0]
        data_content = sections_content.get(full_name, '')
        if ':' in full_name:
            sheet_name = full_name.split(':', 1)[1]
        else:
            sheet_name = 'default'
        sheets_raw[sheet_name] = data_content
        primary_data_raw = data_content
    else:
        # Multiple data sections
        for full_name, _ in data_sections:
            data_content = sections_content.get(full_name, '')
            if ':' in full_name:
                sheet_name = full_name.split(':', 1)[1]
            else:
                sheet_name = 'default'
            sheets_raw[sheet_name] = data_content
        # Primary data is the first sheet
        primary_data_raw = list(sheets_raw.values())[0] if sheets_raw else ""

    # Build document
    doc = GridDocument(
        meta_raw=meta_content,
        data_raw=primary_data_raw,
        compute_raw=sections_content.get('compute', ''),
        present_raw=sections_content.get('present', ''),
        sheets_raw=sheets_raw,
        meta=meta,
        source_path=source_path,
    )

    return doc


def _validate_meta(meta: dict) -> None:
    """Validate required meta fields."""
    required_fields = {'name', 'engine', 'version'}
    missing = required_fields - set(meta.keys())
    if missing:
        raise ParseError(f"Meta section missing required fields: {', '.join(sorted(missing))}")

    # Validate engine
    supported_engines = {'python'}
    engine = meta.get('engine', '')
    if engine not in supported_engines:
        raise ParseError(
            f"Unsupported engine '{engine}'. Supported: {', '.join(supported_engines)}"
        )
