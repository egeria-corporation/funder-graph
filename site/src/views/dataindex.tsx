/**
 * The index page for data.opengrants.io.
 *
 * An R2 custom domain serves objects and nothing else: `/` is a 404 while every published
 * object under it is a 200. This Worker takes the root of that hostname (and the product
 * prefix roots) and renders what is there, read from the same bucket, so a person landing on
 * the data domain sees the datasets, their versions and manifests, and a query to paste.
 */

import type { FC } from "hono/jsx";
import { count, isoDate } from "../lib/format";
import { Disclosure, Page, REPO } from "./layout";

export type DatasetVersion = {
  version: string;
  manifest: {
    dataset_version: string;
    generated_at: string;
    concordance_version?: string | null;
    bmf_vintage?: string | null;
    license?: string;
    filing_years?: number[];
    rows?: Record<string, number>;
    match_tiers?: Record<string, number>;
    files?: Array<{ path: string; bytes: number; rows: number; filing_year: number }>;
  } | null;
};

const DATA_ORIGIN = "https://data.opengrants.io";

export const DataIndex: FC<{ versions: DatasetVersion[]; latest: DatasetVersion | null }> = ({
  versions,
  latest,
}) => {
  const current = latest?.manifest ?? versions.find((v) => v.manifest)?.manifest ?? null;
  const base = `${DATA_ORIGIN}/funder-graph/${latest ? "latest" : (current?.dataset_version ?? "latest")}`;
  return (
    <Page
      title="data.opengrants.io — open datasets from the OpenGrants program"
      description="Public datasets published by Egeria Corporation and OpenGrants as Parquet with checksummed manifests: funder-graph, the open 990 funding graph. Query over HTTPS from DuckDB with nothing installed."
      canonical={`${DATA_ORIGIN}/`}
    >
      <section class="hero" style="padding-top:40px">
        <h1>Open datasets, served as objects</h1>
        <p class="lede prose">
          Everything under this domain is a file in a public bucket: Parquet you can query over
          HTTPS with range requests, JSON manifests with sizes and SHA-256 checksums, and nothing
          that needs an account. There is no listing API; each dataset's manifest is the list.
        </p>
      </section>

      <section>
        <h2>funder-graph — the open 990 funding graph</h2>
        <p class="prose">
          Every grant a US foundation or grantmaking charity reported paying on Form 990-PF or Form
          990 Schedule I, with recipients resolved to IRS-listed organizations and a match tier on
          every row. Rendered as pages at{" "}
          <a href="https://funders.opengrants.io">funders.opengrants.io</a>; source and schema at{" "}
          <a href={REPO}>github.com/egeria-corporation/funder-graph</a>.
        </p>
        {current ? (
          <dl class="facts">
            <dt>Current version</dt>
            <dd>
              <code>{current.dataset_version}</code> · built {isoDate(current.generated_at)}
              {latest ? (
                <>
                  {" · "}
                  <a href={`${DATA_ORIGIN}/funder-graph/latest/manifest.json`}>
                    latest/manifest.json
                  </a>
                </>
              ) : null}
            </dd>
            {current.rows ? (
              <>
                <dt>Grant rows</dt>
                <dd>{count(current.rows.total)}</dd>
              </>
            ) : null}
            {current.filing_years ? (
              <>
                <dt>Filing years</dt>
                <dd>{current.filing_years.join(", ")}</dd>
              </>
            ) : null}
            <dt>License</dt>
            <dd>{current.license ?? "Apache-2.0"}</dd>
          </dl>
        ) : (
          <p class="note">
            No dataset version is published under <code>/funder-graph/</code> yet. The hosted site
            renders from precomputed payloads; the Parquet release follows the precision check
            described on{" "}
            <a href="https://funders.opengrants.io/methodology#verification">
              the methodology page
            </a>
            .
          </p>
        )}
        {versions.length > 0 ? (
          <>
            <h3>Versions</h3>
            <ul class="plain">
              {versions.map((v) => (
                <li>
                  <a href={`${DATA_ORIGIN}/funder-graph/${v.version}/manifest.json`}>
                    <code>{v.version}</code>
                  </a>{" "}
                  <span class="meta">
                    {v.manifest
                      ? `built ${isoDate(v.manifest.generated_at)}${v.manifest.rows ? ` · ${count(v.manifest.rows.total)} rows` : ""}`
                      : "no manifest"}
                  </span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {current?.files && current.files.length > 0 ? (
          <>
            <h3>Query it</h3>
            <p class="prose">
              One object per filing year, listed explicitly, because HTTP has no directory listing.
              Paste into DuckDB (or <a href="https://shell.duckdb.org">shell.duckdb.org</a>):
            </p>
            <pre class="sql">{`INSTALL httpfs; LOAD httpfs;
SELECT funder_name, recipient_name_raw, amount_usd, tax_year
FROM read_parquet(
  [ ${current.files.map((f) => `'${base}/${f.path}'`).join(",\n    ")} ],
  hive_partitioning = 1
)
WHERE amount_type = 'paid'
ORDER BY amount_usd DESC
LIMIT 25;`}</pre>
          </>
        ) : null}
      </section>

      <section>
        <h2>Conventions</h2>
        <div class="prose">
          <p>
            Paths are <code>/&lt;dataset&gt;/&lt;version&gt;/…</code>. Versions are{" "}
            <code>YYYY.MM.PATCH</code>; <code>latest/</code> is a copy of the current version and
            moves. Pin a version for anything you will be asked to reproduce. Every object supports
            HTTP range requests, so a Parquet reader pulls only the row groups it needs.
          </p>
          <p>
            A health check lives at{" "}
            <a href={`${DATA_ORIGIN}/funder-graph/healthcheck.txt`}>
              /funder-graph/healthcheck.txt
            </a>
            .
          </p>
        </div>
      </section>
      <Disclosure />
    </Page>
  );
};
