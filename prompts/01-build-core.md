# Claude Code kickoff — funder-graph core: pipeline, dataset, CLI, MCP server

You are building `funder-graph`, the flagship repository of a five-repo open source program run by
Egeria Corporation and sponsored by OpenGrants. Assume you have no context beyond this file and the
contents of this repository. Everything you need to start is here or in the files named below.

Budget: **six to eight weeks of focused work.** This is deliberately not a fast project. The parsing
is the entire competitive point and it should be done properly once.

---

## 1. Mission

The most valuable question in fundraising is *"who has this funder actually given money to, and how
much?"* The answer is public and nobody has published it as usable data.

- **Form 990-PF, Part XV** lists every grant a private foundation paid during the year: recipient
  name, address, purpose, amount.
- **Form 990, Schedule I** lists grants made by public charities to other organizations, usually
  including the recipient's EIN.

Across the IRS electronic filing corpus these are tens of millions of funder-to-recipient edges.
They are the substance of what the paid foundation-research products in this category sell access
to. The reason nobody has freed them is that the source XML spans hundreds of schema versions with
inconsistent XPaths and no stable field naming.

**Do not name or price a commercial product anywhere in this repository** — not in code, help text,
command output, documentation, or a hosted page. See `docs/program/CONVENTIONS.md`.

**Your job:** parse the full IRS e-file XML corpus into one normalized funder-to-recipient grant edge
list, resolve recipients to EINs with published confidence scores, and publish the result as
versioned, partitioned Parquet on Cloudflare R2 — plus a CLI and an MCP server that query the
published dataset without downloading it.

The success test is one sentence: **a grant consultant who has never heard of this project runs one
DuckDB query against a URL and gets every grant the Packard Foundation paid, in under a second, with
nothing installed.**

---

## 2. Read these first, in this order

1. `docs/program/CONVENTIONS.md` — binding program conventions. Dual CLI + MCP
   interface, Apache 2.0, attribution rules, data honesty rules, repo layout, engineering standards.
2. `docs/program/RESEARCH.md` — verified source URLs, patterns, and competitive facts.
3. `docs/research/data-sources.md` in this repo — **the operational spec for the parsing work.** It
   names the exact XPaths and concordance targets for both grant tables and the drift patterns you
   will hit. Do not start writing extraction code before you have read it.
4. `docs/research/prior-art.md` — who we build on and what we owe them. The upstream-first rule is
   not optional.
5. `README.md` — the published schema table is the contract. If you change it, you update the README
   in the same commit.
6. `docs/NON-GOALS.md` — the scope fence. When you find yourself building something that is on that
   list, stop.

---

## 3. What you are delivering

1. **`funder_graph/` library** — all logic. Download, extract, map, normalize, resolve, partition,
   publish, query.
2. **`funder-graph` CLI** — `uvx`-runnable console entry point, human-readable by default, `--json`.
3. **MCP server** — `funder-graph mcp`, stdio transport, same capabilities as the CLI.
4. **The published dataset** — versioned Parquet on R2 with a manifest, plus the companion tables.
5. **Tests** — fixture-based, using real committed filing fragments.
6. **CI** — GitHub Actions, lint and tests on push and PR.

Repo layout follows `CONVENTIONS.md` exactly. The files that already exist (README, NOTICE,
CONTRIBUTING, .env.example, docs/, prompts/) are written — **do not rewrite them**, but do keep them
accurate as you build. Missing pieces you must add: `LICENSE` (Apache 2.0 full text),
`CODE_OF_CONDUCT.md`, `SECURITY.md`, `.gitignore`, `CHANGELOG.md`, `pyproject.toml`,
`.github/workflows/ci.yml`.

---

## 4. Hard constraints

**Non-negotiable. Violating any of these means the build is wrong even if it runs.**

1. **Users do not run the ETL.** The quickstart is a DuckDB query against a hosted Parquet URL. If
   the fastest path to a real answer requires a clone, an install, or a download, the design has
   failed.
2. **No hand-rolled XPaths.** All field resolution goes through the Nonprofit Open Data Collective
   IRS E-file Master Concordance File, pinned to a commit SHA. Local overrides are permitted only in
   `data/overrides/concordance-overrides.toml`, and **every override entry must carry an upstream
   issue or PR URL** — CI fails otherwise.
3. **Upstream-first.** A fix that belongs in the concordance or in a GivingTuesday tool goes there
   before it goes here. Record it in `docs/research/prior-art.md`.
4. **Business logic lives in the library.** The CLI and MCP server are thin adapters. Logic in a
   command handler is a bug.
5. **Match confidence is a first-class published column.** Never emit a resolved EIN without a
   confidence score and a tier. Never overstate a tier.
6. **A missing grant must never look like a zero grant.** If a schema version is unmapped or a
   filing fails to parse, it goes into an error report and the affected filing is marked, loudly.
   Silently emitting zero rows for a foundation that made grants is the worst failure this project
   can have.
7. **Every row carries provenance.** `object_id`, `tax_period_end`, `filing_submission_date`,
   `return_version`, `concordance_version`, `dataset_version`. No exceptions.
8. **Reproducible builds.** Same pinned inputs plus same code equals byte-identical output, or you
   have a bug. Record every source file URL with its checksum in the manifest.
9. **OpenGrants is optional.** Everything works with zero credentials. No nag, ever, in command
   output. Enrichment failures degrade silently.
10. **Python 3.11+, `uv`, `ruff`, `pytest`, DuckDB.** No Postgres, no Elasticsearch, no
    docker-compose. Adding a service dependency is out of scope by definition.
11. **Apache 2.0, `NOTICE` accurate, README Credits above the fold.**
12. **Individual grant recipients are tagged and excluded from the default edge view.** Do not
    publish named natural persons with dollar amounts against their names.

---

## 5. Pipeline stages

Build these as separately invocable stages with checkpointed state, so a failure at stage 5 does not
mean re-downloading 400 GB. Each stage reads and writes to `FUNDER_GRAPH_WORK_DIR` (default
`./build`) and records completion in a small state database (`build/state.duckdb`).

```
funder-graph build download   [--years 2019-2026]
funder-graph build extract
funder-graph build map
funder-graph build normalize
funder-graph build resolve
funder-graph build publish    [--version 2026.08.0] [--dry-run]
funder-graph build all
```

### Stage 1 — download

- Fetch the year directory listing from `https://apps.irs.gov/pub/epostcard/990/xml/{YEAR}/`.
  **Enumerate the listing; do not construct filenames.** Naming differs across years (see
  `docs/research/data-sources.md`).
- Download every `.zip` plus the year index CSV. Record for each: resolved URL, ETag or
  `Last-Modified`, byte size, SHA-256, download timestamp.
- Resume partial downloads. Retry with exponential backoff. Send the User-Agent from
  `FUNDER_GRAPH_USER_AGENT`. Cap concurrency at something polite — 4 connections. The IRS is not a
  CDN and this is a shared public resource.
- Also download the EO Business Master File, Publication 78 data, and the Automatic Revocation list
  from the TEOS bulk downloads page.

### Stage 2 — extract and index

- Parse each year's index CSV into `filings_index`. Normalize column names — headers have varied
  across years, so map defensively and fail loudly on an unrecognized header rather than guessing.
- Filter to `RETURN_TYPE in ('990', '990PF')`.
- **Deduplicate amended and superseded returns:** group on `(EIN, TAX_PERIOD, RETURN_TYPE)`, keep the
  row with the latest `SUB_DATE`, and write the losers to a `superseded_filings` table rather than
  discarding them. Log the count — it is a useful health signal.
- Reconcile the index against actual ZIP contents in both directions. Report the delta; do not crash.
- Extract lazily. Stream XML out of the ZIPs rather than exploding hundreds of gigabytes to disk.

### Stage 3 — map via concordance

This is the stage the whole project lives or dies on. Spend the time here.

- Pin the concordance to a commit SHA in `data/upstream-pins.toml`. Vendor it into
  `data/concordance/` at that SHA so builds are reproducible without network access.
- Load the concordance and build, for each `returnVersion` in the corpus, a resolved map from our
  logical field names to concrete XPaths. Cache these resolved maps — building them per filing is
  the obvious performance mistake.
- The logical fields you need are enumerated in `docs/research/data-sources.md` section 4. Two
  repeating groups (990-PF paid, 990-PF approved-future), one repeating table (Schedule I Part II),
  plus header fields.
- **Coverage gate.** Before parsing anything, compute the coverage matrix: every `returnVersion`
  present in the corpus, the number of filings carrying it, and whether the concordance resolves
  every required grant field for it. Write `build/reports/version-coverage.csv`. **If coverage is
  below 100% of filings by volume, stop and report the number before proceeding.** This measurement
  is also the most valuable thing we contribute back upstream — see `docs/research/prior-art.md`.
- **Drift detection.** Use GivingTuesday's `form-990-xml-mapper` to enumerate every XPath in each
  schema version. Diff against what we consume, restricted to the Part XV and Schedule I subtrees.
  Write `build/reports/unmapped-fields.csv` with occurrence counts.
- Parse namespace-aware, or strip the `http://www.irs.gov/efile` namespace uniformly and document
  that you did.
- Use `lxml` with `iterparse` and clear elements as you go. You are processing millions of
  documents; a DOM per filing held in memory will not survive it.
- Parallelize by ZIP archive across processes. Each worker writes its own Parquet shard. Do not try
  to share a DuckDB connection across processes.

### Stage 4 — normalize

- Amounts: integer USD. Strip currency symbols and commas, handle parenthesized negatives, reject
  anything non-numeric into an error report rather than coercing to zero.
- EINs: strip everything non-digit, zero-pad to 9. **Reject and flag anything that is not 9 digits
  after normalization** rather than silently truncating.
- Dates: ISO 8601. `tax_year` derives from `tax_period_end`, using the calendar year of the period
  end.
- `recipient_type`: `individual` when `RecipientPersonNm` is populated or the name matches a personal
  name pattern with no organizational tokens; `government` when the name matches
  city/county/state/US-agency patterns; otherwise `organization`.
- `recipient_name_normalized`: uppercase; strip punctuation; collapse whitespace; `&` → `AND`; drop
  leading `THE`; normalize legal suffixes (`INCORPORATED`/`INC`, `CORPORATION`/`CORP`,
  `FOUNDATION`/`FDN`, `UNIVERSITY`/`UNIV`, `ASSOCIATION`/`ASSN`, `SOCIETY`/`SOC`,
  `INTERNATIONAL`/`INTL`, `SAINT`/`ST`); drop trailing `INC`. Split `DBA` / `AKA` / `FKA` and keep
  both forms as match candidates.
- `recipient_zip5`: first five digits of the ZIP where present.
- **Reconciliation checks, and these are the most valuable QA in the pipeline:**
  - 990-PF: sum of parsed `paid` edges per filing versus the filing's own reported total grants paid.
    Write `build/reports/pf-total-reconciliation.csv` with the delta and the percentage.
  - Schedule I: parsed recipient-organization row count versus the filer's own
    `Total501c3OrgCnt + TotalOtherOrgCnt`. Same report treatment.
  - Filings where the structured group is empty but the reported total is large go into
    `build/reports/pf-missing-detail.csv` — these are grants reported as an attachment rather than
    structured data. This is a real, known limitation. Measure it and publish the measurement.

### Stage 5 — resolve recipient EINs

See section 7 below. This is a required stage, not an optimization.

### Stage 6 — partition and write

- Partition by `filing_year` (the year of the IRS bulk posting, not the tax year). This is the right
  key because a monthly update touches only recent `filing_year` partitions, which makes incremental
  publishing cheap.
- **Sort within each partition by `funder_ein`, then `tax_year`, then `amount_usd DESC`.** This is
  what makes Parquet row-group statistics prune effectively for the dominant query shape
  (`WHERE funder_ein = ?`). Without it, a query still reads far more than it needs and the 60-second
  quickstart stops feeling instant.
- Target row groups of roughly 100,000–200,000 rows and files around 128–256 MB. Compression: ZSTD.
- Write a secondary copy sorted by `recipient_ein_resolved` under `grants_by_recipient/` for the
  reverse query. Storage is $0.015/GB-month and egress is free; doubling the dataset to make the
  second-most-common query fast is obviously correct.
- Emit companion tables: `funders/`, `recipients/`, `unmatched/`.

### Stage 7 — publish

- Compute `manifest.json`: dataset version, build timestamp, per-partition row counts, total row
  count, per-file SHA-256, list of source IRS files with their checksums, concordance commit SHA,
  code version, and the summary QA metrics (coverage percentage, match tier distribution,
  reconciliation deltas).
- Upload to R2 under `{version}/`. Update the `latest` pointer **only after** upload completes and
  verification passes.
- `--dry-run` writes everything locally and skips the upload. Every publish run must be dry-runnable.

---

## 6. Output schema

This is the public contract. It is documented in `README.md` and consumers will pin it. Match it
exactly; if you must change it, change the README in the same commit and note it in `CHANGELOG.md`.

Table `grants`, partitioned by `filing_year`:

| Column | Type | Null | Notes |
|---|---|---|---|
| `grant_id` | VARCHAR | no | Deterministic: `sha256(object_id + ':' + group_name + ':' + row_ordinal)`, first 32 hex chars. Must be stable across re-ingests of the same filing. |
| `funder_ein` | VARCHAR(9) | no | |
| `funder_name` | VARCHAR | no | |
| `funder_state` | VARCHAR(2) | yes | |
| `funder_form_type` | VARCHAR | no | `990PF` \| `990` |
| `object_id` | VARCHAR | no | IRS OBJECT_ID of the source filing |
| `tax_year` | INTEGER | no | |
| `tax_period_end` | DATE | no | |
| `filing_submission_date` | DATE | yes | From the index CSV, not the XML |
| `filing_year` | INTEGER | no | **Partition key** |
| `return_version` | VARCHAR | no | e.g. `2023v4.0` |
| `amount_usd` | BIGINT | no | |
| `noncash_amount_usd` | BIGINT | yes | Schedule I only |
| `amount_type` | VARCHAR | no | `paid` \| `approved_future` |
| `grant_purpose` | VARCHAR | yes | Verbatim |
| `recipient_name_raw` | VARCHAR | no | Verbatim, typos preserved |
| `recipient_name_normalized` | VARCHAR | no | |
| `recipient_ein_reported` | VARCHAR(9) | yes | |
| `recipient_ein_resolved` | VARCHAR(9) | yes | |
| `recipient_ein_source` | VARCHAR | no | `reported_verified` \| `reported_unverified` \| `bmf_deterministic` \| `bmf_strong` \| `bmf_probable` \| `unresolved` |
| `match_confidence` | DOUBLE | yes | Null iff `recipient_ein_resolved` is null |
| `match_tier` | VARCHAR(1) | no | `A` \| `B` \| `C` \| `D` \| `U` |
| `match_method` | VARCHAR | yes | Short machine-readable rule name, e.g. `name_zip5_state_unique` |
| `recipient_address_line1` | VARCHAR | yes | |
| `recipient_city` | VARCHAR | yes | |
| `recipient_state` | VARCHAR(2) | yes | |
| `recipient_zip` | VARCHAR | yes | As filed |
| `recipient_zip5` | VARCHAR(5) | yes | |
| `recipient_country` | VARCHAR(2) | yes | `US` unless stated otherwise |
| `recipient_bmf_name` | VARCHAR | yes | Legal name from BMF for the resolved EIN |
| `recipient_ntee_code` | VARCHAR | yes | |
| `recipient_subsection_code` | VARCHAR | yes | |
| `recipient_type` | VARCHAR | no | `organization` \| `individual` \| `government` \| `unknown` |
| `recipient_relationship` | VARCHAR | yes | 990-PF Part XV |
| `recipient_foundation_status` | VARCHAR | yes | 990-PF Part XV |
| `concordance_version` | VARCHAR | no | Commit SHA |
| `dataset_version` | VARCHAR | no | e.g. `2026.08.0` |
| `ingested_at` | TIMESTAMP | no | |

Companion tables:

- **`funders`** — `ein`, `name`, `city`, `state`, `ntee_code`, `form_type`, `total_paid_usd`,
  `grant_count`, `recipient_count`, `first_tax_year`, `last_tax_year`, `latest_filing_date`,
  `accepts_unsolicited_applications` (from 990-PF Part XV application info, indicator only —
  **never the contact person**), `application_deadline_text`.
- **`recipients`** — one row per resolved EIN: BMF attributes, `total_received_usd`, `funder_count`,
  `first_tax_year`, `last_tax_year`.
- **`unmatched`** — `recipient_name_raw`, `recipient_name_normalized`, `recipient_city`,
  `recipient_state`, `recipient_zip5`, `occurrence_count`, `total_amount_usd`, `candidate_count`,
  `best_candidate_ein`, `best_candidate_score`. Published on purpose. It is the honest accounting of
  what we do not know and the best place for community fixes.

---

## 7. EIN resolution — strategy and confidence tiers

990-PF Part XV usually gives you a recipient name and a mailing address and no EIN. Without
resolution you have strings, not a graph. This is the second-hardest part of the project after
schema mapping and it is where the dataset's credibility is won or lost.

### Reference data

The EO Business Master File, roughly 1.9 million rows: EIN, legal name, sort name, street, city,
state, ZIP, subsection, NTEE, ruling date. Load it into DuckDB with precomputed normalized name,
ZIP5, and blocking keys.

### Blocking — do this before anything else

You cannot compare 30 million grant rows against 1.9 million BMF rows pairwise. Block on:

- exact normalized name (catches the large majority cheaply)
- `state` + first token of normalized name
- `zip5` + first token of normalized name
- double-metaphone of the first two name tokens + `state`

Only candidates surviving a block get scored. Deduplicate distinct
`(recipient_name_normalized, city, state, zip5)` tuples first and resolve each **once** — the same
recipient string appears thousands of times across the corpus and resolving per row is wasted work
by three orders of magnitude.

### Tiers — the exact rules

| Tier | Confidence | `recipient_ein_source` | Rule |
|---|---|---|---|
| A | 1.00 | `reported_verified` | EIN reported on the filing, 9 digits after normalization, present in the BMF. |
| A | 0.95 | `reported_unverified` | EIN reported, structurally valid, **not** in the current BMF. Usually merged, revoked, or terminated. Cross-check the Automatic Revocation list and note it. Trust the filer over our reference data, but flag it. |
| B | 0.90–0.94 | `bmf_deterministic` | No reported EIN. Exact normalized name + exact ZIP5 + state matches exactly **one** BMF row. 0.94 when the raw names also match exactly; 0.90 at the floor. |
| C | 0.75–0.89 | `bmf_strong` | Exact normalized name + state matches exactly one BMF row (no ZIP corroboration), **or** Jaro-Winkler ≥ 0.94 on normalized name with matching ZIP5. Scale within the band by string similarity and by whether city also agrees. |
| D | 0.50–0.74 | `bmf_probable` | Fuzzy name match within state, single candidate above threshold, no address corroboration. Scale by similarity. **This is a guess with a number attached.** |
| U | NULL | `unresolved` | No candidate above 0.50; **or** two or more candidates within 0.03 of each other (ambiguity is unresolved, never a coin flip); **or** `recipient_type` is `individual` or `government`. |

### Rules that will save you from the specific failure modes

- **Ambiguity resolves to U, never to the top candidate.** Two Boys and Girls Clubs in the same state
  is not a 51/49 call, it is an unknown. Getting this wrong is how the dataset becomes
  untrustworthy in a way nobody can see from the outside.
- **Chapter organizations need geography.** Maintain a list of chapter-organization name patterns
  (Boys and Girls Club, United Way, Habitat for Humanity, YMCA, YWCA, Goodwill, Big Brothers Big
  Sisters, Salvation Army, American Red Cross, Rotary, Kiwanis). For any name matching one of these,
  **cap the tier at C unless ZIP5 or city agrees.** These are the highest-risk matches in the corpus.
- **Address disagreement is weak negative evidence.** BMF addresses are frequently a lawyer, an
  accountant, or a lockbox. Agreement is strong positive evidence; disagreement should reduce
  confidence, not veto a match.
- **University and hospital systems are a known hard case.** "Harvard University" is filed against
  "PRESIDENT AND FELLOWS OF HARVARD COLLEGE". Try the BMF sort name as well as legal name. Where a
  well-known alias table would help, put it in `data/overrides/name-aliases.csv` with a source note,
  keep it small, and do not let it become an unmaintained pile of special cases.
- **Manual corrections** live in `data/overrides/ein-corrections.csv`, applied after automated
  matching, published as part of the release so they are auditable.

### Evaluation — required, not optional

Build a labeled evaluation set of at least 1,000 recipient strings with hand-verified EINs, sampled
stratified across tiers, and commit it at `tests/fixtures/matching/labeled_pairs.csv`. Report
precision and recall per tier in `build/reports/matching-eval.md` on every build.

**Precision targets, and treat these as gates:**

- Tier A: 100% by construction (audit a sample anyway)
- Tier B: ≥ 99%
- Tier C: ≥ 95%
- Tier D: ≥ 80%

If a tier misses its target, the correct fix is to demote rows out of that tier, not to loosen the
target. Understated confidence costs a user a lead. Overstated confidence costs the project its
reputation, once, permanently.

---

## 8. Incremental updates

The IRS posts monthly. A full rebuild is hours of compute and hundreds of gigabytes of transfer, and
running one every month is both wasteful and a reliability risk.

- Maintain `state/processed_filings` in DuckDB: `object_id`, source ZIP URL, source SHA-256,
  `parsed_at`, `dataset_version_first_seen`, row count produced.
- `funder-graph build incremental`:
  1. Re-fetch each year's index CSV. Diff against `processed_filings` on `OBJECT_ID`.
  2. Download only ZIPs containing new or changed OBJECT_IDs (compare ETag/SHA-256 — the IRS
     re-posts archives).
  3. Handle **supersedes**: a new filing for an `(EIN, TAX_PERIOD, RETURN_TYPE)` already present
     retires the previous filing's rows. Retirement is a partition rewrite, not a delete-in-place.
  4. Parse, normalize, resolve. **Re-resolve previously unmatched rows against the new BMF** — the
     BMF updates monthly and last month's `unresolved` is this month's tier B. Do not re-resolve rows
     already at tier A or B; that is churn with no benefit.
  5. Rewrite only the affected `filing_year` partitions. Copy the rest by reference into the new
     version prefix.
  6. Recompute companion tables and the manifest. Publish a new version. Flip `latest`.
- **Every published version is immutable.** Never mutate a version prefix in place. Consumers pin
  versions and a mutated version breaks reproducibility silently, which is the worst way to break it.
- Emit `build/reports/version-delta.md` on every incremental run: new filings, superseded filings,
  new funders, rows added and retired per partition, match tier distribution shift. This is the
  changelog for the dataset and the hosted site's ingest job consumes it.

---

## 9. Dataset versioning and publishing

- Version format `YYYY.MM.PATCH`. `2026.08.0` is the first release from the August 2026 IRS posting.
  A monthly ingest bumps the month. A mapping or matching fix with no new source data bumps the patch.
- Layout on R2:

```
funder-graph/2026.08.0/grants/filing_year=2024/part-0000.parquet
funder-graph/2026.08.0/grants_by_recipient/...
funder-graph/2026.08.0/funders/part-0000.parquet
funder-graph/2026.08.0/recipients/part-0000.parquet
funder-graph/2026.08.0/unmatched/part-0000.parquet
funder-graph/2026.08.0/manifest.json
funder-graph/latest/            -> pointer, updated last
funder-graph/versions.json      -> list of every published version with build dates
```

- Public base: `https://data.opengrants.io/funder-graph/`. R2 custom domain, no egress cost.
- Set permissive CORS on the bucket so browser DuckDB-WASM (the
  [shell.duckdb.org](https://shell.duckdb.org) path in the README) works. **Test this specifically.**
  The zero-install quickstart depends on it and it is the kind of thing that is broken for a month
  before anyone notices.

---

## 10. CLI

`uvx funder-graph <command>`. Human-readable tables by default, `--json` on every command. Every
human-readable output ends with a footer stating dataset version, build date, and:

> This is informational only, derived from public data on the dates shown. It is not an eligibility
> determination, and not legal, tax, or accounting advice. Verify against the official source before
> relying on it.

| Command | Behavior |
|---|---|
| `query --funder-ein <ein>` | Grants paid by a funder. Filters: `--min-amount`, `--max-amount`, `--year`, `--years 2020-2023`, `--recipient-state`, `--purpose-contains`, `--amount-type paid\|approved_future\|all` (default `paid`), `--min-confidence` (default 0.90), `--limit`, `--sort`. |
| `funders-of --recipient-ein <ein>` | The reverse query. Who funded this organization, totals by funder, years, latest. |
| `similar --keyword <text>` | Funders who have made grants whose purpose or recipient name matches, with `--state`, `--ntee`, `--min-amount`. This is "who funds organizations like mine". |
| `funder <ein>` | Profile: totals by year, top recipients, geographic and NTEE concentration, whether they accept unsolicited applications, source filings. Optional OpenGrants enrichment appends currently open opportunities. |
| `recipient <ein>` | Mirror profile from the recipient side. |
| `dataset info` | Current version, build date, row counts, coverage and match-tier summary, source vintages. |
| `dataset versions` | Every published version. |
| `diagnose --funder-ein <ein> [--year N]` | For contributors: OBJECT_ID, `returnVersion`, XPaths sought, XPaths found, the raw Part XV / Schedule I XML fragment. |
| `fetch-raw --object-id <id>` | Download one filing's XML straight from the IRS. |
| `mcp` | Start the MCP server on stdio. |
| `build ...` | Pipeline stages, section 5. |

Global flags: `--dataset-version`, `--data-url`, `--json`, `--no-color`, `--quiet`.

**Query implementation:** DuckDB with `httpfs`, reading Parquet over HTTP with `hive_partitioning=1`.
Push filters down so partition and row-group pruning actually happen. Cache Parquet footers locally
in `FUNDER_GRAPH_CACHE_DIR`. **Never download whole partitions to answer a filtered query** — if a
`--funder-ein` lookup transfers more than a few tens of megabytes, the sort order or the predicate
pushdown is wrong, and that is a bug worth stopping to fix.

---

## 11. MCP server

`funder-graph mcp`, stdio transport, thin adapter over the same library functions the CLI calls.

Tools:

- `funder_grants(funder_ein, min_amount?, year?, amount_type?, min_confidence?, limit?)`
- `funders_of_recipient(recipient_ein, min_confidence?, limit?)`
- `find_funders_by_keyword(keyword, state?, ntee?, min_amount?, limit?)`
- `funder_profile(ein)`
- `recipient_profile(ein)`
- `dataset_info()`

Every tool response includes `dataset_version`, and every row that carries a `match_confidence`
below 1.0 must surface it. An agent quoting a tier D edge as fact is a failure mode we can prevent
at the interface, and we should.

Tool descriptions must state the confidence semantics and the `amount_type` double-count trap in
plain language. The model reading them has no other documentation.

---

## 12. OpenGrants enrichment

Optional, additive, silent on failure.

- Read `OPENGRANTS_API_KEY`. Absent means skip entirely, with no message.
- Base `https://qnoicxojartltrownmal.supabase.co/functions/v1/`, header
  `Authorization: Bearer <key>`. Endpoints: `GET /funders-api`, `GET /funders-api/{id}`,
  `GET /grants-api`.
- Used only in `funder` and `recipient` profiles, to append currently open opportunities.
- Mark every enriched line `— live from OpenGrants`. Users must always be able to tell public-source
  data from API-sourced data.
- Wrap in a timeout and a broad exception handler. A network failure, an expired key, a 429 — none
  of them may break the command. Respect `X-RateLimit-Remaining`.
- Mention the key exactly once, in the README. **Never in command output.**

---

## 13. Milestones, in build order

Each milestone ends in something demonstrable. Do not proceed past a milestone whose exit criterion
is not met.

**M1 — Skeleton and one filing (week 1).** `pyproject.toml`, `uv` env, `ruff`, `pytest`, CI, LICENSE,
`.gitignore`. Download one 2023 ZIP, extract one known 990-PF, parse Part XV through the concordance,
print grant rows. *Exit: real grant rows from one real filing, and a test asserting them against a
committed fixture.*

**M2 — Concordance coverage measured (week 1–2).** Full concordance load, per-version resolved maps,
the coverage matrix across every `returnVersion` in the corpus, drift report via
`form-990-xml-mapper`. *Exit: `build/reports/version-coverage.csv` exists and the percentage of
filings with full grant-field coverage is a known number. Report it before writing more code — it
sets the risk profile for everything that follows.*

**M3 — Full parse to Parquet (week 2–3).** Both grant tables, both forms, parallelized, checkpointed,
reconciliation reports. *Exit: complete unresolved edge list on disk; 990-PF parsed totals reconcile
to filers' own reported totals within 1% for ≥ 95% of filings.*

**M4 — Entity resolution (week 3–5).** BMF load, blocking, tiers, labeled evaluation set. *Exit:
every tier meets its precision target on the labeled set, and `matching-eval.md` proves it.*

**M5 — Publish (week 5).** Partitioning, sort order, companion tables, manifest, R2 upload, `latest`
pointer, CORS. *Exit: the README's DuckDB one-liner works, from a clean machine, against the real
URL, in under two seconds.*

**M6 — CLI (week 5–6).** All commands, `--json`, footers, enrichment. *Exit:
`uvx funder-graph query --funder-ein 94-2278431 --min-amount 25000` returns correct rows on a machine
that has never seen this repo.*

**M7 — MCP server (week 6).** *Exit: works end to end in a real MCP client, with confidence surfaced.*

**M8 — Incremental updates (week 6–7).** *Exit: an incremental run over one new monthly posting
produces a correct new version and a `version-delta.md`, without a full rebuild.*

**M9 — Hardening and honesty (week 7–8).** Full test suite, docs reconciled against reality, the
upstream contributions from `docs/research/prior-art.md` actually filed, `CHANGELOG.md`, v1.0.0 tag.
*Exit: someone who has never seen the project gets a real answer in 60 seconds from the README alone.*

---

## 14. Acceptance criteria

Checkable. Not aspirational.

**Data correctness**

- [ ] For ≥ 95% of 990-PF filings with structured Part XV data, summed parsed `paid` edges reconcile
      to the filing's own reported total grants paid within 1%.
- [ ] For ≥ 95% of Schedule I filings, parsed recipient-organization row counts match the filer's
      stated organization counts exactly.
- [ ] Zero filings are silently skipped. Every skip appears in an error report with a reason.
- [ ] `approved_future` rows are never mixed into `paid` totals anywhere in the CLI, the MCP server,
      or the docs.
- [ ] No row carries a resolved EIN without a confidence score and a tier.
- [ ] No `recipient_type = 'individual'` row appears in the default edge view.

**Matching**

- [ ] Labeled evaluation set of ≥ 1,000 pairs committed.
- [ ] Tier B ≥ 99% precision, tier C ≥ 95%, tier D ≥ 80% on that set.
- [ ] `unmatched` table published with occurrence counts.
- [ ] Ambiguous candidates resolve to `U`, verified by a test with a constructed tie.

**Performance**

- [ ] `SELECT * FROM read_parquet(<hosted url>) WHERE funder_ein = '942278431'` returns in under two
      seconds from a normal home connection, transferring under 50 MB.
- [ ] `uvx funder-graph query --funder-ein 94-2278431` returns in under five seconds cold.
- [ ] A full corpus build completes in under 12 hours on a 16-core machine.
- [ ] An incremental monthly update completes in under 60 minutes.

**Interface**

- [ ] `uvx funder-graph --help` works with no clone and no config.
- [ ] Every command supports `--json` and emits valid JSON.
- [ ] Every human-readable output carries the dataset vintage and the disclosure footer.
- [ ] MCP server exposes all six tools and works in a real client.
- [ ] With no `OPENGRANTS_API_KEY`, nothing mentions OpenGrants in any command output.
- [ ] With an invalid key, every command still succeeds, un-enriched.

**Reproducibility and honesty**

- [ ] `manifest.json` contains every source file URL and checksum, the concordance SHA, and the QA
      metrics.
- [ ] Two builds from identical pinned inputs produce identical row counts and identical `grant_id`
      values.
- [ ] Every override in `concordance-overrides.toml` has an upstream link; CI enforces it.
- [ ] `NOTICE` lists every upstream project with its license as read from that repo.
- [ ] README Credits section is above the fold.
- [ ] The upstream contributions listed in `docs/research/prior-art.md` are filed, with links added
      back into that file.

---

## 15. Verification — spot-check against real filings

Automated tests prove the code does what you told it to. These checks prove you told it the right
thing. **Do all of them manually, against the actual filings, before declaring M5 or M9 done.**

For each foundation: find its filing on ProPublica Nonprofit Explorer
(`https://projects.propublica.org/nonprofits/organizations/{ein-digits}`), open the actual 990-PF or
990 PDF or XML, read Part XV or Schedule I with your own eyes, and compare against what the pipeline
produced.

**The EINs below are given as starting points and several should be confirmed against the BMF before
you rely on them. Verify each one resolves to the organization named; if it does not, use the
correct EIN and correct this file.**

| Organization | EIN | Why this one |
|---|---|---|
| The David and Lucile Packard Foundation | 94-2278431 | The canonical example throughout our docs. Large, clean, structured Part XV. If this one is wrong, everything is wrong. |
| Ford Foundation | 13-1684331 | Very large grant volume. Tests chunking, memory behavior, and whether huge Part XV groups parse completely. |
| Bill & Melinda Gates Foundation | 56-2618866 | The largest grantmaker in the corpus. Extreme row counts and very large individual amounts — check for integer handling and truncation. |
| William and Flora Hewlett Foundation | 94-1655673 | Well-structured filer, good baseline for reconciliation. |
| Robert Wood Johnson Foundation | 22-6029397 | Large, long history across many schema versions. |
| The Andrew W. Mellon Foundation | 13-1879954 | Grants to universities — the hardest recipient-matching case in the dataset. Check that "Harvard University" style names resolve correctly, or land in `U` rather than resolving wrongly. |
| Conrad N. Hilton Foundation | 95-3402444 | Significant international grantmaking. Check `recipient_country` handling. |
| Walton Family Foundation | 13-3441466 | High volume of small grants; good test of the long tail. |
| Silicon Valley Community Foundation | 20-5205488 | A **public charity** filing Schedule I, and simultaneously a major grant recipient. Tests the 990 path and confirms an entity can correctly appear on both sides of the graph. |
| Feeding America | 36-3673599 | Recipient-side test. Run `funders-of` and confirm the funders returned are plausible and traceable. |

**The specific checks:**

1. **Row-for-row on one filing.** Pick one Packard Foundation 990-PF. Count the grant lines in the
   actual Part XV. Count our rows for that `object_id`. They must be equal. Compare five individual
   grants field by field: recipient name, address, purpose, amount.
2. **Totals reconcile.** For each foundation above, our summed `paid` edges per filing versus the
   foundation's own reported total on the same filing. Investigate anything over 1%.
3. **Schema-version spread.** Confirm each foundation's filings across 2019–2025 span multiple
   `returnVersion` values and that the row counts do not fall off a cliff in any year. **A year with
   suspiciously few rows is a mapping failure, not a quiet year for the foundation.** This is the
   check most likely to catch a real bug.
4. **Matching spot-check.** Pull 50 tier B, 50 tier C, and 50 tier D rows at random. Verify each
   against the BMF by hand. If tier D is worse than 80%, demote the tier thresholds.
5. **Ambiguity check.** Find a grant to a chapter organization ("Boys and Girls Club of X"). Confirm
   it resolved to the correct local chapter or to `U`, never to a plausible-looking wrong chapter.
6. **The reverse query.** `funders-of --recipient-ein 36-3673599`. Are the funders plausible? Pick
   three and confirm the grant appears in that funder's actual filing.
7. **The cold-start test.** On a machine that has never seen this repo, paste the README quickstart
   query. Time it. If it is not under two seconds with real rows, M5 is not done.

---

## 16. Stop and ask the human

Do not guess on any of these. Stop, write down what you found, and ask.

1. **Concordance coverage is materially incomplete.** If the coverage matrix shows less than ~95% of
   filings by volume with full grant-field coverage, stop. The project's shape changes: it becomes a
   concordance-contribution project first and an ETL project second. That is a strategy decision, not
   an engineering one.
2. **990-PF attachment-reported grants are a large share.** If a significant fraction of large
   foundations report Part XV as an unstructured attachment rather than structured data, the
   dataset's coverage claim needs to change and the README needs rewriting. Quantify it and ask.
3. **Total corpus size or build time is far off.** If the full download is dramatically larger than a
   few hundred gigabytes, or a full build looks like it will take days rather than hours, ask before
   burning the compute.
4. **Any upstream license is more restrictive than expected.** If the concordance or a GivingTuesday
   tool carries a license incompatible with Apache 2.0 redistribution, **stop immediately.** Do not
   work around it. This is a legal and relationship question, not a technical one.
5. **A matching approach would need an LLM or a paid service.** Do not introduce either without
   asking. It changes the cost model, the reproducibility story, and the "no account required"
   promise.
6. **The published schema needs a breaking change after v1.0.** Consumers pin versions. Ask.
7. **Anything that would put a named natural person into the published dataset.** Ask. The default is
   no.
8. **R2 storage looks like it will exceed roughly 100 GB.** Cheap, but not free, and worth a
   conversation.
9. **You find a bug in an upstream project.** File it upstream, then ask whether to carry a local
   override in the meantime. Do not fork.
10. **Anything in `docs/NON-GOALS.md` starts looking necessary.** It is not. Ask.

---

## 17. Writing standards for anything you add to the docs

Write for a smart grant consultant who is not a developer. Expand every acronym on first use. Use
real EINs and real foundations rather than `foo` and `bar`. State the problem in the reader's
language before naming the tool. No marketing voice, no filler, no hedging. When something is
uncertain, say it is uncertain and say what would resolve it.

The README's published-schema table and the quickstart are load-bearing. Keep them true.
