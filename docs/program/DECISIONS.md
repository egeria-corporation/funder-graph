<!-- VENDORED COPY. Canonical source: https://github.com/egeria-corporation/.github
     Do not edit here. Edit the canonical copy and re-vendor. -->

> **Program-level document, vendored into this repository.** The canonical copy lives in
> [`egeria-corporation/.github`](https://github.com/egeria-corporation/.github). It is copied here so that a fresh clone of this
> repository — and any coding agent working in one — can read it without fetching another
> repository. When a build prompt tells you to stop and ask a question, check here first — it may already be answered.

# Program Decision Record

Decisions made at the program level, binding on every repo. The build prompts each carry a
"stop and ask the human" section; when one of those questions is answered, the answer is recorded
here and the prompt is updated to point at the record rather than to block.

Format: what was decided, when, why, what it costs, and what would reopen it. A decision that
cannot say what would reopen it is a preference, not a decision.

---

## D-001 — Bundle the SAM.gov public entity snapshot into the published index

**Decided:** 2026-08-31 · **Status:** accepted · **Affects:** `grantcheck` core M5, `grantcheck`
hosted H4

### The decision

The `grantcheck` monthly ingest downloads the **SAM.gov Public Entity Extract**, derives a minimal
subset of public-tier fields, and publishes it inside the index shards that users download. The
keyless quickstart therefore covers all eleven checks, offline, with no account.

Fields taken, and nothing else:

```
UEI, legal business name, state, city,
registration status, registration expiration date, registration purpose
```

No Controlled Unclassified Information. No "For Official Use Only" tier. No sensitive tier — which
is where taxpayer identification number, banking information, and points of contact live. Those are
permanently out of scope and are listed in `grantcheck/docs/NON-GOALS.md`.

### Why

Three of `grantcheck`'s eleven checks come from SAM.gov: registration active, registration
expiration, and UEI presence. An expired registration is the most common avoidable disqualification
in the federal system, so dropping these checks materially weakens the tool.

The live API cannot carry them. GSA's published rate limits for the Entity Management API give a
**non-federal user with no SAM.gov role ten requests per day**; a user with a role, or a system
account, gets 1,000. Ten organization lookups a day is unusable for a consultant checking a client
roster, and it breaks the cron-over-a-roster workflow that `grantcheck`'s exit codes exist to
support. Verified against <https://open.gsa.gov/api/entity-api/> on 2026-08-31.

A bundled snapshot is therefore not a convenience. It is the only design in which the keyless
promise and the SAM checks coexist.

The alternative that preserves both without redistributing anything — a proxy endpoint on
`check.opengrants.io` holding our key — was **rejected outright**, and would stay rejected even if
the legal question came back the other way. It would make an open source tool depend on our
infrastructure to function, it would stop working the day we stop paying for it, and our logs would
record which EINs users research. That last one is forbidden by the core build prompt in as many
words: the tool must never report which EINs were checked.

### The risk we are accepting

The SAM.gov Public Entity Extract is, in GSA's own description, publicly available entity data
released under the Freedom of Information Act, and United States government works are not subject
to domestic copyright (17 U.S.C. §105). **Neither the Entity Management API nor the Entity Extracts
API documentation states any restriction on redistribution, republication, or reuse — and neither
grants permission either.** The risk is that silence, and it is procedural rather than substantive.

Two hedges, both running in parallel with the build rather than ahead of it:

1. **A written clarification request to GSA** via the Federal Service Desk, asking whether
   republication of a derived subset of the Public Entity Extract is permitted. Open before M5
   starts; do not wait on the reply to build.
2. **`grantcheck/data/SOURCES.md`**, published with the index, stating exactly which public-tier
   fields were taken, the extract date they came from, and that the artifact is a derived subset of
   a FOIA-releasable federal extract. A deliverable of M5, not a nice-to-have.

### What would reopen this

- GSA replies that republication is not permitted, or attaches conditions we cannot meet.
- GSA publishes redistribution terms that contradict the reading above.
- The extract begins carrying fields above the public tier, which would make the derivation step a
  filtering obligation rather than a convenience.

**The fallback, if reopened, is not the proxy.** It is to degrade the three SAM checks to `unknown`
unless the user supplies their own `SAM_API_KEY`, with the README saying so plainly. That path is a
configuration change rather than a rewrite, because both the CLI and the hosted site already have
to handle the low-confidence `unknown` case for organizations whose EIN cannot be matched to a SAM
entity with confidence.

### Unchanged by this decision

- **The EIN-to-UEI join is still inferred**, by normalized name and state, with a published
  confidence score and a `--uei` flag to pin it. Taxpayer identification number is sensitive-tier
  and is not searchable, so there is no lookup to be had at any tier we will use.
- **SAM.gov exclusions and debarment remain out of scope** on an inferred match. Accusing the wrong
  organization of being debarred is defamatory. Only a confirmed UEI, and even then only as a
  pointer to the official record.

---

## Resolved constraints

Not decisions, but facts that were open and are now closed. Recorded because several prompts plan
around them.

### C-001 — `opengrants.io` DNS is under our full control

**Confirmed:** 2026-08-31.

`_shared/HOSTING.md` and four of the five hosted build prompts treat the DNS zone as an unknown to
ask about on day one, on the grounds that it was the one launch step that could not be unblocked by
working harder. It is ours, and adding a CNAME plus a Cloudflare validation record is a task rather
than a coordination problem.

**Consequence for sequencing:** hosted launches are no longer gated on anything external. Each
hosted companion ships as soon as its repo is ready, so the program runs repo-then-site five times
rather than five repos followed by five sites.
