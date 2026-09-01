# funders.opengrants.io — hosted companion architecture

The hosted companion renders one page per grantmaking organization: *"Grants paid by the David and
Lucile Packard Foundation, 2019–2025"*, with the amounts, the recipients, and the source filing
stated on the page.

There are roughly 120,000 private foundations filing Form 990-PF and considerably more public
charities filing Schedule I. That is a few hundred thousand pages of factual, uniquely-sourced,
genuinely useful content — the largest search and generative-engine asset in the Egeria portfolio,
by a wide margin.

It is also the single design most likely to be built wrong, in a specific and predictable way.

---

## The file ceiling, stated explicitly

The obvious build is a static site generator: query the Parquet, emit one HTML file per funder,
deploy the directory. It does not work, and it is worth being precise about why so nobody
rediscovers this in week three.

**Cloudflare Pages allows 20,000 files per deployment on the Free plan and 100,000 on paid plans**
(requiring `PAGES_WRANGLER_MAJOR_VERSION=4`). Source:
https://developers.cloudflare.com/pages/platform/limits/

Count the pages we want:

| Page type | Approximate count |
|---|---|
| 990-PF filing foundations | ~120,000 |
| Schedule I grantmaking public charities | ~60,000+ |
| Resolved recipient pages | 400,000+ |
| Per-funder per-year pages | several hundred thousand |

The funder pages alone exceed the paid ceiling. Adding recipient pages exceeds it by an order of
magnitude. And even in a hypothetical world where the count fit, the build would not: Pages enforces
a 20-minute build timeout, and rendering hundreds of thousands of pages from a multi-gigabyte
Parquet dataset does not finish in twenty minutes. A build that takes an hour is a deploy loop
nobody uses, which means the site stops being updated, which means the pages go stale and the SEO
asset decays.

**Therefore: edge SSR from R2 and D1. Not static pre-render.** Only the shell, the docs, and the top
few thousand highest-traffic funder pages are pre-rendered as assets. Everything else is rendered on
demand at the edge and cached.

There is a second, subtler point that resolves the obvious objection. The Pages limit is on
*deployment assets*. **R2 has no comparable object-count limit**, so publishing 180,000 precomputed
per-funder JSON objects to R2 is fine, while publishing 180,000 HTML files as Pages assets is not.
That distinction is what makes the architecture below work.

---

## Platform

**Cloudflare Workers**, not Pages, because this is an application with a data backend rather than a
site with a few functions. Hono for routing, per the TypeScript conventions.

The decisive economic fact: **R2 has no egress fees.** This site serves a public dataset and
succeeds by being pulled hard. On a bandwidth-metered host, the program working as designed becomes
a bill, and the predictable response to a surprising bill is throttling the thing that caused it.
Here, success is close to free — the Workers Paid plan at $5/month plus R2 storage at roughly
$0.015/GB-month covers the whole portfolio well past launch.

---

## Data layout

Three stores, each doing the one thing it is good at.

### R2 — bulk facts

```
r2://funder-graph/
  2026.08.0/
    grants/filing_year=2019/part-*.parquet      # the public dataset
    grants/filing_year=2020/...
    ...
    manifest.json
    funders/941156365.json                      # precomputed per-funder render payload
    funders/131684331.json
    recipients/363673599.json                   # precomputed per-recipient payload
    sitemaps/sitemap-index.xml
    sitemaps/funders-00001.xml.gz
  latest -> 2026.08.0                           # pointer object, not a copy
```

**Why precomputed JSON rather than querying Parquet at request time:** a Worker cannot run DuckDB.
Reading Parquet from a Worker means implementing footer parsing and range reads by hand, which is a
real project with a real failure mode on every schema change. The ingest job already has the whole
dataset loaded in DuckDB, so having it emit one JSON object per funder is nearly free, and the
request path becomes a single R2 GET plus template rendering — the fastest and least breakable thing
available.

Payload shape per funder (`funders/{ein}.json`):

- funder identity: EIN, name, city, state, NTEE, form type
- totals: lifetime grants paid, count, first and last tax year
- per-year totals, for the chart and the year navigation
- top 250 recipients by total amount, with resolved EINs and match tiers
- the most recent 500 individual grants, with amount, purpose, tax year, and `object_id`
- provenance: list of source filings with `object_id`, `tax_period_end`, `filing_submission_date`
- `dataset_version` and `built_at`

**Chunk large funders.** A foundation with 20,000 grant rows does not belong in one JSON object.
Above a threshold (start at 2,000 rows), split by tax year into
`funders/{ein}/{tax_year}.json` with `funders/{ein}/index.json` as the summary. Ford Foundation
(EIN 13-1684331) and the Bill & Melinda Gates Foundation (EIN 56-2618866) are the shapes to test
against.

### D1 — the index

D1 holds only what needs to be *searched* or *ranked*, not the bulk rows. Keeping D1 small keeps it
fast and keeps it inside its storage limits.

```sql
CREATE TABLE funders (
  ein               TEXT PRIMARY KEY,   -- 9 digits, no dash, canonical
  name              TEXT NOT NULL,
  name_normalized   TEXT NOT NULL,
  city              TEXT,
  state             TEXT,
  ntee_code         TEXT,
  form_type         TEXT NOT NULL,      -- 990PF | 990
  total_paid_usd    INTEGER NOT NULL,
  grant_count       INTEGER NOT NULL,
  recipient_count   INTEGER NOT NULL,
  first_tax_year    INTEGER,
  last_tax_year     INTEGER,
  latest_filing_dt  TEXT,
  payload_key       TEXT NOT NULL,      -- R2 key, chunked or not
  is_chunked        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_funders_state_total ON funders(state, total_paid_usd DESC);
CREATE INDEX idx_funders_ntee        ON funders(ntee_code, total_paid_usd DESC);

CREATE TABLE recipients (
  ein               TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  city              TEXT, state TEXT, ntee_code TEXT,
  total_received_usd INTEGER NOT NULL,
  funder_count      INTEGER NOT NULL,
  payload_key       TEXT NOT NULL
);

-- FTS5 over funder and recipient names for /search
CREATE VIRTUAL TABLE entity_search USING fts5(ein UNINDEXED, kind UNINDEXED, name, city, state);

CREATE TABLE dataset_vintage (
  version    TEXT PRIMARY KEY,
  built_at   TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 0,
  grant_rows INTEGER, funder_rows INTEGER
);
```

Roughly 180,000 funder rows plus 400,000+ recipient rows plus an FTS index is a few hundred
megabytes, comfortably within D1's limits. The grant rows themselves — tens of millions — never go
in D1. That is the whole point of the split.

### Workers KV — vintage pointer

One key, `current_dataset_version`. Read on every request (KV reads are cheap and edge-cached), used
to build the cache key. Flipping this key is what atomically cuts the whole site over to a new
dataset.

---

## Request path

```
GET /funders/94-1156365
  ├─ normalize EIN → 941156365; if the request form was not canonical, 301 to the canonical URL
  ├─ read vintage from KV → "2026.08.0"
  ├─ check Cache API with key `${url}?v=2026.08.0`
  │    hit  → return, revalidate in background via ctx.waitUntil
  │    miss → continue
  ├─ D1: SELECT ... FROM funders WHERE ein = ?   (404 page if absent)
  ├─ R2: GET 2026.08.0/funders/941156365.json
  ├─ render full HTML server-side, including JSON-LD
  ├─ if OPENGRANTS_API_KEY is bound: fetch open opportunities, wrapped so any failure
  │    degrades to the un-enriched page; mark enriched content "— live from OpenGrants"
  └─ Cache-Control: public, max-age=604800, stale-while-revalidate=86400
```

**No client-side data fetching for primary content.** Every fact on the page is in the initial HTML
response. Charts render from data already inlined in the document. A crawler or a language model
with JavaScript disabled sees the complete grant list, which is the entire reason this site exists.

---

## Routes

| Route | Renders |
|---|---|
| `/` | What the dataset is, search box, a few real examples, link to the Parquet |
| `/funders/{ein}` | **Canonical funder page.** Totals, per-year chart, top recipients, recent grants, provenance |
| `/funders/{ein}/{year}` | One tax year in full |
| `/funders/{ein}/recipients` | Full recipient list, paginated |
| `/recipients/{ein}` | Who has funded this organization, with amounts and years |
| `/search?q=` | FTS over funder and recipient names |
| `/browse/state/{code}`, `/browse/ntee/{code}` | Crawlable index pages — these are what get the long tail discovered |
| `/data` | Dataset documentation, versions, DuckDB examples, `schema.org/Dataset` markup |
| `/api/funders/{ein}.json` | Convenience JSON. Explicitly not an SLA-backed API |
| `/sitemap.xml` | Sitemap index |
| `/sitemaps/{name}.xml.gz` | Sitemap chunks, proxied from R2 |
| `/llms.txt` | What this dataset is, how to use it, how to cite it |
| `/robots.txt` | Allow all, point at the sitemap index |

**One canonical URL per entity, keyed on EIN.** `/funders/941156365`, `/funders/94-1156365`, and any
slug variant such as `/funders/94-1156365/packard-foundation` all 301 to the single canonical form.
Two URLs serving the same organization splits the ranking signal and it is the most common way a
site like this quietly underperforms. Pick one form, redirect everything else, emit
`<link rel="canonical">` on every page.

---

## Caching

The underlying data changes monthly. Nothing here needs second-level freshness and treating it as if
it does is how the request bill grows.

- **7 days** (`max-age=604800`) for pure IRS-derived pages.
- **24 hours** for pages carrying OpenGrants live enrichment.
- **`stale-while-revalidate`** everywhere. A slightly old 990 figure beats a spinner, every time.
- **Cache key includes the dataset vintage.** Flipping the KV pointer at the end of an ingest
  invalidates the entire site cleanly and atomically, with no purge API call and no waiting for TTLs
  to lapse. This is why the vintage is in the key rather than relying on expiry.

---

## SEO and GEO requirements

These are not optional polish. The repos do not rank; these pages do.

1. **Server-rendered HTML with real content in the initial response.** Non-negotiable.
2. **`schema.org` structured data on every entity page.** `Organization` / `NGO` with `taxID`,
   `address`, and `url`. Individual grants marked up as `MonetaryGrant` with `funder`, `recipient`,
   `amount`, and `datePublished`. `MonetaryGrant` is exactly the right vocabulary for this data and
   almost nobody uses it correctly, which makes these pages unusually easy for a model to quote
   precisely.
3. **One canonical URL per entity, keyed on EIN.** See above.
4. **Sitemap index chunked at 50,000 URLs per file**, generated from the dataset at ingest time,
   gzipped, stored in R2, served through the Worker. Generated at ingest, never at request time.
5. **`llms.txt` at the root** describing what the dataset is, what it covers, how it may be used,
   and how to cite it — including the confidence-tier caveat, so a model quoting a fuzzy-matched
   edge has been told it is fuzzy.
6. **Every page states its source and vintage inline.** "Derived from the Form 990-PF for tax year
   2023, filed 2024-11-14 (IRS OBJECT_ID 202443159349100234), dataset version 2026.08.0." Pages that
   show their work get cited. Pages that assert bare numbers do not.
7. **Cross-link the portfolio.** Every entity page links to the same EIN on the sibling sites:
   `check.opengrants.io/{ein}` for exempt status, `awards.opengrants.io/{ein}` for federal awards,
   `answers.opengrants.io` for guidance, `opengrants.io` for open opportunities. Five sites that
   reference each other read as one authoritative body of work rather than five orphans.
8. **Descriptive, factual titles.** "Grants paid by the David and Lucile Packard Foundation
   (EIN 94-1156365) — 4,812 grants, $2.1B, 2019–2025". The title is a fact, not a pitch.

---

## Ingest job

Runs monthly, after a new dataset version is published to R2 by the pipeline. A GitHub Actions
workflow, not a Worker — it is a long batch job.

1. Read the new `manifest.json`; confirm row counts and checksums.
2. From DuckDB, emit per-funder and per-recipient JSON payloads to R2 under the new version prefix.
   Chunk any entity above the row threshold.
3. Build the D1 index into a **new** set of tables, verify counts, then swap. Never mutate the live
   index in place — a half-written index serving traffic is worse than a stale one.
4. Generate sitemaps, gzip, upload to R2 under the new version prefix.
5. Smoke-test a fixed list of known EINs against the new version, including at least one very large
   funder, one tiny one, one Schedule I filer, and one with a known chunked payload.
6. **Only then** flip `current_dataset_version` in KV. That flip is the cutover, and it is the only
   irreversible step.
7. Ping the sitemap index. Post the delta — new funders, changed totals — to the changelog.

---

## DNS

`funders.opengrants.io` is a subdomain of `opengrants.io`, whose DNS is managed externally rather
than at the registrar's default. The Worker needs a custom domain, which needs a CNAME plus
Cloudflare's validation record added in whatever zone actually holds `opengrants.io`.

**Confirm who holds that zone before the first launch.** This is the step most likely to sit blocked
for a day, and it is the most annoying possible reason for a finished site not to be live.

The dataset itself is served from `data.opengrants.io`, an R2 custom domain on the same zone.
Getting both records requested at the same time saves a round trip.

---

## What this site is not

No accounts, no login, no saved searches, no session state, no write path. It is a read-only,
server-rendered view of a public dataset. The moment it needs a session it has become a different
product, and that product is `grantdesk`.
