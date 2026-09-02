/**
 * funders.opengrants.io - one page per grantmaking organization, rendered at the edge.
 *
 * The request path, from docs/hosted/architecture.md:
 *
 *   normalize the EIN (301 if the request form was not canonical)
 *   -> vintage from KV
 *   -> Cache API keyed on `${url}?v=${vintage}` (a KV flip invalidates the whole site)
 *   -> D1 for the index row (404 page if absent)
 *   -> R2 for the precomputed payload
 *   -> full HTML, JSON-LD included, Cache-Control: 7 days, stale-while-revalidate a day
 *
 * No client-side data fetching, no session, no write path. If it needs a session it has
 * become grantdesk.
 */

import { Hono } from "hono";
import type { Context } from "hono";
import { canonicalEin, isCanonical } from "./lib/ein";
import {
  BROWSE_PAGE,
  type Env,
  browseNtee,
  browseState,
  currentVintage,
  funderIndex,
  funderPayload,
  funderYearPage,
  recipientIndex,
  recipientPayload,
  search,
  sitemapObject,
  states,
  topFunders,
  vintageInfo,
} from "./lib/store";
import { Browse, BrowseIndex } from "./views/browse";
import { DataIndex, type DatasetVersion } from "./views/dataindex";
import { About, DataPage, Methodology } from "./views/docs";
import { Funder, FunderYear } from "./views/funder";
import { Landing } from "./views/landing";
import { NoDataset, NotFound } from "./views/notfound";
import { Recipient } from "./views/recipient";
import { Search } from "./views/search";
import { llmsTxt, robotsTxt } from "./views/text";

type Bindings = { Bindings: Env };
const app = new Hono<Bindings>();

/**
 * data.opengrants.io is an R2 custom domain: objects only, so its root is a 404. This Worker
 * also holds routes for that hostname's root and the product prefix roots, and renders an
 * index from the bucket. Every other path on that host never reaches this code.
 */
const DATA_HOST = "data.opengrants.io";

async function datasetVersions(
  env: Env,
): Promise<{ versions: DatasetVersion[]; latest: DatasetVersion | null }> {
  const listed = await env.DATA.list({ prefix: `${env.DATA_PREFIX}/`, delimiter: "/" });
  const names = (listed.delimitedPrefixes ?? [])
    .map((p) => p.slice(env.DATA_PREFIX.length + 1).replace(/\/$/, ""))
    .filter((n) => /^\d{4}\.\d{2}\.\d+$/.test(n))
    .sort()
    .reverse();
  const read = async (version: string): Promise<DatasetVersion> => {
    const obj = await env.DATA.get(`${env.DATA_PREFIX}/${version}/manifest.json`);
    return { version, manifest: obj ? ((await obj.json()) as DatasetVersion["manifest"]) : null };
  };
  const versions = await Promise.all(names.map(read));
  const latestObj = await env.DATA.get(`${env.DATA_PREFIX}/latest/manifest.json`);
  const latest = latestObj
    ? { version: "latest", manifest: (await latestObj.json()) as DatasetVersion["manifest"] }
    : null;
  return { versions, latest };
}

app.use("*", async (c, next) => {
  if (new URL(c.req.url).hostname !== DATA_HOST) return next();
  const { versions, latest } = await datasetVersions(c.env);
  return c.html(<DataIndex versions={versions} latest={latest} />, 200, {
    "cache-control": "public, max-age=3600, stale-while-revalidate=86400",
  });
});

const WEEK = 604_800;
const DAY = 86_400;
const MINUTE = 60;
// Bump on any change to what a cached page contains that the vintage does not capture (a
// template change, a payload layout change). The vintage handles data; this handles code.
const CACHE_EPOCH = "2";

function cacheControl(seconds = WEEK): string {
  return `public, max-age=${seconds}, stale-while-revalidate=${DAY}`;
}

/**
 * Render through the Cache API with the vintage in the key. A hit returns immediately and
 * revalidates in the background; a miss renders, stores, and returns.
 */
async function cached(
  c: Context<Bindings>,
  vintage: string,
  render: () => Promise<Response>,
): Promise<Response> {
  const url = new URL(c.req.url);
  const key = new Request(
    `${url.origin}${url.pathname}${url.search}?v=${vintage}&e=${CACHE_EPOCH}`,
  );
  const cache = caches.default;
  const hit = await cache.match(key);
  if (hit) return hit;
  const res = await render();
  if (res.status === 200) {
    c.executionCtx.waitUntil(cache.put(key, res.clone()));
  }
  return res;
}

async function requireVintage(c: Context<Bindings>): Promise<string | Response> {
  const v = await currentVintage(c.env);
  if (!v) {
    return c.html(<NoDataset />, 503, { "cache-control": "no-store" });
  }
  return v;
}

function origin(c: Context<Bindings>): string {
  return c.env.SITE_ORIGIN || new URL(c.req.url).origin;
}

/** Normalize any EIN form; returns the canonical EIN or a 301/404 response. */
function einOr(
  c: Context<Bindings>,
  raw: string,
  path: (ein: string) => string,
): string | Response | Promise<Response> {
  const ein = canonicalEin(raw);
  if (!ein) return c.notFound();
  if (!isCanonical(raw)) return c.redirect(path(ein), 301);
  return ein;
}

// --- pages ---------------------------------------------------------------------------------

app.get("/", async (c) => {
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  return cached(c, v, async () => {
    const [info, top, st] = await Promise.all([
      vintageInfo(c.env, v),
      topFunders(c.env, 12),
      states(c.env),
    ]);
    return c.html(
      <Landing canonical={`${origin(c)}/`} vintage={info} top={top} states={st} />,
      200,
      {
        "cache-control": cacheControl(),
      },
    );
  });
});

app.get("/funders/:ein", async (c) => {
  const ein = einOr(c, c.req.param("ein"), (e) => `/funders/${e}`);
  if (typeof ein !== "string") return ein;
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  return cached(c, v, async () => {
    const row = await funderIndex(c.env, ein);
    const payload = row ? await funderPayload(c.env, v, row) : null;
    if (!row || !payload) {
      return c.html(<NotFound ein={ein} kind="funder" />, 404, {
        "cache-control": cacheControl(MINUTE),
      });
    }
    return c.html(
      <Funder payload={payload} canonical={`${origin(c)}/funders/${ein}`} origin={origin(c)} />,
      200,
      {
        "cache-control": cacheControl(),
      },
    );
  });
});

// A slug or anything else after the EIN redirects to the canonical page.
app.get("/funders/:ein/:rest{[a-zA-Z][a-zA-Z0-9-]*}", (c) => {
  const ein = canonicalEin(c.req.param("ein"));
  if (!ein) return c.notFound();
  const rest = c.req.param("rest");
  if (rest === "recipients") return c.redirect(`/funders/${ein}#recipients`, 301);
  return c.redirect(`/funders/${ein}`, 301);
});

app.get("/funders/:ein/:year{[0-9]{4}}", async (c) => {
  const ein = einOr(c, c.req.param("ein"), (e) => `/funders/${e}/${c.req.param("year")}`);
  if (typeof ein !== "string") return ein;
  const year = Number(c.req.param("year"));
  const page = Math.max(1, Number(c.req.query("page") ?? "1") || 1);
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  return cached(c, v, async () => {
    const row = await funderIndex(c.env, ein);
    const payload = row ? await funderPayload(c.env, v, row) : null;
    if (!row || !payload) {
      return c.html(<NotFound ein={ein} kind="funder" />, 404, {
        "cache-control": cacheControl(MINUTE),
      });
    }
    const yearPage = payload.chunked ? await funderYearPage(c.env, v, ein, year, page) : null;
    const hasYear = payload.years.some((y) => y.tax_year === year);
    if (!hasYear || (payload.chunked && !yearPage)) {
      return c.html(<NotFound ein={ein} kind="year" year={year} />, 404, {
        "cache-control": cacheControl(MINUTE),
      });
    }
    return c.html(
      <FunderYear
        payload={payload}
        year={year}
        yearPage={yearPage}
        page={page}
        canonical={`${origin(c)}/funders/${ein}/${year}${page > 1 ? `?page=${page}` : ""}`}
        origin={origin(c)}
      />,
      200,
      { "cache-control": cacheControl() },
    );
  });
});

app.get("/recipients/:ein", async (c) => {
  const ein = einOr(c, c.req.param("ein"), (e) => `/recipients/${e}`);
  if (typeof ein !== "string") return ein;
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  return cached(c, v, async () => {
    const row = await recipientIndex(c.env, ein);
    const payload = row ? await recipientPayload(c.env, v, row) : null;
    if (!row || !payload) {
      // An organization that is a funder but not a resolved recipient: point there.
      const asFunder = await funderIndex(c.env, ein);
      return c.html(
        <NotFound
          ein={ein}
          kind="recipient"
          funderExists={Boolean(asFunder)}
          indexed={Boolean(row)}
        />,
        404,
        {
          "cache-control": cacheControl(DAY),
        },
      );
    }
    return c.html(
      <Recipient
        payload={payload}
        canonical={`${origin(c)}/recipients/${ein}`}
        origin={origin(c)}
      />,
      200,
      { "cache-control": cacheControl() },
    );
  });
});

app.get("/recipients/:ein/:rest{.+}", (c) => {
  const ein = canonicalEin(c.req.param("ein"));
  return ein ? c.redirect(`/recipients/${ein}`, 301) : c.notFound();
});

app.get("/search", async (c) => {
  const q = (c.req.query("q") ?? "").trim().slice(0, 120);
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  // An EIN typed into the box goes straight to the page.
  const digits = q.replace(/\D/g, "");
  if (digits.length === 9 && /^[\d-]+$/.test(q)) {
    const funder = await funderIndex(c.env, digits);
    if (funder) return c.redirect(`/funders/${digits}`, 302);
    const recipient = await recipientIndex(c.env, digits);
    if (recipient) return c.redirect(`/recipients/${digits}`, 302);
  }
  return cached(c, v, async () => {
    const hits = q ? await search(c.env, q, 50) : [];
    return c.html(<Search q={q} hits={hits} canonical={`${origin(c)}/search`} />, 200, {
      "cache-control": cacheControl(DAY),
    });
  });
});

app.get("/browse", async (c) => {
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  return cached(c, v, async () => {
    const st = await states(c.env);
    return c.html(<BrowseIndex states={st} canonical={`${origin(c)}/browse`} />, 200, {
      "cache-control": cacheControl(),
    });
  });
});

app.get("/browse/state/:code", async (c) => {
  const code = c.req.param("code").toUpperCase().slice(0, 2);
  if (code !== c.req.param("code")) return c.redirect(`/browse/state/${code}`, 301);
  const page = Math.max(1, Number(c.req.query("page") ?? "1") || 1);
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  return cached(c, v, async () => {
    const { rows, total } = await browseState(c.env, code, page);
    if (rows.length === 0)
      return c.html(<NotFound kind="browse" />, 404, { "cache-control": cacheControl(MINUTE) });
    return c.html(
      <Browse
        kind="state"
        code={code}
        rows={rows}
        total={total}
        page={page}
        perPage={BROWSE_PAGE}
        canonical={`${origin(c)}/browse/state/${code}${page > 1 ? `?page=${page}` : ""}`}
      />,
      200,
      { "cache-control": cacheControl() },
    );
  });
});

app.get("/browse/ntee/:code", async (c) => {
  const code = c.req.param("code").toUpperCase().slice(0, 3);
  if (code !== c.req.param("code")) return c.redirect(`/browse/ntee/${code}`, 301);
  const page = Math.max(1, Number(c.req.query("page") ?? "1") || 1);
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  return cached(c, v, async () => {
    const { rows, total } = await browseNtee(c.env, code, page);
    if (rows.length === 0)
      return c.html(<NotFound kind="browse" />, 404, { "cache-control": cacheControl(MINUTE) });
    return c.html(
      <Browse
        kind="ntee"
        code={code}
        rows={rows}
        total={total}
        page={page}
        perPage={BROWSE_PAGE}
        canonical={`${origin(c)}/browse/ntee/${code}${page > 1 ? `?page=${page}` : ""}`}
      />,
      200,
      { "cache-control": cacheControl() },
    );
  });
});

app.get("/data", async (c) => {
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  return cached(c, v, async () => {
    const info = await vintageInfo(c.env, v);
    return c.html(<DataPage vintage={info} version={v} canonical={`${origin(c)}/data`} />, 200, {
      "cache-control": cacheControl(),
    });
  });
});

app.get("/methodology", (c) =>
  c.html(<Methodology canonical={`${origin(c)}/methodology`} />, 200, {
    "cache-control": cacheControl(),
  }),
);
app.get("/about", (c) =>
  c.html(<About canonical={`${origin(c)}/about`} />, 200, { "cache-control": cacheControl() }),
);

// --- machine endpoints ---------------------------------------------------------------------

app.get("/api/funders/:file{[0-9-]+\\.json}", async (c) => {
  const ein = canonicalEin(c.req.param("file").replace(/\.json$/, ""));
  if (!ein) return c.notFound();
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  const row = await funderIndex(c.env, ein);
  const payload = row ? await funderPayload(c.env, v, row) : null;
  if (!payload) return c.json({ error: "not found", ein }, 404);
  return c.json(payload, 200, {
    "cache-control": cacheControl(),
    "access-control-allow-origin": "*",
  });
});

app.get("/sitemap.xml", async (c) => {
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  const res = await sitemapObject(c.env, v, "sitemap-index.xml");
  if (!res) return c.notFound();
  return new Response(res.body, {
    headers: { "content-type": "application/xml", "cache-control": cacheControl(DAY) },
  });
});

app.get("/sitemaps/:name{[a-z]+-[0-9]+\\.xml\\.gz}", async (c) => {
  const v = await requireVintage(c);
  if (v instanceof Response) return v;
  const res = await sitemapObject(c.env, v, c.req.param("name"));
  if (!res) return c.notFound();
  return new Response(res.body, {
    headers: { "content-type": "application/gzip", "cache-control": cacheControl(DAY) },
  });
});

app.get("/llms.txt", async (c) => {
  const v = await currentVintage(c.env);
  return c.text(llmsTxt(origin(c), v), 200, { "cache-control": cacheControl(DAY) });
});

app.get("/robots.txt", (c) =>
  c.text(robotsTxt(origin(c)), 200, { "cache-control": cacheControl(DAY) }),
);

app.notFound((c) =>
  c.html(<NotFound kind="page" />, 404, { "cache-control": cacheControl(MINUTE) }),
);

export default app;
