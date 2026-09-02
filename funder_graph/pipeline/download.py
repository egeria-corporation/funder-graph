"""Stage 1: fetch the IRS bulk archives, politely, resumably, with provenance.

What the spec asks for and why each piece is here:

* **Enumerate; never construct filenames.** Naming differs across years, and the year
  directory listing at ``apps.irs.gov/pub/epostcard/990/xml/{year}/`` returns a 302 to the IRS
  404 page (verified 2026-09-01). The enumerable source is the landing page, whose ``href``s
  are read and grouped by year.
* **Record provenance before and after.** Each file's resolved URL, ``ETag``,
  ``Last-Modified``, byte size and SHA-256 go into ``build/state.duckdb``. The publish stage
  copies them into ``manifest.json``: a build is reproducible only if its inputs are named.
* **Resume, retry, back off, cap at four connections.** The IRS is not a CDN. A partial
  download is continued with a ``Range`` request; the hash is computed over the whole file,
  including the bytes already on disk, so a resumed file and a fresh one hash identically.
* **A size mismatch is an error, not a success.** The ``.part`` file is kept for the next
  attempt; nothing half-written is ever renamed into place.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx

LANDING_PAGE = "https://www.irs.gov/charities-non-profits/form-990-series-downloads"
XML_BASE = "https://apps.irs.gov/pub/epostcard/990/xml/"
DEFAULT_USER_AGENT = "funder-graph/0.1 (+https://github.com/egeria-corporation/funder-graph)"

# Four is the spec's number. It is a shared public resource and this is a courtesy.
MAX_CONNECTIONS = 4
ATTEMPTS = 5
CHUNK = 1 << 20  # 1 MiB

_HREF = re.compile(r'href="([^"]+?\.(?:zip|csv))"', re.I)
_YEAR = re.compile(r"/990/xml/(\d{4})/")


@dataclass
class SourceFile:
    """One IRS bulk file, as enumerated and then as downloaded."""

    url: str
    year: int
    filename: str
    etag: str | None = None
    last_modified: str | None = None
    expected_bytes: int | None = None
    bytes: int | None = None
    sha256: str | None = None
    status: str = "planned"  # planned | partial | complete | error
    error: str | None = None

    @property
    def is_index(self) -> bool:
        return self.filename.lower().endswith(".csv")


def user_agent() -> str:
    return os.environ.get("FUNDER_GRAPH_USER_AGENT") or DEFAULT_USER_AGENT


def _client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent()},
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, read=300.0),
        transport=transport,
    )


def enumerate_files(
    years: list[int], transport: httpx.BaseTransport | None = None
) -> list[SourceFile]:
    """Every ZIP and index CSV the landing page links for the requested years.

    Returns them in a stable order (year, then filename) so two runs plan identically.
    """
    with _client(transport) as client:
        response = client.get(LANDING_PAGE)
        response.raise_for_status()
        html = response.text

    wanted = set(years)
    found: dict[str, SourceFile] = {}
    for href in _HREF.findall(html):
        url = httpx.URL(LANDING_PAGE).join(href).copy_with(fragment=None)
        text = str(url)
        if not text.startswith(XML_BASE):
            continue
        match = _YEAR.search(text)
        if not match:
            continue
        year = int(match.group(1))
        if year in wanted:
            found[text] = SourceFile(url=text, year=year, filename=text.rsplit("/", 1)[-1])
    return sorted(found.values(), key=lambda f: (f.year, f.filename))


def parse_years(spec: str) -> list[int]:
    """``2023`` or ``2019-2026`` or ``2019,2021,2023``."""
    years: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            years.update(range(int(lo), int(hi) + 1))
        elif part:
            years.add(int(part))
    return sorted(years)


# ---------------------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_files (
  url            VARCHAR PRIMARY KEY,
  year           INTEGER NOT NULL,
  filename       VARCHAR NOT NULL,
  etag           VARCHAR,
  last_modified  VARCHAR,
  expected_bytes BIGINT,
  bytes          BIGINT,
  sha256         VARCHAR,
  status         VARCHAR NOT NULL,
  error          VARCHAR,
  downloaded_at  TIMESTAMP
);
"""


class State:
    """``build/state.duckdb``: what has been fetched, and its checksums."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(path))
        self.conn.execute(_SCHEMA)

    def get(self, url: str) -> SourceFile | None:
        row = self.conn.execute(
            "SELECT url, year, filename, etag, last_modified, expected_bytes, bytes, sha256, "
            "status, error FROM source_files WHERE url = ?",
            [url],
        ).fetchone()
        return SourceFile(*row) if row else None

    def put(self, f: SourceFile) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO source_files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                f.url,
                f.year,
                f.filename,
                f.etag,
                f.last_modified,
                f.expected_bytes,
                f.bytes,
                f.sha256,
                f.status,
                f.error,
                datetime.now(UTC) if f.status == "complete" else None,
            ],
        )

    def completed(self) -> list[SourceFile]:
        rows = self.conn.execute(
            "SELECT url, year, filename, etag, last_modified, expected_bytes, bytes, sha256, "
            "status, error FROM source_files WHERE status = 'complete' ORDER BY year, filename"
        ).fetchall()
        return [SourceFile(*r) for r in rows]

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------------------
# Transfer
# ---------------------------------------------------------------------------------------


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _head(client: httpx.Client, f: SourceFile) -> None:
    response = client.head(f.url)
    response.raise_for_status()
    f.etag = response.headers.get("etag")
    f.last_modified = response.headers.get("last-modified")
    length = response.headers.get("content-length")
    f.expected_bytes = int(length) if length and length.isdigit() else None


def _transfer(client: httpx.Client, f: SourceFile, dest: Path) -> None:
    """Fetch ``f`` to ``dest``, resuming a ``.part`` if one exists. Sets bytes and sha256."""
    part = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    have = 0
    if part.exists():
        # A resumed file must hash exactly like a fresh one: fold in what is already there.
        with part.open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
                have += len(chunk)

    headers = {"Range": f"bytes={have}-"} if have else {}
    with client.stream("GET", f.url, headers=headers) as response:
        if have and response.status_code == 200:
            # The server ignored the range. Start over rather than append a second copy.
            digest = hashlib.sha256()
            have = 0
            mode = "wb"
        elif have and response.status_code == 206:
            mode = "ab"
        else:
            response.raise_for_status()
            mode = "wb"
        with part.open(mode) as handle:
            for chunk in response.iter_bytes(CHUNK):
                handle.write(chunk)
                digest.update(chunk)
                have += len(chunk)

    if f.expected_bytes is not None and have != f.expected_bytes:
        raise OSError(f"{f.filename}: got {have:,} bytes, server announced {f.expected_bytes:,}")

    part.replace(dest)
    f.bytes = have
    f.sha256 = digest.hexdigest()


def _fetch_one(f: SourceFile, work_dir: Path, transport: httpx.BaseTransport | None) -> SourceFile:
    dest = work_dir / "raw" / str(f.year) / f.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    delay = 2.0
    with _client(transport) as client:
        for attempt in range(1, ATTEMPTS + 1):
            try:
                _head(client, f)
                _transfer(client, f, dest)
                f.status = "complete"
                f.error = None
                return f
            except (httpx.TransportError, httpx.HTTPStatusError, OSError) as error:
                f.status = (
                    "partial" if dest.with_suffix(dest.suffix + ".part").exists() else "error"
                )
                f.error = f"attempt {attempt}: {error}"
                if attempt == ATTEMPTS:
                    f.status = "error"
                    return f
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
    return f


def _already_complete(f: SourceFile, prior: SourceFile | None, dest: Path) -> bool:
    """Skip work that is done: same URL, recorded complete, file present at the recorded size."""
    return (
        prior is not None
        and prior.status == "complete"
        and dest.exists()
        and prior.bytes is not None
        and dest.stat().st_size == prior.bytes
    )


def download(
    years: list[int],
    work_dir: Path,
    *,
    transport: httpx.BaseTransport | None = None,
    max_connections: int = MAX_CONNECTIONS,
    progress=None,
) -> list[SourceFile]:
    """Download every bulk file for ``years`` into ``work_dir/raw/{year}/``, recording state.

    ``progress``, if given, is called with each finished ``SourceFile`` as it lands.
    """
    state = State(work_dir / "state.duckdb")
    try:
        planned = enumerate_files(years, transport)
        results: list[SourceFile] = []
        todo: list[SourceFile] = []
        for f in planned:
            dest = work_dir / "raw" / str(f.year) / f.filename
            prior = state.get(f.url)
            if _already_complete(f, prior, dest):
                results.append(prior)  # type: ignore[arg-type]
                if progress:
                    progress(prior)
            else:
                state.put(f)
                todo.append(f)

        # One client per worker: httpx clients are not documented as safe for concurrent
        # streaming, and the cost of four is nothing.
        with ThreadPoolExecutor(max_workers=max_connections) as pool:
            futures = {pool.submit(_fetch_one, f, work_dir, transport): f for f in todo}
            for future in as_completed(futures):
                f = future.result()
                state.put(f)
                results.append(f)
                if progress:
                    progress(f)
        return sorted(results, key=lambda f: (f.year, f.filename))
    finally:
        state.close()
