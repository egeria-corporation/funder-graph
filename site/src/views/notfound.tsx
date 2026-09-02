/**
 * A real 404 with search, and a 404 status. A nonexistent EIN is the most common way here.
 */

import type { FC } from "hono/jsx";
import { displayEin } from "../lib/ein";
import { Page } from "./layout";

export const NotFound: FC<{
  kind: "funder" | "recipient" | "year" | "browse" | "page";
  ein?: string;
  year?: number;
  funderExists?: boolean;
}> = ({ kind, ein, year, funderExists }) => {
  const what =
    kind === "funder"
      ? `No funder with EIN ${ein ? displayEin(ein) : ""} in this dataset`
      : kind === "recipient"
        ? `No resolved recipient with EIN ${ein ? displayEin(ein) : ""} in this dataset`
        : kind === "year"
          ? `No grants for tax year ${year} on this funder`
          : kind === "browse"
            ? "Nothing to browse here"
            : "Page not found";
  return (
    <Page title={`${what} — funder-graph`} description={what} noindex>
      <section class="notfound">
        <h1>{what}</h1>
        <div class="prose">
          {kind === "funder" ? (
            <p>
              The organization may not file a Form 990-PF or a Form 990 with Schedule I, may file on
              paper, or may not yet appear in the IRS e-file posting this dataset version was built
              from. Absence here is not evidence that it makes no grants.
            </p>
          ) : null}
          {kind === "recipient" ? (
            <p>
              Recipient pages exist only for organizations the matcher resolved to an EIN in the IRS
              Business Master File. This one may be named on filings under a name that did not
              resolve, or may not have been named at all.{" "}
              {funderExists && ein ? (
                <>
                  It does appear as a <a href={`/funders/${ein}`}>funder</a>.
                </>
              ) : null}
            </p>
          ) : null}
          {kind === "year" && ein ? (
            <p>
              <a href={`/funders/${ein}`}>See every year this funder reported.</a>
            </p>
          ) : null}
        </div>
        <form class="search" action="/search" method="get">
          <label class="sr" for="q">
            Foundation or nonprofit name, or EIN
          </label>
          <input
            id="q"
            name="q"
            type="search"
            placeholder="Foundation or nonprofit name, or an EIN"
          />
          <button type="submit">Look up</button>
        </form>
        <p class="hint">
          Or <a href="/browse">browse funders by state</a>.
        </p>
      </section>
    </Page>
  );
};

export const NoDataset: FC = () => (
  <Page
    title="funder-graph — no dataset published yet"
    description="No dataset version is published."
    noindex
  >
    <section class="notfound">
      <h1>No dataset version is published yet</h1>
      <p class="prose">
        This site renders a published version of the funder-graph dataset, and none is marked
        current. If you run this site, seed the index and set the vintage pointer.
      </p>
    </section>
  </Page>
);
