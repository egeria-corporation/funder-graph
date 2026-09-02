"""The build pipeline: separately invocable, checkpointed stages.

``download -> extract -> map -> normalize -> resolve -> publish``. Each stage reads and writes
``FUNDER_GRAPH_WORK_DIR`` (default ``./build``) and records completion in ``build/state.duckdb``,
so a failure at stage five never means re-downloading four hundred gigabytes.

Users do not run this. The published dataset is the product; this is how it is made, kept in
the repo so the making is auditable and reproducible.
"""
