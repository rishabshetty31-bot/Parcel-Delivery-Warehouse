-- Q2 Due-shipment outcomes. Grain: one approved due-outcome category at active cutoff.
-- Population/date role: D, the promise-date cohort due by T. Numerator: category
-- count; denominator: all D. Unknown/conflicted outcomes remain in D. Unit: share 0..1.
WITH categories AS (
    SELECT * FROM (VALUES (1,'ON_TIME'),(2,'LATE'),(3,'OPEN_AT_DEADLINE'),
                         (4,'OPEN_OVERDUE'),(5,'RETURNED'),(6,'LOST'),(7,'UNKNOWN')) AS v(sort_order,outcome_code)
), a AS (
    SELECT outcome_code,COUNT_BIG(*) AS outcome_count FROM gold.fact_shipments
    WHERE is_due=1 GROUP BY outcome_code
), context AS (
    SELECT MAX(run_id) AS run_id,MAX(reporting_cutoff_utc) AS reporting_cutoff_utc,
           MAX(extract_as_of_utc) AS extract_as_of_utc,
           COALESCE(SUM(CAST(is_due AS bigint)),0) AS due_count
    FROM gold.fact_shipments
)
SELECT c.run_id,c.reporting_cutoff_utc,c.extract_as_of_utc,k.sort_order,k.outcome_code,
       COALESCE(a.outcome_count,0) AS outcome_count,c.due_count,
       CAST(CAST(COALESCE(a.outcome_count,0) AS decimal(19,6))/NULLIF(CAST(c.due_count AS decimal(19,6)),0) AS decimal(19,6)) AS outcome_share
FROM categories AS k CROSS JOIN context AS c
LEFT JOIN a ON a.outcome_code=k.outcome_code
ORDER BY k.sort_order;
