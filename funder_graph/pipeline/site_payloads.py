"""Stage 7: the hosted site's payloads, from the same Parquet the dataset ships.

funders.opengrants.io renders at the edge from precomputed JSON, because a Worker cannot run
DuckDB and this stage already has the whole dataset in one. For a version it writes::

    build/site/<version>/
      funders/<ein>.json                 # one per funder; the request path is one R2 GET
      funders/<ein>/index.json           # large funders: the summary ...
      funders/<ein>/<tax_year>/p<n>.json # ... and one file per 1,000 rows of each tax year
      recipients/<ein>.json              # one per resolved recipient (tiers A-D)
      d1/NNNN.sql                        # the index, in INSERT batches
      sitemaps/sitemap-index.xml, funders-00001.xml.gz, recipients-00001.xml.gz, browse.xml.gz
      site-manifest.json                 # counts, for the ingest smoke test

Every figure a page shows comes from here, and two rules the README makes are enforced
here rather than in a template: ``paid`` and ``approved_future`` are never summed - they are
separate fields everywhere - and a recipient page exists only for a *resolved* EIN, with the
match tier carried on every row so the page can say how it knows.

Funder identity comes from the filings; city, NTEE and subsection come from the BMF when the
EIN is there. Recipient identity comes from the BMF row the matcher chose.

The job is batched by EIN prefix so memory is bounded by a hundredth of the corpus, not the
corpus. ``limit`` keeps the top-N funders by dollars paid (and only their recipients) for a
local sample; the spec says to build the site against one when the dataset is not yet
published, and to say so - the manifest records the limit.
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

CHUNK_THRESHOLD = 2_000  # grant rows; above this a funder is split by tax year
PAGE_ROWS = 1_000  # rows per page inside a chunked year; a Worker parses one page per request
TOP_RECIPIENTS = 250
# A funder under the chunk threshold carries every row in its payload, so the page shows the
# complete list; a chunked funder carries this many most-recent rows as a preview and the
# rest in year pages.
RECENT_GRANTS = CHUNK_THRESHOLD
PREVIEW_GRANTS = 500  # what a chunked funder's summary page shows; its year pages have the rest
SITEMAP_URLS = 50_000
D1_BATCH = 500
SITE_ORIGIN = "https://funders.opengrants.io"


@dataclass
class SiteBuild:
    dataset_version: str
    built_at: str
    out_dir: Path
    funders: int = 0
    funders_chunked: int = 0
    recipients: int = 0
    grant_rows: int = 0
    limit: int | None = None


def _q(s: str | None) -> str:
    """SQL string literal."""
    return "NULL" if s is None else "'" + s.replace("'", "''") + "'"


def _n(v: int | float | None) -> str:
    return "NULL" if v is None else str(int(v))


def _grant_files(parquet_dir: Path, years: list[int] | None) -> list[Path]:
    parts = sorted((parquet_dir / "grants").glob("filing_year=*"))
    if years:
        wanted = {f"filing_year={y}" for y in years}
        parts = [p for p in parts if p.name in wanted]
    return [f for p in parts for f in sorted(p.glob("*.parquet"))]


def _list(paths: list[Path]) -> str:
    return "[" + ", ".join(f"'{p.as_posix()}'" for p in paths) + "]"


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


# --- funders -------------------------------------------------------------------------------

_GRANT_COLS = (
    "grant_id, tax_year, amount_type, amount_usd, noncash_amount_usd, recipient_name_raw, "
    "recipient_ein_resolved, match_tier, match_confidence, match_method, grant_purpose, "
    "recipient_city, recipient_state, recipient_type, object_id"
)


def _grant_row(r: tuple) -> dict:
    (
        grant_id,
        tax_year,
        amount_type,
        amount,
        noncash,
        name,
        ein,
        tier,
        conf,
        method,
        purpose,
        city,
        state,
        rtype,
        object_id,
    ) = r
    return {
        "grant_id": grant_id,
        "tax_year": tax_year,
        "amount_type": amount_type,
        "amount_usd": amount,
        "noncash_amount_usd": noncash,
        "recipient_name": name,
        "recipient_ein": ein,
        "match_tier": tier,
        "match_confidence": conf,
        "match_method": method,
        "purpose": purpose,
        "city": city,
        "state": state,
        "recipient_type": rtype,
        "object_id": object_id,
    }


def _emit_funder_batch(
    conn: duckdb.DuckDBPyConnection, prefix: str, build: SiteBuild, d1: list[str], urls: list[str]
) -> None:
    """Every funder whose EIN starts with ``prefix``: summaries, chunks, D1 rows, sitemap URLs."""
    where = f"funder_ein LIKE '{prefix}%'" + (
        " AND funder_ein IN (SELECT ein FROM keep)" if build.limit else ""
    )
    ident = conn.execute(
        f"""
        SELECT g.funder_ein, any_value(g.funder_name), any_value(g.funder_state),
               any_value(g.funder_form_type), any_value(b.city), any_value(b.ntee_cd),
               any_value(b.subsection),
               SUM(CASE WHEN amount_type='paid' THEN amount_usd END),
               SUM(CASE WHEN amount_type='paid' THEN 1 ELSE 0 END),
               COUNT(DISTINCT CASE WHEN amount_type='paid' THEN COALESCE(recipient_ein_resolved, recipient_name_normalized || '|' || COALESCE(recipient_state,'')) END),
               SUM(CASE WHEN amount_type='approved_future' THEN amount_usd END),
               SUM(CASE WHEN amount_type='approved_future' THEN 1 ELSE 0 END),
               MIN(tax_year), MAX(tax_year), MAX(filing_submission_date), COUNT(*)
        FROM grants g LEFT JOIN bmf b ON b.ein = g.funder_ein
        WHERE {where}
        GROUP BY g.funder_ein ORDER BY g.funder_ein
        """
    ).fetchall()
    if not ident:
        return
    years: dict[str, list[dict]] = defaultdict(list)
    for ein, ty, at, usd, n in conn.execute(
        f"SELECT funder_ein, tax_year, amount_type, SUM(amount_usd), COUNT(*) FROM grants "
        f"WHERE {where} GROUP BY 1,2,3 ORDER BY 1,2,3"
    ).fetchall():
        years[ein].append({"tax_year": ty, "amount_type": at, "usd": usd, "count": n})
    top: dict[str, list[dict]] = defaultdict(list)
    for ein, name, rein, tier, conf, city, state, usd, n, last in conn.execute(
        f"""
        SELECT funder_ein, name, rein, tier, conf, city, state, usd, n, last FROM (
          SELECT funder_ein, any_value(recipient_name_raw) AS name, recipient_ein_resolved AS rein,
                 any_value(match_tier) AS tier, MAX(match_confidence) AS conf,
                 any_value(recipient_city) AS city, any_value(recipient_state) AS state,
                 SUM(amount_usd) AS usd, COUNT(*) AS n, MAX(tax_year) AS last,
                 row_number() OVER (PARTITION BY funder_ein ORDER BY SUM(amount_usd) DESC NULLS LAST) AS rk
          FROM grants WHERE {where} AND amount_type = 'paid'
          GROUP BY funder_ein, recipient_ein_resolved,
                   CASE WHEN recipient_ein_resolved IS NULL THEN recipient_name_normalized || '|' || COALESCE(recipient_state,'') END
        ) WHERE rk <= {TOP_RECIPIENTS} ORDER BY funder_ein, rk
        """
    ).fetchall():
        top[ein].append(
            {
                "name": name,
                "ein": rein,
                "tier": tier,
                "confidence": conf,
                "city": city,
                "state": state,
                "paid_usd": usd,
                "count": n,
                "last_tax_year": last,
            }
        )
    recent: dict[str, list[dict]] = defaultdict(list)
    for row in conn.execute(
        f"""
        SELECT funder_ein, {_GRANT_COLS} FROM (
          SELECT *, row_number() OVER (PARTITION BY funder_ein ORDER BY tax_year DESC, amount_usd DESC NULLS LAST, grant_id) AS rk
          FROM grants WHERE {where}
        ) WHERE rk <= {RECENT_GRANTS} ORDER BY funder_ein, rk
        """
    ).fetchall():
        recent[row[0]].append(_grant_row(row[1:]))
    filings: dict[str, list[dict]] = defaultdict(list)
    for ein, oid, tpe, fsd, rv, fy, ty, ft in conn.execute(
        f"SELECT DISTINCT funder_ein, object_id, tax_period_end, filing_submission_date, return_version, "
        f"filing_year, tax_year, funder_form_type FROM grants WHERE {where} ORDER BY 1, tax_year DESC, object_id"
    ).fetchall():
        filings[ein].append(
            {
                "object_id": oid,
                "tax_period_end": str(tpe) if tpe else None,
                "filing_submission_date": str(fsd) if fsd else None,
                "return_version": rv,
                "filing_year": fy,
                "tax_year": ty,
                "form_type": ft,
            }
        )

    for (
        ein,
        name,
        state,
        form,
        city,
        ntee,
        subsection,
        paid,
        paid_n,
        rcpts,
        fut,
        fut_n,
        first,
        last,
        latest_filing,
        rows,
    ) in ident:
        chunked = rows > CHUNK_THRESHOLD
        payload = {
            "ein": ein,
            "name": name,
            "city": city,
            "state": state,
            "ntee_code": ntee,
            "subsection_code": subsection,
            "form_type": form,
            "totals": {
                "paid_usd": paid or 0,
                "paid_count": paid_n or 0,
                "recipient_count": rcpts or 0,
                "approved_future_usd": fut or 0,
                "approved_future_count": fut_n or 0,
                "first_tax_year": first,
                "last_tax_year": last,
                "grant_rows": rows,
            },
            "years": years.get(ein, []),
            "top_recipients": top.get(ein, []),
            "recent_grants": recent.get(ein, []),
            "filings": filings.get(ein, []),
            "chunked": chunked,
            "dataset_version": build.dataset_version,
            "built_at": build.built_at,
        }
        if chunked:
            key = f"funders/{ein}/index.json"
            payload["pages"] = _write_year_chunks(conn, build, ein, name, payload["years"])
            payload["recent_grants"] = payload["recent_grants"][:PREVIEW_GRANTS]
            build.funders_chunked += 1
        else:
            key = f"funders/{ein}.json"
        _write_json(build.out_dir / key, payload)
        build.funders += 1
        build.grant_rows += rows
        d1.append(
            "INSERT INTO funders VALUES ("
            + ", ".join(
                [
                    _q(ein),
                    _q(name),
                    _q(_normalize(name)),
                    _q(city),
                    _q(state),
                    _q(ntee),
                    _q(form),
                    _n(paid or 0),
                    _n(paid_n or 0),
                    _n(rcpts or 0),
                    _n(first),
                    _n(last),
                    _q(str(latest_filing) if latest_filing else None),
                    _q(key),
                    "1" if chunked else "0",
                ]
            )
            + ");"
        )
        d1.append(
            f"INSERT INTO entity_search (ein, kind, name, city, state) VALUES "
            f"({_q(ein)}, 'funder', {_q(name)}, {_q(city)}, {_q(state)});"
        )
        urls.append(f"{SITE_ORIGIN}/funders/{ein}")


def _write_year_chunks(
    conn: duckdb.DuckDBPyConnection, build: SiteBuild, ein: str, name: str, years: list[dict]
) -> dict[str, int]:
    """One file per PAGE_ROWS for each tax year: funders/<ein>/<year>/p<n>.json.

    Returns the page count per year; the index carries it so a page can navigate without
    listing R2. A 65,000-row year (Fidelity Charitable, 2022) is 28 MB as one object, and
    no request should parse that.
    """
    pages: dict[str, int] = {}
    for ty in sorted({y["tax_year"] for y in years if y["tax_year"] is not None}):
        rows = [
            _grant_row(r)
            for r in conn.execute(
                f"SELECT {_GRANT_COLS} FROM grants WHERE funder_ein = '{ein}' AND tax_year = {ty} "
                "ORDER BY amount_usd DESC NULLS LAST, grant_id"
            ).fetchall()
        ]
        n_pages = max(1, -(-len(rows) // PAGE_ROWS))
        for page in range(n_pages):
            _write_json(
                build.out_dir / "funders" / ein / str(ty) / f"p{page + 1}.json",
                {
                    "ein": ein,
                    "name": name,
                    "tax_year": ty,
                    "page": page + 1,
                    "pages": n_pages,
                    "rows": len(rows),
                    "grants": rows[page * PAGE_ROWS : (page + 1) * PAGE_ROWS],
                    "dataset_version": build.dataset_version,
                    "built_at": build.built_at,
                },
            )
        pages[str(ty)] = n_pages
    return pages


def _normalize(name: str | None) -> str:
    import re

    return re.sub(r"[^A-Z0-9 ]+", " ", (name or "").upper()).strip()


# --- recipients ----------------------------------------------------------------------------


def _emit_recipient_batch(
    conn: duckdb.DuckDBPyConnection, prefix: str, build: SiteBuild, d1: list[str], urls: list[str]
) -> None:
    where = f"recipient_ein_resolved LIKE '{prefix}%'" + (
        " AND funder_ein IN (SELECT ein FROM keep)" if build.limit else ""
    )
    ident = conn.execute(
        f"""
        SELECT g.recipient_ein_resolved, any_value(COALESCE(b.name, g.recipient_bmf_name, g.recipient_name_raw)),
               any_value(b.city), any_value(b.state), any_value(b.ntee_cd), any_value(b.subsection),
               SUM(CASE WHEN amount_type='paid' THEN amount_usd END),
               SUM(CASE WHEN amount_type='paid' THEN 1 ELSE 0 END),
               COUNT(DISTINCT funder_ein), MIN(tax_year), MAX(tax_year),
               SUM(CASE WHEN amount_type='approved_future' THEN amount_usd END)
        FROM grants g LEFT JOIN bmf b ON b.ein = g.recipient_ein_resolved
        WHERE {where} GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    if not ident:
        return
    funders: dict[str, list[dict]] = defaultdict(list)
    for rein, fein, fname, fstate, usd, n, last, tiers in conn.execute(
        f"""
        SELECT rein, funder_ein, fname, fstate, usd, n, last, tiers FROM (
          SELECT recipient_ein_resolved AS rein, funder_ein, any_value(funder_name) AS fname,
                 any_value(funder_state) AS fstate, SUM(amount_usd) AS usd, COUNT(*) AS n,
                 MAX(tax_year) AS last, string_agg(DISTINCT match_tier, '') AS tiers,
                 row_number() OVER (PARTITION BY recipient_ein_resolved ORDER BY SUM(amount_usd) DESC NULLS LAST) AS rk
          FROM grants WHERE {where} AND amount_type = 'paid' GROUP BY 1, 2
        ) WHERE rk <= {TOP_RECIPIENTS} ORDER BY rein, rk
        """
    ).fetchall():
        funders[rein].append(
            {
                "funder_ein": fein,
                "funder_name": fname,
                "funder_state": fstate,
                "paid_usd": usd,
                "count": n,
                "last_tax_year": last,
                "tiers": "".join(sorted(set(tiers or ""))),
            }
        )
    recent: dict[str, list[dict]] = defaultdict(list)
    for row in conn.execute(
        f"""
        SELECT recipient_ein_resolved, funder_ein, funder_name, {_GRANT_COLS} FROM (
          SELECT *, row_number() OVER (PARTITION BY recipient_ein_resolved ORDER BY tax_year DESC, amount_usd DESC NULLS LAST, grant_id) AS rk
          FROM grants WHERE {where}
        ) WHERE rk <= {RECENT_GRANTS} ORDER BY recipient_ein_resolved, rk
        """
    ).fetchall():
        g = _grant_row(row[3:])
        g["funder_ein"], g["funder_name"] = row[1], row[2]
        recent[row[0]].append(g)

    for ein, name, city, state, ntee, subsection, paid, paid_n, fcount, first, last, fut in ident:
        key = f"recipients/{ein}.json"
        _write_json(
            build.out_dir / key,
            {
                "ein": ein,
                "name": name,
                "city": city,
                "state": state,
                "ntee_code": ntee,
                "subsection_code": subsection,
                "totals": {
                    "received_usd": paid or 0,
                    "grant_count": paid_n or 0,
                    "funder_count": fcount,
                    "approved_future_usd": fut or 0,
                    "first_tax_year": first,
                    "last_tax_year": last,
                },
                "funders": funders.get(ein, []),
                "recent_grants": recent.get(ein, []),
                "dataset_version": build.dataset_version,
                "built_at": build.built_at,
            },
        )
        build.recipients += 1
        d1.append(
            "INSERT INTO recipients VALUES ("
            + ", ".join(
                [
                    _q(ein),
                    _q(name),
                    _q(city),
                    _q(state),
                    _q(ntee),
                    _n(paid or 0),
                    _n(fcount),
                    _q(key),
                ]
            )
            + ");"
        )
        d1.append(
            f"INSERT INTO entity_search (ein, kind, name, city, state) VALUES "
            f"({_q(ein)}, 'recipient', {_q(name)}, {_q(city)}, {_q(state)});"
        )
        urls.append(f"{SITE_ORIGIN}/recipients/{ein}")


# --- sitemaps and the index files ----------------------------------------------------------


def _write_sitemaps(out: Path, series: dict[str, list[str]], lastmod: str) -> list[str]:
    (out / "sitemaps").mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for label, urls in series.items():
        for i in range(0, max(len(urls), 1), SITEMAP_URLS):
            chunk = urls[i : i + SITEMAP_URLS]
            if not chunk:
                continue
            name = f"{label}-{i // SITEMAP_URLS + 1:05d}.xml.gz"
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "".join(f"<url><loc>{u}</loc><lastmod>{lastmod}</lastmod></url>\n" for u in chunk)
                + "</urlset>\n"
            )
            with gzip.open(out / "sitemaps" / name, "wb") as fh:
                fh.write(body.encode("utf-8"))
            names.append(name)
    index = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(
            f"<sitemap><loc>{SITE_ORIGIN}/sitemaps/{n}</loc><lastmod>{lastmod}</lastmod></sitemap>\n"
            for n in names
        )
        + "</sitemapindex>\n"
    )
    (out / "sitemaps" / "sitemap-index.xml").write_text(index, encoding="utf-8")
    return names


def _flush_d1(out: Path, statements: list[str], seq: int) -> int:
    if not statements:
        return seq
    (out / "d1").mkdir(parents=True, exist_ok=True)
    for i in range(0, len(statements), D1_BATCH * 4):
        seq += 1
        (out / "d1" / f"{seq:04d}.sql").write_text(
            "\n".join(statements[i : i + D1_BATCH * 4]) + "\n", encoding="utf-8"
        )
    statements.clear()
    return seq


def build_site(
    parquet_dir: Path,
    state_db: Path | None,
    out_root: Path,
    *,
    years: list[int] | None = None,
    limit: int | None = None,
    now: datetime | None = None,
    bmf_csv: str | None = None,
) -> SiteBuild:
    """Write every payload for the dataset in ``parquet_dir`` under ``out_root/<version>/``."""
    files = _grant_files(parquet_dir, years)
    if not files:
        raise FileNotFoundError(f"no grants partitions under {parquet_dir}")
    conn = duckdb.connect()
    conn.execute(
        f"CREATE VIEW grants AS SELECT * FROM read_parquet({_list(files)}, hive_partitioning = true)"
    )
    if bmf_csv and Path(bmf_csv).is_dir():
        # A directory means every CSV in it; a glob on the command line gets expanded by the
        # shell on Windows before the CLI sees it.
        bmf_csv = (Path(bmf_csv) / "*.csv").as_posix()
    if bmf_csv:
        # The IRS BMF CSVs directly (build/bmf/eo*.csv): no lock on the state file, which a
        # running pipeline stage holds. Same six columns the matcher loaded from them.
        conn.execute(
            "CREATE VIEW bmf AS SELECT EIN AS ein, NAME AS name, CITY AS city, STATE AS state, "
            f"NTEE_CD AS ntee_cd, SUBSECTION AS subsection FROM read_csv_auto('{bmf_csv}', all_varchar = true)"
        )
    elif state_db and state_db.exists():
        conn.execute(f"ATTACH '{state_db.as_posix()}' AS state (READ_ONLY)")
        conn.execute(
            "CREATE VIEW bmf AS SELECT ein, name, city, state, ntee_cd, subsection FROM state.bmf"
        )
    else:
        conn.execute(
            "CREATE VIEW bmf AS SELECT NULL::VARCHAR AS ein, NULL::VARCHAR AS name, NULL::VARCHAR AS city, "
            "NULL::VARCHAR AS state, NULL::VARCHAR AS ntee_cd, NULL::VARCHAR AS subsection WHERE FALSE"
        )
    (version,) = conn.execute("SELECT any_value(dataset_version) FROM grants").fetchone()
    built_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    out = out_root / version
    build = SiteBuild(version, built_at, out, limit=limit)
    if limit:
        conn.execute(
            f"CREATE TABLE keep AS SELECT funder_ein AS ein FROM grants WHERE amount_type='paid' "
            f"GROUP BY 1 ORDER BY SUM(amount_usd) DESC NULLS LAST LIMIT {int(limit)}"
        )
    d1: list[str] = []
    funder_urls: list[str] = []
    recipient_urls: list[str] = []
    seq = 0
    for i in range(100):
        prefix = f"{i:02d}"
        _emit_funder_batch(conn, prefix, build, d1, funder_urls)
        seq = _flush_d1(out, d1, seq)
    for i in range(100):
        prefix = f"{i:02d}"
        _emit_recipient_batch(conn, prefix, build, d1, recipient_urls)
        seq = _flush_d1(out, d1, seq)
    states = [
        s
        for (s,) in conn.execute(
            "SELECT DISTINCT funder_state FROM grants WHERE funder_state IS NOT NULL ORDER BY 1"
        ).fetchall()
    ]
    browse_urls = [f"{SITE_ORIGIN}/browse/state/{s}" for s in states]
    d1.append(
        f"INSERT INTO dataset_vintage VALUES ({_q(version)}, {_q(built_at)}, 0, {build.grant_rows}, "
        f"{build.funders}, {build.recipients});"
    )
    seq = _flush_d1(out, d1, seq)
    lastmod = built_at[:10]
    _write_sitemaps(
        out, {"funders": funder_urls, "recipients": recipient_urls, "browse": browse_urls}, lastmod
    )
    _write_json(
        out / "site-manifest.json",
        {
            "dataset_version": version,
            "built_at": built_at,
            "funders": build.funders,
            "funders_chunked": build.funders_chunked,
            "recipients": build.recipients,
            "grant_rows": build.grant_rows,
            "sample_limit": limit,
            "d1_files": seq,
            "states": states,
        },
    )
    conn.close()
    return build
