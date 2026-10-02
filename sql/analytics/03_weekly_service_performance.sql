-- Q3 Promise-week service performance. Grain: UTC promise ISO week and service.
-- Population: D; date role: promise date, grouped by ISO week. Numerator: confirmed
-- on-time D; denominator: all due shipments in that week/service. Unknown service
-- remains visible. This is a cohort view at current T, not historical weekly snapshots.
-- Units: shipment counts and a 0..1 rate; zero due denominator yields NULL.
WITH a AS (
    SELECT f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
           d.iso_year,d.iso_week,d.week_start_date,
           s.service_key,s.service_name,s.is_unknown AS service_is_unknown,
           COUNT_BIG(*) AS due_count,SUM(CAST(f.is_confirmed_on_time AS bigint)) AS on_time_count
    FROM gold.fact_shipments AS f
    JOIN gold.dim_date AS d ON d.run_id=f.run_id AND d.date_key=f.promise_date_key
    JOIN gold.dim_services AS s ON s.run_id=f.run_id AND s.service_key=f.service_key
    WHERE f.is_due=1
    GROUP BY f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
             d.iso_year,d.iso_week,d.week_start_date,
             s.service_key,s.service_name,s.is_unknown
)
SELECT run_id,reporting_cutoff_utc,extract_as_of_utc,iso_year,iso_week,week_start_date,
       service_key,service_name,service_is_unknown,due_count,on_time_count,
       CAST(CAST(on_time_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS on_time_rate
FROM a
ORDER BY week_start_date,iso_year,iso_week,service_key;
