# SQL map

The warehouse uses SQL Server, but its executable stages are orchestrated through the Python entry points in [`tools/`](../tools/). This layout keeps CSV parsing, run IDs, transactions, and the selected database together. Do not run a single SQL file as a substitute for the [ordered runbook](../docs/SETUP_AND_RUN.md).

| Area | SQL location | Entry point and purpose |
| --- | --- | --- |
| Bronze and control | `tools/bronze_pipeline.py` | Creates schemas/control tables, validates six UTF-8 CSVs, and preserves raw field text with source provenance. |
| Silver references | `tools/reference_sql.py` | Resolves current customers, locations, depots, and services, with row dispositions and quality issues. Called by `silver_reference.py`. |
| Silver shipments and events | `tools/event_sql.py` | Resolves bookings and messages. Called by `silver_shipments_events.py`. |
| Silver analytical evidence | `tools/analytics_sql.py` | Builds attempts, certificate checks, shipment summaries, and dates. Called by `silver_analytics.py`. |
| Gold | [`04_create_gold_views.sql`](04_create_gold_views.sql) | Defines five run-parameterized projections and five published-run views. `gold_pipeline.py` validates and switches publication. |
| Business analysis | [`analytics/`](analytics/) | Twelve numbered, read-only queries against the published Gold views. `tools/run_analysis.py` runs one or all of them. |

The numbered analysis files begin with purpose, grain, population, and denominator notes where applicable. SQL object definitions and source-to-target rules are in [WAREHOUSE_DESIGN.md](../docs/WAREHOUSE_DESIGN.md) and [SOURCE_TO_TARGET.md](../docs/SOURCE_TO_TARGET.md). The approved business sources remain exactly six CSVs; the `ctl` and Silver helper objects are technical controls, not new feeds.
