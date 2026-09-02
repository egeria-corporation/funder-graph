-- The D1 index for funders.opengrants.io. Only what is searched or ranked lives here;
-- grant rows never do. The ingest job writes a fresh table set per dataset version and
-- swaps; it never mutates the live index in place.

CREATE TABLE IF NOT EXISTS funders (
  ein               TEXT PRIMARY KEY,   -- nine digits, no dash: the canonical form
  name              TEXT NOT NULL,
  name_normalized   TEXT NOT NULL,
  city              TEXT,
  state             TEXT,
  ntee_code         TEXT,
  form_type         TEXT NOT NULL,      -- 990PF | 990
  total_paid_usd    INTEGER NOT NULL,
  grant_count       INTEGER NOT NULL,
  recipient_count   INTEGER NOT NULL,
  first_tax_year    INTEGER,
  last_tax_year     INTEGER,
  latest_filing_dt  TEXT,
  payload_key       TEXT NOT NULL,      -- R2 key relative to the version prefix
  is_chunked        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_funders_state_total ON funders(state, total_paid_usd DESC);
CREATE INDEX IF NOT EXISTS idx_funders_ntee ON funders(ntee_code, total_paid_usd DESC);
CREATE INDEX IF NOT EXISTS idx_funders_total ON funders(total_paid_usd DESC);

CREATE TABLE IF NOT EXISTS recipients (
  ein                TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  city               TEXT,
  state              TEXT,
  ntee_code          TEXT,
  total_received_usd INTEGER NOT NULL,
  funder_count       INTEGER NOT NULL,
  payload_key        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recipients_state_total ON recipients(state, total_received_usd DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS entity_search USING fts5(
  ein UNINDEXED, kind UNINDEXED, name, city, state
);

CREATE TABLE IF NOT EXISTS dataset_vintage (
  version      TEXT PRIMARY KEY,
  built_at     TEXT NOT NULL,
  is_current   INTEGER NOT NULL DEFAULT 0,
  grant_rows   INTEGER,
  funder_rows  INTEGER,
  recipient_rows INTEGER
);
