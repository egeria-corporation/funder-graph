/**
 * Crawlable index pages. These are how the long tail of funder pages gets discovered; no
 * sitemap alone reliably gets deep pages crawled. Every page links to real entity pages,
 * paginated, with no JavaScript.
 */

import type { FC } from "hono/jsx";
import { displayEin } from "../lib/ein";
import { compactMoney, count, money, niceName, plural } from "../lib/format";
import type { FunderIndexRow } from "../lib/types";
import { Page } from "./layout";

const STATE_NAMES: Record<string, string> = {
  AL: "Alabama",
  AK: "Alaska",
  AZ: "Arizona",
  AR: "Arkansas",
  CA: "California",
  CO: "Colorado",
  CT: "Connecticut",
  DE: "Delaware",
  DC: "District of Columbia",
  FL: "Florida",
  GA: "Georgia",
  HI: "Hawaii",
  ID: "Idaho",
  IL: "Illinois",
  IN: "Indiana",
  IA: "Iowa",
  KS: "Kansas",
  KY: "Kentucky",
  LA: "Louisiana",
  ME: "Maine",
  MD: "Maryland",
  MA: "Massachusetts",
  MI: "Michigan",
  MN: "Minnesota",
  MS: "Mississippi",
  MO: "Missouri",
  MT: "Montana",
  NE: "Nebraska",
  NV: "Nevada",
  NH: "New Hampshire",
  NJ: "New Jersey",
  NM: "New Mexico",
  NY: "New York",
  NC: "North Carolina",
  ND: "North Dakota",
  OH: "Ohio",
  OK: "Oklahoma",
  OR: "Oregon",
  PA: "Pennsylvania",
  RI: "Rhode Island",
  SC: "South Carolina",
  SD: "South Dakota",
  TN: "Tennessee",
  TX: "Texas",
  UT: "Utah",
  VT: "Vermont",
  VA: "Virginia",
  WA: "Washington",
  WV: "West Virginia",
  WI: "Wisconsin",
  WY: "Wyoming",
  PR: "Puerto Rico",
  VI: "U.S. Virgin Islands",
  GU: "Guam",
  AS: "American Samoa",
  MP: "Northern Mariana Islands",
};

const NTEE_MAJOR: Record<string, string> = {
  A: "Arts, culture and humanities",
  B: "Education",
  C: "Environment",
  D: "Animal-related",
  E: "Health care",
  F: "Mental health and crisis intervention",
  G: "Diseases, disorders and medical disciplines",
  H: "Medical research",
  I: "Crime and legal-related",
  J: "Employment",
  K: "Food, agriculture and nutrition",
  L: "Housing and shelter",
  M: "Public safety, disaster preparedness and relief",
  N: "Recreation and sports",
  O: "Youth development",
  P: "Human services",
  Q: "International, foreign affairs and national security",
  R: "Civil rights, social action and advocacy",
  S: "Community improvement and capacity building",
  T: "Philanthropy, voluntarism and grantmaking foundations",
  U: "Science and technology",
  V: "Social science",
  W: "Public and societal benefit",
  X: "Religion-related",
  Y: "Mutual and membership benefit",
  Z: "Unknown",
};

export function stateName(code: string): string {
  return STATE_NAMES[code] ?? code;
}

export const BrowseIndex: FC<{
  states: Array<{ state: string; n: number }>;
  canonical: string;
}> = ({ states, canonical }) => (
  <Page
    title="Browse funders by state and by field — funder-graph"
    description="Every grantmaking organization in the dataset, indexed by the state on its return and by NTEE field."
    canonical={canonical}
  >
    <p class="crumbs">
      <a href="/">funder-graph</a>
      {" › "}Browse
    </p>
    <h1>Browse funders</h1>
    <section>
      <h2>By state</h2>
      <ul class="plain">
        {states.map((s) => (
          <li>
            <a href={`/browse/state/${s.state}`}>{stateName(s.state)}</a>{" "}
            <span class="meta">{plural(s.n, "funder")}</span>
          </li>
        ))}
      </ul>
    </section>
    <section>
      <h2>By field (NTEE major group of the funder)</h2>
      <ul class="plain">
        {Object.entries(NTEE_MAJOR).map(([code, name]) => (
          <li>
            <a href={`/browse/ntee/${code}`}>{name}</a> <span class="meta">{code}</span>
          </li>
        ))}
      </ul>
      <p class="hint">
        NTEE codes come from the IRS Business Master File and describe the funder, not its grants.
        Many foundations carry T (philanthropy) regardless of what they fund.
      </p>
    </section>
  </Page>
);

export const Browse: FC<{
  kind: "state" | "ntee";
  code: string;
  rows: FunderIndexRow[];
  total: number;
  page: number;
  perPage: number;
  canonical: string;
}> = ({ kind, code, rows, total, page, perPage, canonical }) => {
  const label =
    kind === "state"
      ? stateName(code)
      : (NTEE_MAJOR[code.slice(0, 1)] ?? code) + (code.length > 1 ? ` (${code})` : "");
  const pages = Math.max(1, Math.ceil(total / perPage));
  const base = `/browse/${kind}/${code}`;
  const title =
    kind === "state"
      ? `Foundations and grantmakers in ${label} — ${plural(total, "funder")}`
      : `Grantmakers in ${label} — ${plural(total, "funder")}`;
  return (
    <Page
      title={`${title}${page > 1 ? ` — page ${page}` : ""} — funder-graph`}
      description={`${plural(total, "grantmaking organization")} ${kind === "state" ? `with a ${label} address` : `classified ${label}`} in the funder-graph dataset, ranked by total grants paid, with a page for each.`}
      canonical={canonical}
    >
      <p class="crumbs">
        <a href="/browse">Browse</a>
        {" › "}
        {label}
      </p>
      <h1>{title}</h1>
      <p class="hint">
        Ranked by total grants paid across every tax year in the corpus.
        {pages > 1 ? ` Page ${page} of ${pages}.` : ""}
      </p>
      <table class="data">
        <caption>Funders {kind === "state" ? `in ${label}` : `classified ${label}`}</caption>
        <thead>
          <tr>
            <th>Funder</th>
            <th>City</th>
            <th>Form</th>
            <th class="num">Grants</th>
            <th class="num">Total paid</th>
            <th class="num">Years</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((f) => (
            <tr>
              <td>
                <a href={`/funders/${f.ein}`}>{niceName(f.name)}</a>
                <span class="tag"> {displayEin(f.ein)}</span>
              </td>
              <td>{niceName(f.city)}</td>
              <td>{f.form_type === "990PF" ? "990-PF" : "990 Sch. I"}</td>
              <td class="num">{count(f.grant_count)}</td>
              <td class="num" title={money(f.total_paid_usd)}>
                {compactMoney(f.total_paid_usd)}
              </td>
              <td class="num">
                {f.first_tax_year && f.last_tax_year && f.first_tax_year !== f.last_tax_year
                  ? `${f.first_tax_year}–${f.last_tax_year}`
                  : (f.last_tax_year ?? "")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {pages > 1 ? (
        <nav class="pager" aria-label="Pages">
          {page > 1 ? (
            <a href={`${base}${page > 2 ? `?page=${page - 1}` : ""}`}>← Previous</a>
          ) : null}
          {page < pages ? <a href={`${base}?page=${page + 1}`}>Next →</a> : null}
        </nav>
      ) : null}
    </Page>
  );
};
