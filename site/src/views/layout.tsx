/**
 * Page shell and the whole design system.
 *
 * The tokens, the type scale and the header/footer shape are grantcheck's, so the two sites
 * read as one program. The CSS is inlined and hand-written, under 15 KB, with no web fonts
 * and no client framework: these pages exist to be read by crawlers and language models, and
 * every byte between them and the facts is cost. No layout shift, because the page is fully
 * formed at first paint. Zero client-side JavaScript.
 */

import type { FC, PropsWithChildren } from "hono/jsx";

export const SITE = "funders.opengrants.io";
export const REPO = "https://github.com/egeria-corporation/funder-graph";
export const DATA_URL = "https://data.opengrants.io/funder-graph";

export const DISCLOSURE =
  "This is informational only, derived from public data on the dates shown. It is not an " +
  "eligibility determination, and not legal, tax, or accounting advice. Verify against the " +
  "official source before relying on it.";

const CSS = `
:root {
  --ink: #14171a;
  --ink-soft: #4a5057;
  --ink-faint: #6b7280;
  --line: #e3e6ea;
  --line-soft: #eef1f4;
  --paper: #ffffff;
  --paper-tint: #f7f9fb;
  --accent: #12594a;
  --accent-soft: #e6f2ee;
  --pass: #1a7f5a;
  --warn: #9a6212;
  --warn-soft: #fdf5e6;
  --fail: #a32020;
  --fail-soft: #fdeeee;
  --muted: #7b8794;
  --bar: #2f8f73;
  --radius: 10px;
  --measure: 68ch;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ink: #e8eaed; --ink-soft: #b3b9c0; --ink-faint: #8b939c;
    --line: #2a2f36; --line-soft: #21252b;
    --paper: #14171a; --paper-tint: #1a1e23;
    --accent: #6fd3b4; --accent-soft: #16302a;
    --pass: #5fc99b; --warn: #e0ac54; --warn-soft: #2c2416;
    --fail: #e88b8b; --fail-soft: #2e1c1c; --muted: #8b939c; --bar: #4fb992;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; font-family: var(--font); font-size: 17px; line-height: 1.65; color: var(--ink); background: var(--paper); -webkit-font-smoothing: antialiased; }
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
a:hover { text-decoration-thickness: 2px; }
code, .mono { font-family: var(--mono); font-size: 0.92em; }
.wrap { max-width: 1040px; margin: 0 auto; padding: 0 24px; }
.prose { max-width: var(--measure); }
header.site { border-bottom: 1px solid var(--line); padding: 18px 0; font-size: 15px; }
header.site .wrap { display: flex; gap: 20px; align-items: baseline; flex-wrap: wrap; }
header.site .brand { font-weight: 650; color: var(--ink); text-decoration: none; letter-spacing: -0.01em; }
header.site nav { margin-left: auto; display: flex; gap: 20px; }
header.site nav a { color: var(--ink-soft); text-decoration: none; }
header.site nav a:hover { color: var(--accent); text-decoration: underline; }
h1 { font-size: 2.1rem; line-height: 1.2; letter-spacing: -0.02em; margin: 0 0 12px; font-weight: 680; }
h2 { font-size: 1.35rem; line-height: 1.3; letter-spacing: -0.01em; margin: 40px 0 12px; font-weight: 650; }
h3 { font-size: 1.05rem; margin: 28px 0 8px; font-weight: 650; }
p { margin: 0 0 16px; }
.lede { font-size: 1.17rem; line-height: 1.55; color: var(--ink-soft); }
.hero { padding: 64px 0 8px; }
.hero h1 { font-size: 2.7rem; max-width: 22ch; }
@media (max-width: 600px) { .hero { padding: 40px 0 4px; } .hero h1 { font-size: 2rem; } }
form.search { display: flex; gap: 10px; margin: 28px 0 10px; flex-wrap: wrap; }
form.search input { flex: 1 1 320px; font: inherit; padding: 13px 15px; border: 1.5px solid var(--line); border-radius: var(--radius); background: var(--paper); color: var(--ink); }
form.search input:focus { outline: 2px solid var(--accent); outline-offset: 1px; border-color: transparent; }
form.search button { font: inherit; font-weight: 600; padding: 13px 24px; border: 0; border-radius: var(--radius); background: var(--accent); color: #fff; cursor: pointer; }
@media (prefers-color-scheme: dark) { form.search button { color: #0c1a16; } }
form.search button:hover { filter: brightness(1.08); }
.hint { font-size: 14px; color: var(--muted); margin: 0; }
.grid { display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); margin: 24px 0; }
.card { border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 20px; background: var(--paper-tint); }
.card h3 { margin: 0 0 6px; font-size: 0.98rem; }
.card p { margin: 0; font-size: 15px; color: var(--ink-soft); }
.card .big { font-size: 1.6rem; font-weight: 680; letter-spacing: -0.02em; color: var(--ink); line-height: 1.2; margin: 2px 0 4px; }
.identity { color: var(--ink-soft); font-size: 15px; margin: 0 0 8px; }
.identity .ein { font-family: var(--mono); }
.summary { font-size: 1.08rem; line-height: 1.6; max-width: 76ch; border-left: 3px solid var(--accent); padding: 4px 0 4px 18px; margin: 22px 0 8px; }
.source { font-size: 14px; color: var(--muted); margin: 8px 0 0; max-width: 80ch; }
.source code { font-size: 0.9em; }
table.data { width: 100%; border-collapse: collapse; margin: 8px 0 4px; font-size: 15px; }
table.data caption { text-align: left; font-size: 14px; color: var(--muted); padding: 0 0 8px; caption-side: top; }
table.data th { text-align: left; font-size: 12.5px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 600; padding: 12px 10px 8px 0; border-bottom: 1px solid var(--line); }
table.data td { padding: 10px 10px 10px 0; border-bottom: 1px solid var(--line-soft); vertical-align: top; }
table.data th.num, table.data td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; padding-right: 0; }
table.data td.mono { font-family: var(--mono); font-size: 13.5px; color: var(--ink-soft); white-space: nowrap; }
table.data td.purpose { color: var(--ink-soft); font-size: 14.5px; max-width: 46ch; }
table.data tr:hover td { background: var(--paper-tint); }
.tier { display: inline-block; font-family: var(--mono); font-size: 11.5px; font-weight: 650; letter-spacing: 0.04em; padding: 2px 7px; border-radius: 999px; vertical-align: 1px; text-decoration: none; }
.tier-a { background: var(--accent-soft); color: var(--pass); }
.tier-b { background: var(--accent-soft); color: var(--pass); }
.tier-c { background: var(--warn-soft); color: var(--warn); }
.tier-d { background: var(--warn-soft); color: var(--warn); }
.tier-u { background: var(--line-soft); color: var(--muted); }
.legend { font-size: 14px; color: var(--muted); margin: 10px 0 0; }
.legend .tier { margin-right: 4px; }
.chart { display: grid; grid-template-columns: max-content 1fr max-content; gap: 6px 14px; align-items: center; margin: 12px 0 4px; font-size: 14.5px; max-width: 720px; }
.chart .y { font-family: var(--mono); color: var(--ink-soft); }
.chart .bar { height: 14px; background: var(--bar); border-radius: 3px; min-width: 2px; }
.chart .v { font-variant-numeric: tabular-nums; text-align: right; }
.years { display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0 4px; font-size: 14.5px; }
.years a, .years span { padding: 5px 11px; border: 1px solid var(--line); border-radius: 999px; text-decoration: none; color: var(--ink-soft); }
.years a:hover { border-color: var(--accent); color: var(--accent); }
.years span.current { background: var(--accent-soft); color: var(--pass); border-color: transparent; font-weight: 600; }
.pager { display: flex; gap: 16px; margin: 18px 0; font-size: 15px; }
.note { border-left: 3px solid var(--line); padding: 2px 0 2px 16px; color: var(--ink-soft); font-size: 15px; margin: 20px 0; max-width: 76ch; }
.note.warn { border-color: var(--warn); }
.disclosure { border-top: 1px solid var(--line); margin-top: 44px; padding-top: 18px; font-size: 14px; color: var(--muted); max-width: 70ch; }
.siblings { font-size: 14.5px; color: var(--muted); margin: 26px 0 0; }
.siblings a { color: var(--ink-soft); }
footer.site { border-top: 1px solid var(--line); margin-top: 56px; padding: 28px 0 56px; font-size: 14.5px; color: var(--muted); }
footer.site .wrap { display: flex; gap: 24px; flex-wrap: wrap; align-items: baseline; }
footer.site nav { display: flex; gap: 18px; flex-wrap: wrap; }
footer.site a { color: var(--ink-soft); }
footer.site .credits { flex-basis: 100%; font-size: 13.5px; max-width: 90ch; }
.spacer { margin-left: auto; }
ul.plain { list-style: none; padding: 0; margin: 16px 0; }
ul.plain li { padding: 7px 0; border-bottom: 1px solid var(--line-soft); }
ul.plain li .meta { color: var(--muted); font-size: 14px; }
.tag { font-family: var(--mono); font-size: 13px; color: var(--muted); }
pre.sql { background: #0f1419; color: #d7dde3; border-radius: var(--radius); padding: 18px 20px; overflow-x: auto; font-family: var(--mono); font-size: 13.5px; line-height: 1.6; margin: 18px 0; border: 1px solid #232a31; white-space: pre; }
dl.facts { display: grid; grid-template-columns: max-content 1fr; gap: 6px 18px; font-size: 15px; margin: 12px 0; }
dl.facts dt { color: var(--muted); }
dl.facts dd { margin: 0; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.crumbs { font-size: 14px; color: var(--muted); margin: 26px 0 6px; }
.crumbs a { color: var(--ink-soft); }
.notfound { padding: 60px 0 20px; }
`;

export type HeadProps = {
  title: string;
  description: string;
  canonical?: string;
  noindex?: boolean;
  jsonLd?: unknown;
};

export const Page: FC<PropsWithChildren<HeadProps>> = ({
  title,
  description,
  canonical,
  noindex,
  jsonLd,
  children,
}) => (
  <html lang="en">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>{title}</title>
      <meta name="description" content={description} />
      {canonical ? <link rel="canonical" href={canonical} /> : null}
      {noindex ? <meta name="robots" content="noindex, follow" /> : null}
      <meta property="og:title" content={title} />
      <meta property="og:description" content={description} />
      <meta property="og:type" content="website" />
      {canonical ? <meta property="og:url" content={canonical} /> : null}
      <meta name="twitter:card" content="summary" />
      {/* biome-ignore lint/security/noDangerouslySetInnerHtml: an authored constant, never
          user input; a stylesheet has to be injected as raw text to be a stylesheet. */}
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      {jsonLd ? (
        <script
          type="application/ld+json"
          // biome-ignore lint/security/noDangerouslySetInnerHtml: JSON-LD must be raw.
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, "\\u003c") }}
        />
      ) : null}
    </head>
    <body>
      <header class="site">
        <div class="wrap">
          <a class="brand" href="/">
            funder-graph
          </a>
          <nav>
            <a href="/search">Search</a>
            <a href="/browse">Browse</a>
            <a href="/data">Data</a>
            <a href="/methodology">Methodology</a>
            <a href={REPO}>Source</a>
          </nav>
        </div>
      </header>
      <main class="wrap">{children}</main>
      <footer class="site">
        <div class="wrap">
          <span>
            Built by Egeria Corporation · sponsored by{" "}
            <a href="https://opengrants.io">OpenGrants</a>
          </span>
          <nav class="spacer">
            <a href={REPO}>Open source</a>
            <a href="/data">Dataset</a>
            <a href="/llms.txt">llms.txt</a>
            <a href="/about">About</a>
          </nav>
          <span class="credits">
            Apache License 2.0. Derived from IRS Form 990 and 990-PF e-file XML and the IRS Exempt
            Organizations Business Master File. Field mapping via the IRS E-file Master Concordance
            File by the Nonprofit Open Data Collective; the e-file corpus is stewarded by
            GivingTuesday's data commons. Grants to named individuals are never shown.
          </span>
        </div>
      </footer>
    </body>
  </html>
);

export const Disclosure: FC = () => <p class="disclosure">{DISCLOSURE}</p>;

/** Links to the same EIN on the sibling sites. Five sites that reference each other read as one. */
export const Siblings: FC<{ ein: string }> = ({ ein }) => (
  <p class="siblings">
    The same organization elsewhere in the program:{" "}
    <a href={`https://check.opengrants.io/ein/${ein}`}>exempt status and filing health</a> ·{" "}
    <a href={`https://awards.opengrants.io/${ein}`}>federal awards</a> ·{" "}
    <a href="https://answers.opengrants.io">grant guidance</a> ·{" "}
    <a href="https://opengrants.io">open opportunities</a>.
  </p>
);
