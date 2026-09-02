/**
 * The site against the launch checklist, on a fixture payload: every fact in the HTML, the
 * markup a model needs, no placeholders, the two amounts never summed, canonical redirects.
 */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import app, { cacheKey } from "../src/index";
import { canonicalEin, displayEin, isCanonical } from "../src/lib/ein";
import { compactMoney, money, niceName, yearSpan } from "../src/lib/format";
import type { Env } from "../src/lib/store";
import type { FunderPayload } from "../src/lib/types";
import { Funder, FunderYear, funderSummary, funderTitle } from "../src/views/funder";
import { llmsTxt } from "../src/views/text";

const payload = JSON.parse(
  readFileSync(new URL("./fixtures/funder.json", import.meta.url), "utf-8"),
) as FunderPayload;

const ORIGIN = "https://funders.opengrants.io";

function html(node: unknown): string {
  return String(node);
}

/** An Env whose KV answers the vintage and whose D1 is never reached. */
function env(overrides: Partial<Env> = {}): Env {
  const unreachable = () => {
    throw new Error("D1 must not be touched by this route");
  };
  return {
    DATA: {} as R2Bucket,
    DB: { prepare: unreachable } as unknown as D1Database,
    VINTAGE: { get: async () => "2026.09.0" } as unknown as KVNamespace,
    SITE_ORIGIN: ORIGIN,
    DATA_PREFIX: "funder-graph",
    PAYLOAD_BASE_URL: "",
    ...overrides,
  };
}

describe("EIN canonical form", () => {
  it("normalizes every written form to nine digits", () => {
    expect(canonicalEin("94-1156365")).toBe("941156365");
    expect(canonicalEin("941156365")).toBe("941156365");
    expect(canonicalEin("1156365")).toBe("001156365"); // a spreadsheet dropped the zeros
    expect(canonicalEin("")).toBeNull();
    expect(canonicalEin("1234567890")).toBeNull();
    expect(isCanonical("941156365")).toBe(true);
    expect(isCanonical("94-1156365")).toBe(false);
    expect(displayEin("941156365")).toBe("94-1156365");
  });
});

describe("formatting", () => {
  it("renders full and compact money, and never a bare NaN", () => {
    expect(money(2147500)).toBe("$2,147,500");
    expect(compactMoney(2147500)).toBe("$2.1M");
    expect(compactMoney(9987178460)).toBe("$10.0B");
    expect(money(null)).toBe("—");
    expect(yearSpan(2019, 2025)).toBe("2019–2025");
    expect(yearSpan(2022, 2022)).toBe("2022");
    expect(niceName("BOYS AND GIRLS CLUB OF SACRAMENTO")).toBe("Boys and Girls Club of Sacramento");
  });
});

describe("the funder page", () => {
  const page = html(Funder({ payload, canonical: `${ORIGIN}/funders/846725611`, origin: ORIGIN }));

  it("puts every grant row and every source filing in the initial HTML", () => {
    for (const g of payload.recent_grants) {
      expect(page).toContain(g.object_id);
      expect(page).toContain(money(g.amount_usd));
    }
    expect(page).toContain("Iowa State University Alumni Association");
    expect(page).toContain("2022v5.0");
    expect(page).toContain(
      '<link rel="canonical" href="https://funders.opengrants.io/funders/846725611"',
    );
  });

  it("opens with a quotable summary carrying name, EIN, totals, form and vintage", () => {
    const s = funderSummary(payload);
    expect(s).toContain("Broderick Charitable Foundation Trust");
    expect(s).toContain("EIN 84-6725611");
    expect(s).toContain("4 grants paid totaling $50,000");
    expect(s).toContain("Form 990-PF, Part XV");
    expect(s).toContain("dataset version 2026.09.0");
    expect(s).toContain("not included in the total");
    expect(page).toContain(s);
  });

  it("never sums paid and approved-for-future", () => {
    expect(page).toContain("$50,000"); // paid total
    expect(page).toContain("$12,000"); // future, on its own card and row
    expect(page).not.toContain("$62,000"); // the sum, nowhere
    expect(page).toContain("not included in grants paid");
  });

  it("marks inferred matches and links unresolved ones nowhere", () => {
    expect(page).toContain('class="tier tier-b"');
    expect(page).toContain('class="tier tier-u"');
    expect(page).toContain('href="/recipients/420987654"');
    expect(page).not.toContain('href="/recipients/null"');
    expect(page).toContain("Mccalester College Alumni Association");
    expect(page).toContain("independent");
  });

  it("carries schema.org markup: the organization with taxID, MonetaryGrant rows, breadcrumbs", () => {
    expect(page).toContain('"@type":"NGO"');
    expect(page).toContain('"taxID":"84-6725611"');
    expect(page).toContain('"@type":"MonetaryGrant"');
    expect(page).toContain('"@type":"BreadcrumbList"');
    expect(page).toContain('"currency":"USD"');
  });

  it("has a factual title and no placeholder anywhere", () => {
    const title = funderTitle(payload);
    expect(title).toBe(
      "Grants paid by Broderick Charitable Foundation Trust (EIN 84-6725611) — 4 grants, $50K, 2022",
    );
    for (const bad of ["undefined", "NaN", "null", "[object Object]", "${"]) {
      expect(page).not.toContain(bad);
    }
  });

  it("renders a tax-year page from the payload when the funder is not chunked", () => {
    const year = html(
      FunderYear({
        payload,
        year: 2022,
        yearPage: null,
        page: 1,
        canonical: `${ORIGIN}/funders/846725611/2022`,
        origin: ORIGIN,
      }),
    );
    expect(year).toContain("tax year 2022");
    expect(year).toContain("202343189349100114");
    expect(year).not.toContain("undefined");
  });
});

describe("routes", () => {
  it("301s every non-canonical EIN form and slug to the canonical URL", async () => {
    const e = env();
    for (const [path, target] of [
      ["/funders/84-6725611", "/funders/846725611"],
      ["/funders/846725611/broderick-charitable", "/funders/846725611"],
      ["/funders/84-6725611/2022", "/funders/846725611/2022"],
      ["/recipients/42-0987654", "/recipients/420987654"],
      ["/recipients/420987654/anything/here", "/recipients/420987654"],
      ["/browse/state/ia", "/browse/state/IA"],
    ] as Array<[string, string]>) {
      const res = await app.request(path, {}, e);
      expect(res.status, path).toBe(301);
      expect(res.headers.get("location"), path).toBe(target);
    }
  });

  it("serves robots.txt pointing at the sitemap index, and llms.txt with the tiers", async () => {
    const e = env();
    const robots = await (await app.request("/robots.txt", {}, e)).text();
    expect(robots).toContain(`Sitemap: ${ORIGIN}/sitemap.xml`);
    const llms = await (await app.request("/llms.txt", {}, e)).text();
    expect(llms).toContain("Tier D, Probable name match");
    expect(llms).toContain("Do not add the two");
    expect(llms).toContain("2026.09.0");
    expect(llmsTxt(ORIGIN, null)).toContain("none published");
  });

  it("answers 503 with no dataset published rather than an empty page", async () => {
    const e = env({
      VINTAGE: { get: async () => null } as unknown as KVNamespace,
      DB: {
        prepare: () => ({ first: async () => null, bind: () => ({ first: async () => null }) }),
      } as unknown as D1Database,
    });
    const res = await app.request("/", {}, e);
    expect(res.status).toBe(503);
    expect(await res.text()).toContain("No dataset version is published yet");
  });

  it("serves a real 404 for an unknown page", async () => {
    const res = await app.request("/nothing/here", {}, env());
    expect(res.status).toBe(404);
    expect(await res.text()).toContain("Page not found");
  });
});

describe("the data domain index", () => {
  it("renders the datasets from the bucket at the root of data.opengrants.io", async () => {
    const manifest = {
      dataset_version: "2026.09.0",
      generated_at: "2026-09-02T18:00:00+00:00",
      license: "Apache-2.0",
      filing_years: [2023],
      rows: { total: 2847781, paid: 2800708 },
      files: [
        {
          path: "grants/filing_year=2023/part-0000.parquet",
          bytes: 1,
          rows: 2847781,
          filing_year: 2023,
        },
      ],
    };
    const DATA = {
      list: async () => ({
        delimitedPrefixes: [
          "funder-graph/2026.09.0/",
          "funder-graph/latest/",
          "funder-graph/_smoke/",
        ],
      }),
      get: async (key: string) =>
        key.endsWith("/manifest.json") ? { json: async () => manifest } : null,
    } as unknown as R2Bucket;
    const res = await app.request("https://data.opengrants.io/", {}, env({ DATA }));
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain("Open datasets, served as objects");
    expect(body).toContain("2026.09.0");
    expect(body).toContain("2,847,781");
    expect(body).toContain("latest/manifest.json");
    expect(body).toContain("grants/filing_year=2023/part-0000.parquet");
    expect(body).not.toContain("_smoke");
    expect(body).not.toContain("undefined");
  });

  it("does not touch the funders site's routing", async () => {
    const res = await app.request("/robots.txt", {}, env());
    expect(res.status).toBe(200);
  });
});

describe("the cache key", () => {
  it("changes with the vintage, the index stamp, and the code epoch", () => {
    const url = new URL("https://funders.opengrants.io/funders/941156365?page=2");
    const a = cacheKey(url, "2026.09.0", "2026-09-02T18:00:00");
    expect(a).toContain("/funders/941156365?page=2?v=2026.09.0&s=2026-09-02T18:00:00&e=");
    expect(cacheKey(url, "2026.09.0", "later")).not.toBe(a);
    expect(cacheKey(url, "2026.10.0", "2026-09-02T18:00:00")).not.toBe(a);
  });
});
