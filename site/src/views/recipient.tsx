/**
 * The recipient page: who has funded this organization. It exists only for an EIN the
 * matcher resolved, and every edge on it carries the tier it was resolved at, because the
 * reverse question is where inferred matches do the most damage if presented as fact.
 */

import type { FC } from "hono/jsx";
import { displayEin } from "../lib/ein";
import {
  TIER_CLASS,
  compactMoney,
  count,
  isoDate,
  money,
  niceName,
  plural,
  yearSpan,
} from "../lib/format";
import type { RecipientPayload, Tier } from "../lib/types";
import { GrantsTable, TierLegend } from "./funder";
import { DATA_URL, Disclosure, Page, Siblings } from "./layout";

function title(p: RecipientPayload): string {
  const t = p.totals;
  return `Who funds ${niceName(p.name)} (EIN ${displayEin(p.ein)}) — ${plural(t.funder_count, "funder")}, ${compactMoney(
    t.received_usd,
  )} in ${plural(t.grant_count, "grant")}, ${yearSpan(t.first_tax_year, t.last_tax_year) || "tax years as filed"}`;
}

function summary(p: RecipientPayload): string {
  const t = p.totals;
  const where = [niceName(p.city), p.state].filter(Boolean).join(", ");
  const span = yearSpan(t.first_tax_year, t.last_tax_year);
  return (
    `${niceName(p.name)}${where ? ` of ${where}` : ""} (EIN ${displayEin(p.ein)}) was named as a grant recipient by ` +
    `${plural(t.funder_count, "funder")} in ${plural(t.grant_count, "grant")} paid totaling ${money(t.received_usd)}` +
    `${span ? ` in tax years ${span}` : ""}, as reported on the funders' Forms 990-PF and 990 Schedule I. ` +
    `Each edge was resolved to this EIN at the match tier shown. Dataset version ${p.dataset_version}, built ${isoDate(p.built_at)}.`
  );
}

function jsonLd(p: RecipientPayload, canonical: string, origin: string) {
  return [
    {
      "@context": "https://schema.org",
      "@type": p.subsection_code === "03" ? "NGO" : "Organization",
      name: niceName(p.name),
      taxID: displayEin(p.ein),
      url: canonical,
      ...(p.city || p.state
        ? {
            address: {
              "@type": "PostalAddress",
              ...(p.city ? { addressLocality: niceName(p.city) } : {}),
              ...(p.state ? { addressRegion: p.state } : {}),
              addressCountry: "US",
            },
          }
        : {}),
      sameAs: [`https://apps.irs.gov/app/eos/detailsPage?ein=${p.ein}`],
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "Recipients", item: `${origin}/search` },
        { "@type": "ListItem", position: 2, name: niceName(p.name), item: canonical },
      ],
    },
    {
      "@context": "https://schema.org",
      "@graph": p.recent_grants.slice(0, 100).map((g) => ({
        "@type": "MonetaryGrant",
        funder: {
          "@type": "Organization",
          name: niceName(g.funder_name),
          taxID: g.funder_ein ? displayEin(g.funder_ein) : undefined,
        },
        recipient: { "@type": "Organization", name: niceName(p.name), taxID: displayEin(p.ein) },
        amount: { "@type": "MonetaryAmount", currency: "USD", value: g.amount_usd ?? 0 },
        ...(g.purpose ? { description: g.purpose } : {}),
        ...(g.tax_year ? { datePublished: String(g.tax_year) } : {}),
        identifier: g.grant_id,
      })),
    },
  ];
}

const Tiers: FC<{ tiers: string }> = ({ tiers }) => (
  <>
    {tiers.split("").map((t) => (
      <a class={`tier ${TIER_CLASS[t as Tier]}`} href="/methodology#tiers">
        {t}
      </a>
    ))}
  </>
);

export const Recipient: FC<{ payload: RecipientPayload; canonical: string; origin: string }> = ({
  payload: p,
  canonical,
  origin,
}) => (
  <Page
    title={title(p)}
    description={summary(p).slice(0, 300)}
    canonical={canonical}
    jsonLd={jsonLd(p, canonical, origin)}
  >
    <p class="crumbs">
      <a href="/">funder-graph</a>
      {" › "}Recipients
    </p>
    <h1>Who funds {niceName(p.name)}</h1>
    <p class="identity">
      <span class="ein">EIN {displayEin(p.ein)}</span>
      {p.city || p.state ? ` · ${[niceName(p.city), p.state].filter(Boolean).join(", ")}` : ""}
      {p.ntee_code ? ` · NTEE ${p.ntee_code}` : ""}
    </p>
    <p class="summary">{summary(p)}</p>
    <div class="grid">
      <div class="card">
        <h3>Grants received</h3>
        <p class="big">{money(p.totals.received_usd)}</p>
        <p>{plural(p.totals.grant_count, "grant")} paid</p>
      </div>
      <div class="card">
        <h3>Funders</h3>
        <p class="big">{count(p.totals.funder_count)}</p>
        <p>distinct grantmakers</p>
      </div>
      <div class="card">
        <h3>Tax years</h3>
        <p class="big">{yearSpan(p.totals.first_tax_year, p.totals.last_tax_year) || "—"}</p>
        <p>as reported by funders</p>
      </div>
      {p.totals.approved_future_usd > 0 ? (
        <div class="card">
          <h3>Approved for future payment</h3>
          <p class="big">{money(p.totals.approved_future_usd)}</p>
          <p>not included in grants received</p>
        </div>
      ) : null}
    </div>
    <section id="funders">
      <h2>Funders</h2>
      <table class="data">
        <caption>Grantmakers ranked by total paid to {niceName(p.name)}</caption>
        <thead>
          <tr>
            <th>Funder</th>
            <th>State</th>
            <th>Match</th>
            <th class="num">Grants</th>
            <th class="num">Total paid</th>
            <th class="num">Latest year</th>
          </tr>
        </thead>
        <tbody>
          {p.funders.map((f) => (
            <tr>
              <td>
                <a href={`/funders/${f.funder_ein}`}>{niceName(f.funder_name)}</a>
                <span class="tag"> {displayEin(f.funder_ein)}</span>
              </td>
              <td>{f.funder_state ?? ""}</td>
              <td>
                <Tiers tiers={f.tiers} />
              </td>
              <td class="num">{count(f.count)}</td>
              <td class="num">{money(f.paid_usd)}</td>
              <td class="num">{f.last_tax_year ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TierLegend />
    </section>
    <section id="grants">
      <h2>Grants</h2>
      <p class="hint">
        The {count(p.recent_grants.length)} most recent grants naming this organization.
      </p>
      <GrantsTable
        rows={p.recent_grants}
        caption={`Grants to ${niceName(p.name)} as reported by funders`}
        showFunder
      />
      <TierLegend />
    </section>
    <p class="note warn">
      Recipient matching for this dataset version has not yet completed its independent precision
      check. Tier A rows carry the EIN the filer reported; tiers B–D are inferred and should be read
      as leads until the check is published on the{" "}
      <a href="/methodology#verification">methodology page</a>.
    </p>
    <p class="source">
      Derived from IRS Form 990-PF and Form 990 Schedule I e-file XML filed by the funders shown;
      this organization's identity from the IRS Exempt Organizations Business Master File. Dataset
      version <code>{p.dataset_version}</code>, built {isoDate(p.built_at)}. The rows as Parquet:{" "}
      <a href={`${DATA_URL}/${p.dataset_version}/manifest.json`}>manifest</a>.
    </p>
    <Siblings ein={p.ein} />
    <Disclosure />
  </Page>
);
