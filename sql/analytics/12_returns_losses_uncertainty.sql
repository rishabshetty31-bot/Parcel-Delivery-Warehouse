-- Q12 Returns, losses, and uncertainty at active cutoff. Grain: one snapshot.
-- Population/date role: D, the promise-due population by T. Numerators:
-- coherent RETURNED, LOST, and UNKNOWN D; denominator: all D, including
-- unknowns and open parcels. No loss is inferred from age or missing scans.
-- Each rate is 0..1; zero D returns NULL. Units are shipment counts and rates.
WITH a AS (
    SELECT MAX(run_id) AS run_id,MAX(reporting_cutoff_utc) AS reporting_cutoff_utc,
           MAX(extract_as_of_utc) AS extract_as_of_utc,
           COALESCE(SUM(CAST(is_due AS bigint)),0) AS due_count,
           COALESCE(SUM(CASE WHEN is_due=1 AND outcome_code='RETURNED' THEN CAST(1 AS bigint) ELSE 0 END),0) AS returned_count,
           COALESCE(SUM(CASE WHEN is_due=1 AND outcome_code='LOST' THEN CAST(1 AS bigint) ELSE 0 END),0) AS lost_count,
           COALESCE(SUM(CASE WHEN is_due=1 AND outcome_code='UNKNOWN' THEN CAST(1 AS bigint) ELSE 0 END),0) AS unknown_due_count,
           COALESCE(SUM(CASE WHEN is_due=1 AND outcome_uncertainty_code IS NOT NULL THEN CAST(1 AS bigint) ELSE 0 END),0) AS due_with_uncertainty_code_count
    FROM gold.fact_shipments
)
SELECT run_id,reporting_cutoff_utc,extract_as_of_utc,due_count,returned_count,
       CAST(CAST(returned_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS return_rate,
       lost_count,CAST(CAST(lost_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS loss_rate,
       unknown_due_count,CAST(CAST(unknown_due_count AS decimal(19,6))/NULLIF(CAST(due_count AS decimal(19,6)),0) AS decimal(19,6)) AS unknown_due_share,
       due_with_uncertainty_code_count
FROM a;
