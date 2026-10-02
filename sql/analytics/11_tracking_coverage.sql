-- Q11 Tracking coverage by booked service and assigned destination depot.
-- Grain: service x destination depot at active T. Population/date role: H,
-- notionally handover cohort. Numerator: H with >=1 valid by-T tracking message;
-- denominator: all H, including uncovered. Multiple messages do not add H rows.
-- Unknown service/depot members stay visible; coverage rate 0..1 or NULL at zero H.
WITH a AS (
    SELECT f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
           s.service_key,s.service_name,d.depot_key AS destination_depot_key,
           d.depot_name AS destination_depot_name,
           COUNT_BIG(*) AS handed_over_count,SUM(CAST(f.is_tracked AS bigint)) AS covered_count
    FROM gold.fact_shipments AS f
    JOIN gold.dim_services AS s ON s.run_id=f.run_id AND s.service_key=f.service_key
    JOIN gold.dim_depots AS d ON d.run_id=f.run_id AND d.depot_key=f.destination_depot_key
    WHERE f.is_handed_over=1
    GROUP BY f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
             s.service_key,s.service_name,d.depot_key,d.depot_name
)
SELECT run_id,reporting_cutoff_utc,extract_as_of_utc,service_key,service_name,
       destination_depot_key,destination_depot_name,handed_over_count,
       covered_count,handed_over_count-covered_count AS uncovered_count,
       CAST(CAST(covered_count AS decimal(19,6))/NULLIF(CAST(handed_over_count AS decimal(19,6)),0) AS decimal(19,6)) AS coverage_rate
FROM a ORDER BY service_key,destination_depot_key;
