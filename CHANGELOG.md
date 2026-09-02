# Changelog

All notable changes to `funder-graph` are documented here. This project follows [Semantic Versioning](https://semver.org/) and the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [Unreleased]

### Added
- Milestone 1: package skeleton (`pyproject.toml`, `uv`, `ruff`, `pytest`, CI), the
  concordance loader, and the Part XV / Schedule I extractor. `funder-graph parse-filing`
  prints real grant rows from one real filing, mapped through the concordance.
- The IRS E-file Master Concordance File vendored at commit `d8266da9` (2026-08-23) under
  `data/concordance/`, including `F990-PF-FULL.CSV` and the 21 per-version XPath inventories
  from `03-versions/raw-mappings/`, with per-file SHA-256s in `data/upstream-pins.toml`.
- Seven real filing fixtures from the 2023 bulk posting, spanning `2020v4.0`, `2021v4.0`,
  `2021v4.2` and `2022v5.0`: organization recipients, a genuine set of individual recipients,
  organization names typed into the person slot, a foreign address, an aggregate placeholder
  row standing in for an attachment, a paid/approved-future pair, and a Schedule I filing.
- Aggregate-placeholder detection (`VARIOUS ORGANIZATIONS`, `SEE ATTACHED SCHEDULE`) feeding
  the missing-detail report, and organizational-token screening before any row is tagged an
  individual.
- Repository scaffolding: documentation, research dossier, and build prompts.

### Changed
- Published schema: added `match_method` (VARCHAR, nullable) after `match_tier`. The build
  spec's table carried it and entity resolution needs it to be auditable; the README's table
  had lagged. A test now parses the README table and asserts it equals the writer's columns.
- `docs/research/data-sources.md`: corrected against the real files. The IRS year directory
  listing returns 404 (enumerate the landing page instead); `SUB_DATE` is year-only in the 2023
  index; there is no per-filing URL anywhere; 990-PF lives in a separate concordance file; the
  concordance's `versions` annotations stop at 2016 while its XPaths match 2022 filings.
