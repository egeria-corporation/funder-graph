<!-- VENDORED COPY. Canonical source: https://github.com/egeria-corporation/.github
     Do not edit here. Edit the canonical copy and re-vendor. -->

> **Program-level document, vendored into this repository.** The canonical copy lives in
> [`egeria-corporation/.github`](https://github.com/egeria-corporation/.github). It is copied here so that a fresh clone of this
> repository — and any coding agent working in one — can read it without fetching another
> repository. 

# Hosting Plan — Egeria Open Source Companion Sites

## Recommendation: Cloudflare, not Netlify

You asked for Netlify ideally, Cloudflare Pages if cheaper. Cloudflare is meaningfully cheaper here, and the reason is specific rather than general.

**R2 has no egress fees.** Four of these five sites serve derived public datasets — the 990 funding graph alone will be multiple gigabytes of Parquet that we want people to query directly and freely. On Netlify, bandwidth is a metered cost that scales with exactly the adoption we are trying to create: the program working as designed becomes a bill. On Cloudflare, R2 egress is zero and Workers requests are cheap, so success is close to free. A tool whose hosting cost rises with its popularity will get quietly throttled the moment it works, and that would defeat the whole program.

**Second reason: the file-count ceiling.** Cloudflare Pages allows 20,000 files per deployment on the free plan and 100,000 on paid plans. There are roughly 120,000 private foundations filing 990-PF, plus far more public charities. Pre-rendering a static page per organization does not fit under either ceiling, and even where it fits it produces 20-minute-plus builds that break the deploy loop. **This changes the architecture:** the data-backed sites must render on demand at the edge from R2/D1, not pre-render at build time. Only the shell, the docs, and the top few thousand highest-traffic pages get pre-rendered. Getting this wrong is the single most likely way these sites fail, so it is baked into every hosted build prompt.

**Where Netlify still makes sense.** You already own an OpenGrants Netlify team, and pure-static docs sites with no dataset behind them cost nothing meaningful on either platform. If brand consistency and one deploy dashboard matter more than platform uniformity, put `answers.opengrants.io` on Netlify and leave the rest on Cloudflare. That is a real, defensible split — just make it a deliberate choice rather than a drift.

## Site map

| Repo | Domain | Platform | Rendering | Backing store |
|---|---|---|---|---|
| `grantcheck` | `check.opengrants.io` | Cloudflare Pages + Functions | Edge SSR per EIN, cached | D1 (BMF + Pub 78 + revocation, ~2M rows) |
| `funder-graph` | `funders.opengrants.io` | Cloudflare Workers | Edge SSR per funder, cached | R2 (Parquet) + D1 (index) |
| `precedent` | `awards.opengrants.io` | Cloudflare Workers | Edge SSR, cached | KV cache over USAspending + FAC APIs |
| `answerbank` | `answers.opengrants.io` | Netlify **or** Cloudflare Pages | Fully static | none — docs + template gallery |
| `grantdesk` | `desk.opengrants.io` | Cloudflare Workers | App (SSR + API) | D1 + R2 |

## Cost expectation

The Workers Paid plan at $5/month covers the request volume for all five sites well past launch, plus R2 storage at roughly $0.015/GB-month with zero egress. Realistic monthly spend for the whole portfolio at launch scale is under $20. The equivalent on Netlify, once the Parquet dataset is being pulled by the public, is bandwidth-metered and unbounded.

## Caching strategy — applies to every data-backed site

The underlying data changes monthly (IRS bulk files) or daily at the fastest (OpenGrants API). Nothing here needs to be fresh by the second, and treating it as if it does is how the request bill grows.

- Cache rendered pages at the edge with a long TTL — 24 hours for OpenGrants-enriched pages, 7 days for pure IRS-derived pages.
- Key the cache on the dataset vintage so a new monthly ingest invalidates everything cleanly, rather than relying on TTL expiry.
- Serve stale while revalidating. A slightly old 990 figure is always better than a spinner.

## SEO and GEO requirements — applies to every hosted site

This is where the category-ownership objective actually gets served. The repos do not rank; these pages do.

- **Server-rendered HTML with real content in the initial response.** No client-side data fetching for primary content. LLM crawlers and search engines must see the facts in the HTML.
- **`schema.org` structured data** on every entity page — `Organization` / `NGO` with `taxID`, `address`, and `funder` relationships where known. This is what makes the pages machine-quotable.
- **One canonical URL per entity**, keyed on EIN: `/funders/94-2278431`. Slug variants redirect to canonical. Never let two URLs serve the same organization.
- **Sitemap index, chunked at 50,000 URLs per file**, generated from the dataset at ingest time and served from R2.
- **`llms.txt` at the root** of every site describing what the dataset is, how it may be used, and how to cite it. Cheap to write, and it is increasingly how models decide what a site is for.
- **Every page states its source and vintage inline** — "derived from the Form 990-PF filed 2025-11-14." Pages that show their work get cited; pages that assert bare numbers do not.
- **Cross-link the portfolio.** Each entity page links to the same EIN on the sibling sites. Five sites that reference each other read as one authoritative body of work rather than five orphans.

## DNS note

These are all subdomains of `opengrants.io`, whose DNS is managed externally rather than at the registrar's default. Each site needs a CNAME added wherever that zone is hosted, and Cloudflare custom domains on Pages/Workers will need the validation record. Worth confirming who holds that zone before the first launch, since it is the step most likely to sit blocked for a day.
