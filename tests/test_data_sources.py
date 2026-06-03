"""Tests for gridlang.data_sources — directive parsing, fetch, cache, fallback."""

from __future__ import annotations

import json
import time
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from gridlang.data_sources import (
    parse_directives,
    resolve,
    load_dataframes,
    DataSourceSpec,
    DataSourceError,
    _parse_duration,
    _detect_format,
    _json_select,
    _file_url_to_path,
)
from gridlang.parser import parse_string


# ─── Directive parsing ───────────────────────────────────────────────────

class TestParseDirectives:
    def test_no_directives_passthrough(self):
        spec, body = parse_directives("a,b\n1,2\n")
        assert spec.source == ""
        assert spec.is_remote is False
        assert body == "a,b\n1,2"

    def test_basic_source(self):
        spec, body = parse_directives(
            "@source: file:///tmp/x.csv\n"
            "\n"
            "Region,Total\n"
            "North,100\n"
        )
        assert spec.source == "file:///tmp/x.csv"
        assert spec.scheme == "file"
        assert spec.is_remote is True
        assert "Region,Total" in body

    def test_format_explicit(self):
        spec, _ = parse_directives("@source: https://api/x\n@format: json\n")
        assert spec.format == "json"

    def test_format_auto_inferred(self):
        spec, _ = parse_directives("@source: https://api/x.csv\n")
        assert spec.format == "csv"
        spec2, _ = parse_directives("@source: https://api/x.json\n")
        assert spec2.format == "json"
        spec3, _ = parse_directives("@source: https://api/x.tsv\n")
        assert spec3.format == "tsv"

    def test_invalid_format_raises(self):
        with pytest.raises(DataSourceError, match="@format"):
            parse_directives("@source: https://x/y.csv\n@format: xml\n")

    def test_cache_durations(self):
        for raw, secs in [("30s", 30), ("5m", 300), ("1h", 3600), ("7d", 604800), ("0", 0)]:
            spec, _ = parse_directives(f"@source: https://x.csv\n@cache: {raw}\n")
            assert spec.cache_ttl == secs

    def test_invalid_cache_raises(self):
        with pytest.raises(DataSourceError, match="@cache"):
            parse_directives("@source: https://x.csv\n@cache: forever\n")

    def test_headers_collected(self):
        spec, _ = parse_directives(
            "@source: https://api/x.csv\n"
            "@header: Authorization: Bearer abc\n"
            "@header: X-API-Key: 123\n"
        )
        assert spec.headers == {"Authorization": "Bearer abc", "X-API-Key": "123"}

    def test_invalid_header_raises(self):
        with pytest.raises(DataSourceError, match="@header"):
            parse_directives("@source: https://x.csv\n@header: NoColon\n")

    def test_select_path(self):
        spec, _ = parse_directives(
            "@source: https://api/x.json\n@select: data.records\n"
        )
        assert spec.select == "data.records"

    def test_unknown_directive_raises(self):
        with pytest.raises(DataSourceError, match="Unknown directive"):
            parse_directives("@source: https://x.csv\n@unknown: foo\n")

    def test_invalid_scheme_raises(self):
        with pytest.raises(DataSourceError, match="scheme"):
            parse_directives("@source: ftp://x/y.csv\n")

    def test_comment_line_skipped_in_directives(self):
        spec, body = parse_directives(
            "# my note\n"
            "@source: file:///tmp/x.csv\n"
            "# another note\n"
            "\n"
            "a,b\n1,2\n"
        )
        assert spec.source == "file:///tmp/x.csv"
        # The body still gets the inline CSV (comments at top of body are dropped by parse_data later).
        assert "a,b" in body

    def test_blank_first_line_then_directive(self):
        spec, _ = parse_directives("\n\n@source: file:///tmp/x.csv\n\nA,B\n1,2\n")
        assert spec.source == "file:///tmp/x.csv"


# ─── Helpers ────────────────────────────────────────────────────────────

class TestHelpers:
    def test_parse_duration_off(self):
        assert _parse_duration("0") == 0
        assert _parse_duration("off") == 0
        assert _parse_duration("none") == 0

    def test_detect_format_query_string(self):
        assert _detect_format("https://api.x/data.csv?key=abc") == "csv"
        assert _detect_format("https://api.x/data.json?v=1") == "json"

    def test_detect_format_fallback(self):
        # Unknown extension defaults to csv.
        assert _detect_format("https://api.x/data") == "csv"

    def test_file_url_to_path_posix(self):
        assert str(_file_url_to_path("file:///tmp/x.csv")) == "/tmp/x.csv"

    def test_json_select_dot_path(self):
        payload = {"data": {"records": [{"a": 1}, {"a": 2}]}}
        assert _json_select(payload, "data.records") == [{"a": 1}, {"a": 2}]

    def test_json_select_array_index(self):
        payload = {"items": [10, 20, 30]}
        assert _json_select(payload, "items[1]") == 20

    def test_json_select_missing_key_raises(self):
        with pytest.raises(DataSourceError, match="missing"):
            _json_select({"a": 1}, "nope")


# ─── resolve() — file:// fixtures ─────────────────────────────────────

@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "sales.csv"
    p.write_text("Region,Q1,Q2\nNorth,120,135\nSouth,95,110\n")
    return p


@pytest.fixture
def tsv_file(tmp_path):
    p = tmp_path / "sales.tsv"
    p.write_text("Region\tQ1\tQ2\nNorth\t120\t135\nSouth\t95\t110\n")
    return p


@pytest.fixture
def json_file(tmp_path):
    p = tmp_path / "sales.json"
    p.write_text(json.dumps({
        "meta": {"version": 1},
        "data": {"records": [
            {"Region": "North", "Total": 120},
            {"Region": "South", "Total": 95},
        ]},
    }))
    return p


class TestResolve:
    def test_resolve_inline_when_no_source(self):
        spec = DataSourceSpec()
        df, label = resolve(spec, "Region,Total\nNorth,100\n")
        assert label == "inline"
        assert df.shape == (1, 2)

    def test_resolve_file_csv(self, csv_file):
        spec = DataSourceSpec(source=f"file://{csv_file}", format="csv")
        df, label = resolve(spec, "")
        assert label.startswith("file:")
        assert df.shape == (2, 3)
        assert list(df.columns) == ["Region", "Q1", "Q2"]
        assert df["Q1"].tolist() == [120, 95]

    def test_resolve_file_tsv(self, tsv_file):
        spec = DataSourceSpec(source=f"file://{tsv_file}", format="tsv")
        df, _ = resolve(spec, "")
        assert df.shape == (2, 3)

    def test_resolve_file_json_with_select(self, json_file):
        spec = DataSourceSpec(
            source=f"file://{json_file}", format="json",
            select="data.records",
        )
        df, _ = resolve(spec, "")
        assert df.shape == (2, 2)
        assert sorted(df.columns) == ["Region", "Total"]

    def test_resolve_file_json_without_select(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text(json.dumps([{"a": 1}, {"a": 2}]))
        spec = DataSourceSpec(source=f"file://{p}", format="json")
        df, _ = resolve(spec, "")
        assert df.shape == (2, 1)

    def test_resolve_remote_denied_uses_fallback(self):
        spec = DataSourceSpec(source="https://example.com/x.csv", format="csv")
        df, label = resolve(spec, "Region,Total\nNorth,100\n", allow_remote=False)
        assert "fallback" in label
        assert df.shape == (1, 2)

    def test_resolve_remote_denied_no_fallback_raises(self):
        spec = DataSourceSpec(source="https://example.com/x.csv", format="csv")
        with pytest.raises(DataSourceError, match="--allow-remote"):
            resolve(spec, "", allow_remote=False)

    def test_resolve_file_missing_falls_back_when_inline_present(self):
        spec = DataSourceSpec(source="file:///nonexistent/path/x.csv", format="csv")
        # file:// is "always allowed" but a missing file should still raise — and
        # since allow_remote doesn't gate file://, there's no automatic fallback
        # for file:// failures; the user gets an error to fix the path.
        with pytest.raises(Exception):
            resolve(spec, "Region,Total\nNorth,100\n")


# ─── Cache behavior (uses file:// to stay offline) ─────────────────────

class TestCache:
    def test_cache_dir_disabled_for_file(self, csv_file, tmp_path):
        # file:// fetches do not consult the cache; the file is the source of truth.
        spec = DataSourceSpec(source=f"file://{csv_file}", format="csv")
        df1, _ = resolve(spec, "", cache_dir=tmp_path / "cache")
        df2, _ = resolve(spec, "", cache_dir=tmp_path / "cache")
        assert df1.equals(df2)
        # No cache file should have been written for file:// sources.
        cache_dir = tmp_path / "cache"
        if cache_dir.exists():
            assert list(cache_dir.glob("*.csv")) == []


# ─── Parser integration ───────────────────────────────────────────────

class TestParserIntegration:
    def test_directives_extracted_into_data_specs(self):
        src = (
            "--- meta ---\n"
            "name: r\nengine: python\nversion: \"1.0\"\n\n"
            "--- data ---\n"
            "@source: file:///tmp/x.csv\n"
            "@format: csv\n"
            "@cache: 1h\n"
            "\n"
            "a,b\n1,2\n"
            "\n--- compute ---\n"
            "def transform(df): return df\n"
            "\n--- present ---\n"
        )
        doc = parse_string(src)
        spec = doc.data_specs["default"]
        assert spec.source == "file:///tmp/x.csv"
        assert spec.format == "csv"
        assert spec.cache_ttl == 3600
        # Inline body still survives.
        assert "a,b" in doc.sheets_raw["default"]

    def test_pure_csv_data_section_yields_empty_spec(self):
        src = (
            "--- meta ---\n"
            "name: r\nengine: python\nversion: \"1.0\"\n\n"
            "--- data ---\n"
            "Region,Total\nNorth,100\n"
            "\n--- compute ---\ndef transform(df): return df\n\n--- present ---\n"
        )
        doc = parse_string(src)
        assert doc.data_specs["default"].source == ""
        assert doc.data_specs["default"].is_remote is False

    def test_multi_sheet_each_has_its_own_spec(self):
        src = (
            "--- meta ---\n"
            "name: r\nengine: python\nversion: \"1.0\"\n\n"
            "--- data:sales ---\n"
            "@source: file:///tmp/sales.csv\n"
            "\n"
            "Region,Q1\nNorth,100\n"
            "--- data:targets ---\n"
            "Region,Target\nNorth,90\n"
            "\n--- compute ---\ndef transform(df): return df\n\n--- present ---\n"
        )
        doc = parse_string(src)
        assert doc.data_specs["sales"].source == "file:///tmp/sales.csv"
        assert doc.data_specs["targets"].source == ""

    def test_load_dataframes_with_file_source(self, csv_file):
        src = (
            "--- meta ---\n"
            "name: r\nengine: python\nversion: \"1.0\"\n\n"
            "--- data ---\n"
            f"@source: file://{csv_file}\n"
            "\n"
            "fallback,row\nused,if-fetch-fails\n"
            "\n--- compute ---\ndef transform(df): return df\n\n--- present ---\n"
        )
        doc = parse_string(src)
        sheets, labels = load_dataframes(doc, allow_remote=False)
        assert labels["default"].startswith("file:")
        # The remote (file) data should win, not the inline fallback.
        assert "Region" in sheets["default"].columns

    def test_load_dataframes_remote_denied_falls_back(self):
        src = (
            "--- meta ---\n"
            "name: r\nengine: python\nversion: \"1.0\"\n\n"
            "--- data ---\n"
            "@source: https://example.com/x.csv\n"
            "\n"
            "Region,Total\nFallback,1\n"
            "\n--- compute ---\ndef transform(df): return df\n\n--- present ---\n"
        )
        doc = parse_string(src)
        sheets, labels = load_dataframes(doc, allow_remote=False)
        assert "fallback" in labels["default"]
        assert sheets["default"].iloc[0]["Region"] == "Fallback"
