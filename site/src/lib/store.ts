/**
 * The three stores, each doing the one thing it is good at (docs/hosted/architecture.md):
 * R2 for precomputed payloads, D1 for what is searched or ranked, KV for the vintage pointer.
 *
 * Nothing here writes. The ingest job writes; the Worker reads.
 *
 * In development `PAYLOAD_BASE_URL` points at a static server over `build/site/`, so
 * `wrangler dev` renders real payloads without loading half a million objects into the
 * local R2 simulation. It is never set in production; the R2 binding is the source.
 */

import type {
  FunderIndexRow,
  FunderPayload,
  RecipientIndexRow,
  RecipientPayload,
  SearchHit,
  Vintage,
  YearPage,
} from "./types";

export type Env = {
  DATA: R2Bucket;
  DB: D1Database;
  VINTAGE: KVNamespace;
  SITE_ORIGIN: string;
  DATA_PREFIX: string;
  PAYLOAD_BASE_URL: string;
  OPENGRANTS_API_KEY?: string;
};

export const VINTAGE_KEY = "current_dataset_version";
/**
 * A second KV key, set by every reseed of the index, that the cache key also carries. The
 * vintage says which dataset; the stamp says which build of the index. Flipping either
 * invalidates every rendered page without a deploy.
 */
export const STAMP_KEY = "current_dataset_stamp";

export async function currentStamp(env: Env): Promise<string> {
  return (await env.VINTAGE.get(STAMP_KEY)) ?? "";
}

/**
 * The dataset version every request renders. KV is the pointer and the cutover; D1's
 * `dataset_vintage.is_current` is the fallback so a fresh environment with a seeded index
 * and an empty KV still serves. No dataset at all is a 503, never an empty page.
 */
export async function currentVintage(env: Env): Promise<string | null> {
  const fromKv = await env.VINTAGE.get(VINTAGE_KEY);
  if (fromKv) return fromKv;
  const row = await env.DB.prepare(
    "SELECT version FROM dataset_vintage WHERE is_current = 1 ORDER BY built_at DESC LIMIT 1",
  ).first<{ version: string }>();
  return row?.version ?? null;
}

export async function vintageInfo(env: Env, version: string): Promise<Vintage | null> {
  return env.DB.prepare(
    "SELECT version, built_at, grant_rows, funder_rows, recipient_rows FROM dataset_vintage WHERE version = ?",
  )
    .bind(version)
    .first<Vintage>();
}

async function readPayload<T>(env: Env, vintage: string, key: string): Promise<T | null> {
  if (env.PAYLOAD_BASE_URL) {
    const res = await fetch(`${env.PAYLOAD_BASE_URL.replace(/\/$/, "")}/${vintage}/${key}`);
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`payload ${key}: HTTP ${res.status}`);
    return (await res.json()) as T;
  }
  const obj = await env.DATA.get(`${env.DATA_PREFIX}/${vintage}/${key}`);
  if (!obj) return null;
  return (await obj.json()) as T;
}

export async function funderIndex(env: Env, ein: string): Promise<FunderIndexRow | null> {
  return env.DB.prepare("SELECT * FROM funders WHERE ein = ?").bind(ein).first<FunderIndexRow>();
}

export async function recipientIndex(env: Env, ein: string): Promise<RecipientIndexRow | null> {
  return env.DB.prepare("SELECT * FROM recipients WHERE ein = ?")
    .bind(ein)
    .first<RecipientIndexRow>();
}

export function funderPayload(
  env: Env,
  vintage: string,
  row: FunderIndexRow,
): Promise<FunderPayload | null> {
  return readPayload<FunderPayload>(env, vintage, row.payload_key);
}

export function funderYearPage(
  env: Env,
  vintage: string,
  ein: string,
  year: number,
  page: number,
): Promise<YearPage | null> {
  return readPayload<YearPage>(env, vintage, `funders/${ein}/${year}/p${page}.json`);
}

export function recipientPayload(
  env: Env,
  vintage: string,
  row: RecipientIndexRow,
): Promise<RecipientPayload | null> {
  return readPayload<RecipientPayload>(env, vintage, row.payload_key);
}

/** FTS5 over funder and recipient names; prefix-matched on the last token so typing works. */
export async function search(env: Env, q: string, limit = 25): Promise<SearchHit[]> {
  const tokens = q
    .toUpperCase()
    .replace(/[^A-Z0-9 ]+/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 8);
  if (tokens.length === 0) return [];
  const match = tokens.map((t, i) => (i === tokens.length - 1 ? `"${t}"*` : `"${t}"`)).join(" ");
  const { results } = await env.DB.prepare(
    "SELECT ein, kind, name, city, state FROM entity_search WHERE entity_search MATCH ? " +
      "ORDER BY rank LIMIT ?",
  )
    .bind(match, limit)
    .all<SearchHit>();
  return results ?? [];
}

export async function topFunders(env: Env, limit = 12): Promise<FunderIndexRow[]> {
  const { results } = await env.DB.prepare(
    "SELECT * FROM funders ORDER BY total_paid_usd DESC LIMIT ?",
  )
    .bind(limit)
    .all<FunderIndexRow>();
  return results ?? [];
}

export const BROWSE_PAGE = 100;

export async function browseState(
  env: Env,
  state: string,
  page: number,
): Promise<{ rows: FunderIndexRow[]; total: number }> {
  const total =
    (
      await env.DB.prepare("SELECT COUNT(*) AS n FROM funders WHERE state = ?")
        .bind(state)
        .first<{ n: number }>()
    )?.n ?? 0;
  const { results } = await env.DB.prepare(
    "SELECT * FROM funders WHERE state = ? ORDER BY total_paid_usd DESC LIMIT ? OFFSET ?",
  )
    .bind(state, BROWSE_PAGE, (page - 1) * BROWSE_PAGE)
    .all<FunderIndexRow>();
  return { rows: results ?? [], total };
}

export async function browseNtee(
  env: Env,
  code: string,
  page: number,
): Promise<{ rows: FunderIndexRow[]; total: number }> {
  // NTEE codes are hierarchical: "B" is education, "B20" elementary and secondary schools.
  const like = `${code}%`;
  const total =
    (
      await env.DB.prepare("SELECT COUNT(*) AS n FROM funders WHERE ntee_code LIKE ?")
        .bind(like)
        .first<{ n: number }>()
    )?.n ?? 0;
  const { results } = await env.DB.prepare(
    "SELECT * FROM funders WHERE ntee_code LIKE ? ORDER BY total_paid_usd DESC LIMIT ? OFFSET ?",
  )
    .bind(like, BROWSE_PAGE, (page - 1) * BROWSE_PAGE)
    .all<FunderIndexRow>();
  return { rows: results ?? [], total };
}

export async function states(env: Env): Promise<Array<{ state: string; n: number }>> {
  const { results } = await env.DB.prepare(
    "SELECT state, COUNT(*) AS n FROM funders WHERE state IS NOT NULL GROUP BY state ORDER BY state",
  ).all<{ state: string; n: number }>();
  return results ?? [];
}

/** A sitemap chunk, proxied from R2 (or the dev server) as stored: gzipped XML. */
export async function sitemapObject(
  env: Env,
  vintage: string,
  name: string,
): Promise<Response | null> {
  const key = `sitemaps/${name}`;
  if (env.PAYLOAD_BASE_URL) {
    const res = await fetch(`${env.PAYLOAD_BASE_URL.replace(/\/$/, "")}/${vintage}/${key}`);
    return res.ok ? res : null;
  }
  const obj = await env.DATA.get(`${env.DATA_PREFIX}/${vintage}/${key}`);
  if (!obj) return null;
  return new Response(obj.body, {
    headers: {
      "content-type": name.endsWith(".gz") ? "application/gzip" : "application/xml",
    },
  });
}
