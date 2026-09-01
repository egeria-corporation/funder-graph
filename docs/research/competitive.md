# What this replaces

## The one-sentence version

Every paid foundation-research product in this category is a user interface sitting on top of Form
990 data that costs nothing to obtain, and their moat is not the data — it is the difficulty of
parsing it, which is a one-time engineering cost that disappears permanently once somebody pays it
in public.

This repository is that payment.

---

## Pricing, and the rule for quoting it

All figures below were current as of the noted verification and are here to size the market, not to
be pasted into marketing copy.

| Product | Price | Verified | Notes |
|---|---|---|---|
| **Candid Foundation Directory** | $1,599/year, or $219.99/month at the professional level | Sourced from a May 2024 comparison — **VERIFY** | A lower "essential" tier exists; **VERIFY** its current price |
| **Instrumentl** | $179 / $299 / $499 / $899 per month across four tiers | Capterra, current at 2026-08-30 | |
| **Cause IQ** | $199/month or $999/year, limited free tier | May 2024 comparison — **VERIFY** | |
| **Grant Gopher** | $9/month, limited free option | May 2024 comparison — **VERIFY** | |
| **Plinth** | Not public at verification | 2026-08-30 | https://www.useplinth.com/ · https://data.useplinth.com/us-nonprofit-data |

Comparison source: https://fundingforgood.org/comparing-grant-research-databases/

**Rule for any public-facing copy:** re-verify the price on the vendor's own pricing page before
publishing it, and date-stamp it in the text — "as of August 2026". Stale competitor pricing in a
README is an accuracy problem and it is the easiest possible way for a competitor to make us look
sloppy. If you cannot verify it today, do not print a number; say "a four-figure annual
subscription" and move on.

---

## Who they are and what they actually sell

### Candid (Foundation Directory)

The incumbent, the product of the GuideStar and Foundation Center merger, and the organization that
built the category. Foundation Directory is the canonical "who funds what" database and it is deeply
embedded in university libraries and development offices.

**What they genuinely have that we do not:** decades of curated editorial profiles, staff-verified
contact information, program officer names, application guidelines gathered from sources other than
the 990, and their own data collection channels including direct foundation reporting. That curation
is real work and it is not reproducible by parsing filings.

**What they sell that is just parsed 990s:** the grant lists. Who received money, how much, and for
what. That is Part XV and Schedule I, and it costs nothing to obtain.

**Where their model is exposed:** the parsed grant data is the part people query most and the part
that is least defensible. A nonprofit paying four figures a year primarily to look up what a
foundation funded is paying for ETL.

### Instrumentl

The modern, well-designed competitor. Sells a workflow — opportunity discovery, deadline tracking,
saved searches, team collaboration — with 990-derived giving history as one input among several.
Pricing starts at $179/month, which is real money for a small nonprofit and a rounding error for a
large one.

**What they genuinely have:** an excellent product experience, active opportunity data with
deadlines, and workflow features that matter to a working grants team.

**Where we intersect:** their funder giving-history views. Ours is queryable, free, and joinable to
anything. Theirs is inside a subscription.

**Honest read:** Instrumentl is not primarily a data company, and the existence of a free edge list
does not kill them. It does remove one of their justifications for a $179/month floor.

### Cause IQ

Nonprofit-market intelligence aimed largely at vendors selling *to* nonprofits, rather than at
nonprofits seeking funding. Heavy 990 derivation with firmographic packaging.

### Plinth

The newest entrant and the most direct: explicitly positions a 990-derived "funding graph" as the
product. Pricing was not public at verification, which usually means enterprise sales.

**Why they matter most:** Plinth validates that the funding graph is the valuable artifact, not a
feature. They are building the same object we are. The difference is the distribution model — theirs
is a proprietary asset behind a sales conversation, ours is a Parquet file on a URL. Only one of
those can be cited in a paper, embedded in someone else's product, or queried by an LLM at inference
time.

### ProPublica Nonprofit Explorer

Free, excellent, widely used, and not a competitor. Nonprofit Explorer is filing-centric: find an
organization, see its returns, read the document. It is not built to answer "every grant this
foundation paid, as rows, sorted by amount, joinable to something else." Different job, done well,
and it deserves credit rather than a comparison table.

---

## Where the incumbent moat actually is

Being precise about this matters, because the wrong analysis produces the wrong build.

**The moat is not:**

- Access to the data. It is a public download.
- Legal rights. US federal government works are not copyrightable in the United States.
- Volume. The whole corpus fits on a laptop's disk.

**The moat is:**

1. **Schema drift across hundreds of e-file versions.** The real cost, and the reason a competent
   engineer's weekend project produces something that works for 2023 and silently returns nothing
   for 2019.
2. **Entity resolution.** 990-PF Part XV usually reports the recipient by name and address with no
   EIN. Without resolution you have strings, not a graph, and you cannot answer "who else funds
   organizations like me."
3. **Editorial curation.** Program officers, guidelines, deadlines. Genuinely defensible, genuinely
   expensive, and genuinely not what most subscribers are looking up.

Items 1 and 2 are engineering costs. They are paid once. Once paid in public, they are paid for
everyone, permanently, and no amount of incumbent spending can un-pay them. That is why this repo
gets six to eight weeks and a mandate to do the parsing properly rather than quickly. The parsing
*is* the moat being dismantled.

Item 3 is a real moat and we should say so out loud. A project that claims to replace Candid
entirely is making a claim any development director can falsify in ten minutes, and losing that
argument costs more credibility than the claim was worth.

---

## What we do that none of them do

- **The data is a file, not a login.** Versioned Parquet on a public URL, no account, no rate limit,
  no seat. It can be pinned, cited, diffed, and embedded in someone else's product.
- **Match confidence is published.** Every commercial product presents fuzzy-matched recipients as
  fact. Ours ships a per-row confidence score, a tier, and a published table of the rows we could
  not resolve. Honesty is a feature here, and it is one the incumbents structurally cannot copy
  without admitting their own numbers are inferred too.
- **The reverse query is free and first-class.** "Who funds organizations like mine" is the query a
  development director actually has, and it is gated behind the highest tier almost everywhere.
- **History joined to current openings.** Candid has the history. Grants.gov-derived tools have the
  openings. Almost nobody joins them. With `OPENGRANTS_API_KEY` set, funder-graph shows what a
  funder has actually funded and what they have open right now, in one view. This is the single
  most differentiated capability in the program and it is a join, not a moat — which is why it needs
  to ship before someone else notices.
- **It is machine-readable by default.** An MCP server and a Parquet URL mean an agent can answer
  funding questions directly. Every incumbent's data is behind a login, which is to say invisible to
  the interface a growing share of research now happens in.

## What we deliberately do not do

- No program officer contacts, no application guidelines scraped into a directory, no deadlines from
  the graph.
- No fit scores, no predictions, no rankings.
- No curated editorial profiles.
- No workflow, no collaboration, no saved searches.

See `docs/NON-GOALS.md`. The list is a positioning statement as much as a scope document: the
credible claim is "the grant edge list is free now", not "we replaced Candid."

---

## The strategic point

The commercial value in this category has been the gap between "the data is public" and "the data is
usable." Every dollar of that value is a toll on an engineering cost nobody had paid in public.

Once the edge list exists as a versioned, citable, freely queryable file, the toll cannot be
re-collected. Competitors have to compete on curation, workflow, and product — which is a fair
fight, and one where the nonprofits paying $1,599 a year to look up what a foundation funded stop
paying for the lookup.

That outcome is worth six to eight weeks of careful parsing, and it is worth doing once, properly,
rather than three times, badly.
