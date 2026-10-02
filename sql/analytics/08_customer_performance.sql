-- Q8 Paying-customer performance. Grain: one current customer account at export E.
-- Population: all fact shipments for volume; D for on-time; overdue is T snapshot.
-- Date role: promise for due/on-time; no historical customer-attribute versioning.
-- Numerator/denominator: on time/D. Unknown customer and geography stay visible.
-- Small customers remain visible and carry a 30-D comparison flag, not a rank.
-- Units: shipment counts, elapsed calendar hours, and a 0..1 on-time rate.
WITH a AS (
    SELECT f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
           c.customer_key,c.customer_name,c.customer_segment,c.geography_status,
           c.region_code,c.is_unknown AS customer_is_unknown,
           COUNT_BIG(*) AS shipment_count,SUM(CAST(f.is_due AS bigint)) AS due_count,
           SUM(CAST(f.is_confirmed_on_time AS bigint)) AS on_time_count,
           SUM(CAST(f.is_open_overdue AS bigint)) AS open_overdue_count,
           SUM(f.overdue_hours) AS overdue_hours_sum
    FROM gold.fact_shipments AS f
    JOIN gold.dim_customers AS c ON c.run_id=f.run_id AND c.customer_key=f.customer_key
    GROUP BY f.run_id,f.reporting_cutoff_utc,f.extract_as_of_utc,
             c.customer_key,c.customer_name,c.customer_segment,c.geography_status,
             c.region_code,c.is_unknown
)
SELECT run_id,reporting_cutoff_utc,extract_as_of_utc,customer_key,customer_name,
       customer_segment,geography_status,region_code,customer_is_unknown,
       shipment_count,due_count,on_time_count,due_count-on_time_count AS not_confirmed_on_time_count,
       CAST(CAST(on_time_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS on_time_rate,
       open_overdue_count,overdue_hours_sum,
       CAST(CASE WHEN due_count>=30 THEN 1 ELSE 0 END AS bit) AS has_comparison_sample
FROM a ORDER BY customer_key;
