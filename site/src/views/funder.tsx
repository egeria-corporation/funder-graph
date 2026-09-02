/**
 * The canonical funder page: "Grants paid by X, 2019-2025", with every fact in the HTML.
 *
 * It opens with one quotable paragraph - name, EIN, totals, years, form, vintage - written
 * so a model can lift it whole and still be correct and attributable. Then the numbers as
 * cards, the years as a bar chart with its table, the recipients, the grants, and the
 * filings the whole thing came from. `paid` and `approved_future` never meet in one sum.
 */

import type { FC } from "hono/jsx";
import { displayEin } from "../lib/ein";
import {
  TIER_CLASS,
  TIER_LABEL,
  TIER_MEANING,
  compactMoney,
  count,
  isoDate,
  money,
  niceName,
  plural,
  yearSpan,
} from "../lib/format";
import type { FunderPayload, GrantRow, Tier, TopRecipient, YearPage } from "../lib/types";
import { DATA_URL, Disclosure, Page, Siblings } from "./layout";

const FORM_NAME: Record<string, string> = {
  "990PF": "Form 990-PF, Part XV",
  "990": "Form 990, Schedule I",
};

export const TierBadge: FC<{ tier: Tier }> = ({ tier }) => (
  <a class={`tier ${TIER_CLASS[tier]}`} href="/methodology#tiers" title={TIER_MEANING[tier]}>
    {tier}
  </a>
);

export const TierLegend: FC = () => (
  <p class="legend">
    Match tier:{" "}
    {(["A", "B", "C", "D", "U"] as Tier[]).map((t) => (
      <>
        <span class={`tier ${TIER_CLASS[t]}`}>{t}</span> {TIER_LABEL[t]}
        {t === "U" ? "" : " · "}
      </>
    ))}
    . Tiers C and D are inferred, not reported; see{" "}
    <a href="/methodology#tiers">how matching works</a>.
  </p>
);

export function funderTitle(p: FunderPayload): string {
  const t = p.totals;
  return `Grants paid by ${niceName(p.name)} (EIN ${displayEin(p.ein)}) — ${plural(
    t.paid_count,
    "grant",
  )}, ${compactMoney(t.paid_usd)}, ${yearSpan(t.first_tax_year, t.last_tax_year) || "tax years as filed"}`;
}

export function funderSummary(p: FunderPayload): string {
  const t = p.totals;
  const where = [niceName(p.city), p.state].filter(Boolean).join(", ");
  const span = yearSpan(t.first_tax_year, t.last_tax_year);
  return `${niceName(p.name)}${where ? ` of ${where}` : ""} (EIN ${displayEin(p.ein)}) reported ${plural(t.paid_count, "grant")} paid totaling ${money(t.paid_usd)} to ${plural(t.recipient_count, "recipient")}${span ? ` in tax years ${span}` : ""}, on ${FORM_NAME[p.form_type] ?? p.form_type}. ${
    t.approved_future_count > 0
      ? `It also reported ${money(t.approved_future_usd)} approved for future payment, which is listed separately and not included in the total. `
      : ""
  }Derived from IRS e-file data, dataset version ${p.dataset_version}, built ${isoDate(p.built_at)}.`;
}

function jsonLd(p: FunderPayload, canonical: string, origin: string) {
  const grants = p.recent_grants.slice(0, 100).map((g) => ({
    "@type": "MonetaryGrant",
    funder: { "@type": "Organization", name: niceName(p.name), taxID: displayEin(p.ein) },
    ...(g.recipient_name
      ? {
          fundedItem: undefined,
          recipient: {
            "@type": "Organization",
            name: niceName(g.recipient_name),
            ...(g.recipient_ein && g.match_tier !== "U"
              ? { taxID: displayEin(g.recipient_ein) }
              : {}),
          },
        }
      : {}),
    amount: { "@type": "MonetaryAmount", currency: "USD", value: g.amount_usd ?? 0 },
    ...(g.purpose ? { description: g.purpose } : {}),
    ...(g.tax_year ? { datePublished: String(g.tax_year) } : {}),
    identifier: g.grant_id,
  }));
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
        { "@type": "ListItem", position: 1, name: "Funders", item: `${origin}/browse` },
        ...(p.state
          ? [
              {
                "@type": "ListItem",
                position: 2,
                name: p.state,
                item: `${origin}/browse/state/${p.state}`,
              },
            ]
          : []),
        { "@type": "ListItem", position: p.state ? 3 : 2, name: niceName(p.name), item: canonical },
      ],
    },
    { "@context": "https://schema.org", "@graph": grants },
  ];
}

const YearChart: FC<{ p: FunderPayload; current?: number }> = ({ p, current }) => {
  const paid = p.years.filter((y) => y.amount_type === "paid" && y.tax_year != null);
  const future = p.years.filter((y) => y.amount_type === "approved_future" && y.tax_year != null);
  if (paid.length === 0) return null;
  const max = Math.max(...paid.map((y) => y.usd ?? 0), 1);
  return (
    <>
      <div class="chart" role="img" aria-label="Grants paid by tax year">
        {paid.map((y) => (
          <>
            <span class="y">
              {p.chunked || p.totals.grant_rows > 0 ? (
                current === y.tax_year ? (
                  <b>{y.tax_year}</b>
                ) : (
                  <a href={`/funders/${p.ein}/${y.tax_year}`}>{y.tax_year}</a>
                )
              ) : (
                y.tax_year
              )}
            </span>
            <span
              class="bar"
              style={`width:${Math.max(1, Math.round(((y.usd ?? 0) / max) * 100))}%`}
            />
            <span class="v">{compactMoney(y.usd)}</span>
          </>
        ))}
      </div>
      <table class="data">
        <caption>Grants by tax year, as the same figures</caption>
        <thead>
          <tr>
            <th>Tax year</th>
            <th class="num">Grants paid</th>
            <th class="num">Amount paid</th>
            <th class="num">Approved for future</th>
          </tr>
        </thead>
        <tbody>
          {paid.map((y) => {
            const f = future.find((x) => x.tax_year === y.tax_year);
            return (
              <tr>
                <td>
                  <a href={`/funders/${p.ein}/${y.tax_year}`}>{y.tax_year}</a>
                </td>
                <td class="num">{count(y.count)}</td>
                <td class="num">{money(y.usd)}</td>
                <td class="num">{f ? money(f.usd) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
};

const RecipientLink: FC<{ name: string | null; ein: string | null; tier: Tier }> = ({
  name,
  ein,
  tier,
}) =>
  ein && tier !== "U" ? (
    <a href={`/recipients/${ein}`}>{niceName(name) || displayEin(ein)}</a>
  ) : (
    <>{niceName(name) || "(name not stated)"}</>
  );

const TopRecipients: FC<{ p: FunderPayload; rows: TopRecipient[] }> = ({ p, rows }) => (
  <section id="recipients">
    <h2>Top recipients</h2>
    <p class="hint">
      The {count(rows.length)} recipients receiving the most from {niceName(p.name)}, by grants
      paid, out of {plural(p.totals.recipient_count, "distinct recipient")}.
    </p>
    <table class="data">
      <caption>Recipients ranked by total grants paid</caption>
      <thead>
        <tr>
          <th>Recipient</th>
          <th>Match</th>
          <th>Location</th>
          <th class="num">Grants</th>
          <th class="num">Total paid</th>
          <th class="num">Latest year</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr>
            <td>
              <RecipientLink name={r.name} ein={r.ein} tier={r.tier} />
              {r.ein && r.tier !== "U" ? <span class="tag"> {displayEin(r.ein)}</span> : null}
            </td>
            <td>
              <TierBadge tier={r.tier} />
            </td>
            <td>{[niceName(r.city), r.state].filter(Boolean).join(", ")}</td>
            <td class="num">{count(r.count)}</td>
            <td class="num">{money(r.paid_usd)}</td>
            <td class="num">{r.last_tax_year ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
    <TierLegend />
  </section>
);

export const GrantsTable: FC<{ rows: GrantRow[]; caption: string; showFunder?: boolean }> = ({
  rows,
  caption,
  showFunder,
}) => (
  <table class="data">
    <caption>{caption}</caption>
    <thead>
      <tr>
        <th>Tax year</th>
        {showFunder ? <th>Funder</th> : null}
        <th>Recipient</th>
        <th>Match</th>
        <th class="num">Amount</th>
        <th>Type</th>
        <th>Purpose</th>
        <th>Source filing</th>
      </tr>
    </thead>
    <tbody>
      {rows.map((g) => (
        <tr>
          <td class="num">{g.tax_year ?? "—"}</td>
          {showFunder ? (
            <td>
              <a href={`/funders/${g.funder_ein}`}>{niceName(g.funder_name)}</a>
            </td>
          ) : null}
          <td>
            <RecipientLink name={g.recipient_name} ein={g.recipient_ein} tier={g.match_tier} />
            {g.city || g.state ? (
              <span class="tag"> {[niceName(g.city), g.state].filter(Boolean).join(", ")}</span>
            ) : null}
          </td>
          <td>
            <TierBadge tier={g.match_tier} />
          </td>
          <td class="num">
            {money(g.amount_usd)}
            {g.noncash_amount_usd ? (
              <span class="tag"> + {money(g.noncash_amount_usd)} non-cash</span>
            ) : null}
          </td>
          <td>{g.amount_type === "paid" ? "paid" : "approved, future"}</td>
          <td class="purpose">{g.purpose ?? ""}</td>
          <td class="mono">{g.object_id}</td>
        </tr>
      ))}
    </tbody>
  </table>
);

const Filings: FC<{ p: FunderPayload }> = ({ p }) => (
  <section>
    <h2>Source filings</h2>
    <p class="hint">
      Every figure above is traceable to one of these IRS e-file returns by its OBJECT_ID.
    </p>
    <table class="data">
      <caption>Returns this page is derived from</caption>
      <thead>
        <tr>
          <th>Form</th>
          <th>Tax year</th>
          <th>Period end</th>
          <th>Filed</th>
          <th>Schema</th>
          <th>IRS OBJECT_ID</th>
        </tr>
      </thead>
      <tbody>
        {p.filings.map((f) => (
          <tr>
            <td>{f.form_type}</td>
            <td class="num">{f.tax_year ?? "—"}</td>
            <td class="mono">{isoDate(f.tax_period_end)}</td>
            <td class="mono">{isoDate(f.filing_submission_date)}</td>
            <td class="mono">{f.return_version ?? ""}</td>
            <td class="mono">{f.object_id}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </section>
);

const Head: FC<{ p: FunderPayload; year?: number }> = ({ p, year }) => (
  <>
    <p class="crumbs">
      <a href="/browse">Funders</a>
      {p.state ? (
        <>
          {" › "}
          <a href={`/browse/state/${p.state}`}>{p.state}</a>
        </>
      ) : null}
      {year ? (
        <>
          {" › "}
          <a href={`/funders/${p.ein}`}>{niceName(p.name)}</a>
          {" › "}
          {year}
        </>
      ) : null}
    </p>
    <h1>
      Grants paid by {niceName(p.name)}
      {year ? `, tax year ${year}` : ""}
    </h1>
    <p class="identity">
      <span class="ein">EIN {displayEin(p.ein)}</span>
      {p.city || p.state ? ` · ${[niceName(p.city), p.state].filter(Boolean).join(", ")}` : ""}
      {" · "}
      {FORM_NAME[p.form_type] ?? p.form_type}
      {p.ntee_code ? (
        <>
          {" · NTEE "}
          <a href={`/browse/ntee/${p.ntee_code.slice(0, 1)}`}>{p.ntee_code}</a>
        </>
      ) : null}
    </p>
  </>
);

const Cards: FC<{ p: FunderPayload }> = ({ p }) => (
  <div class="grid">
    <div class="card">
      <h3>Grants paid</h3>
      <p class="big">{money(p.totals.paid_usd)}</p>
      <p>{plural(p.totals.paid_count, "grant")}</p>
    </div>
    <div class="card">
      <h3>Recipients</h3>
      <p class="big">{count(p.totals.recipient_count)}</p>
      <p>distinct organizations</p>
    </div>
    <div class="card">
      <h3>Tax years</h3>
      <p class="big">{yearSpan(p.totals.first_tax_year, p.totals.last_tax_year) || "—"}</p>
      <p>{plural(p.filings.length, "filing")} in the corpus</p>
    </div>
    {p.totals.approved_future_count > 0 ? (
      <div class="card">
        <h3>Approved for future payment</h3>
        <p class="big">{money(p.totals.approved_future_usd)}</p>
        <p>{plural(p.totals.approved_future_count, "commitment")} · not included in grants paid</p>
      </div>
    ) : null}
  </div>
);

const VerificationNote: FC = () => (
  <p class="note warn">
    Recipient matching for this dataset version has not yet completed its independent precision
    check. Tier A rows carry the EIN the filer reported; tiers B–D are the matcher's inference and
    should be read as leads until the check is published on the{" "}
    <a href="/methodology#verification">methodology page</a>.
  </p>
);

export const Funder: FC<{ payload: FunderPayload; canonical: string; origin: string }> = ({
  payload: p,
  canonical,
  origin,
}) => (
  <Page
    title={funderTitle(p)}
    description={funderSummary(p).slice(0, 300)}
    canonical={canonical}
    jsonLd={jsonLd(p, canonical, origin)}
  >
    <Head p={p} />
    <p class="summary">{funderSummary(p)}</p>
    <Cards p={p} />
    <section>
      <h2>By tax year</h2>
      <YearChart p={p} />
    </section>
    {p.top_recipients.length > 0 ? <TopRecipients p={p} rows={p.top_recipients} /> : null}
    <section id="grants">
      <h2>{p.chunked ? "Most recent grants" : "Every grant"}</h2>
      {p.chunked ? (
        <p class="hint">
          The {count(p.recent_grants.length)} most recent of{" "}
          {plural(p.totals.grant_rows, "grant row")}. Each tax year above links to its complete
          list.
        </p>
      ) : (
        <p class="hint">
          All {plural(p.totals.grant_rows, "grant row")} this organization reported, most recent
          first.
        </p>
      )}
      <GrantsTable rows={p.recent_grants} caption={`Grants reported by ${niceName(p.name)}`} />
      <TierLegend />
    </section>
    <VerificationNote />
    <Filings p={p} />
    <p class="source">
      Derived from IRS Form {p.form_type === "990PF" ? "990-PF" : "990"} e-file XML; recipient
      identities from the IRS Exempt Organizations Business Master File. Dataset version{" "}
      <code>{p.dataset_version}</code>, built {isoDate(p.built_at)}. The same rows as Parquet:{" "}
      <a href={`${DATA_URL}/${p.dataset_version}/manifest.json`}>manifest</a>; as JSON:{" "}
      <a href={`/api/funders/${p.ein}.json`}>/api/funders/{p.ein}.json</a>.
    </p>
    <Siblings ein={p.ein} />
    <Disclosure />
  </Page>
);

export const FunderYear: FC<{
  payload: FunderPayload;
  year: number;
  yearPage: YearPage | null;
  page: number;
  canonical: string;
  origin: string;
}> = ({ payload: p, year, yearPage, page, canonical, origin }) => {
  const rows = yearPage ? yearPage.grants : p.recent_grants.filter((g) => g.tax_year === year);
  const pages = yearPage?.pages ?? 1;
  const paidYear = p.years.find((y) => y.tax_year === year && y.amount_type === "paid");
  const title = `Grants paid by ${niceName(p.name)} in tax year ${year} (EIN ${displayEin(p.ein)}) — ${plural(
    paidYear?.count ?? rows.length,
    "grant",
  )}, ${compactMoney(paidYear?.usd ?? 0)}`;
  return (
    <Page
      title={title}
      description={`${niceName(p.name)} reported ${plural(paidYear?.count ?? rows.length, "grant")} paid totaling ${money(
        paidYear?.usd ?? 0,
      )} in tax year ${year}. Dataset version ${p.dataset_version}.`}
      canonical={canonical}
      jsonLd={jsonLd({ ...p, recent_grants: rows }, canonical, origin)}
    >
      <Head p={p} year={year} />
      <p class="summary">
        In tax year {year}, {niceName(p.name)} (EIN {displayEin(p.ein)}) reported{" "}
        {plural(paidYear?.count ?? rows.length, "grant")} paid totaling {money(paidYear?.usd ?? 0)}.
        Dataset version {p.dataset_version}, built {isoDate(p.built_at)}.
      </p>
      <div class="years">
        {p.years
          .filter((y) => y.amount_type === "paid" && y.tax_year != null)
          .map((y) =>
            y.tax_year === year ? (
              <span class="current">{y.tax_year}</span>
            ) : (
              <a href={`/funders/${p.ein}/${y.tax_year}`}>{y.tax_year}</a>
            ),
          )}
      </div>
      <section>
        <h2>
          Every grant, {year}
          {pages > 1 ? ` · page ${page} of ${pages}` : ""}
        </h2>
        <GrantsTable
          rows={rows}
          caption={`Grants reported by ${niceName(p.name)} for tax year ${year}`}
        />
        <TierLegend />
        {pages > 1 ? (
          <nav class="pager" aria-label="Pages">
            {page > 1 ? (
              <a href={`/funders/${p.ein}/${year}?page=${page - 1}`}>← Previous</a>
            ) : null}
            {page < pages ? (
              <a href={`/funders/${p.ein}/${year}?page=${page + 1}`}>Next →</a>
            ) : null}
          </nav>
        ) : null}
      </section>
      <VerificationNote />
      <p class="source">
        Derived from IRS Form {p.form_type === "990PF" ? "990-PF" : "990"} e-file XML. Dataset
        version <code>{p.dataset_version}</code>, built {isoDate(p.built_at)}.{" "}
        <a href={`/funders/${p.ein}`}>All years for this funder</a>.
      </p>
      <Siblings ein={p.ein} />
      <Disclosure />
    </Page>
  );
};
