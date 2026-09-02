/**
 * The explainers: /data (the dataset, with schema.org/Dataset markup and a query to paste),
 * /methodology (how the numbers are derived and what the tiers mean, in a grant consultant's
 * language), /about.
 */

import type { FC } from "hono/jsx";
import { TIER_LABEL, TIER_MEANING, count, isoDate } from "../lib/format";
import type { Tier, Vintage } from "../lib/types";
import { DATA_URL, Disclosure, Page, REPO } from "./layout";

const QUICKSTART = (version: string) => `INSTALL httpfs; LOAD httpfs;

SELECT recipient_name_raw, recipient_state, amount_usd, tax_year, grant_purpose
FROM read_parquet(
  [ '${DATA_URL}/${version}/grants/filing_year=2023/part-0000.parquet' ],
  hive_partitioning = 1
)
WHERE funder_ein = '941156365'   -- The David and Lucile Packard Foundation
  AND amount_type = 'paid'
ORDER BY amount_usd DESC
LIMIT 25;`;

export const DataPage: FC<{ vintage: Vintage | null; version: string; canonical: string }> = ({
  vintage,
  version,
  canonical,
}) => (
  <Page
    title={`The funder-graph dataset, version ${version} — Parquet, schema, versions`}
    description="The open 990 funding graph as Parquet: every grant reported paid or approved on Form 990-PF and Form 990 Schedule I, with resolved recipient EINs and match tiers. Query it from DuckDB over HTTPS with nothing installed."
    canonical={canonical}
    jsonLd={{
      "@context": "https://schema.org",
      "@type": "Dataset",
      name: "funder-graph: The Open 990 Funding Graph",
      description:
        "Every grant reported paid or approved for future payment on IRS Form 990-PF Part XV and Form 990 Schedule I, from the IRS bulk e-file XML, with recipient identities resolved against the IRS Exempt Organizations Business Master File and a match tier on every row.",
      url: canonical,
      version,
      ...(vintage ? { dateModified: vintage.built_at.slice(0, 10) } : {}),
      license: "https://www.apache.org/licenses/LICENSE-2.0",
      isAccessibleForFree: true,
      creator: {
        "@type": "Organization",
        name: "Egeria Corporation",
        url: "https://opengrants.io",
      },
      distribution: [
        {
          "@type": "DataDownload",
          encodingFormat: "application/vnd.apache.parquet",
          contentUrl: `${DATA_URL}/${version}/manifest.json`,
          description:
            "manifest.json lists one Parquet object per filing year with sizes and SHA-256 checksums",
        },
      ],
      includedInDataCatalog: { "@type": "DataCatalog", name: "OpenGrants open data" },
      keywords: [
        "IRS Form 990",
        "Form 990-PF",
        "foundation grants",
        "grantmaking",
        "nonprofit",
        "philanthropy",
      ],
    }}
  >
    <p class="crumbs">
      <a href="/">funder-graph</a>
      {" › "}Data
    </p>
    <h1>The dataset</h1>
    <p class="lede prose">
      The pages on this site are one view of a public dataset. The dataset is the product: every
      grant a foundation reported paying, as Parquet, with a checksummed manifest, queryable over
      HTTPS from DuckDB with nothing installed and no account.
    </p>
    <dl class="facts">
      <dt>Current version</dt>
      <dd>
        <code>{version}</code>
        {vintage ? ` · built ${isoDate(vintage.built_at)}` : ""}
      </dd>
      {vintage ? (
        <>
          <dt>Grant rows</dt>
          <dd>{count(vintage.grant_rows)}</dd>
          <dt>Funders</dt>
          <dd>{count(vintage.funder_rows)}</dd>
          <dt>Resolved recipients</dt>
          <dd>{count(vintage.recipient_rows)}</dd>
        </>
      ) : null}
      <dt>Manifest</dt>
      <dd>
        <a href={`${DATA_URL}/${version}/manifest.json`}>
          {DATA_URL}/{version}/manifest.json
        </a>
      </dd>
      <dt>Latest alias</dt>
      <dd>
        <a href={`${DATA_URL}/latest/manifest.json`}>{DATA_URL}/latest/manifest.json</a> — a copy of
        the current version; pin an explicit version for anything you will be asked to reproduce
      </dd>
      <dt>License</dt>
      <dd>
        Apache 2.0 for the derived structure, matching and documentation; the source is US federal
        government work
      </dd>
      <dt>Schema and pipeline</dt>
      <dd>
        <a href={`${REPO}#published-schema`}>README, published schema</a> ·{" "}
        <a href={REPO}>source</a>
      </dd>
    </dl>

    <section>
      <h2>Query it in sixty seconds</h2>
      <p class="prose">
        Paste this into DuckDB — the desktop shell, or{" "}
        <a href="https://shell.duckdb.org">shell.duckdb.org</a> in a browser. It reads the Parquet
        footer over HTTP range requests and pulls only the row groups it needs; it does not download
        the dataset. One URL per filing year, listed explicitly, because HTTP has no directory
        listing; the manifest carries the full list.
      </p>
      <pre class="sql">{QUICKSTART(version)}</pre>
      <p class="prose">
        Reverse the question with{" "}
        <code>WHERE recipient_ein_resolved = '363673599' AND match_confidence &gt;= 0.90</code> to
        see who has funded Feeding America. The confidence filter is there for a reason; see{" "}
        <a href="/methodology#tiers">the tiers</a>.
      </p>
    </section>

    <section>
      <h2>The convenience JSON</h2>
      <p class="prose">
        Every funder page is also available as <code>/api/funders/&lt;ein&gt;.json</code>: the same
        payload the page renders from, CORS-open, no key. It is a convenience for people who want
        the page's numbers without the page. It is explicitly not an SLA-backed API; the Parquet is
        the stable interface.
      </p>
    </section>

    <section>
      <h2>Versions and citation</h2>
      <p class="prose">
        Versions are <code>YYYY.MM.PATCH</code>: the year and month of the IRS posting the release
        was built from, and a patch for a re-release that fixes a mapping without new source data.
        Every row carries <code>object_id</code>, <code>tax_period_end</code> and{" "}
        <code>filing_submission_date</code>, so any figure you publish traces to one filing.
      </p>
      <p class="note">
        Egeria Corporation. <i>funder-graph: The Open 990 Funding Graph</i>, dataset version{" "}
        {version}. Derived from IRS Form 990 and 990-PF e-file XML and the IRS Exempt Organizations
        Business Master File. Field mapping via the Nonprofit Open Data Collective IRS E-file Master
        Concordance File. {REPO}
      </p>
    </section>
    <Disclosure />
  </Page>
);

export const Methodology: FC<{ canonical: string }> = ({ canonical }) => (
  <Page
    title="Methodology — how funder-graph derives every number, and how much to trust each one"
    description="Where the grant rows come from, how recipients are matched to the IRS Business Master File, what the match tiers A to D and U mean, the paid versus approved-for-future trap, coverage, and filing lag."
    canonical={canonical}
  >
    <p class="crumbs">
      <a href="/">funder-graph</a>
      {" › "}Methodology
    </p>
    <h1>Methodology</h1>
    <div class="prose">
      <p class="lede">
        Every figure on this site is derived from a public IRS filing by a pipeline whose source is
        open. This page explains the derivation in the terms a grant consultant uses, so that a
        number here can be cited with the right amount of confidence and no more.
      </p>

      <h2 id="sources">Where the rows come from</h2>
      <p>
        The IRS publishes every electronically filed Form 990-series return as XML. Private
        foundations list the grants they paid, and those approved for future payment, on{" "}
        <b>Form 990-PF, Part XV</b>. Public charities that make grants list them on{" "}
        <b>Form 990, Schedule I, Part II</b>. The pipeline reads every such row from the current IRS
        posting, maps each schema version's element names through the Nonprofit Open Data
        Collective's IRS E-file Master Concordance File, and writes one row per grant. Nothing is
        keyed by hand and nothing is rewritten: names, amounts and purposes are as filed.
      </p>
      <p>
        Filers whose total paid is stated but whose grantee list is an attachment the XML does not
        carry are recorded as such and excluded from the edge list, with the stated total published
        separately. A funder absent from this site is not a funder that made no grants.
      </p>

      <h2 id="amounts">Two kinds of amounts, never added together</h2>
      <p>
        Form 990-PF reports grants <i>paid during the year</i> and grants{" "}
        <i>approved for future payment</i> in two separate tables, and they overlap across years: a
        grant approved in one year appears again as paid in the next. Summing both overstates
        giving, sometimes badly. This site keeps them apart on every page. Totals are grants paid;
        approved-for-future commitments are shown in their own card and their own column, and never
        enter a total.
      </p>

      <h2 id="tiers">How recipients are matched, and what the tiers mean</h2>
      <p>
        Form 990-PF usually names a recipient by name and address only. Turning that into an
        organization means matching it against the IRS Exempt Organizations Business Master File,
        and matching is inference. Every edge carries a tier, and the tier is the honest answer to
        "how do you know it is them?":
      </p>
      <ul class="plain">
        {(["A", "B", "C", "D", "U"] as Tier[]).map((t) => (
          <li>
            <b>
              {t} — {TIER_LABEL[t]}.
            </b>{" "}
            {TIER_MEANING[t]}
          </li>
        ))}
      </ul>
      <p>
        The matcher blocks candidates by exact normalized name, by state and first name token, by
        ZIP code and first token, and by state and phonetic key; scores the survivors on name
        similarity with small adjustments for a matching city and ZIP; and refuses any tuple with
        two candidates within a few points of each other, which is how chapter-style names ("Rotary
        Club of …", "American Legion Post …") stay unresolved rather than wrongly resolved. Grants
        to named individuals are excluded before any of this.
      </p>

      <h2 id="verification">How the matching is verified</h2>
      <p>
        Precision is measured, not asserted, against a hand-labeled set of recipient tuples drawn
        evenly from every tier: at least a thousand pairs, each verified against the Business Master
        File and the filing by a person. The targets are 99% for tier A, 97% for tier B, 95% for
        tier C and 90% for tier D. A build that misses a target for a tier does not lower the
        target; it reports the miss. Until the check for a dataset version is complete and published
        here, the site says so on every entity page, and tiers B–D should be read as leads.
      </p>

      <h2 id="coverage">Coverage and what is missing</h2>
      <p>
        The IRS bulk XML corpus covers electronically filed returns, mandatory for most filers for
        tax years beginning after July 1, 2019. Earlier years are real but partial, and small paper
        filers are absent. Within the posting, the field mapping resolves every publishable field
        for more than 99% of grant-bearing filings, and parsed 990-PF totals reconcile with the
        filers' own stated totals within 1% for more than 99.9% of filings that state one. Both
        figures are published with each dataset version.
      </p>

      <h2 id="lag">Filing lag</h2>
      <p>
        A foundation's grants for a calendar year are reported on a return filed the following year
        and posted by the IRS some months after that. This is a rear-view mirror by construction.
        The dataset version on every page says which IRS posting it saw.
      </p>

      <h2 id="daf">Donor-advised fund sponsors</h2>
      <p>
        The largest grantmakers by dollars are donor-advised fund sponsors — Fidelity Charitable,
        Schwab Charitable, the National Philanthropic Trust, community foundations. Their grants are
        the recommendations of thousands of individual account holders, not the priorities of one
        institution. They are real grants, correctly attributed to the sponsor that paid them, and
        they should be read with that in mind.
      </p>

      <h2 id="individuals">Named individuals</h2>
      <p>
        Scholarship, fellowship and hardship payments to natural persons are reported on the same
        forms. They are tagged at parse time and excluded from the edge list, the pages, and the
        published dataset's default view. Publishing named individuals would serve nobody.
      </p>
    </div>
    <Disclosure />
  </Page>
);

export const About: FC<{ canonical: string }> = ({ canonical }) => (
  <Page
    title="About funder-graph"
    description="funder-graph is an open dataset and site: every grant a US foundation reported paying, from IRS e-file data, built by Egeria Corporation and sponsored by OpenGrants."
    canonical={canonical}
  >
    <p class="crumbs">
      <a href="/">funder-graph</a>
      {" › "}About
    </p>
    <h1>About</h1>
    <div class="prose">
      <p class="lede">
        The relationship between a foundation and the organizations it funds is public information,
        filed under penalty of perjury and published by the IRS. It has mostly been available only
        through paid research products. This project makes it a dataset anyone can query and a set
        of pages anyone can cite.
      </p>
      <p>
        funder-graph is built and maintained by Egeria Corporation and sponsored by{" "}
        <a href="https://opengrants.io">OpenGrants</a>. The pipeline, the field mapping, the matcher
        and this site are open source under the Apache License 2.0 at{" "}
        <a href={REPO}>github.com/egeria-corporation/funder-graph</a>. Corrections to a match are
        welcome as pull requests against the overrides files; each one is reviewed against the
        filing and the Business Master File.
      </p>
      <p>
        It stands on the work of others: the Nonprofit Open Data Collective's IRS E-file Master
        Concordance File, which maps every schema version's element names to stable variables, and
        the e-file corpus stewarded by GivingTuesday's data commons.
      </p>
      <p>
        It is one of five sibling sites that reference the same organizations by EIN:{" "}
        <a href="https://check.opengrants.io">check.opengrants.io</a> for exempt status and filing
        health, <a href="https://awards.opengrants.io">awards.opengrants.io</a> for federal awards,{" "}
        <a href="https://answers.opengrants.io">answers.opengrants.io</a> for guidance, and{" "}
        <a href="https://opengrants.io">opengrants.io</a> for open opportunities.
      </p>
      <p>
        No accounts, no tracking beyond ordinary web request logs, no write path. It is a read-only
        view of a public dataset.
      </p>
    </div>
    <Disclosure />
  </Page>
);
