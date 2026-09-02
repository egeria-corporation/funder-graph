"""The download stage, against a mock IRS.

Every behaviour here is one the real IRS made necessary: the directory listing 404s so
enumeration reads the landing page; archives are gigabytes so resume must work and must hash
identically to a fresh fetch; and a truncated transfer must be an error that keeps its partial
file, never a success that renames garbage into place.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from funder_graph.pipeline.download import (
    LANDING_PAGE,
    SourceFile,
    State,
    download,
    enumerate_files,
    parse_years,
)

BASE = "https://apps.irs.gov/pub/epostcard/990/xml"
PAYLOAD_A = bytes(range(256)) * 400  # 102,400 bytes, non-trivial content
PAYLOAD_B = b"RETURN_ID,EIN,OBJECT_ID\n1,271067272,202343159349100234\n"

LANDING_HTML = f"""
<html><body>
<a href="{BASE}/2023/2023_TEOS_XML_12A.zip">2023 12A</a>
<a href="{BASE}/2023/index_2023.csv">2023 index</a>
<a href="{BASE}/2023/2023_TEOS_XML_01A.zip">2023 01A</a>
<a href="{BASE}/2022/2022_TEOS_XML_01A.zip">2022 01A</a>
<a href="https://www.irs.gov/pub/irs-pdf/f990.pdf">not a bulk file</a>
<a href="https://example.com/other/2023/decoy.zip">wrong host, right year</a>
<a href="{BASE}/2023/2023_TEOS_XML_12A.zip#dup">duplicate with fragment</a>
</body></html>
"""

FILES = {
    f"{BASE}/2023/2023_TEOS_XML_12A.zip": PAYLOAD_A,
    f"{BASE}/2023/2023_TEOS_XML_01A.zip": PAYLOAD_A[:5000],
    f"{BASE}/2023/index_2023.csv": PAYLOAD_B,
    f"{BASE}/2022/2022_TEOS_XML_01A.zip": PAYLOAD_A[:100],
}


class FakeIRS:
    """A transport that serves the files above, honours Range, and counts requests."""

    def __init__(self, *, honour_range: bool = True, truncate: set[str] | None = None) -> None:
        self.honour_range = honour_range
        self.truncate = truncate or set()
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((request.method, url))
        if url == LANDING_PAGE:
            return httpx.Response(200, text=LANDING_HTML)
        body = FILES.get(url)
        if body is None:
            return httpx.Response(404)
        headers = {
            "ETag": f'"{hashlib.md5(body).hexdigest()[:12]}"',
            "Last-Modified": "Thu, 06 Mar 2025 17:04:34 GMT",
            "Content-Length": str(len(body)),
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        if url in self.truncate:
            # Announce the full size, deliver half. What a dropped connection looks like.
            return httpx.Response(200, headers=headers, content=body[: len(body) // 2])
        rng = request.headers.get("Range")
        if rng and self.honour_range:
            start = int(rng.removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, headers=headers, content=body[start:])
        return httpx.Response(200, headers=headers, content=body)


def transport(irs: FakeIRS) -> httpx.MockTransport:
    return httpx.MockTransport(irs)


class TestEnumeration:
    def test_reads_the_landing_page_not_a_directory_listing(self) -> None:
        irs = FakeIRS()
        files = enumerate_files([2023], transport(irs))
        # The index comes first: it is what `build extract` needs to start on archives as
        # they land, and plain filename order would fetch it last.
        assert [f.filename for f in files] == [
            "index_2023.csv",
            "2023_TEOS_XML_01A.zip",
            "2023_TEOS_XML_12A.zip",
        ]
        assert irs.requests == [("GET", LANDING_PAGE)]

    def test_filters_by_year_and_by_the_irs_bulk_base(self) -> None:
        files = enumerate_files([2022], transport(FakeIRS()))
        assert [f.filename for f in files] == ["2022_TEOS_XML_01A.zip"]
        # The example.com decoy shares the year and extension and must not appear.
        assert all(f.url.startswith(BASE) for f in files)

    def test_dedupes_a_fragment_variant(self) -> None:
        files = enumerate_files([2023], transport(FakeIRS()))
        assert len([f for f in files if f.filename == "2023_TEOS_XML_12A.zip"]) == 1

    def test_marks_the_index_csv(self) -> None:
        files = enumerate_files([2023], transport(FakeIRS()))
        assert [f.filename for f in files if f.is_index] == ["index_2023.csv"]


class TestParseYears:
    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("2023", [2023]),
            ("2019-2021", [2019, 2020, 2021]),
            ("2019,2023, 2021", [2019, 2021, 2023]),
        ],
    )
    def test_forms(self, spec, expected) -> None:
        assert parse_years(spec) == expected


class TestDownload:
    def test_fetches_records_provenance_and_hashes(self, tmp_path: Path) -> None:
        irs = FakeIRS()
        results = download([2023], tmp_path, transport=transport(irs), max_connections=2)
        by_name = {f.filename: f for f in results}

        big = by_name["2023_TEOS_XML_12A.zip"]
        assert big.status == "complete"
        assert big.bytes == len(PAYLOAD_A) == big.expected_bytes
        assert big.sha256 == hashlib.sha256(PAYLOAD_A).hexdigest()
        assert big.etag and big.last_modified
        assert (tmp_path / "raw" / "2023" / "2023_TEOS_XML_12A.zip").read_bytes() == PAYLOAD_A
        assert not (tmp_path / "raw" / "2023" / "2023_TEOS_XML_12A.zip.part").exists()

        # The state database holds the same record the manifest will later need.
        state = State(tmp_path / "state.duckdb")
        try:
            rows = state.completed()
        finally:
            state.close()
        assert {r.filename for r in rows} == {
            "2023_TEOS_XML_01A.zip",
            "2023_TEOS_XML_12A.zip",
            "index_2023.csv",
        }
        assert all(r.sha256 and r.bytes for r in rows)

    def test_resume_continues_from_the_part_file_and_hashes_the_whole(self, tmp_path: Path) -> None:
        dest_dir = tmp_path / "raw" / "2023"
        dest_dir.mkdir(parents=True)
        # Simulate a dropped connection: the first 40,000 bytes are already on disk.
        (dest_dir / "2023_TEOS_XML_12A.zip.part").write_bytes(PAYLOAD_A[:40_000])

        irs = FakeIRS()
        results = download([2023], tmp_path, transport=transport(irs))
        big = next(f for f in results if f.filename == "2023_TEOS_XML_12A.zip")

        ranged = [u for m, u in irs.requests if m == "GET" and u.endswith("2023_TEOS_XML_12A.zip")]
        assert ranged, "the big file was never fetched"
        assert big.status == "complete"
        assert big.bytes == len(PAYLOAD_A)
        # The hash covers the resumed prefix plus the fetched remainder - identical to a
        # fresh download. This is the property that makes resume safe to trust.
        assert big.sha256 == hashlib.sha256(PAYLOAD_A).hexdigest()

    def test_resume_restarts_cleanly_when_the_server_ignores_range(self, tmp_path: Path) -> None:
        dest_dir = tmp_path / "raw" / "2023"
        dest_dir.mkdir(parents=True)
        (dest_dir / "2023_TEOS_XML_12A.zip.part").write_bytes(b"garbage that must not survive")

        results = download([2023], tmp_path, transport=transport(FakeIRS(honour_range=False)))
        big = next(f for f in results if f.filename == "2023_TEOS_XML_12A.zip")
        assert big.status == "complete"
        assert big.sha256 == hashlib.sha256(PAYLOAD_A).hexdigest()

    def test_second_run_skips_completed_files_without_refetching(self, tmp_path: Path) -> None:
        first = FakeIRS()
        download([2023], tmp_path, transport=transport(first))

        second = FakeIRS()
        results = download([2023], tmp_path, transport=transport(second))
        gets = [u for m, u in second.requests if m == "GET" and u != LANDING_PAGE]
        assert gets == [], f"re-fetched: {gets}"
        assert all(f.status == "complete" for f in results)

    def test_a_truncated_transfer_is_an_error_that_keeps_its_part_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No backoff sleeps in a test.
        monkeypatch.setattr("funder_graph.pipeline.download.time.sleep", lambda _s: None)
        url = f"{BASE}/2023/2023_TEOS_XML_01A.zip"
        results = download([2023], tmp_path, transport=transport(FakeIRS(truncate={url})))
        small = next(f for f in results if f.filename == "2023_TEOS_XML_01A.zip")

        assert small.status == "error"
        assert small.error and "announced" in small.error
        assert small.sha256 is None
        final = tmp_path / "raw" / "2023" / "2023_TEOS_XML_01A.zip"
        assert not final.exists(), "a short file must never be renamed into place"
        assert final.with_suffix(".zip.part").exists()

        # The others are unaffected: one bad archive does not poison the run.
        assert {f.status for f in results if f.filename != "2023_TEOS_XML_01A.zip"} == {"complete"}

    def test_progress_callback_sees_every_file(self, tmp_path: Path) -> None:
        seen: list[SourceFile] = []
        download([2023], tmp_path, transport=transport(FakeIRS()), progress=seen.append)
        assert sorted(f.filename for f in seen) == [
            "2023_TEOS_XML_01A.zip",
            "2023_TEOS_XML_12A.zip",
            "index_2023.csv",
        ]
