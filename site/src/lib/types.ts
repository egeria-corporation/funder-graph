/**
 * The payload shapes the ingest job writes (`funder_graph/pipeline/site_payloads.py`).
 * Every fact a page renders is one of these; the Worker never derives a number the
 * ingest did not already compute, so a page and the Parquet cannot disagree.
 */

export type Tier = "A" | "B" | "C" | "D" | "U";
export type AmountType = "paid" | "approved_future";

export type GrantRow = {
  grant_id: string;
  tax_year: number | null;
  amount_type: AmountType;
  amount_usd: number | null;
  noncash_amount_usd: number | null;
  recipient_name: string | null;
  recipient_ein: string | null;
  match_tier: Tier;
  match_confidence: number | null;
  match_method: string | null;
  purpose: string | null;
  city: string | null;
  state: string | null;
  recipient_type: string | null;
  object_id: string;
  /** Present on recipient pages only. */
  funder_ein?: string;
  funder_name?: string;
};

export type YearTotal = {
  tax_year: number | null;
  amount_type: AmountType;
  usd: number | null;
  count: number;
};

export type TopRecipient = {
  name: string | null;
  ein: string | null;
  tier: Tier;
  confidence: number | null;
  city: string | null;
  state: string | null;
  paid_usd: number | null;
  count: number;
  last_tax_year: number | null;
};

export type Filing = {
  object_id: string;
  tax_period_end: string | null;
  filing_submission_date: string | null;
  return_version: string | null;
  filing_year: number;
  tax_year: number | null;
  form_type: string;
};

export type FunderPayload = {
  ein: string;
  name: string;
  city: string | null;
  state: string | null;
  ntee_code: string | null;
  subsection_code: string | null;
  form_type: "990PF" | "990";
  totals: {
    paid_usd: number;
    paid_count: number;
    recipient_count: number;
    approved_future_usd: number;
    approved_future_count: number;
    first_tax_year: number | null;
    last_tax_year: number | null;
    grant_rows: number;
  };
  years: YearTotal[];
  top_recipients: TopRecipient[];
  recent_grants: GrantRow[];
  filings: Filing[];
  chunked: boolean;
  /** Chunked funders only: page count per tax year. */
  pages?: Record<string, number>;
  dataset_version: string;
  built_at: string;
};

export type YearPage = {
  ein: string;
  name: string;
  tax_year: number;
  page: number;
  pages: number;
  rows: number;
  grants: GrantRow[];
  dataset_version: string;
  built_at: string;
};

export type RecipientFunder = {
  funder_ein: string;
  funder_name: string;
  funder_state: string | null;
  paid_usd: number | null;
  count: number;
  last_tax_year: number | null;
  /** The distinct tiers behind this edge, e.g. "AB". */
  tiers: string;
};

export type RecipientPayload = {
  ein: string;
  name: string;
  city: string | null;
  state: string | null;
  ntee_code: string | null;
  subsection_code: string | null;
  totals: {
    received_usd: number;
    grant_count: number;
    funder_count: number;
    approved_future_usd: number;
    first_tax_year: number | null;
    last_tax_year: number | null;
  };
  funders: RecipientFunder[];
  recent_grants: GrantRow[];
  dataset_version: string;
  built_at: string;
};

/** A row of the D1 `funders` table. */
export type FunderIndexRow = {
  ein: string;
  name: string;
  city: string | null;
  state: string | null;
  ntee_code: string | null;
  form_type: string;
  total_paid_usd: number;
  grant_count: number;
  recipient_count: number;
  first_tax_year: number | null;
  last_tax_year: number | null;
  latest_filing_dt: string | null;
  payload_key: string;
  is_chunked: number;
};

export type RecipientIndexRow = {
  ein: string;
  name: string;
  city: string | null;
  state: string | null;
  ntee_code: string | null;
  total_received_usd: number;
  funder_count: number;
  payload_key: string;
};

export type SearchHit = {
  ein: string;
  kind: "funder" | "recipient";
  name: string;
  city: string | null;
  state: string | null;
};

export type Vintage = {
  version: string;
  built_at: string;
  grant_rows: number | null;
  funder_rows: number | null;
  recipient_rows: number | null;
};
