# Claude Code kickoff — funders.opengrants.io, the hosted companion

You are building the hosted companion site for `funder-graph`, the open 990 funding graph. Assume no
context beyond this file and this repository.

This is not a marketing site. It is a public, server-rendered view of a public dataset, and it is the
largest search and generative-engine asset in a five-repo program: a few hundred thousand pages of
factual, uniquely-sourced content that does not exist anywhere else for free.

---

## 1. Read these first

1. `docs/program/HOSTING.md` — **binding.** Platform choice, the file-count ceiling,
   the caching strategy, and the SEO/GEO requirements. Read it before you write a line.
2. `docs/program/CONVENTIONS.md` — TypeScript standards, Apache 2.0, attribution,
   data honesty, the optional-OpenGrants rule.
3. `docs/hosted/architecture.md` in this repo — the design this prompt implements. If you disagree
   with something there, raise it before building, not after.
4. `README.md` — the published dataset schema. Every field the site renders comes from it.
5. `docs/research/data-sources.md` — what the numbers actually mean, especially the `amount_type`
   double-count trap and the match-confidence tiers.
6. `docs/NON-GOALS.md` — the scope fence.

The core repo (`prompts/01-build-core.md`) publishes the dataset this site reads. **That must exist
and be published to R2 before this site can do anything.** If it does not exist yet, build against a
locally generated sample and say so.

---

## 2. Mission

Render one page per grantmaking organization: *"Grants paid by the David and Lucile Packard
Foundation, 2019–2025"* — with the recipients, the amounts, the years, and the source filing stated
on the page. Then the reverse: one page per recipient showing who has funded them.

Domain: **funders.opengrants.io**. Platform: **Cloudflare Workers**. Stack: TypeScript strict,
`pnpm`, `biome`, `vitest`, **Hono** for routing.

---

## 3. Why this cannot be a static site — read this before you reach for a static generator

The obvious build is a static site generator: query the Parquet, emit one HTML file per funder,
deploy the directory. **It does not work.** Getting this wrong is the single most likely way this
site fails, so the reasoning is spelled out rather than asserted.

**Cloudflare Pages allows 20,000 files per deployment on the Free plan and 100,000 on paid plans**
(paid requires `PAGES_WRANGLER_MAJOR_VERSION=4`).
Source: https://developers.cloudflare.com/pages/platform/limits/

What we need to render:

| Page type | Approximate count |
|---|---|
| Private foundations filing 990-PF | ~120,000 |
| Public charities filing Schedule I | ~60,000+ |
| Resolved recipient pages | 400,000+ |
| Per-funder per-year pages | several hundred thousand |

The funder pages alone exceed the paid ceiling. Recipient pages exceed it by an order of magnitude.

And the ceiling is not even the binding constraint. **Pages enforces a 20-minute build timeout.**
Rendering hundreds of thousands of pages out of a multi-gigabyte Parquet dataset does not finish in
twenty minutes. A build that takes an hour is a deploy loop nobody uses, which means the site stops
being updated, which means the content goes stale and the entire SEO asset decays. A slow deploy loop
kills this site more reliably than a hard limit does.

**Therefore: edge SSR from R2 and D1.** Pre-render only the shell, the docs, and the top few thousand
highest-traffic funder pages. Everything else renders on demand at the edge and is cached.

The point that resolves the obvious objection: **the Pages limit applies to deployment assets, not to
R2 objects.** R2 has no comparable object-count limit. Publishing 180,000 precomputed per-funder JSON
payloads to R2 is completely fine; publishing 180,000 HTML files as deployment assets is not. That
distinction is what makes the whole architecture work — precompute the *data* at ingest, render the
*HTML* at request time.

**Also:** R2 has no egress fees. This site succeeds by being pulled hard, and on a bandwidth-metered
host the program working as designed becomes a bill. The predictable response to a surprising bill is
throttling the thing that caused it, which would defeat the point of the program.

---

## 4. Data layout

### R2 — bulk

Written by the ingest job, never at request time:

```
r2://funder-graph/2026.08.0/
  grants/filing_year=*/**.parquet     # the public dataset, served directly to DuckDB users
  manifest.json
  funders/941156365.json              # precomputed render payload, one per funder
  funders/131684331/index.json        # chunked form for very large funders
  funders/131684331/2023.json
  recipients/363673599.json
  sitemaps/sitemap-index.xml
  sitemaps/funders-00001.xml.gz
```

**Precompute JSON rather than reading Parquet from the Worker.** A Worker cannot run DuckDB, and
implementing Parquet footer parsing plus range reads in a Worker is a real project with a fresh
failure mode on every schema change. The ingest job already has the dataset loaded in DuckDB, so
emitting one JSON per funder is nearly free, and the request path collapses to one R2 GET plus
rendering.

Funder payload contents:

- identity: EIN, name, city, state, NTEE, form type
- totals: lifetime `paid` grants, count, distinct recipients, first and last tax year
- per-year totals for the chart and year navigation
- top 250 recipients by total, with resolved EIN, match tier, and grant count
- most recent 500 individual grants: amount, recipient, purpose, tax year, `object_id`, match tier
- provenance: every source filing with `object_id`, `tax_period_end`, `filing_submission_date`,
  `return_version`
- `dataset_version`, `built_at`

**Chunk above 2,000 grant rows** into `funders/{ein}/{tax_year}.json` plus an `index.json` summary.
Test against Ford Foundation (EIN 13-1684331) and the Bill & Melinda Gates Foundation (EIN
56-2618866), which are the shapes that break naive implementations.

### D1 — the index

D1 holds only what is searched or ranked. Grant rows — tens of millions — never go in D1.

```sql
CREATE TABLE funders (
  ein TEXT PRIMARY KEY, name TEXT NOT NULL, name_normalized TEXT NOT NULL,
  city TEXT, state TEXT, ntee_code TEXT, form_type TEXT NOT NULL,
  total_paid_usd INTEGER NOT NULL, grant_count INTEGER NOT NULL,
  recipient_count INTEGER NOT NULL,
  first_tax_year INTEGER, last_tax_year INTEGER, latest_filing_dt TEXT,
  payload_key TEXT NOT NULL, is_chunked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_funders_state_total ON funders(state, total_paid_usd DESC);
CREATE INDEX idx_funders_ntee ON funders(ntee_code, total_paid_usd DESC);

CREATE TABLE recipients (
  ein TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT, state TEXT, ntee_code TEXT,
  total_received_usd INTEGER NOT NULL, funder_count INTEGER NOT NULL, payload_key TEXT NOT NULL
);

CREATE VIRTUAL TABLE entity_search USING fts5(ein UNINDEXED, kind UNINDEXED, name, city, state);

CREATE TABLE dataset_vintage (
  version TEXT PRIMARY KEY, built_at TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 0, grant_rows INTEGER, funder_rows INTEGER
);
```

### KV — the vintage pointer

One key, `current_dataset_version`. Read on every request, used in the cache key. Flipping it is what
cuts the site over to a new dataset atomically.

---

## 5. Routes

| Route | Renders |
|---|---|
| `/` | What the dataset is, search, real examples, link to the Parquet and the repo |
| `/funders/{ein}` | **Canonical funder page** |
| `/funders/{ein}/{year}` | One tax year in full |
| `/funders/{ein}/recipients` | Full recipient list, paginated |
| `/recipients/{ein}` | Who has funded this organization |
| `/search?q=` | FTS5 over funder and recipient names |
| `/browse/state/{code}` | Crawlable state index, paginated |
| `/browse/ntee/{code}` | Crawlable NTEE index, paginated |
| `/data` | Dataset docs, versions, DuckDB examples, `schema.org/Dataset` markup |
| `/api/funders/{ein}.json` | Convenience JSON. Explicitly not an SLA-backed API — say so on `/data` |
| `/sitemap.xml` | Sitemap index |
| `/sitemaps/{name}.xml.gz` | Chunks, proxied from R2 |
| `/llms.txt` | What this is, coverage, caveats, citation |
| `/robots.txt` | Allow all, point at the sitemap index |
| `/about`, `/methodology` | How the data is derived, the confidence tiers, the limitations |

**One canonical URL per entity, keyed on EIN.** Canonical form is nine digits, no dash:
`/funders/941156365`. `/funders/94-1156365` and any slug variant such as
`/funders/94-1156365/packard-foundation` 301 to canonical. Emit `<link rel="canonical">` on every
page. Two URLs serving one organization splits the ranking signal, and it is the most common way a
site like this quietly underperforms for a year before anyone diagnoses it.

The `/browse/` routes are not decoration. They are how the long tail of 180,000 funder pages gets
discovered, since no sitemap alone reliably gets deep pages crawled. Every browse page links to real
entity pages, paginated, with no JavaScript required.

---

## 6. Request path

```
GET /funders/94-1156365
  ├─ normalize EIN → 941156365 → 301 (request form was non-canonical)
GET /funders/941156365
  ├─ vintage = KV.get('current_dataset_version')            // "2026.08.0"
  ├─ cache.match(`${url}?v=${vintage}`)  → hit: return, revalidate via ctx.waitUntil
  ├─ D1: SELECT * FROM funders WHERE ein = ?                 // miss → 404 page with search
  ├─ R2: get(`${vintage}/funders/941156365.json`)            // or index.json if chunked
  ├─ render complete HTML server-side, including JSON-LD
  ├─ if OPENGRANTS_API_KEY bound: fetch open opportunities, try/catch, degrade silently
  └─ Cache-Control: public, max-age=604800, stale-while-revalidate=86400
```

**No client-side data fetching for primary content.** Every fact is in the initial HTML response.
Charts render from data already inlined in the document — inline SVG generated server-side, or a tiny
progressive-enhancement script over a `<table>` that is itself readable. A crawler or a language
model with JavaScript disabled must see the complete grant list. That is the entire reason this site
exists.

Total client-side JavaScript budget: **under 20 KB**, and the page must be fully useful with zero.

---

## 7. Caching

The underlying data changes monthly. Nothing needs second-level freshness, and treating it as if it
does is how the request bill grows.

- **7 days** (`max-age=604800`) for pure IRS-derived pages.
- **24 hours** for pages carrying OpenGrants live enrichment.
- **`stale-while-revalidate=86400`** everywhere. A slightly old 990 figure beats a spinner.
- **The cache key includes the dataset vintage:** `${url}?v=${vintage}`. Flipping the KV pointer at
  the end of an ingest invalidates the entire site cleanly and atomically — no purge API call, no
  waiting for TTLs to lapse, no half-old-half-new state. This is the reason the vintage is in the key
  rather than relying on expiry, and it is worth getting exactly right.
- Set `Vary` correctly. Do not vary on anything you do not actually branch on.

---

## 8. SEO and GEO requirements

From `HOSTING.md`, and all of them are required. The repos do not rank; these pages do.

1. **Server-rendered HTML with real content in the initial response.** No client-side fetching for
   primary content.
2. **`schema.org` structured data on every entity page.** JSON-LD in the head:
   - The organization as `Organization` (use `NGO` where the subsection supports it) with `taxID`,
     `name`, `address` (`PostalAddress`), and `url`.
   - Individual grants as **`MonetaryGrant`** with `funder`, `recipient`, `amount`
     (`MonetaryAmount` with `currency: "USD"`), `description` from the grant purpose, and
     `datePublished` from the filing date. `MonetaryGrant` is exactly the right vocabulary for this
     data, almost nobody uses it correctly, and it is what makes these pages precisely quotable by a
     model rather than approximately quotable.
   - `/data` gets `Dataset` with `distribution` pointing at the Parquet, plus `license`,
     `creator`, `dateModified`, and `version`.
   - Include `BreadcrumbList`. Validate every template against the Rich Results Test before launch.
3. **One canonical URL per entity, keyed on EIN.** See section 5.
4. **Sitemap index chunked at 50,000 URLs per file.** Generated **at ingest time**, never at request
   time, gzipped, stored in R2, served through the Worker. Include `<lastmod>` from the dataset build
   date. Separate chunk series for funders, recipients, and browse pages. Reference the index from
   `robots.txt`.
5. **`llms.txt` at the root.** Plain text, describing what the dataset is, what it covers and does
   not, the confidence tiers and what they mean, the `amount_type` double-count trap, the license,
   how to cite it, and the URL of the Parquet for anyone who wants the data rather than the page. A
   model that quotes a tier D edge as fact has been failed by our documentation; `llms.txt` is where
   we prevent that.
6. **Every page states its source and vintage inline**, in the visible body, not just a meta tag:
   "Derived from the Form 990-PF for tax year 2023, filed 2024-11-14, IRS OBJECT_ID
   202443159349100234. Dataset version 2026.08.0, built 2026-08-12." Pages that show their work get
   cited. Pages that assert bare numbers do not.
7. **Cross-link the portfolio.** Every entity page links to the same EIN on the sibling sites:
   `check.opengrants.io/{ein}` (exempt status), `awards.opengrants.io/{ein}` (federal awards),
   `answers.opengrants.io` (guidance), `opengrants.io` (open opportunities). Five sites that
   reference each other read as one authoritative body of work rather than five orphans.
8. **Titles and meta descriptions are facts, not pitches.** "Grants paid by the David and Lucile
   Packard Foundation (EIN 94-1156365) — 4,812 grants totaling $2.1B, 2019–2025". Generate them from
   the data. Never ship a template with unfilled placeholders — a crawled page titled
   "Grants paid by undefined" is permanent embarrassment.
9. **Match confidence is visible on the page.** Fuzzy-matched recipients are marked in the UI with a
   plain-language explanation and a link to `/methodology`. Every competitor presents inferred
   matches as fact. Showing our uncertainty is a differentiator, not a weakness, and it is the thing
   that makes the pages trustworthy enough to cite.
10. Core Web Vitals: server-rendered, minimal JS, no layout shift, no web fonts blocking render.
    System font stack. This should be trivially fast, and if it is not, something is wrong.

---

## 9. Accessibility and presentation

- Semantic HTML. Grant lists are `<table>` elements with real `<th>` and captions, because they are
  tables and because that is also what makes them parseable.
- Every chart has an accessible table equivalent in the DOM, not behind a toggle.
- WCAG AA contrast, keyboard navigable, focus visible.
- Dollar amounts formatted with `Intl.NumberFormat`, full precision available on hover or in the
  table cell — do not render "$2.1M" as the only representation of a number someone might cite.

---

## 10. Ingest job

A GitHub Actions workflow, not a Worker. It is a long batch job that runs monthly after the core
pipeline publishes a new dataset version.

1. Read the new `manifest.json` from R2. Verify row counts and checksums.
2. From DuckDB over the published Parquet, emit per-funder and per-recipient JSON payloads to R2
   under the **new version prefix**. Chunk anything above the row threshold.
3. Build the D1 index into a **new** table set, verify counts against the manifest, then swap.
   **Never mutate the live index in place.** A half-written index serving traffic is worse than a
   stale one.
4. Generate sitemaps, chunked at 50,000 URLs, gzipped, uploaded to R2 under the new version prefix.
5. Smoke-test a fixed EIN list against the new version before cutover — at minimum: Packard
   (94-1156365), Ford (13-1684331, chunked), Gates (56-2618866, largest), Silicon Valley Community
   Foundation (20-5205488, Schedule I filer), Feeding America (36-3673599, recipient page), plus one
   very small foundation and one known 404.
6. **Only then** flip `current_dataset_version` in KV. That flip is the cutover and the only
   irreversible step.
7. Ping the sitemap index. Publish the delta from `version-delta.md` to a public changelog page.
8. Keep the previous version's R2 objects for at least 60 days so a rollback is a KV flip rather than
   a rebuild.

---

## 11. OpenGrants enrichment

Optional, additive, silent on failure — same rule as the CLI.

- Bind `OPENGRANTS_API_KEY` as a Worker secret. Absent means the site works exactly as documented
  with no mention of OpenGrants anywhere in the rendered output.
- When present, funder pages show currently open opportunities from that funder, fetched from
  `GET /funders-api/{id}` and `GET /grants-api` at
  `https://qnoicxojartltrownmal.supabase.co/functions/v1/`.
- Mark every enriched block **"— live from OpenGrants"** so a reader always knows which facts are
  public-source and which are API-sourced.
- Wrap in a timeout and a broad catch. Enriched pages get the 24-hour TTL, un-enriched get 7 days.
- This combination is the single most differentiated thing in the program: *here is what they have
  actually funded, and here is what they have open right now.* Candid has the history. Grants.gov
  tools have the openings. Almost nobody joins them. Make the join obvious on the page.

---

## 12. DNS

`funders.opengrants.io` is a subdomain of `opengrants.io`, **whose DNS is managed externally rather
than at the registrar's default.**

- The Worker needs a Cloudflare custom domain, which needs a CNAME plus Cloudflare's validation
  record added in whatever zone actually holds `opengrants.io`.
- The dataset is served from `data.opengrants.io`, an R2 custom domain on the same zone. Request both
  records at the same time.
- **Confirm who holds that zone before you need it.** This is the step most likely to sit blocked for
  a day, and it is the most annoying possible reason for a finished site not to be live.
- R2 bucket CORS must permit browser DuckDB-WASM reads, since the zero-install quickstart in the
  README depends on it. Test it from `shell.duckdb.org` specifically.

---

## 13. Launch checklist

**Correctness**

- [ ] Ten known EINs render correct pages with numbers matching the published Parquet exactly.
- [ ] A chunked funder (Ford, Gates) renders completely with working year navigation.
- [ ] A Schedule I filer (Silicon Valley Community Foundation) renders on the funder side.
- [ ] An organization appearing as both funder and recipient renders correctly on both, with
      cross-links.
- [ ] `paid` and `approved_future` are never summed together anywhere on the site.
- [ ] Fuzzy-matched recipients are visibly marked, with the tier explained in plain language.
- [ ] A nonexistent EIN returns a real 404 page with search, and a 404 status code.

**SEO / GEO**

- [ ] `view-source` on a funder page shows the full grant table in the HTML.
- [ ] JSON-LD validates in the Rich Results Test on funder, recipient, and `/data` pages.
- [ ] `MonetaryGrant` markup present and correct on grant rows.
- [ ] Non-canonical EIN forms and slug variants 301 to canonical.
- [ ] `<link rel="canonical">` on every page.
- [ ] Sitemap index resolves; every chunk is under 50,000 URLs; `lastmod` present.
- [ ] `robots.txt` allows all and references the sitemap index.
- [ ] `llms.txt` present, accurate, and includes the confidence caveat and citation guidance.
- [ ] Every page shows source filing and dataset vintage in the visible body.
- [ ] Cross-links to all four sibling sites present on every entity page.
- [ ] No page title or meta description contains a placeholder, `undefined`, or `NaN`.
- [ ] Lighthouse SEO 100, Performance 95+ on a funder page.

**Operations**

- [ ] Cache key includes the dataset vintage; a KV flip demonstrably invalidates the site.
- [ ] `stale-while-revalidate` behavior verified.
- [ ] Ingest job runs end to end on a real dataset version and swaps D1 without downtime.
- [ ] Rollback tested: flipping KV back to the previous version restores the previous site.
- [ ] Worker analytics and error logging on; a dashboard exists that would show a spike in R2 misses.
- [ ] Cost projection at 1M requests/month calculated and under $20 for the portfolio.
- [ ] Custom domain live; DNS validated; HTTPS enforced; `data.opengrants.io` serving Parquet with
      working CORS.
- [ ] `funder-graph` README links to the live site and the site links back to the repo.
- [ ] Apache 2.0 and the upstream credits from `NOTICE` appear in the site footer, including the
      Nonprofit Open Data Collective and GivingTuesday by name.

**Honesty**

- [ ] `/methodology` explains the confidence tiers, coverage limits, filing lag, and the
      `approved_future` trap in language a grant consultant understands without a glossary.
- [ ] The required disclosure appears in the footer of every entity page:
      *"This is informational only, derived from public data on the dates shown. It is not an
      eligibility determination, and not legal, tax, or accounting advice. Verify against the
      official source before relying on it."*
- [ ] No page presents a fuzzy match as a fact.
- [ ] No named individual grant recipients appear anywhere on the site.

---

## 14. Stop and ask the human

1. **D1 index exceeds its storage limits** with the real row counts. There are answers (drop
   recipient rows below a threshold, move FTS to a different store) but they are product decisions.
2. **Per-funder JSON payloads are far larger than expected**, such that R2 storage or request latency
   becomes a problem. The chunking threshold is a tuning decision with a real tradeoff.
3. **The DNS zone for `opengrants.io` cannot be located or modified.** Nothing ships without it. Ask
   early, not at launch.
4. **Any design pressure toward client-side rendering of primary content.** The answer is no, but if
   something makes SSR genuinely infeasible, that is a conversation, not a workaround.
5. **Anything requiring user accounts, sessions, or a write path.** That is `grantdesk`, not this.
   See `docs/NON-GOALS.md`.
6. **Crawl volume or bot traffic drives costs above expectation.** Rate limiting a public dataset
   site is a policy call.
7. **A sibling site's URL scheme differs from what the cross-links assume.** Confirm before shipping
   links that 404.
