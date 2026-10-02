# Parcel delivery business analysis — demonstration snapshot

**SYNTHETIC — FOR LEARNING ONLY.** These are reviewed outputs from a local demonstration run, not claims about a real carrier. Its reporting cutoff was **2026-06-28 23:59:59 UTC**, with export evidence available through **2026-07-07 23:59:59 UTC**. Rates below are rounded for display; the SQL and [validation summary](VALIDATION.md) retain numerators and denominators. This public ZIP does not contain a populated database.

| Demonstration measure | Verified result | Interpretation |
| --- | ---: | --- |
| In-scope shipments | 9,795 | One Gold fact row per shipment. |
| Handed over | 9,285 | Tracking-coverage population. |
| Due by cutoff | 8,975 | Confirmed-on-time and due-outcome population. |
| Confirmed on time | 6,964 / 8,975 (77.59%) | Unconfirmed outcomes remain in the denominator. |
| First physical attempt succeeded | 6,335 / 8,484 (74.67%) | Only shipments with complete, trustworthy attempt evidence. |
| Tracking covered | 9,185 / 9,285 (98.92%) | At least one valid pre-cutoff tracking message. |
| Open overdue | 650 | One cutoff snapshot, not a historical trend. |
| Due outcome unknown | 11 | Retained in due totals, not assumed delivered or lost. |

These figures were rechecked during an isolated SQL replay on the same computer, as described in [VALIDATION.md](VALIDATION.md). The complete [metric catalog](METRIC_CATALOG.md) defines eligibility, exclusions, and duration precision; the table is a reading aid, not a substitute for those definitions.

The [overall scorecard](../sql/analytics/01_overall_scorecard.sql) contains **9,795** in-scope shipments, **9,285** handed over, and **8,975** due. **6,964/8,975 (77.59%)** were confirmed delivered on time. Average transit among **7,974 eligible delivered shipments** was **32.40 calendar hours**. These are different populations: the due denominator includes open, returned, lost, and uncertain shipments, while transit includes only coherent deliveries.

At the cutoff, **650** due shipments were open overdue. Their displayed Gold-hour sum is **135,225.751661 hours**, and their mean age is **208.04 hours**. The [aging query](../sql/analytics/09_overdue_backlog_aging.sql) places **211** in [72,168) hours and **439** in [168,∞) hours; the two younger bands contain zero. This describes one backlog snapshot, not how backlog changed over time. Step 7 separately verified the exact duration sum as **486,812,706 seconds**; the tiny difference between that and summed displayed hours comes from Gold's six-decimal per-shipment precision.

Among the **8,484** shipments eligible for first-attempt measurement, **6,335 (74.67%)** succeeded on the first physical attempt. Another **801** handed-over shipments were excluded: **775** had no attempt by cutoff, **11** had a terminal-evidence hold, **10** lacked a complete certificate, and **5** had a certificate count mismatch. An event message is not necessarily a distinct physical attempt, and exclusion does not imply failure.

Tracking covered **9,185/9,285 (98.92%)** handed-over shipments. Among due shipments, **250/8,975 (2.79%)** had a confirmed return, **100/8,975 (1.11%)** had a confirmed loss, and **11/8,975 (0.12%)** had an `UNKNOWN` outcome. The Unknown group stays in the due denominator and is not classified as open backlog or loss. The [due-outcome query](../sql/analytics/02_due_outcomes.sql) also shows **1,000** late deliveries and **650** open-overdue shipments; its categories reconcile to all 8,975 due shipments.

The [service comparison](../sql/analytics/04_service_comparison.sql) shows confirmed on-time counts of **1,751/2,241 (78.13%)** for Economy, **1,735/2,253 (77.01%)** for Express, and **3,478/4,481 (77.62%)** for Standard. Transit-eligible means were **45.85**, **18.65**, and **32.47** hours respectively. Those descriptive differences do not establish why a shipment arrived when it did. The fictional service names and commitments were generated for learning.

The [destination-depot query](../sql/analytics/06_destination_depot_comparison.sql) shows, for example, **89** open-overdue shipments assigned to Reedhaven Depot out of **1,156** due there. Depot and lane reports include due sample sizes and a comparison flag; this document does not label a group “best” or “worst.” The [customer query](../sql/analytics/08_customer_performance.sql) retains **three** shipments under `UNK_CUSTOMER` rather than dropping them from totals. Customer geography describes the current primary paying-sender site as of export time, not the parcel's destination.

The [promise-week query](../sql/analytics/03_weekly_service_performance.sql) groups due shipments by the UTC week of their promised date and assesses each group's outcome **at this one reporting cutoff**. It is not a series of historical as-known-at-week-end snapshots. These synthetic observations suggest useful questions for an operator to investigate, but the warehouse alone cannot establish causes, costs, or post-cutoff outcomes.
