-- Q10 First-attempt performance and primary exclusion breakdown.
-- Grain: one primary exclusion category plus ALL_HANDED_OVER total at T.
-- Population/date role: H at cutoff, notionally handover cohort. Numerator:
-- first-attempt successes; denominator: certified eligible H with a by-T attempt.
-- Excluded H remain in their one primary reason category; overlapping source
-- issues are not added. Rate 0..1; zero eligible denominator returns NULL.
WITH classified AS (
    SELECT run_id,reporting_cutoff_utc,extract_as_of_utc,
           CASE WHEN is_first_attempt_eligible=1 THEN CAST('ELIGIBLE' AS varchar(64))
                ELSE COALESCE(first_attempt_exclusion_code,'UNSPECIFIED_EXCLUSION') END AS category,
           is_first_attempt_eligible,is_first_attempt_success
    FROM gold.fact_shipments WHERE is_handed_over=1
), a AS (
    SELECT MAX(run_id) AS run_id,MAX(reporting_cutoff_utc) AS reporting_cutoff_utc,
           MAX(extract_as_of_utc) AS extract_as_of_utc,
           GROUPING(category) AS is_total,
           CASE WHEN GROUPING(category)=1 THEN 'ALL_HANDED_OVER' ELSE category END AS category,
           COUNT_BIG(*) AS handed_over_count,
           COALESCE(SUM(CAST(is_first_attempt_eligible AS bigint)),0) AS eligible_count,
           COALESCE(SUM(CAST(COALESCE(is_first_attempt_success,0) AS bigint)),0) AS successful_count
    FROM classified GROUP BY ROLLUP(category)
)
SELECT run_id,reporting_cutoff_utc,extract_as_of_utc,is_total,category,
       handed_over_count,eligible_count,handed_over_count-eligible_count AS excluded_count,
       successful_count,
       CAST(CAST(successful_count AS decimal(19,6))/NULLIF(CAST(eligible_count AS decimal(19,6)),0) AS decimal(19,6)) AS success_rate
FROM a ORDER BY is_total DESC,category;
