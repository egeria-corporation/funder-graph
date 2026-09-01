# Contributing to funder-graph

The most valuable contributions to this project are not code. They are corrections to the field
mapping and to entity resolution, because those are the two places where the dataset can be
quietly wrong at scale.

Everything here is Apache 2.0. By contributing you agree your contribution is licensed the same
way.

---

## The upstream-first rule

**This is the most important rule in the repo, and it is not negotiable.**

funder-graph exists because the Nonprofit Open Data Collective and GivingTuesday did the hard
part first and gave it away. We are a consumer of that work and a citizen of that community. If
we hoard fixes to shared infrastructure in order to make our dataset better than theirs, we have
taken something from the commons and given nothing back, and we deserve to be treated
accordingly.

So:

1. **If a fix belongs upstream, it goes upstream first.** A missing or wrong XPath for a schema
   version belongs in the [IRS E-file Master Concordance
   File](https://github.com/Nonprofit-Open-Data-Collective/irs-efile-master-concordance-file). An
   XML traversal bug belongs in
   [form-990-xml-parser](https://github.com/Giving-Tuesday/form-990-xml-parser). Open the PR or
   the issue there before you open one here, and link it.
2. **We may carry a local override in the meantime.** Overrides live in
   `data/overrides/concordance-overrides.toml`, and **every entry must carry the upstream issue
   or PR URL and the date it was filed.** An override with no upstream link fails CI. This is
   deliberate: it makes the debt visible and stops the override file from quietly becoming a fork.
3. **Overrides get removed when upstream merges.** A monthly maintenance task checks every
   override against upstream and closes out the ones that have landed.
4. **We do not re-implement something an upstream project already does well** in order to own the
   code. If you are about to write a 990 XML parser, stop and ask why
   `form-990-xml-parser` is not the answer.

If upstream is unresponsive after a reasonable window, say so in the override entry and keep
waiting. Maintainers of volunteer open data projects are busy. Loud forks are cheap and
relationships are not.

---

## Contributing a schema-mapping fix

This is the highest-leverage contribution type and it does not require you to be a Python
developer. The workflow:

### 1. Find the miss

Every pipeline run writes `build/reports/unmapped-fields.csv`, listing XPaths that appeared in a
filing's schema version but that our concordance-derived map did not consume, together with how
many filings contained them. You can also spot a miss from the other direction: a foundation you
know made grants shows zero rows for a given year.

```bash
funder-graph diagnose --funder-ein 13-1684331 --year 2022
```

`diagnose` prints, for one filing: the OBJECT_ID, the `returnVersion`, the XPaths the mapper
looked for, which of them were found, and the raw XML fragment for the Part XV or Schedule I
node. That fragment is what you need to file a good report.

### 2. Confirm it against the real filing

Download the raw XML directly and look at it. Never file a mapping report based on our parsed
output alone.

```bash
funder-graph fetch-raw --object-id 202343159349100234 --out ./filing.xml
```

### 3. File it upstream, then here

Upstream first: a concordance issue naming the `returnVersion`, the logical field, and the
correct XPath. Then open an issue here with the upstream link, and if you want to unblock the
next release, a PR adding the override:

```toml
[[override]]
return_version_pattern = "2021v4.*"
logical_field          = "pf_grant_recipient_zip"
xpath                  = "/Return/ReturnData/IRS990PF/SupplementaryInformationGrp/GrantOrContributionPdDurYrGrp/RecipientUSAddress/ZIPCd"
reason                 = "Concordance entry points at the pre-2020 element name for this version."
upstream_url           = "https://github.com/Nonprofit-Open-Data-Collective/irs-efile-master-concordance-file/issues/NNN"
filed_on               = "2026-08-30"
```

### 4. Add a fixture

Every mapping fix ships with a real filing fragment committed under `tests/fixtures/filings/`,
named `{return_version}__{object_id}.xml`, trimmed to the relevant nodes with all identifying
content left intact (these are public filings — do not anonymize them, that defeats the purpose).
Mocked-shape tests do not catch schema drift, which is the only failure mode that actually
matters here.

---

## Contributing an entity-resolution fix

The `unmatched` table and the tier D rows are published on purpose. They are the honest accounting
of what the dataset does not know, and they are where community knowledge beats any algorithm.

Two ways to help:

- **A bad match.** Open an issue with the `grant_id`, the `recipient_name_raw`, the
  `recipient_ein_resolved` we produced, and the EIN you believe is correct with your reasoning.
  Confirmed corrections go into `data/overrides/ein-corrections.csv`, which is applied after
  automated matching and is itself published as part of the release so the correction is auditable.
- **A matching-rule improvement.** Normalization rules live in `funder_graph/resolve/normalize.py`
  and are exercised by a labeled test set at `tests/fixtures/matching/labeled_pairs.csv`. **Any
  change to matching must move precision and recall on that labeled set in the right direction,
  and the PR must show the before/after numbers.** "It looks better" is not a measurement.

Never add a rule that raises `match_confidence` without evidence. Overstated confidence is worse
than an unmatched row, because an unmatched row is honest.

---

## Development

```bash
git clone https://github.com/egeria-corporation/funder-graph
cd funder-graph
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Python 3.11+, `uv` for dependencies, `ruff` for lint and format, `pytest` for tests. CI runs lint
and tests on every push and PR and must be green before merge.

You do **not** need to run the full pipeline to contribute. A full corpus build downloads hundreds
of gigabytes and takes many hours. For development, use the sampled corpus:

```bash
uv run funder-graph build --sample --years 2023 --limit-filings 500
```

### Architecture rule

Core logic lives in the library module. The CLI and the MCP server are both thin adapters over it.
**Business logic in a CLI command handler is a bug** and will be sent back. If you cannot call it
from the MCP server without going through argument parsing, it is in the wrong place.

---

## Pull request expectations

- One concern per PR.
- Mapping and matching changes include fixtures and, for matching, before/after metrics.
- Public docs use real EINs and real foundations, not `foo` and `bar`. Write for a grant
  consultant who is not a developer.
- Anything that changes the published schema needs a `CHANGELOG.md` entry and a note on whether it
  is a breaking change for existing consumers. People pin versions and build on this. Treat the
  schema as an API, because it is one.
- Do not commit secrets, `.env` files, or downloaded corpus data. `.gitignore` covers the obvious
  cases; check anyway.

## Reporting a data problem you cannot fix

Open an issue. Include the `grant_id` or `object_id`, the `dataset_version` you queried, and what
you expected. A precise bug report against a specific filing is a real contribution, and it is
frequently more useful than a patch.

## Code of conduct

See [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md). Security issues go to the process in
[`SECURITY.md`](./SECURITY.md), not to a public issue.
