/**
 * Numbers, dates and the plain-language meaning of a match tier.
 *
 * Dollar amounts are rendered in full (`$2,147,500`) wherever a reader might cite them, and
 * compacted (`$2.1M`) only in headings and charts where the full figure is also in the DOM.
 * A compact figure is never the only representation of a number on a page.
 */

import type { Tier } from "./types";

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
const int = new Intl.NumberFormat("en-US");

export function money(n: number | null | undefined): string {
  return n == null ? "—" : usd.format(n);
}

export function compactMoney(n: number | null | undefined): string {
  if (n == null) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(abs >= 1e10 ? 0 : 1)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(abs >= 1e7 ? 0 : 1)}M`;
  if (abs >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return usd.format(n);
}

export function count(n: number | null | undefined): string {
  return n == null ? "—" : int.format(n);
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${int.format(n)} ${n === 1 ? one : many}`;
}

/** "2019–2025", or the single year, or nothing. */
export function yearSpan(first: number | null, last: number | null): string {
  if (first == null && last == null) return "";
  if (first == null || last == null || first === last) return String(last ?? first);
  return `${first}–${last}`;
}

export function isoDate(s: string | null | undefined): string {
  return s ? s.slice(0, 10) : "not stated";
}

/** Title case for names filed in capitals, leaving acronyms and ordinals alone. */
export function niceName(s: string | null | undefined): string {
  if (!s) return "";
  const keepUpper = new Set([
    "LLC",
    "USA",
    "US",
    "NY",
    "NYC",
    "DC",
    "II",
    "III",
    "IV",
    "YMCA",
    "YWCA",
    "UCLA",
    "MIT",
    "PTA",
    "PTO",
    "VFW",
    "AIDS",
    "HIV",
    "STEM",
    "TR",
    "UW",
    "DBA",
  ]);
  return s
    .toLowerCase()
    .split(/(\s+|\/|-)/)
    .map((w) => {
      const up = w.toUpperCase();
      if (keepUpper.has(up)) return up;
      if (/^\d/.test(w)) return w;
      if (/^(of|the|and|for|in|at|to|by|a|an|de|la|del|du)$/.test(w)) return w;
      return w.charAt(0).toUpperCase() + w.slice(1);
    })
    .join("")
    .replace(/^\w/, (c) => c.toUpperCase());
}

export const TIER_LABEL: Record<Tier, string> = {
  A: "Reported EIN",
  B: "Exact name and place",
  C: "Strong name match",
  D: "Probable name match",
  U: "Unresolved",
};

/**
 * What each tier means, in the words a grant consultant would use. This text is what keeps
 * a model from quoting a tier D edge as a fact; it appears wherever a tier does.
 */
export const TIER_MEANING: Record<Tier, string> = {
  A: "The filer reported this recipient's EIN on the return. Where the EIN is in the IRS Business Master File the match is verified; where it is not, the EIN is carried as reported.",
  B: "The recipient's name, normalized, and its ZIP code or state match exactly one organization in the Business Master File. Deterministic, no scoring.",
  C: "The name is a close but inexact match (Jaro-Winkler at or above 0.94 on the normalized name) to one organization in the same state, with no comparable alternative.",
  D: "The name is a probable match (at or above 0.90) to one organization in the same state, with no comparable alternative. Treat as a lead, not a fact.",
  U: "No organization in the Business Master File could be identified with the required confidence. The grant is real; the recipient is shown as filed.",
};

export const TIER_CLASS: Record<Tier, string> = {
  A: "tier-a",
  B: "tier-b",
  C: "tier-c",
  D: "tier-d",
  U: "tier-u",
};

/** Sum a list of amounts, treating nulls as absent. Used only within one amount_type. */
export function sum(values: Array<number | null | undefined>): number {
  let total = 0;
  for (const v of values) if (v != null) total += v;
  return total;
}
