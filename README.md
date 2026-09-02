# funder-graph — The Open 990 Funding Graph

[![CI](https://github.com/egeria-corporation/funder-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/egeria-corporation/funder-graph/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](./LICENSE)

The single most useful question in fundraising is not "who gives to causes like mine?" It is
**"who has this funder actually written checks to, and for how much?"** A funder's stated
priorities are marketing. Their grant list is the truth, and the truth is public.

Every private foundation that files a Form 990-PF must list, on Part XV, every grant it paid
during the year: recipient name, recipient address, the purpose, and the dollar amount. Every
public charity that makes grants to other organizations must list them on Schedule I of the
Form 990, usually including the recipient's EIN. Those two tables, across every filing the IRS
has published electronically, are tens of millions of funder-to-recipient edges. They are the
substance of what the paid foundation-research products sell access to.

Nobody had freed them because the raw data is genuinely unpleasant. The IRS publishes it as
ZIP archives of XML, one file per filing, across hundreds of schema versions with element names
that changed between years and no stable field naming. Parsing it correctly is a real
engineering project. That is the entire moat of the paid products in this category — and it is
a one-time cost that disappears permanently the moment somebody pays it in public.

**This repo is that payment.** It parses the full IRS e-file corpus into one normalized
funder-to-recipient grant edge list and publishes it as versioned, partitioned Parquet with no
paywall, no account, and no rate limit.

---

## Credits

This project stands on work done by other people first, and it would not be possible without
them. Credit is not a footnote here.

| Project | Who | What we use it for |
|---|---|---|
| [IRS E-file Master Concordance File](https://nonprofit-open-data-collective.github.io/irs-efile-master-concordance-file/) | [Nonprofit Open Data Collective](https://github.com/Nonprofit-Open-Data-Collective) | The crosswalk that maps a logical field to the correct XPath in each 990 schema version. This is the single asset that makes the corpus tractable. We do not hand-roll XPaths. |
| [form-990-xml-mapper](https://github.com/Giving-Tuesday/form-990-xml-mapper) | [GivingTuesday](https://990data.givingtuesday.org/tool-repository/) Data Commons | Enumerating every XPath present in a given schema version, which is how we detect drift the concordance has not caught up to yet. |
| [form-990-xml-parser](https://github.com/Giving-Tuesday/form-990-xml-parser) | GivingTuesday Data Commons | Reference implementation for XML traversal and repeating-group handling. |
| [IRS-Efile-Database](https://nonprofit-open-data-collective.github.io/IRS-Efile-Database/) | Nonprofit Open Data Collective | Prior schema design for a normalized 990 database. |
| [ProPublica Nonprofit Explorer API](https://projects.propublica.org/nonprofits/api) | ProPublica | Gap-filling lookups and cross-checks. Not a substitute for parsing the IRS source ourselves. |

Full attribution, licenses, and our contribution plan are in
[`NOTICE`](./NOTICE) and [`docs/research/prior-art.md`](./docs/research/prior-art.md).
Fixes we make to shared mapping logic go upstream first. See
[`CONTRIBUTING.md`](./CONTRIBUTING.md).

---

## 60-second quickstart — no install, no account

You do not run the pipeline. The pipeline already ran. You query the published dataset directly
over HTTPS.

If you have [DuckDB](https://duckdb.org) (`brew install duckdb`, or the in-browser shell at
[shell.duckdb.org](https://shell.duckdb.org) with nothing installed at all), paste this:

```sql
INSTALL httpfs; LOAD httpfs;

SELECT recipient_name_raw,
       recipient_state,
       amount_usd,
       tax_year,
       grant_purpose
FROM read_parquet(
  'https://data.opengrants.io/funder-graph/latest/grants/*/*.parquet',
  hive_partitioning = 1
)
WHERE funder_ein = '941156365'   -- The David and Lucile Packard Foundation
  AND amount_usd >= 250000
ORDER BY amount_usd DESC
LIMIT 25;
```

That returns real rows in about a second. It does not download the dataset. DuckDB reads the
Parquet footer over HTTP range requests, prunes the partitions and row groups it does not need,
and pulls only the bytes that matter — typically a few megabytes out of several gigabytes.

Reverse the question. Who has funded Feeding America (EIN 36-3673599)?

```sql
SELECT funder_name, SUM(amount_usd) AS total, COUNT(*) AS grants, MAX(tax_year) AS latest
FROM read_parquet('https://data.opengrants.io/funder-graph/latest/grants/*/*.parquet', hive_partitioning = 1)
WHERE recipient_ein_resolved = '363673599'
  AND match_confidence >= 0.90
GROUP BY funder_name
ORDER BY total DESC
LIMIT 25;
```

Note the `match_confidence >= 0.90` filter. It is there for a reason — see
[Data honesty](#data-honesty-read-this-before-you-cite-anything) below.

---

## The CLI

For people who would rather not write SQL. Nothing to install and no clone required:

```bash
uvx funder-graph query --funder-ein 94-1156365 --min-amount 25000
```

Output is a human-readable table with a source-and-vintage footer. Add `--json` for machines.

```bash
# Every grant a foundation paid, filtered
uvx funder-graph query --funder-ein 94-1156365 --min-amount 25000 --year 2023

# Who funds this organization
uvx funder-graph funders-of --recipient-ein 36-3673599 --min-confidence 0.90

# Who funds organizations that look like this one
uvx funder-graph similar --keyword "food security" --state CA --limit 50

# A funder's profile: totals by year, top recipients, geographic concentration
uvx funder-graph funder 94-1156365

# What dataset am I actually querying
uvx funder-graph dataset info

# Pin a version so your analysis is reproducible
uvx funder-graph --dataset-version 2026.08.0 query --funder-ein 94-1156365
```

EINs are accepted with or without the dash. Internally they are stored as nine digits, zero-padded,
no punctuation.

### For agents

The same capabilities are exposed as an MCP (Model Context Protocol) server over stdio:

```bash
uvx funder-graph mcp
```

Tools: `funder_grants`, `funders_of_recipient`, `find_funders_by_keyword`, `funder_profile`,
`dataset_info`. The CLI and the MCP server are both thin adapters over the same library module.

---

## Published schema

One row is one reported grant line from one filing. The canonical table is `grants`, partitioned
by `filing_year` and sorted within each partition by `funder_ein`.

| Column | Type | Nullable | Meaning |
|---|---|---|---|
| `grant_id` | `VARCHAR` | no | Stable deterministic ID for this edge. Hash of `object_id` + row ordinal within the filing. Survives re-ingests. |
| `funder_ein` | `VARCHAR(9)` | no | The filing organization's EIN, nine digits, no dash. |
| `funder_name` | `VARCHAR` | no | Filer name as it appears in the return header. |
| `funder_state` | `VARCHAR(2)` | yes | Filer state from the return header. |
| `funder_form_type` | `VARCHAR` | no | `990PF` or `990`. Tells you whether the edge came from Part XV or Schedule I. |
| `object_id` | `VARCHAR` | no | The IRS OBJECT_ID of the source filing. This is your provenance key — it identifies exactly one XML document. |
| `tax_year` | `INTEGER` | no | Tax year of the filing (derived from the tax period end date). |
| `tax_period_end` | `DATE` | no | End of the fiscal period the grant was paid in. |
| `filing_submission_date` | `DATE` | yes | When the return was submitted to the IRS, from the bulk index. |
| `filing_year` | `INTEGER` | no | **Partition key.** Year of the IRS bulk file the filing was published in. |
| `return_version` | `VARCHAR` | no | The XML schema version of the source filing, e.g. `2023v4.0`. Kept so drift is auditable. |
| `amount_usd` | `BIGINT` | no | Grant amount in whole US dollars. Cash grant amount for Schedule I. |
| `noncash_amount_usd` | `BIGINT` | yes | Non-cash assistance value. Schedule I only; null for 990-PF. |
| `amount_type` | `VARCHAR` | no | `paid` (paid during the year) or `approved_future` (approved for future payment). **Filter on this.** Summing both double-counts. |
| `grant_purpose` | `VARCHAR` | yes | Stated purpose, verbatim from the filing. Free text, wildly inconsistent, still the most useful text field in the dataset. |
| `recipient_name_raw` | `VARCHAR` | no | Recipient name exactly as filed, including typos. |
| `recipient_name_normalized` | `VARCHAR` | no | Uppercased, punctuation-stripped, legal-suffix-normalized form used for matching. |
| `recipient_ein_reported` | `VARCHAR(9)` | yes | EIN as reported on the filing. Common on Schedule I, mostly absent on 990-PF Part XV. |
| `recipient_ein_resolved` | `VARCHAR(9)` | yes | Our best determination of the recipient's EIN. Null when we could not resolve one. |
| `recipient_ein_source` | `VARCHAR` | no | How we got it: `reported_verified`, `reported_unverified`, `bmf_deterministic`, `bmf_strong`, `bmf_probable`, `manual_correction` (from `data/overrides/ein-corrections.csv`), `unresolved`. |
| `match_confidence` | `DOUBLE` | yes | 0.0–1.0. Null when `recipient_ein_resolved` is null. See the confidence table below. |
| `match_tier` | `VARCHAR(1)` | no | `A`, `B`, `C`, `D`, or `U`. Coarse bucket for the confidence score, for when you want to filter without thinking about float thresholds. |
| `match_method` | `VARCHAR` | yes | Short machine-readable name of the rule that produced the match, e.g. `name_zip5_state_unique`. Null when unresolved. Lets you audit *why* a row matched, not just how confidently. |
| `recipient_address_line1` | `VARCHAR` | yes | As filed. |
| `recipient_city` | `VARCHAR` | yes | As filed. |
| `recipient_state` | `VARCHAR(2)` | yes | As filed. `recipient_country` is set for foreign recipients. |
| `recipient_zip` | `VARCHAR` | yes | As filed, not normalized to five digits — `recipient_zip5` is. |
| `recipient_zip5` | `VARCHAR(5)` | yes | First five digits, used for matching. |
| `recipient_country` | `VARCHAR(2)` | yes | ISO country code. `US` unless the filing says otherwise. |
| `recipient_bmf_name` | `VARCHAR` | yes | The legal name from the IRS Business Master File for the resolved EIN. Compare it against `recipient_name_raw` to sanity-check a match yourself. |
| `recipient_ntee_code` | `VARCHAR` | yes | NTEE (National Taxonomy of Exempt Entities) code from the BMF, when resolved. |
| `recipient_subsection_code` | `VARCHAR` | yes | IRC subsection from the BMF, e.g. `03` for 501(c)(3). |
| `recipient_type` | `VARCHAR` | no | `organization`, `individual`, `government`, or `unknown`. Individual-recipient rows are excluded from the default edge view. |
| `recipient_relationship` | `VARCHAR` | yes | 990-PF Part XV relationship-to-foundation text, where reported. Non-null here is a flag worth reading: it often means a related party. |
| `recipient_foundation_status` | `VARCHAR` | yes | 990-PF Part XV foundation-status text, where reported. |
| `concordance_version` | `VARCHAR` | no | Commit SHA of the concordance file used to map this row. |
| `dataset_version` | `VARCHAR` | no | The release this row was built in, e.g. `2026.08.0`. |
| `ingested_at` | `TIMESTAMP` | no | When our pipeline wrote the row. |

Companion tables published alongside `grants`:

- `funders` — one row per filing EIN with totals, year coverage, and latest filing date.
- `recipients` — one row per resolved recipient EIN with BMF attributes and inbound totals.
- `unmatched` — recipient strings we could not resolve, with candidate counts. Published on
  purpose: it is the honest accounting of what the dataset does not know, and it is the best
  place for the community to contribute fixes.
- `manifest.json` — dataset version, build timestamp, row counts per partition, the list of
  source IRS files consumed with their checksums, and the concordance commit.

### Match confidence semantics

`match_confidence` is not a vibe. It is a defined score with a defined meaning per tier.

| Tier | Confidence | `recipient_ein_source` | What actually happened |
|---|---|---|---|
| A | 1.00 | `reported_verified` | The filing reported an EIN and that EIN exists in the IRS Business Master File. This is fact, not inference. |
| A | 0.95 | `reported_unverified` | The filing reported a structurally valid EIN that is not in the current BMF. Usually a merged, revoked, or terminated organization — or a typo. Treated as reported, flagged as unverified. |
| B | 0.90–0.94 | `bmf_deterministic` | No EIN on the filing. Normalized name plus ZIP5 plus state matched exactly one BMF record. |
| C | 0.75–0.89 | `bmf_strong` | Normalized name plus state matched exactly one BMF record, or a high string-similarity match confirmed by ZIP. |
| D | 0.50–0.74 | `bmf_probable` | Fuzzy name match within state produced a single candidate above threshold, with no corroborating address signal. **This is a guess with a number attached to it.** |
| U | `NULL` | `unresolved` | No match, or several equally good candidates, or the recipient is an individual or a government body. |

**The CLI defaults to `--min-confidence 0.90`, and so should you.** Tier D rows are included in
the published dataset because throwing away information is worse than labeling it, not because
they are safe to cite.

---

## Dataset versioning and citation

Versions are `YYYY.MM.PATCH` — `2026.08.0` is the first release built from the August 2026 IRS
posting. The IRS publishes monthly, so a normal month bumps the minor position; a re-release that
fixes a mapping bug without new source data bumps the patch.

```
https://data.opengrants.io/funder-graph/2026.08.0/grants/filing_year=2024/part-0000.parquet
https://data.opengrants.io/funder-graph/2026.08.0/manifest.json
https://data.opengrants.io/funder-graph/latest/        -> alias, moves every month
```

Use `latest` for exploration. **Pin an explicit version for anything you will be asked to
reproduce** — a board memo, a research paper, a client deliverable. `latest` moving under a
saved query is not a bug, it is the design.

Suggested citation:

> Egeria Corporation. *funder-graph: The Open 990 Funding Graph*, dataset version 2026.08.0.
> Derived from IRS Form 990 and 990-PF e-file XML and the IRS Exempt Organizations Business
> Master File. Field mapping via the Nonprofit Open Data Collective IRS E-file Master
> Concordance File. https://github.com/egeria-corporation/funder-graph

Every row carries `object_id`, `tax_period_end`, and `filing_submission_date`, so any figure you
publish can be traced back to one specific filing. Do that. It is the difference between a number
and a citation.

---

## Data honesty (read this before you cite anything)

- **Some edges are fuzzy-matched, and we tell you which ones.** Form 990-PF Part XV frequently
  reports a recipient by name and mailing address only, with no EIN. Resolving those to an
  organization requires matching against the Business Master File, and matching is inference.
  `match_confidence` and `match_tier` exist so you never have to guess how much to trust a row.
  Anything below 0.90 should be treated as a lead, not a fact.
- **`amount_type` matters.** 990-PF Part XV reports grants *paid during the year* and grants
  *approved for future payment* in two separate tables. They overlap across years. Summing both
  will overstate giving, sometimes badly. Default to `amount_type = 'paid'`.
- **Coverage starts where electronic filing does.** The IRS bulk XML corpus covers 2019 forward
  in the current posting layout. Electronic filing became mandatory for most filers for tax years
  beginning after July 1, 2019, so earlier years are real but partial, and small paper filers are
  absent entirely.
- **Filings lag.** A foundation's calendar-2025 grants may not appear until late 2026. The
  dataset is a rear-view mirror by construction. Anyone selling you "current" foundation giving
  from 990 data is selling you the same lag with a nicer chart.
- **Recipient names are as filed.** Typos, abbreviations, DBA names, and inconsistent spellings
  are preserved in `recipient_name_raw` on purpose. We normalize for matching; we do not rewrite
  the source.
- **Individual grantees are excluded from the edge list.** 990-PF scholarship payments to natural
  persons are tagged `recipient_type = 'individual'` and filtered out of the default view. They
  are not the funding graph and publishing named individuals serves nobody.
- **Source datasets and cadence:** IRS Form 990 series bulk e-file XML
  ([source](https://www.irs.gov/charities-non-profits/form-990-series-downloads)), monthly. IRS
  Exempt Organizations Business Master File
  ([source](https://www.irs.gov/charities-non-profits/tax-exempt-organization-search-bulk-data-downloads)),
  monthly. Full detail in [`docs/research/data-sources.md`](./docs/research/data-sources.md).

> This is informational only, derived from public data on the dates shown. It is not an
> eligibility determination, and not legal, tax, or accounting advice. Verify against the
> official source before relying on it.

---

## Optional: live opportunities from OpenGrants

funder-graph is fully functional with no credentials of any kind. The dataset is public and the
CLI never asks you for anything.

If you set `OPENGRANTS_API_KEY` in your environment, `funder-graph funder <ein>` will
additionally show currently open opportunities from that funder via the OpenGrants
`/funders-api` and `/grants-api` endpoints, marked `— live from OpenGrants`. That combination —
here is what they have actually funded, and here is what they have open right now — is the one
thing no competitor ships, because the history and the openings live in different products
everywhere else.

Enrichment failures degrade silently. An expired key or a network problem never breaks a query.
Copy [`.env.example`](./.env.example) to `.env` if you want it. This is the only place in the
project that mentions it.

---

## Hosted companion

[**funders.opengrants.io**](https://funders.opengrants.io) renders the same data as a page per
foundation — "Grants paid by the David and Lucile Packard Foundation, 2019–2025" — with the
source filing and vintage stated inline on every page. Architecture in
[`docs/hosted/architecture.md`](./docs/hosted/architecture.md).

---

## What this will never do

Read [`docs/NON-GOALS.md`](./docs/NON-GOALS.md) before opening a feature request. It is short and
it is meant seriously.

---

## License

Apache License 2.0. See [`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE).

The published dataset is derived from US federal government works, which are not subject to
copyright in the United States. Our derived structure, matching, and documentation are released
under Apache 2.0. Attribution is appreciated and makes you easier to trust.

---

Built and maintained by [Egeria Corporation](https://github.com/egeria-corporation), sponsored by
[OpenGrants](https://opengrants.io).
