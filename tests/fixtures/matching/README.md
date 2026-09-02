# The labeled evaluation set

`labeled_pairs.csv` is the set of recipient strings with hand-verified EINs that the matcher
is scored against (`funder-graph build eval`). The build spec requires at least **1,000**
pairs, sampled stratified across tiers, and treats the per-tier precision targets as gates:
A 100%, B >= 99%, C >= 95%, D >= 80%. Until the file holds 1,000 rows the gate fails by
design: an unevaluated gate is a failed gate, not a passed one.

## Columns

| column | meaning |
|---|---|
| `recipient_name_raw` | the name as it appears on the filing |
| `recipient_city`, `recipient_state`, `recipient_zip5` | the address fields on the filing, or empty |
| `recipient_ein_reported` | the EIN on the filing, if any |
| `expected_ein` | the verified EIN, nine digits; **empty means verified to have no resolvable EIN** (not in the BMF, or genuinely ambiguous) |
| `verified_by` | who checked it |
| `verified_on` | when, `YYYY-MM-DD` |
| `source` | what was checked: the BMF row, the filing of the organization itself, an IRS determination letter. Specific enough for someone else to repeat |
| `note` | optional |

Every row needs `verified_by`, `verified_on`, and `source`; the loader refuses a row without
them.

## Building it

1. `funder-graph build resolve` on the corpus, so `resolutions` in the build state is
   populated.
2. `funder-graph build sample-for-labeling --n 1200` writes `build/reports/labeling-sample.csv`
   with an equal share per tier and the matcher suggestion columns (`matcher_*`).
3. Verify each row **against the BMF and the filing**, not against the suggestion. Fill
   `expected_ein` (or leave it empty when nothing resolvable exists), `verified_by`,
   `verified_on`, `source`.
4. Append the verified rows here, without the `matcher_*` columns, and run
   `funder-graph build eval`.

If a tier misses its target, demote rows out of the tier. Do not loosen the target.
