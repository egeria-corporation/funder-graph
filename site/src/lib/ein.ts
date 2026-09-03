/**
 * One canonical URL per entity, keyed on EIN: nine digits, no dash. Every other form a
 * request arrives in - "94-2278431", a slug suffix, leading zeros dropped by a spreadsheet -
 * is normalized and redirected, because two URLs serving one organization splits the ranking
 * signal, and that is the most common way a site like this quietly underperforms.
 */

export function canonicalEin(raw: string): string | null {
  const digits = raw.replace(/\D/g, "");
  if (digits.length === 0 || digits.length > 9) return null;
  const nine = digits.padStart(9, "0");
  // The IRS never issues an EIN with a zero prefix pair in a few reserved ranges, but the
  // set of valid prefixes has grown over time and is not worth policing here: a nonexistent
  // EIN is a 404 from the index, which says more than a rejected URL would.
  return nine;
}

/** "942278431" -> "94-2278431", the form people recognise on paper. */
export function displayEin(ein: string): string {
  return `${ein.slice(0, 2)}-${ein.slice(2)}`;
}

/**
 * Whether a path segment already is the canonical form. A slug after it
 * (`/funders/942278431/packard-foundation`) is handled by the route, which only ever
 * takes the first segment.
 */
export function isCanonical(segment: string): boolean {
  return /^\d{9}$/.test(segment);
}
