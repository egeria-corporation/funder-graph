# What this replaces

> **Note on scope.** This file describes the capability gap `funder-graph` fills. It deliberately
> names no vendor and quotes no price. Comparative analysis of commercial products is maintained
> outside this repository for now. Nothing in the tool, its help text, its command output, the
> published dataset, or any hosted page may name or price a commercial product — see
> `docs/program/CONVENTIONS.md`.

## The one-sentence version

Every paid foundation-research product in this category is a user interface sitting on top of Form
990 data that costs nothing to obtain. The moat is not the data. **The moat is the difficulty of
parsing it** — and that is a one-time engineering cost which disappears permanently the moment
somebody pays it in public.

This repository is that payment.

## Why the parsing is the moat

The IRS publishes the electronic filing corpus as ZIP archives of XML, one file per filing, across
hundreds of schema versions, with element names that change between versions and no stable field
naming. Two tables carry the grant edges:

- **Form 990-PF, Part XV** — every grant a private foundation paid, with recipient name, address,
  purpose, and amount. Recipient EIN is frequently absent.
- **Form 990, Schedule I** — grants made by public charities to other organizations, usually
  including the recipient EIN.

Extracting them reliably means resolving fields through a version-aware concordance rather than
hand-written XPaths, then resolving recipient names to EINs with a published confidence score. Both
are weeks of careful work. Neither is novel. Nobody had done it in the open.

## What is genuinely differentiated here

Not the edges themselves — those are public record. Three things:

1. **Published match confidence.** Every resolved recipient EIN carries a tier and a score. A
   product that presents an inferred match as a lookup is making a claim it cannot support; this
   dataset says how sure it is, row by row.
2. **The published `unmatched` table.** The honest accounting of what the dataset does not know.
   It is the best place for the community to contribute fixes, and no commercial product has an
   incentive to publish its own error surface.
3. **Reproducibility.** Same pinned inputs plus same code produces byte-identical output, with every
   source file checksummed in the manifest. A number from this dataset can be traced to a filing.

## What `funder-graph` does not claim

- It is not a complete picture of philanthropy. It covers what was filed electronically and reported
  in structured form. Grants reported as unstructured attachments are counted as missing, loudly,
  rather than silently as zero.
- It does not predict whether a funder will give to you.
- Individual grant recipients who are natural persons are tagged and excluded from the default view.
