-- Q1 Overall performance scorecard. Grain: one active published cutoff snapshot.
-- Population: all in-scope shipments. Date role: cutoff snapshot; H metrics use handover
-- eligibility and D metrics use promise eligibility, without filtering a separate period.
-- Numerators/denominators: on time/D, first success/first eligible, tracked/H,
-- returned/D, lost/D; transit sum/eligible; overdue sum/open backlog count.
-- Exclusions: Gold flags and primary exclusion codes govern eligibility. Units:
-- rates 0..1, elapsed calendar hours; zero denominators yield NULL.
WITH a AS (
    SELECT MAX(run_id) AS run_id,MAX(reporting_cutoff_utc) AS reporting_cutoff_utc,
           MAX(extract_as_of_utc) AS extract_as_of_utc,COUNT_BIG(*) AS shipment_count,
           COALESCE(SUM(CAST(is_handed_over AS bigint)),0) AS handed_over_count,
           COALESCE(SUM(CAST(is_due AS bigint)),0) AS due_count,
           COALESCE(SUM(CAST(is_confirmed_on_time AS bigint)),0) AS on_time_count,
           COALESCE(SUM(CAST(is_transit_eligible AS bigint)),0) AS transit_eligible_count,
           SUM(transit_hours) AS transit_hours_sum,
           COALESCE(SUM(CAST(is_open_overdue AS bigint)),0) AS open_overdue_count,
           SUM(overdue_hours) AS overdue_hours_sum,
           COALESCE(SUM(CAST(is_first_attempt_eligible AS bigint)),0) AS first_attempt_eligible_count,
           COALESCE(SUM(CAST(COALESCE(is_first_attempt_success,0) AS bigint)),0) AS first_attempt_success_count,
           COALESCE(SUM(CAST(is_tracked AS bigint)),0) AS tracked_count,
           COALESCE(SUM(CASE WHEN is_due=1 AND outcome_code='RETURNED' THEN CAST(1 AS bigint) ELSE 0 END),0) AS returned_count,
           COALESCE(SUM(CASE WHEN is_due=1 AND outcome_code='LOST' THEN CAST(1 AS bigint) ELSE 0 END),0) AS lost_count
    FROM gold.fact_shipments
)
SELECT run_id,reporting_cutoff_utc,extract_as_of_utc,shipment_count,handed_over_count,due_count,
       on_time_count,CAST(CAST(on_time_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS on_time_rate,
       transit_eligible_count,transit_hours_sum,
       CAST(CAST(transit_hours_sum AS decimal(24,6))/NULLIF(CAST(transit_eligible_count AS decimal(12,0)),0) AS decimal(19,6)) AS average_transit_hours,
       open_overdue_count,overdue_hours_sum,
       CAST(CAST(overdue_hours_sum AS decimal(24,6))/NULLIF(CAST(open_overdue_count AS decimal(12,0)),0) AS decimal(19,6)) AS average_overdue_hours,
       first_attempt_eligible_count,first_attempt_success_count,
       CAST(CAST(first_attempt_success_count AS decimal(19,6))/NULLIF(CAST(first_attempt_eligible_count AS decimal(19,6)),0) AS decimal(19,6)) AS first_attempt_success_rate,
       tracked_count,CAST(CAST(tracked_count AS decimal(19,6))/NULLIF(CAST(handed_over_count AS decimal(19,6)),0) AS decimal(19,6)) AS tracking_coverage_rate,
       returned_count,CAST(CAST(returned_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS return_rate,
       lost_count,CAST(CAST(lost_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS loss_rate
FROM a;
