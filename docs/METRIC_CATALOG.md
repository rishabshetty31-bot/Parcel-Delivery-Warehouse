# Metric catalog — published parcel warehouse

**SYNTHETIC — FOR LEARNING ONLY.** These definitions govern the 12 read-only files in [`sql/analytics/`](../sql/analytics/). They use only the five public Gold views. Each result belongs to the single run in `gold.fact_shipments.run_id`, at reporting cutoff **T = 2026-06-28 23:59:59 UTC**, with export evidence available through **E = 2026-07-07 23:59:59 UTC**. The run/cutoff/export columns travel with every nonempty query result; the verifier also records the active run for the entire multi-query execution.

Gold is a restated occurrence-time view: supplied evidence recorded by E can describe an occurrence by T. A message occurring after T cannot add a T delivery, attempt, or tracking event, although evidence through a certificate watermark after T can affect first-attempt *eligibility*. Current customer, depot, and service descriptions are selected as of E. These are operational snapshots, not price or financial measures.

## Populations, units, and calculation rules

| Symbol | Definition | Treatment |
|---|---|---|
| A | All in-scope accepted shipment facts booked by T | One row per shipment; cancelled and open rows remain. |
| H | A with `is_handed_over=1` | Valid handover by T and no lifecycle-start conflict. |
| D | H with `is_due=1` | Valid promise by T; returned, lost, open, and Unknown outcomes stay in D. |
| Transit eligible | `is_transit_eligible=1` | Coherent delivery by T with nonnegative handover-to-delivery duration. |
| Open overdue | `is_open_overdue=1` | D, promise strictly before T, open without a conflicting terminal hold. |
| First-attempt eligible | `is_first_attempt_eligible=1` among H | Complete trustworthy source-issued history and at least one physical attempt by T. |
| Tracked | `is_tracked=1` among H | At least one accepted valid by-T tracking message with handover context; this is evidence coverage, not complete attempt history. |

Every `*_rate` and `*_share` column is a **0–1 fraction**. Multiply by 100 only for a final percent display. Queries return the raw numerator and denominator beside each rate. They sum flags within each cohort and divide the sums using decimal arithmetic; they never average row or subgroup rates. `NULL` means a denominator is zero, while a numerator of zero over a positive denominator yields zero. Empty grouped populations return no groups; one-row scorecards return zero counts and `NULL` rates with null context if no publication exists. Unknown dimension members remain visible under reserved keys.

Gold durations are `decimal(19,6)` elapsed **calendar hours**, rounded to six decimals per shipment by the approved Gold contract. The queries sum those values without rounding and use a higher-precision decimal intermediate before dividing by the eligible count; only the final displayed average is cast to six decimals. Summing per-row displayed hours can differ slightly from the exact integer-second sum verified in Step 7. Do not treat a null ineligible duration as zero when computing an average. The SQL files do not read Silver seconds.

For comparisons, a group with fewer than **30 due shipments** remains visible but has `has_comparison_sample=0`; no query declares a customer, depot, or lane “best” or “worst.” Service and depot attributes are current as of E. This sample-size flag is a presentation caution, not a statistical significance claim.

## Query-level contract

| File / business question | Output grain and population | Date role | Raw numerator / denominator, exclusions, and units |
|---|---|---|---|
| `01_overall_scorecard.sql` — How is the network performing? | One active-run A snapshot; H and D subtotals | T snapshot; H for transit/attempt/coverage, D for promise outcomes | On time/D; first successes/first eligible; tracked/H; return/D; loss/D. Transit hours/eligible and overdue hours/open count. Excluded H/D are not silently counted in another denominator. Counts, 0–1 rates, hours. |
| `02_due_outcomes.sql` — What happened to due parcels? | One row for each of seven approved D outcome codes, including zero-count `OPEN_AT_DEADLINE` and `UNKNOWN` | Promise due by T | Outcome count/D; unknown and conflicting outcomes remain in D. Count and 0–1 share. |
| `03_weekly_service_performance.sql` — How does due on-time performance vary by promise week and service? | UTC ISO promise week × service among D | **Promise week** | Confirmed on time/D in the group. All supplied by-E evidence restates the outcome at T; this is **not** a historical weekly backlog or as-known-then trend. Counts and 0–1 rate. |
| `04_service_comparison.sql` — How do booked services compare? | Booked service among A, retaining Unknown | Overall A volume; H for transit/attempt, D for on-time | On time/D; transit-hour sum/transit eligible; first success/first eligible. Ineligible transit/attempt cases stay out of their specific denominators. Counts, 0–1 rates, hours. |
| `05_origin_depot_comparison.sql` — How do origin depots compare? | Assigned origin depot among A | D promise role for on-time; H handover role for tracking | On time/D; tracked/H. Unknown origin remains. Counts and 0–1 rates. |
| `06_destination_depot_comparison.sql` — How do destination depots compare? | Assigned destination depot among A | D promise role; backlog at T | On time/D; open-overdue count and sum/average overdue hours over open count. Unknown destination remains. Counts, 0–1 rate, hours. |
| `07_origin_destination_lanes.sql` — How do assigned depot lanes compare? | Origin × destination depot pair among A | H handover role for transit; D promise role for on-time | Transit-hour sum/transit eligible; on time/D. A small lane is shown, not ranked. Counts, 0–1 rate, hours. |
| `08_customer_performance.sql` — How are paying sender accounts performing? | Current customer account among A | D promise role; backlog at T | On time/D and D minus on-time as **not confirmed on time**; overdue count/hours. Unknown account and uncertain current geography remain visible. Counts, 0–1 rate, hours. “Not confirmed” does not prove a physically late delivery. |
| `09_overdue_backlog_aging.sql` — How old is the open due backlog? | Four nonoverlapping age bands over open-overdue D | Promise-to-T snapshot | Count and sum of overdue hours in each band; band average = sum/count or NULL. No rate denominator. This cannot be read as a historical trend. |
| `10_first_attempt_performance.sql` — How many H are eligible and why are others excluded? | One primary category per H plus an `ALL_HANDED_OVER` total | H by T | First success/first eligible. A known failed first attempt remains eligible; a trustworthy zero-attempt certificate is not eligible. Each excluded H appears once under its primary Gold exclusion code. Counts and 0–1 rate. |
| `11_tracking_coverage.sql` — Where is tracking evidence missing? | Service × assigned **destination** depot among H | H by T | Covered H/H; uncovered = H − covered. Source messages are not physical attempts; duplicate messages never multiply a shipment. Counts and 0–1 rate. |
| `12_returns_losses_uncertainty.sql` — How many due parcels were returned, lost, or uncertain? | One D snapshot | Promise due by T | RETURNED/D, LOST/D, UNKNOWN/D, with separate raw counts and `due_with_uncertainty_code_count`. Missing scans or old age do not imply loss. Counts and 0–1 rates. |

## Backlog-age boundaries

`overdue_hours` is the elapsed UTC time from the promised timestamp to T. The four bands are **[0,24), [24,72), [72,168), and [168,∞) hours**; the lower bound is included and upper bound excluded. A value of exactly 24 hours enters the second band; exactly 72 enters the third; exactly 168 enters the fourth. Open overdue normally has positive age because its promise is strictly before T. The query emits all four bands, even empty ones, when a published fact population exists. The verifier tests each boundary and proves each open-overdue shipment contributes to exactly one band.

## Interpretation limits

The warehouse has one fixed reporting cutoff, not historical daily or weekly snapshots. Promise-week performance groups due shipments by their promise date while observing their **current restated outcome at T**. Changes across those cohorts are descriptive; they do not establish a trend of what operators knew in each week. No query measures cost, price, causes of delays, or delivery performance after T. The independent expected-result files are used only by the verifier, never by these SQL queries.
