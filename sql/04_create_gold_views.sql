-- Step 7: five published views over run-parameterized candidate projections.
-- GO separates CREATE OR ALTER statements for the ODBC setup runner.
CREATE OR ALTER FUNCTION gold.fn_dim_customers(@run_id uniqueidentifier)
RETURNS TABLE
AS RETURN
(
    SELECT c.run_id, ISNULL(CAST(c.customer_id AS varchar(20)),CAST('UNK_CUSTOMER' AS varchar(20))) AS customer_key,
           c.customer_id,
           ISNULL(c.customer_name,CAST(N'Unknown' AS nvarchar(100))) AS customer_name,
           c.customer_segment, p.selected_location_id AS primary_location_id,
           p.city_name, p.region_code,
           ISNULL(p.selection_status,CAST('NO_PRIMARY' AS varchar(32))) AS geography_status,
           ISNULL(CAST(0 AS bit),CAST(0 AS bit)) AS is_unknown
    FROM silver.customer AS c
    LEFT JOIN silver.customer_primary_location AS p
      ON p.run_id=c.run_id AND p.customer_id=c.customer_id
    WHERE c.run_id=@run_id
    UNION ALL
    SELECT r.run_id, ISNULL(CAST('UNK_CUSTOMER' AS varchar(20)),CAST('UNK_CUSTOMER' AS varchar(20))), CAST(NULL AS char(7)),
           ISNULL(CAST(N'Unknown' AS nvarchar(100)),CAST(N'Unknown' AS nvarchar(100))), CAST(NULL AS varchar(12)),
           CAST(NULL AS char(8)), CAST(NULL AS nvarchar(60)), CAST(NULL AS varchar(12)),
           ISNULL(CAST('UNKNOWN' AS varchar(32)),CAST('UNKNOWN' AS varchar(32))), ISNULL(CAST(1 AS bit),CAST(1 AS bit))
    FROM ctl.refresh_run AS r WHERE r.run_id=@run_id
);
GO
CREATE OR ALTER FUNCTION gold.fn_dim_services(@run_id uniqueidentifier)
RETURNS TABLE
AS RETURN
(
    SELECT s.run_id, ISNULL(CAST(s.service_code AS varchar(20)),CAST('UNK_SERVICE' AS varchar(20))) AS service_key,
           s.service_code, ISNULL(s.service_name,CAST(N'Unknown' AS nvarchar(60))) AS service_name,
           s.nominal_transit_hours, ISNULL(CAST(0 AS bit),CAST(0 AS bit)) AS is_unknown
    FROM silver.service_level AS s WHERE s.run_id=@run_id
    UNION ALL
    SELECT r.run_id, ISNULL(CAST('UNK_SERVICE' AS varchar(20)),CAST('UNK_SERVICE' AS varchar(20))), CAST(NULL AS varchar(8)),
           ISNULL(CAST(N'Unknown' AS nvarchar(60)),CAST(N'Unknown' AS nvarchar(60))), CAST(NULL AS int), ISNULL(CAST(1 AS bit),CAST(1 AS bit))
    FROM ctl.refresh_run AS r WHERE r.run_id=@run_id
);
GO
CREATE OR ALTER FUNCTION gold.fn_dim_depots(@run_id uniqueidentifier)
RETURNS TABLE
AS RETURN
(
    SELECT d.run_id, ISNULL(CAST(d.depot_id AS varchar(20)),CAST('UNK_DEPOT' AS varchar(20))) AS depot_key,
           d.depot_id, ISNULL(d.depot_name,CAST(N'Unknown' AS nvarchar(80))) AS depot_name,
           d.city_name, d.region_code, ISNULL(CAST(0 AS bit),CAST(0 AS bit)) AS is_unknown
    FROM silver.depot AS d WHERE d.run_id=@run_id
    UNION ALL
    SELECT r.run_id, ISNULL(CAST('UNK_DEPOT' AS varchar(20)),CAST('UNK_DEPOT' AS varchar(20))), CAST(NULL AS char(5)),
           ISNULL(CAST(N'Unknown' AS nvarchar(80)),CAST(N'Unknown' AS nvarchar(80))), CAST(NULL AS nvarchar(60)),
           CAST(NULL AS varchar(12)), ISNULL(CAST(1 AS bit),CAST(1 AS bit))
    FROM ctl.refresh_run AS r WHERE r.run_id=@run_id
);
GO
CREATE OR ALTER FUNCTION gold.fn_dim_date(@run_id uniqueidentifier)
RETURNS TABLE
AS RETURN
(
    SELECT d.run_id,ISNULL(d.date_key,CAST(0 AS int)) AS date_key,d.calendar_date,d.calendar_year,d.calendar_month,
           d.day_of_month,d.iso_year,d.iso_week,d.week_start_date,d.month_start_date,
           ISNULL(CAST(CONVERT(nvarchar(10),d.calendar_date,23) AS nvarchar(24)),CAST(N'Unknown / not applicable' AS nvarchar(24))) AS date_label,
           ISNULL(CAST(0 AS bit),CAST(0 AS bit)) AS is_unknown
    FROM silver.calendar_date AS d WHERE d.run_id=@run_id
    UNION ALL
    SELECT r.run_id,ISNULL(CAST(0 AS int),CAST(0 AS int)),CAST(NULL AS date),CAST(NULL AS smallint),
           CAST(NULL AS tinyint),CAST(NULL AS tinyint),CAST(NULL AS smallint),
           CAST(NULL AS tinyint),CAST(NULL AS date),CAST(NULL AS date),
           ISNULL(CAST(N'Unknown / not applicable' AS nvarchar(24)),CAST(N'Unknown / not applicable' AS nvarchar(24))),ISNULL(CAST(1 AS bit),CAST(1 AS bit))
    FROM ctl.refresh_run AS r WHERE r.run_id=@run_id
);
GO
CREATE OR ALTER FUNCTION gold.fn_fact_shipments(@run_id uniqueidentifier)
RETURNS TABLE
AS RETURN
(
    SELECT s.run_id,s.shipment_id,
           ISNULL(dc.customer_key,CAST('UNK_CUSTOMER' AS varchar(20))) AS customer_key,
           ISNULL(ds.service_key,CAST('UNK_SERVICE' AS varchar(20))) AS service_key,
           ISNULL(od.depot_key,CAST('UNK_DEPOT' AS varchar(20))) AS origin_depot_key,
           ISNULL(dd.depot_key,CAST('UNK_DEPOT' AS varchar(20))) AS destination_depot_key,
           ISNULL(CONVERT(int,CONVERT(char(8),s.booked_at_utc,112)),CAST(0 AS int)) AS booking_date_key,
           ISNULL(CONVERT(int,CONVERT(char(8),s.handed_over_at_utc,112)),CAST(0 AS int)) AS handover_date_key,
           ISNULL(CONVERT(int,CONVERT(char(8),s.promised_delivery_at_utc,112)),CAST(0 AS int)) AS promise_date_key,
           ISNULL(CONVERT(int,CONVERT(char(8),y.delivered_at_utc,112)),CAST(0 AS int)) AS delivery_date_key,
           s.booked_at_utc,s.handed_over_at_utc,s.promised_delivery_at_utc,s.cancelled_at_utc,
           y.delivered_at_utc,y.returned_at_utc,y.lost_at_utc,
           r.reporting_cutoff_utc,r.extract_as_of_utc,y.outcome_code,
           y.is_handed_over,y.is_due,y.is_confirmed_on_time,y.is_open_overdue,y.is_tracked,
           y.is_transit_eligible,
           CAST(CAST(y.transit_seconds AS decimal(19,6))/CAST(3600 AS decimal(19,6)) AS decimal(19,6)) AS transit_hours,
           CAST(CAST(y.overdue_seconds AS decimal(19,6))/CAST(3600 AS decimal(19,6)) AS decimal(19,6)) AS overdue_hours,
           y.valid_tracking_message_count_at_cutoff,y.canonical_attempt_count_at_cutoff,
           c.is_certificate_valid,y.is_first_attempt_eligible,y.is_first_attempt_success,
           y.start_exclusion_code,y.promise_exclusion_code,y.outcome_uncertainty_code,
           y.transit_exclusion_code,y.first_attempt_exclusion_code,y.quality_issue_count
    FROM silver.shipment_summary AS y
    JOIN silver.shipment AS s ON s.run_id=y.run_id AND s.shipment_id=y.shipment_id
    JOIN silver.certificate_check AS c ON c.run_id=y.run_id AND c.shipment_id=y.shipment_id
    JOIN ctl.refresh_run AS r ON r.run_id=y.run_id
    LEFT JOIN gold.fn_dim_customers(@run_id) AS dc ON dc.run_id=s.run_id AND dc.customer_id=s.customer_id AND dc.is_unknown=0
    LEFT JOIN gold.fn_dim_services(@run_id) AS ds ON ds.run_id=s.run_id AND ds.service_code=s.service_code AND ds.is_unknown=0
    LEFT JOIN gold.fn_dim_depots(@run_id) AS od ON od.run_id=s.run_id AND od.depot_id=s.origin_depot_id AND od.is_unknown=0
    LEFT JOIN gold.fn_dim_depots(@run_id) AS dd ON dd.run_id=s.run_id AND dd.depot_id=s.destination_depot_id AND dd.is_unknown=0
    WHERE y.run_id=@run_id AND y.is_fact_in_scope=1
);
GO
CREATE OR ALTER VIEW gold.dim_customers AS
SELECT d.* FROM ctl.published_run AS p
CROSS APPLY gold.fn_dim_customers(p.active_run_id) AS d
WHERE p.singleton_id=1;
GO
CREATE OR ALTER VIEW gold.dim_services AS
SELECT d.* FROM ctl.published_run AS p
CROSS APPLY gold.fn_dim_services(p.active_run_id) AS d
WHERE p.singleton_id=1;
GO
CREATE OR ALTER VIEW gold.dim_depots AS
SELECT d.* FROM ctl.published_run AS p
CROSS APPLY gold.fn_dim_depots(p.active_run_id) AS d
WHERE p.singleton_id=1;
GO
CREATE OR ALTER VIEW gold.dim_date AS
SELECT d.* FROM ctl.published_run AS p
CROSS APPLY gold.fn_dim_date(p.active_run_id) AS d
WHERE p.singleton_id=1;
GO
CREATE OR ALTER VIEW gold.fact_shipments AS
SELECT f.* FROM ctl.published_run AS p
CROSS APPLY gold.fn_fact_shipments(p.active_run_id) AS f
WHERE p.singleton_id=1;
GO
