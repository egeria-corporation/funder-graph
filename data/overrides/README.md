# Overrides

Two small, auditable tables applied around the automated matcher. Both are published with
every release. Both are meant to stay small: an entry belongs here only when it cannot be
expressed as a rule, and **every row carries a source** - the loader refuses a row without one.

## `name-aliases.csv`

Applied before blocking. A recipient whose normalized name equals `alias_normalized` is also
blocked and scored as if it were `canonical_normalized`, so "HARVARD UNIVERSITY" can reach
"PRESIDENT AND FELLOWS OF HARVARD COLLEGE". Try the BMF sort name first: most university and
hospital cases are already covered by it, and an alias here is only for the ones that are not.

| column | meaning |
|---|---|
| `alias_normalized` | the name as `normalize_name` produces it |
| `canonical_normalized` | the BMF legal name, normalized the same way |
| `source` | where the equivalence was verified (a BMF sort name, an IRS determination letter, the organization's own filing) |
| `note` | optional |

## `ein-corrections.csv`

Applied after matching. A tuple of `(recipient_name_normalized, state, zip5)` - empty `state`
or `zip5` means the recipient's value is also empty, not a wildcard - is assigned `ein`
outright, published with `recipient_ein_source = manual_correction`, tier A, confidence 1.00.

| column | meaning |
|---|---|
| `recipient_name_normalized` | as `normalize_name` produces it |
| `state`, `zip5` | the recipient tuple's values, or empty |
| `ein` | nine digits |
| `source` | how the EIN was verified, specifically enough for someone else to repeat it |
| `note` | optional |
