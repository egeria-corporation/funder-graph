/**
 * Search results over funder and recipient names. FTS5 in D1; an EIN typed in goes
 * straight to the page before this ever renders.
 */

import type { FC } from "hono/jsx";
import { displayEin } from "../lib/ein";
import { niceName } from "../lib/format";
import type { SearchHit } from "../lib/types";
import { Page } from "./layout";

export const Search: FC<{ q: string; hits: SearchHit[]; canonical: string }> = ({
  q,
  hits,
  canonical,
}) => (
  <Page
    title={q ? `${q} — search — funder-graph` : "Search funders and recipients — funder-graph"}
    description="Find a foundation to see what it funds, or a nonprofit to see who funds it."
    canonical={canonical}
    noindex={Boolean(q)}
  >
    <section class="hero" style="padding-top:40px">
      <h1>{q ? `Results for “${q}”` : "Search"}</h1>
      <form class="search" action="/search" method="get">
        <label class="sr" for="q">
          Foundation or nonprofit name, or EIN
        </label>
        <input
          id="q"
          name="q"
          type="search"
          value={q}
          placeholder="Foundation or nonprofit name, or an EIN"
          autocomplete="off"
        />
        <button type="submit">Look up</button>
      </form>
      <p class="hint">Names as filed with the IRS; try fewer words if nothing matches.</p>
    </section>
    {q ? (
      hits.length > 0 ? (
        <ul class="plain">
          {hits.map((h) => (
            <li>
              <a href={`/${h.kind === "funder" ? "funders" : "recipients"}/${h.ein}`}>
                {niceName(h.name)}
              </a>{" "}
              <span class="meta">
                {displayEin(h.ein)} · {[niceName(h.city), h.state].filter(Boolean).join(", ")} ·{" "}
                {h.kind === "funder" ? "funder" : "recipient"}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p class="note">
          Nothing matched. The index holds funders that filed a Form 990-PF or a Form 990 with
          Schedule I in the IRS e-file corpus, and recipients that resolved to an IRS-listed
          organization. Try a shorter name, or <a href="/browse">browse by state</a>.
        </p>
      )
    ) : null}
  </Page>
);
