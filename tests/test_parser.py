"""Tests for gridlang.parser"""

import pytest
from gridlang.parser import parse_string, parse_file, ParseError, GridDocument


MINIMAL_GRID = """--- meta ---
name: "Test"
engine: python
version: "1.0"

--- data ---
A,B,C
1,2,3
4,5,6

--- compute ---
def transform(df):
    return df

--- present ---
<p>Hello</p>
"""


class TestParseString:
    """Test parsing from string content."""

    def test_minimal_valid(self):
        doc = parse_string(MINIMAL_GRID)
        assert isinstance(doc, GridDocument)
        assert doc.name == "Test"
        assert doc.engine == "python"
        assert doc.version == "1.0"

    def test_data_extraction(self):
        doc = parse_string(MINIMAL_GRID)
        assert "A,B,C" in doc.data_raw
        assert "1,2,3" in doc.data_raw

    def test_compute_extraction(self):
        doc = parse_string(MINIMAL_GRID)
        assert "def transform(df):" in doc.compute_raw

    def test_present_extraction(self):
        doc = parse_string(MINIMAL_GRID)
        assert "<p>Hello</p>" in doc.present_raw

    def test_meta_parsed_as_dict(self):
        doc = parse_string(MINIMAL_GRID)
        assert doc.meta == {
            'name': 'Test',
            'engine': 'python',
            'version': '1.0',
        }

    def test_empty_sections(self):
        content = """--- meta ---
name: "Empty"
engine: python
version: "1.0"

--- data ---

--- compute ---

--- present ---
"""
        doc = parse_string(content)
        assert doc.name == "Empty"
        assert doc.data_raw == ""
        assert doc.compute_raw == ""
        assert doc.present_raw == ""

    def test_summary(self):
        doc = parse_string(MINIMAL_GRID)
        summary = doc.summary()
        assert summary['name'] == "Test"
        assert summary['data_lines'] == 3  # header + 2 data rows
        assert summary['compute_lines'] == 2
        assert summary['has_compute'] is True
        assert summary['has_present'] is True


class TestParseErrors:
    """Test error handling."""

    def test_no_sections(self):
        with pytest.raises(ParseError, match="No sections found"):
            parse_string("just some random text")

    def test_missing_section(self):
        content = """--- meta ---
name: "Test"
engine: python
version: "1.0"

--- data ---
A,B
1,2

--- compute ---
"""
        with pytest.raises(ParseError, match="Missing required section"):
            parse_string(content)

    def test_duplicate_section(self):
        content = """--- meta ---
name: "Test"
engine: python
version: "1.0"

--- meta ---
name: "Duplicate"

--- data ---

--- compute ---

--- present ---
"""
        with pytest.raises(ParseError, match="Duplicate section"):
            parse_string(content)

    def test_wrong_order(self):
        content = """--- data ---
A,B
1,2

--- meta ---
name: "Test"
engine: python
version: "1.0"

--- compute ---

--- present ---
"""
        with pytest.raises(ParseError, match="Sections must appear in order"):
            parse_string(content)

    def test_unknown_section(self):
        content = """--- meta ---
name: "Test"
engine: python
version: "1.0"

--- data ---

--- compute ---

--- present ---

--- extra ---
something
"""
        # This should parse fine — extra content after present is part of present
        # Actually let's test with the unknown section before present
        content2 = """--- meta ---
name: "Test"
engine: python
version: "1.0"

--- data ---

--- unknown ---

--- compute ---

--- present ---
"""
        with pytest.raises(ParseError, match="Unknown section"):
            parse_string(content2)

    def test_missing_meta_field(self):
        content = """--- meta ---
name: "Test"

--- data ---

--- compute ---

--- present ---
"""
        with pytest.raises(ParseError, match="missing required fields"):
            parse_string(content)

    def test_unsupported_engine(self):
        content = """--- meta ---
name: "Test"
engine: javascript
version: "1.0"

--- data ---

--- compute ---

--- present ---
"""
        with pytest.raises(ParseError, match="Unsupported engine"):
            parse_string(content)

    def test_invalid_yaml(self):
        content = """--- meta ---
name: [invalid yaml
  broken: {{

--- data ---

--- compute ---

--- present ---
"""
        with pytest.raises(ParseError, match="Invalid YAML"):
            parse_string(content)


class TestParseFile:
    """Test file-based parsing."""

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            parse_file("/nonexistent/path/test.grid")

    def test_parse_example_file(self, tmp_path):
        grid_file = tmp_path / "test.grid"
        grid_file.write_text(MINIMAL_GRID)
        doc = parse_file(grid_file)
        assert doc.name == "Test"
        assert doc.source_path == grid_file


class TestMetaOptionalFields:
    """Test optional meta fields."""

    def test_all_optional_fields(self):
        content = """--- meta ---
name: "Full Meta"
engine: python
version: "1.0"
author: "Test Author"
description: "A test document"
tags: ["test", "example"]
dependencies: ["scipy"]

--- data ---
X,Y
1,2

--- compute ---

--- present ---
"""
        doc = parse_string(content)
        assert doc.meta['author'] == "Test Author"
        assert doc.meta['description'] == "A test document"
        assert doc.meta['tags'] == ["test", "example"]
        assert doc.dependencies == ["scipy"]
