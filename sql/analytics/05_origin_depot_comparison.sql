-- Q5 Origin-depot comparison. Grain: assigned origin depot at active cutoff.
-- Population: all fact shipments for volume, D for on time, H for tracking.
-- Date roles: promise for on-time, handover for tracking. Unknown origin is retained.
-- Numerators/denominators: on time/D and tracked/H; rates 0..1. No small-group rank.
WITH a AS (
    SELECT f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
           d.depot_key AS origin_depot_key,d.depot_name AS origin_depot_name,
           d.region_code,d.is_unknown AS depot_is_unknown,
           COUNT_BIG(*) AS shipment_count,SUM(CAST(f.is_handed_over AS bigint)) AS handed_over_count,
           SUM(CAST(f.is_due AS bigint)) AS due_count,
           SUM(CAST(f.is_confirmed_on_time AS bigint)) AS on_time_count,
           SUM(CAST(f.is_tracked AS bigint)) AS tracked_count
    FROM gold.fact_shipments AS f
    JOIN gold.dim_depots AS d ON d.run_id=f.run_id AND d.depot_key=f.origin_depot_key
    GROUP BY f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
             d.depot_key,d.depot_name,d.region_code,d.is_unknown
)
SELECT run_id,reporting_cutoff_utc,extract_as_of_utc,origin_depot_key,origin_depot_name,
       region_code,depot_is_unknown,shipment_count,handed_over_count,due_count,on_time_count,
       CAST(CAST(on_time_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS on_time_rate,
       tracked_count,CAST(CAST(tracked_count AS decimal(19,6))/NULLIF(CAST(handed_over_count AS decimal(19,6)),0) AS decimal(19,6)) AS tracking_coverage_rate,
       CAST(CASE WHEN due_count>=30 THEN 1 ELSE 0 END AS bit) AS has_comparison_sample
FROM a ORDER BY origin_depot_key;
