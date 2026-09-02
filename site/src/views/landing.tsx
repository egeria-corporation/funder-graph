/**
 * The landing page. One job: get somebody from "I have a foundation's name" to the page that
 * shows what it funded. The search box is first; everything after it establishes that the
 * numbers can be trusted - where they come from, when, and what the site refuses to say.
 */

import type { FC } from "hono/jsx";
import { displayEin } from "../lib/ein";
import { compactMoney, count, isoDate, money, niceName, plural } from "../lib/format";
import type { FunderIndexRow, Vintage } from "../lib/types";
import { DATA_URL, Disclosure, Page, REPO } from "./layout";

const TITLE = "funder-graph — who funds whom, from every electronically filed Form 990";
const DESCRIPTION =
  "Every grant a US foundation reported paying, from IRS Form 990-PF and Form 990 Schedule I " +
  "e-file data: recipients, amounts, purposes and the source filing for each. One page per " +
  "funder, one per recipient, and the whole dataset as Parquet. Free, open, no account.";

export const Landing: FC<{
  canonical: string;
  vintage: Vintage | null;
  top: FunderIndexRow[];
  states: Array<{ state: string; n: number }>;
}> = ({ canonical, vintage, top, states }) => (
  <Page
    title={TITLE}
    description={DESCRIPTION}
    canonical={canonical}
    jsonLd={{
      "@context": "https://schema.org",
      "@type": "WebSite",
      name: "funder-graph",
      url: canonical,
      description: DESCRIPTION,
      license: "https://www.apache.org/licenses/LICENSE-2.0",
      potentialAction: {
        "@type": "SearchAction",
        target: { "@type": "EntryPoint", urlTemplate: `${canonical}search?q={q}` },
        "query-input": "required name=q",
      },
    }}
  >
    <section class="hero">
      <h1>What has this foundation actually funded?</h1>
      <p class="lede prose">
        Every grant a private foundation or grantmaking charity reported paying on its Form 990-PF
        or Form 990 Schedule I, as the IRS published it: the recipient, the amount, the stated
        purpose, the tax year, and the exact filing it came from. Look up a funder to see who they
        give to. Look up a nonprofit to see who gives to them.
      </p>
      <form class="search" action="/search" method="get">
        <label class="sr" for="q">
          Foundation or nonprofit name, or EIN
        </label>
        <input
          id="q"
          name="q"
          type="search"
          placeholder="Foundation or nonprofit name, or an EIN"
          autocomplete="off"
        />
        <button type="submit">Look up</button>
      </form>
      <p class="hint">
        Try <a href="/search?q=packard+foundation">Packard Foundation</a>,{" "}
        <a href="/search?q=feeding+america">Feeding America</a>, or an EIN like{" "}
        <a href="/funders/562618866">56-2618866</a>.
      </p>
    </section>

    {vintage ? (
      <div class="grid">
        <div class="card">
          <h3>Grant rows</h3>
          <p class="big">{count(vintage.grant_rows)}</p>
          <p>reported paid or approved</p>
        </div>
        <div class="card">
          <h3>Funders</h3>
          <p class="big">{count(vintage.funder_rows)}</p>
          <p>with a page on this site</p>
        </div>
        <div class="card">
          <h3>Recipients</h3>
          <p class="big">{count(vintage.recipient_rows)}</p>
          <p>resolved to an IRS-listed organization</p>
        </div>
        <div class="card">
          <h3>Dataset version</h3>
          <p class="big mono">{vintage.version}</p>
          <p>built {isoDate(vintage.built_at)}</p>
        </div>
      </div>
    ) : null}

    {top.length > 0 ? (
      <section>
        <h2>The largest grantmakers in this dataset</h2>
        <table class="data">
          <caption>By total grants paid, across every tax year in the corpus</caption>
          <thead>
            <tr>
              <th>Funder</th>
              <th>State</th>
              <th>Form</th>
              <th class="num">Grants</th>
              <th class="num">Recipients</th>
              <th class="num">Total paid</th>
            </tr>
          </thead>
          <tbody>
            {top.map((f) => (
              <tr>
                <td>
                  <a href={`/funders/${f.ein}`}>{niceName(f.name)}</a>
                  <span class="tag"> {displayEin(f.ein)}</span>
                </td>
                <td>{f.state ?? ""}</td>
                <td>{f.form_type === "990PF" ? "990-PF" : "990 Sch. I"}</td>
                <td class="num">{count(f.grant_count)}</td>
                <td class="num">{count(f.recipient_count)}</td>
                <td class="num" title={money(f.total_paid_usd)}>
                  {compactMoney(f.total_paid_usd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p class="hint">
          Donor-advised fund sponsors lead every such list; their grants are the recommendations of
          thousands of individual account holders. See <a href="/methodology#daf">methodology</a>.
        </p>
      </section>
    ) : null}

    <section>
      <h2>What this is, and what it is not</h2>
      <div class="prose">
        <p>
          <b>Every number is as filed.</b> Amounts, recipient names, and purposes are what the
          funder wrote on its return. We normalize names to match them to the IRS Business Master
          File; we never rewrite the source. Each row links to the IRS OBJECT_ID of the filing it
          came from, so any figure can be traced to one specific document.
        </p>
        <p>
          <b>Some recipients are inferred, and the page says so.</b> Form 990-PF usually reports a
          recipient by name and address only. Resolving that to an organization is matching, and
          matching is inference. Every edge carries a match tier from A (the filer reported the EIN)
          to D (a probable name match), explained on the{" "}
          <a href="/methodology#tiers">methodology page</a>. Anything below tier B is a lead, not a
          fact.
        </p>
        <p>
          <b>Two kinds of amounts, never added together.</b> Form 990-PF reports grants <i>paid</i>{" "}
          and grants <i>approved for future payment</i> in two tables that overlap across years.
          This site keeps them apart on every page. Totals are grants paid.
        </p>
        <p>
          <b>Filings lag.</b> A foundation's grants for a given year appear a year or more later.
          This is a rear-view mirror by construction, and the dataset version on every page tells
          you how far back it sees.
        </p>
        <p>
          <b>No named individuals.</b> Scholarship and hardship payments to natural persons are
          excluded from the edge list and never shown.
        </p>
      </div>
    </section>

    <section>
      <h2>Browse by state</h2>
      <p class="hint">Every funder in the dataset, by the state on its return.</p>
      <p>
        {states.map((s, i) => (
          <>
            <a href={`/browse/state/${s.state}`}>{s.state}</a>
            <span class="tag"> {count(s.n)}</span>
            {i < states.length - 1 ? " · " : ""}
          </>
        ))}
      </p>
    </section>

    <section>
      <h2>The dataset itself</h2>
      <div class="prose">
        <p>
          The pages on this site are one view of a public dataset. The dataset is the product.
          {vintage ? ` Version ${vintage.version}, ` : " "}
          published as Parquet at <a href={DATA_URL}>data.opengrants.io</a> with a manifest of
          checksums, queryable directly from DuckDB with nothing installed — see{" "}
          <a href="/data">the data page</a> for a query you can paste. Source, pipeline and the
          field mapping are open at <a href={REPO}>github.com/egeria-corporation/funder-graph</a>,
          Apache 2.0.
        </p>
      </div>
    </section>
    <p class="hint">
      {vintage ? `${plural(vintage.grant_rows ?? 0, "grant row")} · ` : ""}
      built from the IRS bulk e-file corpus and the Exempt Organizations Business Master File.
    </p>
    <Disclosure />
  </Page>
);
