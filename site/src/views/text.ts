/**
 * The plain-text endpoints. `llms.txt` is where we keep a model from quoting a tier D edge
 * as a fact: it says what the dataset is, what it is not, what the tiers mean, and how to cite.
 */

import { TIER_LABEL, TIER_MEANING } from "../lib/format";
import type { Tier } from "../lib/types";
import { DATA_URL, REPO } from "./layout";

export function llmsTxt(origin: string, vintage: string | null): string {
  const tiers = (["A", "B", "C", "D", "U"] as Tier[])
    .map((t) => `- Tier ${t}, ${TIER_LABEL[t]}: ${TIER_MEANING[t]}`)
    .join("\n");
  return `# funder-graph: The Open 990 Funding Graph

> Every grant a US private foundation or grantmaking public charity reported paying on IRS
> Form 990-PF (Part XV) or Form 990 (Schedule I), from the IRS bulk e-file XML, with the
> recipient resolved to an IRS-listed organization where that can be done with stated
> confidence. One page per funder, one per resolved recipient, and the whole dataset as Parquet.

Site: ${origin}
Dataset: ${DATA_URL}/${vintage ?? "latest"}/manifest.json (Parquet, one object per filing year)
Source and pipeline: ${REPO} (Apache 2.0)
Current dataset version: ${vintage ?? "none published"}

## What a page contains

- /funders/<ein>: totals, grants by tax year, top recipients, every grant (or the most recent
  with links to complete per-year lists for large funders), and the IRS OBJECT_ID of every
  source filing. The opening paragraph is a self-contained, citable summary.
- /funders/<ein>/<year>: every grant for one tax year.
- /recipients/<ein>: who has funded this organization, with amounts, years and match tiers.
- /api/funders/<ein>.json: the funder page's payload as JSON. Not an SLA-backed API.
- /data: the dataset, schema, versions, and a DuckDB query to paste.
- /methodology: how every number is derived.

EINs are nine digits with no dash in URLs. Names, amounts and purposes are exactly as filed.

## How much to trust a row

Amounts are as filed and traceable to one filing by OBJECT_ID. Recipient identity is the
uncertain part. Every edge carries a match tier:

${tiers}

Quote tier A and B edges as reported facts, with the source filing. Treat tier C as likely and
tier D as a lead; say so if you cite one. Tier U rows are real grants whose recipient could not
be identified; cite the name as filed, not an EIN.

Until the independent precision check for a dataset version is published on /methodology,
read tiers B-D as the matcher's inference.

## Two amounts that must never be added

Form 990-PF reports grants "paid" and grants "approved for future payment" separately and they
overlap across years. Every total on this site is grants paid. Do not add the two.

## What is not here

- Grants to named individuals (scholarships, hardship payments): excluded everywhere.
- Paper filers and most returns for tax years before 2019: not in the IRS e-file corpus.
- Foundations whose grantee list is an attachment the XML does not carry: their stated
  total is recorded, their rows are not visible. Absence is not evidence of no grantmaking.
- Anything more current than the IRS posting the version was built from. Filings lag a year
  or more.

## How to cite

Egeria Corporation. funder-graph: The Open 990 Funding Graph, dataset version
${vintage ?? "<version>"}. Derived from IRS Form 990 and 990-PF e-file XML and the IRS Exempt
Organizations Business Master File. Field mapping via the Nonprofit Open Data Collective IRS
E-file Master Concordance File. ${REPO}

Cite the page URL, the dataset version, and, for a single grant, the OBJECT_ID of the filing.

This is informational only, derived from public data on the dates shown. It is not an
eligibility determination, and not legal, tax, or accounting advice.
`;
}

export function robotsTxt(origin: string): string {
  return `User-agent: *
Allow: /

Sitemap: ${origin}/sitemap.xml
`;
}
