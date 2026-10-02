-- Q6 Destination-depot comparison. Grain: assigned destination depot at T.
-- Population: all fact shipments; D for on-time and overdue backlog. Date role:
-- promise for on-time; overdue is the single T snapshot. Unknown depot remains.
-- Numerator/denominator: on time/D; overdue count and hours are open due only.
WITH a AS (
    SELECT f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
           d.depot_key AS destination_depot_key,d.depot_name AS destination_depot_name,
           d.region_code,d.is_unknown AS depot_is_unknown,
           COUNT_BIG(*) AS shipment_count,SUM(CAST(f.is_due AS bigint)) AS due_count,
           SUM(CAST(f.is_confirmed_on_time AS bigint)) AS on_time_count,
           SUM(CAST(f.is_open_overdue AS bigint)) AS open_overdue_count,
           SUM(f.overdue_hours) AS overdue_hours_sum
    FROM gold.fact_shipments AS f
    JOIN gold.dim_depots AS d ON d.run_id=f.run_id AND d.depot_key=f.destination_depot_key
    GROUP BY f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
             d.depot_key,d.depot_name,d.region_code,d.is_unknown
)
SELECT run_id,reporting_cutoff_utc,extract_as_of_utc,destination_depot_key,
       destination_depot_name,region_code,depot_is_unknown,shipment_count,due_count,
       on_time_count,CAST(CAST(on_time_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS on_time_rate,
       open_overdue_count,overdue_hours_sum,
       CAST(CAST(overdue_hours_sum AS decimal(24,6))/NULLIF(CAST(open_overdue_count AS decimal(12,0)),0) AS decimal(19,6)) AS average_overdue_hours,
       CAST(CASE WHEN due_count>=30 THEN 1 ELSE 0 END AS bit) AS has_comparison_sample
FROM a ORDER BY destination_depot_key;
