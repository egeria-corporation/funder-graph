<!-- VENDORED COPY. Canonical source: https://github.com/egeria-corporation/.github
     Do not edit here. Edit the canonical copy and re-vendor. -->

> **Program-level document, vendored into this repository.** The canonical copy lives in
> [`egeria-corporation/.github`](https://github.com/egeria-corporation/.github). It is copied here so that a fresh clone of this
> repository — and any coding agent working in one — can read it without fetching another
> repository. The competitive pricing section is withheld from vendored copies; see the note inside.

# Verified Research Dossier

All facts below were verified on 2026-08-30. Repos should cite these rather than re-deriving them, and should re-verify anything marked **VERIFY** before it goes into public-facing copy.

## IRS — Form 990 bulk e-file XML

- **Page:** https://www.irs.gov/charities-non-profits/form-990-series-downloads
- **Base URL pattern:** `https://apps.irs.gov/pub/epostcard/990/xml/{YEAR}/`
- **Naming:** `{YEAR}_TEOS_XML_##X.zip` for 2023–2026; `download990xml_{YEAR}_#.zip` for 2019–2020
- **Coverage:** 2019 through 2026 (2026 has monthly files through July as of verification). Each year ships an index CSV.
- **Format:** ZIP archives of TEOS XML, one XML per filing.
- **Cadence:** monthly. Latest posting noted by IRS as 2026-04-20.
- **The hard part:** hundreds of schema versions across years with inconsistent XPaths and no stable field naming. This is the entire moat of the commercial products in this category. Do not hand-roll the mapping — use the Master Concordance File (below).

### The two tables that matter

- **Form 990-PF, Part XV** — "Supplementary Information: Grants and Contributions Paid During the Year or Approved for Future Payment." Every grant a private foundation paid, with recipient name, address, relationship, purpose, and amount. This is the funder→recipient edge list.
- **Form 990, Schedule I** — "Grants and Other Assistance to Organizations, Governments, and Individuals in the United States." The same edges for public charities, including recipient EIN where reported.

Recipient EIN is reported inconsistently on 990-PF Part XV (often name and address only), so name/address matching against the Business Master File is a required step, not an optimization. Treat match confidence as a first-class field in the output schema.

## IRS — Tax Exempt Organization Search (TEOS) bulk downloads

- **Page:** https://www.irs.gov/charities-non-profits/tax-exempt-organization-search-bulk-data-downloads
- **Format:** pipe-delimited ASCII text, ZIP-wrapped. **Cadence:** monthly.
- **Datasets:**
  - **Publication 78 Data** — organizations eligible to receive tax-deductible charitable contributions (last updated 2026-04-14 at verification)
  - **Automatic Revocation of Exemption List** — orgs whose exemption was revoked for three consecutive years of non-filing, with revocation and reinstatement dates (last updated 2026-04-14)
  - **Form 990-N (e-Postcard)** — most recent e-Postcard filings (posted 2026-04-27)
  - **Exempt Organizations Business Master File (EO BMF)** — the master roster: EIN, name, address, subsection code, NTEE code, ruling date, asset and revenue amounts. Roughly 1.9M rows.
- A Data Dictionary and the TEOS annotated forms/schemas are linked from the same page.

## ProPublica Nonprofit Explorer API

- **Docs:** https://projects.propublica.org/nonprofits/api
- **Base:** `https://projects.propublica.org/nonprofits/api/v2`
- **Endpoints:** `GET /search.json` (params: `q`, `page` zero-indexed, `state[id]`, `ntee[id]`, `c_code[id]`), `GET /organizations/{ein}.json`
- **Auth:** none documented. **Rate limits:** none documented, though PDF download links are rate limited. Be a good citizen — cache aggressively and set a descriptive User-Agent.
- **Coverage:** 1.8M+ filings from 2001 onward (990, 990-EZ, 990-PF).
- **Terms:** https://www.propublica.org/about/propublica-data-terms-of-use — read before any redistribution. Use this API for lookups and gap-filling, not as a substitute for parsing the IRS source ourselves.

## Grants.gov

- **API guide:** https://grants.gov/api/api-guide
- **Base:** `https://api.grants.gov` (staging: `https://api.staging.grants.gov`)
- **`search2`** — POST with JSON body, e.g. `{"keyword": "..."}`. **No authentication required.**
- **`fetchOpportunity`** — retrieve a single opportunity. **No authentication required.**
- Other Grants.gov APIs require a key obtained via a Help Desk ticket. The two above are the ones to build on.
- Simpler.Grants.gov is the modernization effort and has its own API — worth tracking as it matures: https://wiki.simpler.grants.gov/product/api

## USAspending

- **API root:** https://api.usaspending.gov/ — open, no key required.
- Key endpoint for this program: `POST /api/v2/search/spending_by_award` — contract documented at https://github.com/fedspendingtransparency/usaspending-api/blob/master/usaspending_api/api_contracts/contracts/v2/search/spending_by_award.md
- Covers direct federal assistance awards (grants, cooperative agreements) and contracts, with recipient name, UEI, amounts, dates, and Assistance Listing (CFDA) numbers.
- **Does not** reliably cover subawards to the depth needed for pass-through analysis — that is what the Federal Audit Clearinghouse is for.

## Federal Audit Clearinghouse (FAC)

- **API:** https://www.fac.gov/api/ — **free API key, obtained by email signup** at https://www.fac.gov/api/signup/
- Built on PostgREST, so standard PostgREST filtering, pagination, and ordering apply.
- **Terms:** https://www.fac.gov/api/terms/ — review before redistribution.
- Contains single audit submissions, including the **Schedule of Expenditures of Federal Awards (SEFA)**, which reports federal money an organization expended *and passed through to subrecipients*.
- **Why this matters strategically:** the pass-through layer is where most small nonprofits actually receive federal dollars, and essentially no commercial product surfaces it. This is the most differentiated data source in the whole program.
- **Single audit threshold:** the 2024 Uniform Guidance revision raised it from $750,000 to $1,000,000 in annual federal expenditures, effective for fiscal years beginning on or after 2024-10-01. Organizations below the threshold do not file, so FAC coverage is inherently partial — say so wherever the data is presented.

## SAM.gov

- **Entity Management API:** https://open.gsa.gov/api/entity-api/ — requires an api.data.gov key.
- **Entity/Exclusions Extracts API:** https://open.gsa.gov/api/sam-entity-extracts-api/ — bulk extracts.
- Provides registration status, expiration date, UEI, and CAGE code. Registration status and expiry are hard gates on federal applications and are the single most common avoidable disqualification.
- Note that public vs. sensitive entity data is tiered; only the public tier should be used here.

## Prior art — credit these loudly

- **Nonprofit Open Data Collective** — https://github.com/Nonprofit-Open-Data-Collective
  - **IRS E-file Master Concordance File** — https://nonprofit-open-data-collective.github.io/irs-efile-master-concordance-file/ — the crosswalk that makes 990 XML tractable across schema versions. This is the single most important upstream asset in the program.
  - IRS-Efile-Database project — https://nonprofit-open-data-collective.github.io/IRS-Efile-Database/
  - Overview and issue tracker — https://nonprofit-open-data-collective.github.io/overview/
- **GivingTuesday** — https://990data.givingtuesday.org/tool-repository/
  - `form-990-xml-mapper` — https://github.com/Giving-Tuesday/form-990-xml-mapper — turns any XML schema into a CSV of all possible XPaths
  - `form-990-xml-parser` — https://github.com/Giving-Tuesday/form-990-xml-parser — processes 990 XML into MongoDB
  - Form 990 Variable Dictionary and the GivingTuesday 990 Data Mart Dictionary
- **`open990odl`** — https://github.com/990consulting/open990odl
- **`propublica990`** — https://github.com/Punderthings/propublica990 — tooling for the ProPublica API
- **NBER Form 990 data** — https://www.nber.org/research/data/irs-form-990-data

Contribution posture: contribute fixes upstream first, credit prominently, and never re-implement what these projects already do well. This community's endorsement is a distribution channel, and burning it to own a codebase would be a bad trade.

## Competitive pricing — the parity targets

**Withheld from the vendored copy.** Competitor pricing is maintained in the internal program
dossier because several figures are sourced from a third-party comparison rather than from the
vendor's own pricing page, and the program rule is that any competitor price is re-verified on
the vendor's own page and date-stamped before it appears in public copy.

This repository's own `docs/research/competitive.md` carries the analysis relevant to this tool,
with each figure's source and verification status. Treat anything marked **VERIFY** there as
not publishable until re-checked.

**Never put a competitor price in code, help text, command output, or a public page.**

## OpenGrants platform facts

- Public REST API docs: https://ops.opengrants.io/api-docs
- Base URL: `https://qnoicxojartltrownmal.supabase.co/functions/v1/`
- Auth: `Authorization: Bearer <key>`, keys from the Developer Dashboard
- Endpoints: `/grants-api`, `/grants-api/{id}`, `/contracts-api`, `/contracts-api/{id}`, `/funders-api`, `/funders-api/{id}`, `/match-grants-api` (POST, Pro/Developer tier)
- Search modes: semantic, keyword, hybrid. Pagination 1–100 per page. Status filter: open, closed, upcoming.
- Webhooks: 8 event types, HMAC-SHA256 signature verification.
- Hosted MCP server: https://mcp.opengrants.io/mcp
- Platform scale: 139,000+ indexed opportunities, 43,000+ currently open, free unlimited search with no account; paid tier $9/month.

## Cloudflare platform limits that constrain design

- **Pages files per deployment:** 20,000 (Free), 100,000 (paid, requires `PAGES_WRANGLER_MAJOR_VERSION=4`). Source: https://developers.cloudflare.com/pages/platform/limits/
- **Max single asset size:** 25 MiB — larger files belong in R2.
- **Builds:** 500/month Free, 5,000 Pro; 20-minute build timeout.
- **100 projects per account**, 100/250/500 custom domains by plan.
- **R2:** no egress fees. This is the decisive economic fact for publishing derived datasets.

**Design consequence:** there are ~120,000 private foundations filing 990-PF and far more public charities. Pre-rendering one static page per organization does not fit under the file ceiling and would blow the build timeout. Data-backed sites render on demand at the edge from R2/D1, with only the shell, docs, and top-traffic pages pre-rendered.
