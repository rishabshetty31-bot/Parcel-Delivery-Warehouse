-- Q4 Service comparison. Grain: one booked service (including Unknown) at T.
-- Population: all fact shipments for volume; H for handover/transit/first-attempt;
-- D for on-time. Date roles: handover for transit/attempt, promise for on-time.
-- Numerators/denominators: on time/D, transit hours/eligible, first success/eligible.
-- Exclusions follow Gold flags; rates are 0..1 and durations are calendar hours.
WITH a AS (
    SELECT f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,s.service_key,s.service_name,
           s.nominal_transit_hours,s.is_unknown AS service_is_unknown,
           COUNT_BIG(*) AS shipment_count,SUM(CAST(f.is_handed_over AS bigint)) AS handed_over_count,
           SUM(CAST(f.is_due AS bigint)) AS due_count,
           SUM(CAST(f.is_confirmed_on_time AS bigint)) AS on_time_count,
           SUM(CAST(f.is_transit_eligible AS bigint)) AS transit_eligible_count,
           SUM(f.transit_hours) AS transit_hours_sum,
           SUM(CAST(f.is_first_attempt_eligible AS bigint)) AS first_attempt_eligible_count,
           SUM(CAST(COALESCE(f.is_first_attempt_success,0) AS bigint)) AS first_attempt_success_count
    FROM gold.fact_shipments AS f
    JOIN gold.dim_services AS s ON s.run_id=f.run_id AND s.service_key=f.service_key
    GROUP BY f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
             s.service_key,s.service_name,s.nominal_transit_hours,s.is_unknown
)
SELECT run_id,reporting_cutoff_utc,extract_as_of_utc,service_key,service_name,
       nominal_transit_hours,service_is_unknown,shipment_count,handed_over_count,due_count,
       on_time_count,CAST(CAST(on_time_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS on_time_rate,
       transit_eligible_count,transit_hours_sum,
       CAST(CAST(transit_hours_sum AS decimal(24,6))/NULLIF(CAST(transit_eligible_count AS decimal(12,0)),0) AS decimal(19,6)) AS average_transit_hours,
       first_attempt_eligible_count,first_attempt_success_count,
       CAST(CAST(first_attempt_success_count AS decimal(19,6))/NULLIF(CAST(first_attempt_eligible_count AS decimal(19,6)),0) AS decimal(19,6)) AS first_attempt_success_rate,
       CAST(CASE WHEN due_count>=30 THEN 1 ELSE 0 END AS bit) AS has_comparison_sample
FROM a ORDER BY service_key;
