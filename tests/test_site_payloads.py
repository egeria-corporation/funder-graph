"""The site ingest: payloads that agree with the Parquet, and the two honesty rules."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from test_write import BRODERICK, HARDSHIP, SCHED_I, records

from funder_graph.pipeline import site_payloads
from funder_graph.pipeline.site_payloads import build_site
from funder_graph.pipeline.write import write_shard


@pytest.fixture
def parquet_dir(tmp_path: Path) -> Path:
    out = tmp_path / "parquet"
    write_shard(records(BRODERICK) + records(SCHED_I) + records(HARDSHIP), out, 2023, 0)
    return out


def _sum(parquet_dir: Path, ein: str, amount_type: str) -> tuple[int, int]:
    g = (parquet_dir / "grants" / "filing_year=2023" / "part-0000.parquet").as_posix()
    total, n = duckdb.sql(
        f"SELECT COALESCE(SUM(amount_usd), 0), COUNT(*) FROM read_parquet('{g}') "
        f"WHERE funder_ein = '{ein}' AND amount_type = '{amount_type}'"
    ).fetchone()
    return int(total), int(n)


class TestFunderPayloads:
    def test_totals_agree_with_the_parquet_and_are_never_summed_across_types(
        self, parquet_dir: Path, tmp_path: Path
    ) -> None:
        b = build_site(parquet_dir, None, tmp_path / "site", now=datetime(2026, 9, 2, tzinfo=UTC))
        assert b.dataset_version and b.built_at == "2026-09-02T00:00:00+00:00"
        payload = json.loads((b.out_dir / "funders" / "846725611.json").read_text(encoding="utf-8"))
        paid, paid_n = _sum(parquet_dir, "846725611", "paid")
        fut, fut_n = _sum(parquet_dir, "846725611", "approved_future")
        assert payload["totals"]["paid_usd"] == paid and payload["totals"]["paid_count"] == paid_n
        assert payload["totals"]["approved_future_usd"] == fut
        assert payload["totals"]["approved_future_count"] == fut_n
        assert "total_usd" not in payload["totals"]  # no field that could be the sum of both
        assert payload["chunked"] is False and "pages" not in payload
        assert payload["dataset_version"] == b.dataset_version

    def test_recent_grants_carry_tier_and_provenance(
        self, parquet_dir: Path, tmp_path: Path
    ) -> None:
        b = build_site(parquet_dir, None, tmp_path / "site")
        payload = json.loads((b.out_dir / "funders" / "846725611.json").read_text(encoding="utf-8"))
        row = payload["recent_grants"][0]
        assert {"amount_usd", "amount_type", "match_tier", "object_id", "recipient_name"} <= set(
            row
        )
        assert payload["filings"][0]["object_id"] == row["object_id"]
        assert payload["recent_grants"] == sorted(
            payload["recent_grants"], key=lambda r: (-r["tax_year"], -(r["amount_usd"] or 0))
        )

    def test_individuals_never_appear(self, parquet_dir: Path, tmp_path: Path) -> None:
        # The HARDSHIP fixture is scholarship rows to named people; the edge list excludes
        # them at write time and the site must not resurrect them.
        b = build_site(parquet_dir, None, tmp_path / "site")
        blob = "".join(
            p.read_text(encoding="utf-8") for p in (b.out_dir / "funders").rglob("*.json")
        )
        assert "individual" not in blob

    def test_large_funders_are_chunked_and_paged(
        self, parquet_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(site_payloads, "CHUNK_THRESHOLD", 2)
        monkeypatch.setattr(site_payloads, "PAGE_ROWS", 2)
        b = build_site(parquet_dir, None, tmp_path / "site")
        assert b.funders_chunked >= 1
        index = json.loads(
            (b.out_dir / "funders" / "846725611" / "index.json").read_text(encoding="utf-8")
        )
        assert index["chunked"] is True
        year, n_pages = next(iter(index["pages"].items()))
        pages = sorted((b.out_dir / "funders" / "846725611" / year).glob("p*.json"))
        assert len(pages) == n_pages >= 2
        rows = [r for p in pages for r in json.loads(p.read_text(encoding="utf-8"))["grants"]]
        assert len(rows) == index["totals"]["grant_rows"]
        assert all(len(json.loads(p.read_text(encoding="utf-8"))["grants"]) <= 2 for p in pages)


class TestRecipientsIndexAndSitemaps:
    def test_recipient_pages_only_for_resolved_eins(
        self, parquet_dir: Path, tmp_path: Path
    ) -> None:
        b = build_site(parquet_dir, None, tmp_path / "site")
        g = (parquet_dir / "grants" / "filing_year=2023" / "part-0000.parquet").as_posix()
        resolved = {
            e
            for (e,) in duckdb.sql(
                f"SELECT DISTINCT recipient_ein_resolved FROM read_parquet('{g}') "
                "WHERE recipient_ein_resolved IS NOT NULL"
            ).fetchall()
        }
        written = (
            {p.stem for p in (b.out_dir / "recipients").glob("*.json")}
            if (b.out_dir / "recipients").exists()
            else set()
        )
        assert written == resolved and b.recipients == len(resolved)

    def test_d1_rows_and_sitemaps_cover_every_page(self, parquet_dir: Path, tmp_path: Path) -> None:
        b = build_site(parquet_dir, None, tmp_path / "site")
        sql = "".join(
            p.read_text(encoding="utf-8") for p in sorted((b.out_dir / "d1").glob("*.sql"))
        )
        assert sql.count("INSERT INTO funders VALUES") == b.funders
        assert sql.count("INSERT INTO recipients VALUES") == b.recipients
        assert sql.count("INSERT INTO entity_search") == b.funders + b.recipients
        assert "INSERT INTO dataset_vintage VALUES" in sql
        index = (b.out_dir / "sitemaps" / "sitemap-index.xml").read_text(encoding="utf-8")
        assert "funders-00001.xml.gz" in index
        with gzip.open(
            b.out_dir / "sitemaps" / "funders-00001.xml.gz", "rt", encoding="utf-8"
        ) as fh:
            body = fh.read()
        assert body.count("<loc>") == b.funders
        assert "https://funders.opengrants.io/funders/846725611" in body
        manifest = json.loads((b.out_dir / "site-manifest.json").read_text(encoding="utf-8"))
        assert manifest["funders"] == b.funders and manifest["sample_limit"] is None

    def test_limit_keeps_the_top_funders_by_dollars_and_says_so(
        self, parquet_dir: Path, tmp_path: Path
    ) -> None:
        b = build_site(parquet_dir, None, tmp_path / "site", limit=1)
        assert b.funders == 1
        manifest = json.loads((b.out_dir / "site-manifest.json").read_text(encoding="utf-8"))
        assert manifest["sample_limit"] == 1
