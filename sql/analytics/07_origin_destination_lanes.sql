-- Q7 Origin-to-destination lane comparison. Grain: assigned depot pair at T.
-- Population: all fact shipments for volume; transit-eligible H for elapsed hours;
-- D for on time. Date roles: handover (transit), promise (on time).
-- Numerators/denominators: transit hours/eligible and on time/D. Unknown keys stay.
-- Small lanes remain visible; the 30-D comparison flag prevents unsupported ranking.
WITH a AS (
    SELECT f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
           o.depot_key AS origin_depot_key,o.depot_name AS origin_depot_name,
           d.depot_key AS destination_depot_key,d.depot_name AS destination_depot_name,
           COUNT_BIG(*) AS shipment_count,SUM(CAST(f.is_due AS bigint)) AS due_count,
           SUM(CAST(f.is_confirmed_on_time AS bigint)) AS on_time_count,
           SUM(CAST(f.is_transit_eligible AS bigint)) AS transit_eligible_count,
           SUM(f.transit_hours) AS transit_hours_sum
    FROM gold.fact_shipments AS f
    JOIN gold.dim_depots AS o ON o.run_id=f.run_id AND o.depot_key=f.origin_depot_key
    JOIN gold.dim_depots AS d ON d.run_id=f.run_id AND d.depot_key=f.destination_depot_key
    GROUP BY f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
             o.depot_key,o.depot_name,d.depot_key,d.depot_name
)
SELECT run_id,reporting_cutoff_utc,extract_as_of_utc,origin_depot_key,origin_depot_name,
       destination_depot_key,destination_depot_name,shipment_count,due_count,on_time_count,
       CAST(CAST(on_time_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS on_time_rate,
       transit_eligible_count,transit_hours_sum,
       CAST(CAST(transit_hours_sum AS decimal(24,6))/NULLIF(CAST(transit_eligible_count AS decimal(12,0)),0) AS decimal(19,6)) AS average_transit_hours,
       CAST(CASE WHEN due_count>=30 THEN 1 ELSE 0 END AS bit) AS has_comparison_sample
FROM a ORDER BY origin_depot_key,destination_depot_key;
