<!-- VENDORED COPY. Canonical source: https://github.com/egeria-corporation/.github
     Do not edit here. Edit the canonical copy and re-vendor. -->

> **Program-level document, vendored into this repository.** The canonical copy lives in
> [`egeria-corporation/.github`](https://github.com/egeria-corporation/.github). It is copied here so that a fresh clone of this
> repository — and any coding agent working in one — can read it without fetching another
> repository. 

# Egeria Open Source Conventions

Every repo in this program follows these rules. Claude Code should treat this file as binding when building any repo in the portfolio.

## Identity

- **GitHub org:** `egeria-corporation` — clone URLs are `https://github.com/egeria-corporation/<repo>`
- **License:** Apache License 2.0, no exceptions. `LICENSE` (full text) + `NOTICE` (attribution) in every repo.
- **Sponsor line:** every README carries the same footer — built and maintained by Egeria Corporation, sponsored by [OpenGrants](https://opengrants.io). No other marketing copy in the README.

## The two hard rules

1. **Easy to deploy and run.** The quickstart must be one command with no account, no API key, no database to stand up. If a new user cannot get a real result inside 60 seconds of reading the README, the design is wrong. Prefer `uvx <tool>` (Python) and `npx` / one-click deploy (TypeScript) over anything requiring a clone-and-install ritual.
2. **Simple, but solving a real critical problem.** Every repo does one job. When a feature request would turn the tool into a platform, the answer is no, and `docs/NON-GOALS.md` says so in advance.

## Dual interface

Every repo ships both:

- a **CLI** — the consultant-facing surface, human-readable output by default, `--json` for machines
- an **MCP server** — the agent-facing surface, same capabilities, exposed as MCP tools

The MCP server is not an afterthought or a wrapper written last. Core logic lives in a library module; the CLI and the MCP server are both thin adapters over it. Business logic in a CLI command handler is a bug.

## OpenGrants integration — optional, never required

Every tool is fully functional with zero OpenGrants credentials. Setting `OPENGRANTS_API_KEY` in the environment adds a live enrichment layer on top of the historical/static public data.

- Base URL: `https://qnoicxojartltrownmal.supabase.co/functions/v1/`
- Auth: `Authorization: Bearer <key>`
- Endpoints: `GET /grants-api`, `GET /grants-api/{id}`, `GET /contracts-api`, `GET /contracts-api/{id}`, `GET /funders-api`, `GET /funders-api/{id}`, `POST /match-grants-api` (Pro/Developer tier only)
- Rate limit headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`
- Docs: https://ops.opengrants.io/api-docs · MCP: https://mcp.opengrants.io/mcp

**Implementation requirements:**

- The enrichment call is always wrapped so it degrades silently to the un-enriched result. A network failure or an expired key must never break the core command.
- Enriched output is visually distinguished (a `— live from OpenGrants` marker) so users always know which data is public-source and which is API-sourced.
- No key means no nag. Mention the optional key exactly once, in the README, and never in command output. Tools that beg for signups do not get adopted.
- Never hardcode a key. `.env.example` documents it; `.env` is gitignored.

## Attribution is a first-class requirement

This program is only credible if it is a good citizen of the nonprofit open data community. Several repos build directly on work by the Nonprofit Open Data Collective and GivingTuesday.

- `NOTICE` names every upstream project, its author, and its license.
- The README has a **Credits** section above the fold, not buried at the bottom.
- Where we fix a bug or extend a mapping in upstream work, we open the PR upstream first and note it in `docs/research/prior-art.md`.
- We never re-implement something a community project already does well just to own it.

## Data honesty

- Every dataset the tool derives from is named in the README with a link to its source and its refresh cadence.
- Every derived output carries the source filing date or dataset vintage. "As of" is not optional.
- No tool makes eligibility determinations, predicts outcomes, or gives legal, tax, or accounting advice.

## Required disclosure

Any tool that reports on an organization's status, eligibility, or compliance posture must carry this text in the README **and** in the command output footer:

> This is informational only, derived from public data on the dates shown. It is not an eligibility determination, and not legal, tax, or accounting advice. Verify against the official source before relying on it.

## Repo layout

```
<repo>/
├── README.md                  # positioning, 60-second quickstart, credits, disclosure
├── LICENSE                    # Apache 2.0 full text
├── NOTICE                     # upstream attribution
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── SECURITY.md
├── .env.example
├── .gitignore
├── docs/
│   ├── NON-GOALS.md           # what this tool will never do
│   ├── research/
│   │   ├── data-sources.md    # verified endpoints, formats, gotchas, refresh cadence
│   │   ├── prior-art.md       # upstream projects, credits, contribution plan
│   │   └── competitive.md     # the paid feature this replaces
│   └── hosted/
│       └── architecture.md    # hosted companion design
├── prompts/
│   ├── 01-build-core.md       # Claude Code kickoff: library + CLI + MCP
│   └── 02-build-hosted.md     # Claude Code kickoff: hosted companion site
└── .github/workflows/ci.yml
```

## Engineering standards

- **Python repos:** Python 3.11+, `uv` for dependency management, `ruff` for lint and format, `pytest` for tests, `pyproject.toml` with a console entry point. Ship as a `uvx`-runnable package.
- **TypeScript repos:** TypeScript strict mode, `pnpm`, `biome` for lint and format, `vitest` for tests, Hono for HTTP on Workers.
- **Tests:** every repo has fixture-based tests using real (small, committed) samples of the actual upstream data. Mocked-shape tests do not catch schema drift, which is the failure mode that actually matters here.
- **CI:** GitHub Actions running lint + tests on push and PR. Green CI badge in the README.
- **Versioning:** semver, `CHANGELOG.md` from the first release.
- **No secrets in the repo, ever.** CI uses repository secrets; local uses `.env`.

## Writing standards for docs

Write for a smart grant consultant who is not a developer. No unexplained jargon, every acronym expanded on first use, and examples using real EINs and real foundations rather than `foo`/`bar`. The README's first paragraph must state the problem in the reader's language before it names the tool.
