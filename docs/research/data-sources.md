# Data sources

Everything funder-graph publishes derives from two IRS bulk datasets and one community-maintained
crosswalk. This document is the operational reference for all three: where the files are, what
they are called, what is inside them, and exactly which fields carry the grant edges.

Facts here were verified on **2026-08-30**. Items marked **VERIFY** should be re-checked before
they go into public-facing copy or into pipeline defaults.

---

## 1. IRS Form 990 series bulk e-file XML

The primary source. Every electronically filed return, as XML, one document per filing.

- **Landing page:** https://www.irs.gov/charities-non-profits/form-990-series-downloads
- **Base URL:** `https://apps.irs.gov/pub/epostcard/990/xml/{YEAR}/`
- **Format:** ZIP archives containing TEOS (Tax Exempt Organization Search) XML, one `.xml` per
  filing.
- **Cadence:** monthly. The IRS noted the latest posting as 2026-04-20 at verification.
- **Coverage:** 2019 through 2026. The 2026 directory had monthly files through July at
  verification.

### File naming, which changed

Naming is not consistent across the covered years, and a downloader that assumes one pattern will
silently fetch nothing for half the corpus.

| Years | Pattern | Notes |
|---|---|---|
| 2023–2026 | `{YEAR}_TEOS_XML_##X.zip` | e.g. `2024_TEOS_XML_01A.zip`. The `##` is a sequence number and the letter distinguishes parts within a posting. **Verified 2026-09-01 for 2023:** twelve files, `01A` through `12A`, 4.09 GB in total, from 120.7 MB (`12A`) to 1.20 GB (`11A`), each with an ETag and `Last-Modified`. |
| 2019–2020 | `download990xml_{YEAR}_#.zip` | e.g. `download990xml_2020_3.zip`. |
| 2021–2022 | **VERIFY** — enumerate rather than assuming. |

**Implementation rule:** do not hardcode file names, and **do not fetch the year directory
listing** — `https://apps.irs.gov/pub/epostcard/990/xml/2023/` returns a 302 to the IRS 404 page
(verified 2026-09-01). The enumerable source is the landing page above: extract every `.zip` and
`.csv` href from it and filter by year. Record the resolved URL plus its ETag or `Last-Modified`
and a SHA-256 of the downloaded bytes in the build manifest. That record is what makes a build
reproducible and what makes an incremental update possible.

### The index CSVs

Each year ships an index CSV alongside the ZIPs. This is the map from a filing to the XML file that
contains it, and it carries fields that are not inside the XML itself.

Typical columns (confirm against the actual header row, which has varied):

| Column | Meaning |
|---|---|
| `RETURN_ID` | IRS internal return identifier. |
| `FILING_TYPE` | `EFILE`. |
| `EIN` | Filer EIN, nine digits, no dash. |
| `TAX_PERIOD` | `YYYYMM` of the tax period end. |
| `SUB_DATE` | Submission date — the only reliable source for **when the filing became public**, and it is not in the XML. Carry it into `filing_submission_date`. |
| `TAXPAYER_NAME` | Filer name. |
| `RETURN_TYPE` | `990`, `990EZ`, `990PF`, `990T`, `990N`. Filter to `990` and `990PF`. |
| `DLN` | Document locator number. |
| `OBJECT_ID` | **The key.** Identifies exactly one XML document. |

`OBJECT_ID` is also the filename inside the ZIP: `{OBJECT_ID}_public.xml`. It is the provenance key
used throughout our schema and it is what lets a consumer trace any published row back to one
specific document.

**Gotchas:**

- **`SUB_DATE` is year-only in `index_2023.csv`** — every row reads `2023`, not a date (verified
  2026-09-01). `filing_submission_date` therefore has year precision for that posting, and the
  column type has to tolerate it. Check each year's index before assuming a full date.
- **The index does not say which ZIP holds a filing.** There is no archive column. The
  filing-to-ZIP map comes from listing each ZIP's members and joining on `OBJECT_ID`.
- Scale for 2023: 705,156 index rows — 347,337 Form 990, 209,957 990-EZ, 124,666 990-PF,
  23,196 990-T. `2023_TEOS_XML_12A.zip` holds 20,007 members, every one present in the index
  (zero delta in either direction), of which 2,852 are 990-PF.
- The index is not perfectly in sync with the ZIP contents in every posting. Reconcile both
  directions and report the delta rather than crashing.
- An organization can appear multiple times for one tax period — amended returns, or a return filed
  late alongside a current one. Deduplicate on `(EIN, TAX_PERIOD, RETURN_TYPE)` keeping the latest
  `SUB_DATE`, and keep the superseded `OBJECT_ID` in a `superseded_by` audit table rather than
  discarding it.
- Filings appear in the bulk posting for the year they were *published*, not the year they were
  *for*. A 2022 tax-year return can land in the 2024 posting. This is precisely why `filing_year`
  (publication) and `tax_year` (fiscal) are separate columns and why the partition key is
  `filing_year` — a monthly update touches recent `filing_year` partitions only.

### Historical note

The AWS S3 bucket `irs-form-990` used to mirror this corpus and is widely referenced in older
tutorials and blog posts. It was discontinued and should not be used as a source. Verified
2026-09-01: `https://s3.amazonaws.com/irs-form-990/{OBJECT_ID}_public.xml` returns 404.

**There is no per-filing URL anywhere.** The IRS path `.../xml/2023/{OBJECT_ID}_public.xml`
returns a 302 to the 404 page, and ProPublica's `full_text/{OBJECT_ID}` returns 404 (both
verified 2026-09-01). A single filing can only be obtained by locating the ZIP that contains it
and streaming that one member out — which is what `fetch-raw --object-id` does. It is a ZIP
member read, not a download "straight from the IRS", and the ZIP it needs may be a gigabyte.

---

## 2. Schema version drift — the actual hard part

Every filing's root element carries the schema version it was filed under:

```xml
<Return returnVersion="2023v4.0" xmlns="http://www.irs.gov/efile">
  <ReturnHeader binaryAttachmentCnt="0">
    ...
```

There are **hundreds** of distinct `returnVersion` values across the corpus. Element names,
nesting, and cardinality all changed between them. Some of the changes are cosmetic renames; some
move a field to a different parent; some split one element into two.

The general shape of the drift, which the pipeline has to survive:

- **The `Grp` suffix migration.** Modern schema versions suffix repeating group containers with
  `Grp` (`GrantOrContributionPdDurYrGrp`). Older versions do not
  (`GrantOrContributionPdDurYr`).
- **Type-suffixed leaf names.** Modern leaves carry a type hint: `Txt`, `Amt`, `Dt`, `Cd`, `Ind`,
  `Nm`, `Cnt`. Older versions use plain names — `Amount` became `Amt`, `PurposeOfGrantOrContribution`
  became `GrantOrContributionPurposeTxt`.
- **Address container changes.** `USAddress` versus `AddressUS` versus `RecipientUSAddress`, and a
  separate `ForeignAddress` container whose presence is how you detect a non-US recipient.
- **Namespace.** `http://www.irs.gov/efile` is used consistently in the modern corpus, but XPath
  evaluation must be namespace-aware regardless. Stripping namespaces before parsing is a
  legitimate simplification as long as it is done uniformly and documented.

**Do not hand-roll XPaths against this.** That is the mistake that has kept this data locked up,
and it is exactly the cost the commercial products are charging you to have already paid.

---

## 3. IRS E-file Master Concordance File

- **Docs:** https://nonprofit-open-data-collective.github.io/irs-efile-master-concordance-file/
- **Repo:** https://github.com/Nonprofit-Open-Data-Collective/irs-efile-master-concordance-file
- **Maintainer:** Nonprofit Open Data Collective

The concordance is a table that maps a stable logical variable name to the correct XPath **for each
schema version**. It is the reason this project is a six-week build instead of an eighteen-month
one.

Its columns (confirm against the current release; the file has evolved):

- a stable variable name
- the form and part the variable belongs to (990, 990-EZ, 990-PF, and schedules)
- the schema version the row applies to
- the XPath for that version
- a description, data type, and the scope of the containing element (whether it repeats)

**How the pipeline uses it:**

1. Pin a specific commit SHA in `data/upstream-pins.toml`. Record that SHA in every published row
   as `concordance_version`. A dataset built with an unpinned crosswalk is not reproducible.
2. At build start, load the concordance and construct, for each `returnVersion` we will encounter,
   a resolved map from our logical field names to concrete XPaths.
3. If a `returnVersion` in the corpus has no concordance coverage, **fail loudly for that filing
   and record it** in `build/reports/unmapped-versions.csv`. Do not silently emit zero grants for a
   foundation that made grants. A missing row looks identical to "this foundation gave nothing",
   and that is the worst possible failure mode for this dataset.
4. Local overrides for known gaps live in `data/overrides/concordance-overrides.toml` and every
   entry requires an upstream issue link. See `CONTRIBUTING.md`.

**Reality check, performed 2026-09-01 at commit `d8266da9`.** The answer is "mapping exercise",
with three things to know that cost a morning to learn:

1. **Form 990-PF is not in `concordance.csv`.** That file carries the core 990 and every
   schedule (6,864 rows) and contains zero 990-PF rows. Part XV lives in
   `02-concordance-foundations/F990-PF-FULL.CSV` (2,231 rows; 210 for Part XV, also sliced out
   as `f990pf-part-15-v3.csv`). A loader that reads only the main file reports 0% coverage of
   the primary edge list and looks like a strategy crisis. It is a file-layout fact.
2. **The `versions` column is stale; the XPaths are not.** Version annotations for the Part XV
   and Schedule I subtrees stop at `2016v3.0` and `2018v3.x`, while the XPaths flagged
   `current_version = T` match 2019–2022 filings exactly — every required Part XV field
   resolved on real filings at `2020v4.0`, `2021v4.2` and `2022v5.0`, and Schedule I resolved
   12 of 17 fields on a `2021v4.2` filing with the 5 misses being optional leaves genuinely
   absent from it. Resolution must therefore select current XPaths and **not** gate on
   `versions`; a missing annotation is an upstream metadata gap to report, not evidence a
   field is unmapped. Schedule I rows also carry an empty `current_version`, so "not flagged"
   cannot mean "not current" there.
3. **Upstream already holds the per-version truth.** `03-versions/raw-mappings/` has one XPath
   inventory per schema version from `2016v3.0` through `2022v5.0` (columns `Version, Source,
   Xpath, Type, Description, Line, MinOccur, MaxOccur`), each listing the 18 Part XV and 20
   Schedule I XPaths. The `versions` column was simply never regenerated from them. That makes
   the coverage matrix a join rather than a hand-check, and makes the upstream contribution a
   PR that extends `versions` from their own inventories, weighted by real filing volume.
4. **Upstream is already building the volume-weighted matrix — for the 990 core only.**
   `draft-updates/XPATH-VERSION-COUNT.CSV` (verified 2026-09-02) has columns
   `XPATH, VERSION, COUNT`: for each XPath, the `;;`-joined list of schema versions it appears
   in and the number of real filings carrying it, through `2023v`. The Schedule I
   `RecipientTable` row alone stands on 7,156,443 filings. It contains zero Part XV rows. Our
   deliverable is the 990-PF counterpart in those exact three columns. The `raw-mappings/`
   inventories are GivingTuesday's `form-990-xml-mapper` output (MIT; input is the IRS XSD set
   for one version, output is one CSV with these same eight columns), run by upstream through
   `2022v5.0`. The 2023 bulk posting carries `2023v4.0`, `2023v5.0` and `2023v5.1` filings,
   which neither the inventories nor the draft matrix cover yet; we run the mapper for those
   three and contribute the files.

All of it is vendored under `data/concordance/` with per-file SHA-256s in
`data/upstream-pins.toml`.

---

## 4. Where the grant edges actually live

Two tables, two forms. Element names below reflect the modern (roughly 2013-forward) TEOS schema
and are the *targets* to resolve through the concordance, not XPaths to hardcode. Paths are given
from the document root with the `http://www.irs.gov/efile` namespace elided for readability.

### 4a. Form 990-PF, Part XV — private foundation grants

"Supplementary Information: Grants and Contributions Paid During the Year or Approved for Future
Payment."

**Grants paid during the year** — the primary edge list. Repeating group:

```
/Return/ReturnData/IRS990PF/SupplementaryInformationGrp/GrantOrContributionPdDurYrGrp
```

| Child element | Maps to |
|---|---|
| `RecipientBusinessName/BusinessNameLine1Txt` (+ `BusinessNameLine2Txt`) | `recipient_name_raw` |
| `RecipientPersonNm` | recipient is a natural person → `recipient_type = 'individual'` |
| `RecipientUSAddress/AddressLine1Txt` | `recipient_address_line1` |
| `RecipientUSAddress/CityNm` | `recipient_city` |
| `RecipientUSAddress/StateAbbreviationCd` | `recipient_state` |
| `RecipientUSAddress/ZIPCd` | `recipient_zip` |
| `RecipientForeignAddress/*` | non-US recipient; sets `recipient_country` |
| `RecipientRelationshipTxt` | `recipient_relationship` |
| `RecipientFoundationStatusTxt` | `recipient_foundation_status` |
| `GrantOrContributionPurposeTxt` | `grant_purpose` |
| `Amt` | `amount_usd` |

**Grants approved for future payment** — same element shape, different parent:

```
/Return/ReturnData/IRS990PF/SupplementaryInformationGrp/GrantOrContributionApprovedFutGrp
```

Rows from this group get `amount_type = 'approved_future'`. **They must not be summed with paid
grants.** A commitment approved in 2022 and paid in 2023 appears in both, in different filings, and
naively adding them roughly doubles a multi-year funder's apparent giving. This is the single most
common analytical error made with 990-PF data.

**Older schema equivalents** (resolve through the concordance, listed here so the drift is
concrete): `GrantOrContributionPdDurYr`, with `RecipientNameBusiness/BusinessNameLine1`,
`RecipientAddressUS/*`, `PurposeOfGrantOrContribution`, `Amount`, `RecipientRelationship`,
`RecipientFoundationStatus`.

**Also on 990-PF, worth carrying:**

- `/Return/ReturnData/IRS990PF/SupplementaryInformationGrp/ApplicationSubmissionInfoGrp` —
  whether the foundation accepts unsolicited applications, submission deadlines, and the person
  applications go to. **We use only the accepts-applications indicator and the deadline text. We do
  not publish the contact person.** See `docs/NON-GOALS.md`.
- `/Return/ReturnData/IRS990PF/SupplementaryInformationGrp/TotalGrantOrContriPdDurYrAmt` —
  element name **verified 2026-09-01** (it sits inside `SupplementaryInformationGrp`, not
  directly under `IRS990PF`; concordance variable `F9_15_PF_SUINTOGRORCO`, with
  `TotalGrantOrContriApprvFutAmt` alongside it for the future group). The foundation's own
  stated total of grants paid. This is a free integrity check: our summed edges for a filing
  should reconcile to it — and on the first real filing parsed, four rows summed to 50,000
  against a stated 50,000, delta zero. **It is optional and some filers omit it entirely**, so
  the reconciliation report must distinguish "no total stated" from "total disagrees". Publish the reconciliation delta per filing in
  `build/reports/pf-total-reconciliation.csv`. A filing whose parsed edges do not sum to its own
  reported total is a parsing bug with a built-in detector, and this check is the highest-value
  quality control in the entire pipeline.

**Part XV is frequently filed as an attachment.** Some foundations, particularly large ones, report
grants in Part XV via a statement rather than the structured group. Where the structured group is
empty but the foundation's stated total is large, flag it in
`build/reports/pf-missing-detail.csv`. This is a known, real limitation and it must be measured and
published, not hidden.

**It has a second shape the empty-group check misses.** Real example (2022v5.0, from the 2023
posting): one structured row with `RecipientPersonNm = "VARIOUS ORGANIZATIONS"`, address
`SEE ATTACHED SCHEDULE`, country `CI`, amount `9758900`, and no stated total anywhere in the
filing. The group is not empty, so the empty-group detector stays quiet; the person-name slot is
populated, so a naive rule tags $9.76M as a scholarship to a natural person and drops it from the
default view. Both failures are silent. The extractor recognises aggregate placeholders
(`VARIOUS`, `MISCELLANEOUS`, `SEE ATTACHED …`) on the row, tags them `recipient_type = unknown`,
records a row-level flag, and fires the filing-level missing-detail flag when a stated total has
*only* placeholder rows against it.

**`RecipientPersonNm` is not a reliable person indicator on its own.** Filers put organization
names in it routinely. On one 2022v5.0 filing every one of seven rows used the person slot and
every one was an organization — THE TREVOR PROJECT, ACTIVE MINDS INC, YWCA OF GREATER AUSTIN. The
spec's rule is "populated *with no organizational tokens*", and that clause is load-bearing: the
extractor applies a token list (INC, FOUNDATION, UNIVERSITY, …, plus the chapter organizations)
before calling anything an individual. The honest limit is a two-word charity with no token —
COMFORT CASES, BLACK MEN HEAL — which a name-only rule cannot distinguish from a person. That is
why individuals are *tagged and excluded from the default view* rather than deleted: a wrong tag
is recoverable, a deleted row is not.

### 4b. Form 990, Schedule I — public charity grants

"Grants and Other Assistance to Organizations, Governments, and Individuals in the United States."

**Part II, grants to organizations** — the edge list, and it usually includes the recipient EIN:

```
/Return/ReturnData/IRS990ScheduleI/RecipientTable
```

| Child element | Maps to |
|---|---|
| `RecipientBusinessName/BusinessNameLine1Txt` (+ `Line2`) | `recipient_name_raw` |
| `RecipientEIN` | `recipient_ein_reported` |
| `USAddress/AddressLine1Txt`, `CityNm`, `StateAbbreviationCd`, `ZIPCd` | recipient address fields |
| `IRCSectionDesc` | recipient IRC section as stated by the filer |
| `CashGrantAmt` | `amount_usd` |
| `NonCashAssistanceAmt` | `noncash_amount_usd` |
| `ValuationMethodUsedDesc` | valuation method for non-cash |
| `NonCashAssistanceDesc` | description of non-cash assistance |
| `PurposeOfGrantTxt` | `grant_purpose` |

All Schedule I Part II rows get `amount_type = 'paid'`.

**Part III, grants to individuals** —
`/Return/ReturnData/IRS990ScheduleI/GrantsOtherAsstToIndivInUSGrp` — is aggregated by grant type
and contains no named individuals. It is **out of scope** for the edge list.

**Part I** carries `Total501c3OrgCnt` and `TotalOtherOrgCnt`, the filer's own count of recipient
organizations. Same trick as the 990-PF total: reconcile our parsed row count against it and
publish the delta.

**Older schema equivalents:** `RecipientTable` with `EINOfRecipient`, `NameOfOrganization` or
`RecipientNameBusiness`, `AmountOfCashGrant`, `AmountOfNonCashAssistance`, `PurposeOfGrant`,
`MethodOfValuation`.

**Schedule I only exists if the filer answered yes** to the Part IV trigger questions on the core
Form 990 and made more than $5,000 in grants to any single recipient. Absence of Schedule I is not
evidence of no grantmaking.

### 4c. Filer identity, from the return header

Every edge needs its funder and its provenance, and both come from the header:

```
/Return/@returnVersion                                    -> return_version
/Return/ReturnHeader/Filer/EIN                            -> funder_ein
/Return/ReturnHeader/Filer/BusinessName/BusinessNameLine1Txt -> funder_name
/Return/ReturnHeader/Filer/USAddress/StateAbbreviationCd  -> funder_state
/Return/ReturnHeader/TaxPeriodEndDt                       -> tax_period_end -> tax_year
/Return/ReturnHeader/ReturnTypeCd                         -> funder_form_type
/Return/ReturnHeader/BuildTS                              -> build timestamp, audit only
```

`filing_submission_date` comes from the index CSV's `SUB_DATE`, not from the XML.

### 4d. Explicitly out of scope

- **Schedule F** (activities outside the United States) reports foreign grantmaking by region and
  generally without named recipients. Not an edge list.
- **Schedule R** (related organizations) — interesting, different problem.
- Compensation, balance sheet, functional expenses, governance. See `docs/NON-GOALS.md`.

---

## 5. IRS Exempt Organizations Business Master File (EO BMF)

Required for entity resolution, which is required because 990-PF Part XV usually does not report a
recipient EIN.

- **Page:** https://www.irs.gov/charities-non-profits/tax-exempt-organization-search-bulk-data-downloads
- **Files, verified 2026-09-02:** four regional CSVs served directly, not ZIP-wrapped:
  `https://www.irs.gov/pub/irs-soi/eo1.csv` (48.6 MB, 278,014 rows), `eo2.csv` (125.7 MB,
  719,134 rows), `eo3.csv` (164.6 MB, 955,286 rows), `eo4.csv` (0.9 MB, 4,906 rows) - 340 MB and
  1,957,340 organizations in the 2026-08 vintage (`Last-Modified: 10 Aug 2026`).
- **Format:** comma-delimited **with a header** (28 columns, `EIN` .. `SORT_NAME`) and RFC 4180
  quoting; some rows carry a comma inside a quoted field. Not pipe-delimited, which the earlier
  draft of this note said. Parsed by `grantcheck.ingest.teos.parse_bmf`, which asserts the header
  by name so an inserted column fails loudly. The files carry **duplicate EIN rows** (EIN
  000019818 appears twice in the 49-row fixture alone); the loader keeps one per EIN and reports
  rows parsed and organizations kept separately.
- **Loading:** `funder-graph build bmf --file eo1.csv --file eo2.csv --file eo3.csv --file eo4.csv
  --vintage 2026-08`, all four in one invocation. The vintage is the posting month, stamped on
  every row and on every resolution made against it.
- **Cadence:** monthly.
- **Scale:** roughly 1.96 million rows.
- **Key fields:** EIN, legal name, sort name (often the DBA), street, city, state, ZIP, subsection
  code, classification, ruling date, deductibility code, foundation code, NTEE code, asset amount,
  revenue amount, and the tax period of the most recent filing.
- A Data Dictionary is linked from the same page. Read it — several codes are not
  self-explanatory, and the ZIP field includes ZIP+4 inconsistently.

**Also on that page, and relevant:**

- **Publication 78 Data** — organizations eligible to receive tax-deductible contributions. Last
  updated 2026-04-14 at verification. Useful as a corroborating signal in matching.
- **Automatic Revocation of Exemption List** — organizations whose exemption was revoked for three
  consecutive years of non-filing, with revocation and reinstatement dates. Last updated 2026-04-14.
  A recipient that appears here explains a lot of `reported_unverified` EINs.
- **Form 990-N (e-Postcard)** filings, posted 2026-04-27. Small filers, useful for confirming an
  organization is alive.

**Matching gotchas that will bite:**

- The BMF legal name is frequently not the name a grant is filed under. "PRESIDENT AND FELLOWS OF
  HARVARD COLLEGE" receives grants filed as "Harvard University", "Harvard Kennedy School",
  "Harvard T.H. Chan School of Public Health". Sort name helps but does not solve this.
- Address in the BMF is the organization's mailing address, which is frequently a lawyer, an
  accountant, or a lockbox — not where the program runs. Address agreement is strong positive
  evidence; address disagreement is weak negative evidence. Score them asymmetrically.
- Chapter-based organizations (Boys and Girls Clubs, United Way, Habitat for Humanity, YMCA) have
  hundreds of distinct EINs with near-identical names. These are the highest-risk matches in the
  corpus and geography is the only thing that separates them. Consider requiring ZIP or city
  agreement before assigning above tier C for any name matching a known chapter-organization
  pattern.
- Community foundations and donor-advised fund sponsors appear as both funder and recipient at
  enormous volume. Silicon Valley Community Foundation (EIN 20-5205488) is both a major Schedule I
  grantmaker and a major grant recipient. This is correct, not a bug, but it means naive
  "top recipients" rankings are dominated by DAF sponsors and fiscal sponsors. Document it.

---

## 6. ProPublica Nonprofit Explorer API — verification only

- **Docs:** https://projects.propublica.org/nonprofits/api
- **Base:** `https://projects.propublica.org/nonprofits/api/v2`
- **Endpoints:** `GET /search.json` (`q`, `page` zero-indexed, `state[id]`, `ntee[id]`,
  `c_code[id]`), `GET /organizations/{ein}.json`
- **Auth:** none documented. **Rate limits:** none documented, though PDF download links are rate
  limited.
- **Coverage:** 1.8M+ filings from 2001 onward.
- **Terms:** https://www.propublica.org/about/propublica-data-terms-of-use

Used for spot-checking and for resolving ambiguous EINs during development. **Not** a source for
the published dataset and nothing from it is redistributed. Cache aggressively, send a descriptive
User-Agent, and stay well under any reasonable request rate. Being a nuisance to ProPublica would
be an unforced error.

---

## 7. OpenGrants API — optional live layer

Never part of the published dataset. Called at request time only when the user supplies their own
key.

- Base: `https://qnoicxojartltrownmal.supabase.co/functions/v1/`
- Auth: `Authorization: Bearer <key>`
- Used here: `GET /funders-api`, `GET /funders-api/{id}`, `GET /grants-api`
- Rate limit headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`
- Docs: https://ops.opengrants.io/api-docs

---

## 8. Refresh cadence summary

| Source | Cadence | Pipeline action |
|---|---|---|
| IRS 990 bulk XML | Monthly | Incremental: diff index CSVs, parse new OBJECT_IDs, rewrite affected `filing_year` partitions |
| IRS EO BMF | Monthly | Full reload; re-run resolution for previously unmatched rows only |
| Publication 78 | Monthly | Full reload, matching signal |
| Automatic Revocation List | Monthly | Full reload, annotation |
| ProPublica API | Live | Verification only |
| OpenGrants API | Live | Request-time enrichment only |

A monthly release is `YYYY.MM.0`. An out-of-band fix to mapping or matching with no new source data
is `YYYY.MM.N`.
