"""
GridLang Remote Data Sources — fetch, decode, and cache external data
referenced by `@source` directives at the top of a `--- data ---` section.

Syntax (one or more `@key: value` lines, then optional inline CSV fallback):

    --- data ---
    @source: https://example.com/sales.csv
    @format: csv          # csv | tsv | json | xlsx | auto (default)
    @cache: 1h            # 30s | 5m | 1h | 7d | 0 / off
    @timeout: 10          # seconds, default 15
    @header: Authorization: Bearer xyz   # repeatable
    @select: data.records # JSON dot-path drilldown to a list-of-dicts
    @encoding: utf-8      # default utf-8
    @sheet: Sheet1        # for xlsx sources

    Region,Q1,Q2          # ← inline fallback (used when remote denied / failed)
    North,100,120

Security:
  * `file://` URLs are always allowed (already on disk).
  * `http(s)://` URLs are only fetched when the caller passes `allow_remote=True`
    (or `--allow-remote` on the CLI). Otherwise the inline fallback is used.
  * No other URL schemes are accepted.

Failure handling:
  * If a fetch fails (network / HTTP error / parse error) the inline CSV is used
    as a fallback when present; otherwise the original error is re-raised.

Caching:
  * Successful fetches are written to `<cache_dir>/<hash>.<ext>` keyed by
    SHA1(url + sorted_headers + format + select). TTL controlled by `@cache`.
  * Default cache dir: `~/.cache/gridlang/sources/` (override via
    GRIDLANG_CACHE_DIR env var).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import io
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from gridlang.schema import parse_data


# ─── Spec ────────────────────────────────────────────────────────────────

ALLOWED_SCHEMES = ('http', 'https', 'file')
ALLOWED_FORMATS = ('csv', 'tsv', 'json', 'xlsx', 'auto')


@dataclass
class DataSourceSpec:
    """Parsed `@directive` block that controls how a data section is loaded."""
    source: str = ""                            # URL (http/https/file/empty)
    format: str = "auto"                        # csv | tsv | json | xlsx | auto
    cache_ttl: float = 3600.0                   # seconds; 0 disables caching
    timeout: float = 15.0
    headers: dict[str, str] = field(default_factory=dict)
    select: str = ""                            # JSON dot-path
    encoding: str = "utf-8"
    sheet: str = ""                             # xlsx sheet name

    @property
    def is_remote(self) -> bool:
        """True if a non-empty `@source` was set."""
        return bool(self.source)

    @property
    def scheme(self) -> str:
        if not self.source:
            return ""
        m = re.match(r'^([a-z]+)://', self.source)
        return m.group(1) if m else ""

    @property
    def needs_network(self) -> bool:
        return self.scheme in ('http', 'https')

    def cache_key(self) -> str:
        """Stable hash of the parameters that affect the fetched bytes."""
        h = hashlib.sha1()
        h.update(self.source.encode('utf-8'))
        h.update(b'\0')
        for k in sorted(self.headers.keys()):
            h.update(f'{k}:{self.headers[k]}\0'.encode('utf-8'))
        h.update(self.format.encode('utf-8'))
        h.update(b'\0')
        h.update(self.select.encode('utf-8'))
        return h.hexdigest()


# ─── Directive parser ────────────────────────────────────────────────────

_DIRECTIVE_LINE = re.compile(r'^\s*@([A-Za-z][\w]*)\s*:\s*(.*?)\s*$')

# A line counts as part of the directive block if it is blank, a `@key:` line,
# a comment line, or pure-whitespace. The first non-directive, non-blank line
# starts the inline CSV body.
_COMMENT_LINE = re.compile(r'^\s*#')


def parse_directives(section_text: str) -> tuple[DataSourceSpec, str]:
    """
    Split a data section into (DataSourceSpec, inline_csv).

    Lines beginning with `@key:` at the top are extracted as directives. Blank
    lines and comment lines (`# ...`) interspersed with directives are skipped.
    The first non-directive content line starts the inline CSV body.

    Backward compatibility: a section with no `@` lines yields an empty spec
    and the original text as the CSV body.
    """
    if not section_text or not section_text.strip():
        return DataSourceSpec(), section_text

    lines = section_text.splitlines()
    spec = DataSourceSpec()
    headers: list[tuple[str, str]] = []  # preserve order, allow duplicates collapsed
    body_start = 0

    in_directive_zone = True
    for i, line in enumerate(lines):
        if not in_directive_zone:
            break

        stripped = line.strip()
        if not stripped:
            # Blank lines separate directives from CSV body. Once we have seen
            # a directive, the first blank line ends the zone.
            if any(_DIRECTIVE_LINE.match(l) for l in lines[:i]):
                body_start = i + 1
                in_directive_zone = False
            else:
                body_start = i + 1  # skip leading blanks
            continue

        if _COMMENT_LINE.match(line):
            body_start = i + 1
            continue

        m = _DIRECTIVE_LINE.match(line)
        if not m:
            # First real content line — this is the start of the CSV body.
            body_start = i
            in_directive_zone = False
            break

        key, value = m.group(1).lower(), m.group(2).strip()
        if key == 'source':
            spec.source = value
        elif key == 'format':
            v = value.lower()
            if v not in ALLOWED_FORMATS:
                raise DataSourceError(f"@format must be one of {ALLOWED_FORMATS}, got {v!r}")
            spec.format = v
        elif key == 'cache':
            spec.cache_ttl = _parse_duration(value)
        elif key == 'timeout':
            try:
                spec.timeout = float(value)
            except ValueError:
                raise DataSourceError(f"@timeout must be a number, got {value!r}")
        elif key == 'header':
            # "Header-Name: Header-Value"
            if ':' not in value:
                raise DataSourceError(f"@header must be 'Name: Value', got {value!r}")
            hk, hv = value.split(':', 1)
            headers.append((hk.strip(), hv.strip()))
        elif key == 'select':
            spec.select = _strip_quotes(value)
        elif key == 'encoding':
            spec.encoding = _strip_quotes(value) or "utf-8"
        elif key == 'sheet':
            spec.sheet = _strip_quotes(value)
        else:
            raise DataSourceError(f"Unknown directive @{key}")

        body_start = i + 1

    if headers:
        # Last value wins for duplicates.
        spec.headers = {k: v for k, v in headers}

    body = '\n'.join(lines[body_start:]).strip()

    # Validate scheme if a source was given.
    if spec.source:
        sch = spec.scheme
        if sch not in ALLOWED_SCHEMES:
            raise DataSourceError(
                f"@source scheme {sch!r} not allowed. Allowed: {ALLOWED_SCHEMES}"
            )
        if spec.format == 'auto':
            spec.format = _detect_format(spec.source)

    return spec, body


# ─── Resolution ──────────────────────────────────────────────────────────

class DataSourceError(Exception):
    """Raised when a data source directive is malformed or fetch fails fatally."""


def resolve(
    spec: DataSourceSpec,
    inline_csv: str = "",
    *,
    allow_remote: bool = False,
    cache_dir: Optional[Path] = None,
) -> tuple[pd.DataFrame, str]:
    """
    Load a DataFrame for a data section.

    Returns (df, source_label) where source_label describes where the data
    came from: "inline" / "remote:<url>" / "cache:<url>" / "fallback:<url>".

    The decision tree:
        no source           → parse inline_csv
        file:// source      → always fetch + parse
        http(s):// source:
            allow_remote=False → use inline_csv (label: "fallback:<url>")
            allow_remote=True  → fetch with cache; on error fall back to inline
                                 if inline is non-empty
    """
    if not spec.is_remote:
        return parse_data(inline_csv), "inline"

    if spec.scheme == 'file':
        df = _fetch_and_parse(spec, cache_dir=None)  # local files don't need caching
        return df, f"file:{spec.source}"

    # http / https
    if not allow_remote:
        if not inline_csv.strip():
            raise DataSourceError(
                f"Remote source {spec.source!r} requires --allow-remote. "
                f"Pass `allow_remote=True` or provide an inline CSV fallback."
            )
        return parse_data(inline_csv), f"fallback:{spec.source}"

    try:
        df = _fetch_and_parse(spec, cache_dir=cache_dir)
        return df, f"remote:{spec.source}"
    except Exception as e:
        if inline_csv.strip():
            return parse_data(inline_csv), f"fallback:{spec.source} ({type(e).__name__}: {e})"
        raise DataSourceError(f"Failed to fetch {spec.source!r}: {e}") from e


def _fetch_and_parse(spec: DataSourceSpec, cache_dir: Optional[Path]) -> pd.DataFrame:
    """Fetch raw bytes (with cache) and decode them into a DataFrame."""
    raw = _fetch_bytes(spec, cache_dir)
    return _decode(raw, spec)


def _fetch_bytes(spec: DataSourceSpec, cache_dir: Optional[Path]) -> bytes:
    """Fetch the raw bytes for a source, using a TTL-bounded cache when applicable."""
    if spec.scheme == 'file':
        path = _file_url_to_path(spec.source)
        return path.read_bytes()

    # http(s) — try cache first.
    if cache_dir is None and spec.cache_ttl > 0:
        cache_dir = _default_cache_dir()
    if cache_dir and spec.cache_ttl > 0:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{spec.cache_key()}.{_ext_for_format(spec.format)}"
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < spec.cache_ttl:
                return cache_path.read_bytes()

    req = urllib.request.Request(spec.source, headers=spec.headers or {})
    try:
        with urllib.request.urlopen(req, timeout=spec.timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise DataSourceError(f"HTTP {e.code} fetching {spec.source}") from e
    except urllib.error.URLError as e:
        raise DataSourceError(f"Network error fetching {spec.source}: {e.reason}") from e
    except (OSError, TimeoutError) as e:
        raise DataSourceError(f"Connection error fetching {spec.source}: {e}") from e

    # Persist to cache.
    if cache_dir and spec.cache_ttl > 0:
        cache_path = Path(cache_dir) / f"{spec.cache_key()}.{_ext_for_format(spec.format)}"
        try:
            cache_path.write_bytes(data)
        except OSError:
            pass  # cache failures are non-fatal

    return data


def _decode(raw: bytes, spec: DataSourceSpec) -> pd.DataFrame:
    """Decode raw bytes into a DataFrame according to spec.format."""
    fmt = spec.format if spec.format != 'auto' else _detect_format(spec.source)

    if fmt in ('csv', 'tsv'):
        sep = '\t' if fmt == 'tsv' else ','
        text = raw.decode(spec.encoding, errors='replace')
        df = pd.read_csv(io.StringIO(text), sep=sep)
        return df

    if fmt == 'json':
        text = raw.decode(spec.encoding, errors='replace')
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            raise DataSourceError(f"Invalid JSON from {spec.source}: {e}") from e

        if spec.select:
            payload = _json_select(payload, spec.select)

        if isinstance(payload, list) and all(isinstance(x, dict) for x in payload):
            return pd.DataFrame(payload)
        if isinstance(payload, dict):
            # Single object → one-row DataFrame.
            return pd.DataFrame([payload])
        if isinstance(payload, list):
            # List of scalars → single-column DataFrame.
            return pd.DataFrame({'value': payload})
        raise DataSourceError(
            f"JSON @select={spec.select!r} did not resolve to a list of records "
            f"(got {type(payload).__name__})"
        )

    if fmt == 'xlsx':
        try:
            import openpyxl  # noqa: F401  (read_excel needs it for .xlsx)
        except ImportError as e:
            raise DataSourceError("openpyxl is required to read xlsx sources") from e
        sheet = spec.sheet or 0
        df = pd.read_excel(io.BytesIO(raw), sheet_name=sheet)
        return df

    raise DataSourceError(f"Unsupported format: {fmt}")


def _json_select(payload, path: str):
    """Walk a JSON document via dot-path: 'data.items' / 'top.list[0].x'."""
    if not path:
        return payload
    cur = payload
    # Tokenize: splits on '.' but keeps `[N]` brackets intact.
    tokens = re.findall(r'[^.\[\]]+|\[\d+\]', path)
    for tok in tokens:
        if tok.startswith('[') and tok.endswith(']'):
            idx = int(tok[1:-1])
            if not isinstance(cur, list):
                raise DataSourceError(f"@select: cannot index {type(cur).__name__} with [{idx}]")
            cur = cur[idx]
        else:
            if not isinstance(cur, dict):
                raise DataSourceError(f"@select: cannot get key {tok!r} from {type(cur).__name__}")
            if tok not in cur:
                raise DataSourceError(f"@select: key {tok!r} missing in JSON payload")
            cur = cur[tok]
    return cur


# ─── Helpers ─────────────────────────────────────────────────────────────

_DURATION_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*([smhd]?)$', re.IGNORECASE)
_DURATION_MULT = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, '': 1}


def _parse_duration(value: str) -> float:
    """Parse '30s' / '5m' / '1h' / '7d' / '0' / 'off' to seconds."""
    v = value.strip().lower()
    if v in ('0', 'off', 'none', 'no', 'false'):
        return 0.0
    m = _DURATION_RE.match(v)
    if not m:
        raise DataSourceError(f"@cache must be like '30s', '5m', '1h', '7d', or '0', got {value!r}")
    return float(m.group(1)) * _DURATION_MULT[m.group(2)]


def _detect_format(url: str) -> str:
    """Infer format from URL path extension."""
    base = url.split('?', 1)[0].rsplit('/', 1)[-1].lower()
    if base.endswith('.csv'):  return 'csv'
    if base.endswith('.tsv'):  return 'tsv'
    if base.endswith('.json'): return 'json'
    if base.endswith(('.xlsx', '.xls')): return 'xlsx'
    return 'csv'


def _ext_for_format(fmt: str) -> str:
    return {'csv': 'csv', 'tsv': 'tsv', 'json': 'json',
            'xlsx': 'xlsx', 'auto': 'bin'}.get(fmt, 'bin')


def _file_url_to_path(url: str) -> Path:
    """Convert file://... URL to a Path."""
    if url.startswith('file://'):
        path = url[len('file://'):]
    else:
        path = url
    # On POSIX file:///abs/path → /abs/path; on Windows file:///C:/x → C:/x
    if path.startswith('/') and len(path) > 2 and path[2] == ':':
        path = path[1:]  # strip leading slash for Windows-style paths
    return Path(path)


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        return s[1:-1]
    return s


def _default_cache_dir() -> Path:
    """Return the default cache directory; honors GRIDLANG_CACHE_DIR."""
    override = os.environ.get('GRIDLANG_CACHE_DIR')
    if override:
        return Path(override)
    return Path(os.path.expanduser('~/.cache/gridlang/sources'))


# ─── Document-level helper ──────────────────────────────────────────────

def load_dataframes(
    doc,                         # GridDocument
    *,
    allow_remote: bool = False,
    cache_dir: Optional[Path] = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """
    Resolve every sheet in a GridDocument into a DataFrame.

    Returns:
        sheets:        sheet_name → DataFrame
        source_labels: sheet_name → human-readable source label
    """
    sheets: dict[str, pd.DataFrame] = {}
    labels: dict[str, str] = {}
    for name, raw in doc.sheets_raw.items():
        spec = doc.data_specs.get(name) if hasattr(doc, 'data_specs') else None
        spec = spec or DataSourceSpec()
        df, label = resolve(spec, raw, allow_remote=allow_remote, cache_dir=cache_dir)
        sheets[name] = df
        labels[name] = label
    return sheets, labels
