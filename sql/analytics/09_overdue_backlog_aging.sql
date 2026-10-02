-- Q9 Overdue backlog aging. Grain: one nonoverlapping age band at active T.
-- Population: D with is_open_overdue=1. Date role: promise to fixed cutoff T;
-- this is one current snapshot, never a historical trend. Units: calendar hours.
-- Boundaries: [0,24), [24,72), [72,168), [168,infinity), lower inclusive.
-- Every open-overdue shipment belongs to exactly one band; no rate denominator.
WITH bands AS (
    SELECT * FROM (VALUES (1,'0 to <24 hours'),(2,'24 to <72 hours'),
                         (3,'72 to <168 hours'),(4,'168+ hours')) AS v(band_order,age_band)
), classified AS (
    SELECT CASE WHEN overdue_hours<24 THEN 1 WHEN overdue_hours<72 THEN 2
                WHEN overdue_hours<168 THEN 3 ELSE 4 END AS band_order,
           overdue_hours
    FROM gold.fact_shipments WHERE is_open_overdue=1
), context AS (
    SELECT MAX(run_id) AS run_id,MAX(reporting_cutoff_utc) AS reporting_cutoff_utc,
           MAX(extract_as_of_utc) AS extract_as_of_utc FROM gold.fact_shipments
)
SELECT c.run_id,c.reporting_cutoff_utc,c.extract_as_of_utc,b.band_order,b.age_band,
       COUNT(x.band_order) AS open_overdue_count,
       COALESCE(SUM(x.overdue_hours),CAST(0 AS decimal(19,6))) AS overdue_hours_sum,
       CAST(CAST(SUM(x.overdue_hours) AS decimal(24,6))/NULLIF(CAST(COUNT(x.band_order) AS decimal(12,0)),0) AS decimal(19,6)) AS average_overdue_hours
FROM context AS c CROSS JOIN bands AS b
LEFT JOIN classified AS x ON x.band_order=b.band_order
WHERE c.run_id IS NOT NULL
GROUP BY c.run_id,c.reporting_cutoff_utc,c.extract_as_of_utc,b.band_order,b.age_band
ORDER BY b.band_order;
