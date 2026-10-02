"""Step 7: safe Gold setup, candidate validation, and guarded local publication."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

import bronze_pipeline as bronze


ROOT = bronze.ROOT
DEFAULT_CONFIG_PATH = ROOT / "config" / "bronze_runtime.json"
SOURCES = ("shipments", "tracking_events", "customers", "customer_locations", "depots", "service_levels")
REQUIRED_CHECKS = (
    "REF_CUSTOMERS_RECON", "REF_CUSTOMERS_ACCEPTED", "REF_CUSTOMER_LOCATIONS_RECON",
    "REF_CUSTOMER_LOCATIONS_ACCEPTED", "REF_DEPOTS_RECON", "REF_DEPOTS_ACCEPTED",
    "REF_SERVICE_LEVELS_RECON", "REF_SERVICE_LEVELS_ACCEPTED",
    "REF_PRIMARY_ONE_PER_CUSTOMER", "REF_MULTIPLE_PRIMARY_LOCATION_AF",
    "SHIPEVENT_SHIPMENTS_RECON", "SHIPEVENT_TRACKING_EVENTS_RECON",
    "ANALYTICS_SHIPMENTS_RECON", "ANALYTICS_TRACKING_EVENTS_RECON",
    "ANALYTICS_CUSTOMERS_RECON", "ANALYTICS_CUSTOMER_LOCATIONS_RECON",
    "ANALYTICS_DEPOTS_RECON", "ANALYTICS_SERVICE_LEVELS_RECON",
    "ANALYTICS_CERTIFICATE_COUNT", "ANALYTICS_SUMMARY_COUNT",
    "ANALYTICS_FACT_SPLIT", "ANALYTICS_START_SPLIT", "ANALYTICS_DUE_SPLIT",
    "ANALYTICS_DUE_OUTCOME_SPLIT", "ANALYTICS_EVENT_CUTOFF_SPLIT",
    "ANALYTICS_FIRST_ATTEMPT_SPLIT", "ANALYTICS_TRACKING_SPLIT",
    "ANALYTICS_TRANSIT_NONNEGATIVE", "ANALYTICS_OVERDUE_NONNEGATIVE",
)
DIMENSIONS = {"customer": ("fn_dim_customers", "customer_key", "UNK_CUSTOMER"),
              "service": ("fn_dim_services", "service_key", "UNK_SERVICE"),
              "depot": ("fn_dim_depots", "depot_key", "UNK_DEPOT"),
              "date": ("fn_dim_date", "date_key", 0)}
ROLES = ("customer", "service", "origin_depot", "destination_depot",
         "booking_date", "handover_date", "promise_date", "delivery_date")


def scalar(cur, sql: str, *args):
    return cur.execute(sql, *args).fetchval()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def setup(config: dict) -> list[str]:
    created: list[str] = []
    with bronze.connect(config) as conn:
        cur = conn.cursor()
        if scalar(cur, "SELECT SCHEMA_ID('gold')") is None:
            cur.execute("CREATE SCHEMA gold")
            created.append("schema gold")
        if scalar(cur, "SELECT OBJECT_ID('ctl.publication_attempt','U')") is None:
            cur.execute("""CREATE TABLE ctl.publication_attempt (
                attempt_id bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_publication_attempt PRIMARY KEY,
                candidate_run_id uniqueidentifier NOT NULL,
                prior_run_id uniqueidentifier NULL,
                status varchar(12) NOT NULL,
                started_at_utc datetime2(0) NOT NULL CONSTRAINT DF_publication_started DEFAULT SYSUTCDATETIME(),
                finished_at_utc datetime2(0) NULL,
                error_message nvarchar(2048) NULL,
                CONSTRAINT FK_publication_candidate FOREIGN KEY (candidate_run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT FK_publication_prior FOREIGN KEY (prior_run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_publication_status CHECK (status IN ('RUNNING','PUBLISHED','REJECTED','FAILED'))
            )""")
            cur.execute("CREATE INDEX IX_publication_candidate ON ctl.publication_attempt(candidate_run_id,attempt_id)")
            created.append("ctl.publication_attempt")
        script = (ROOT / "sql" / "04_create_gold_views.sql").read_text(encoding="utf-8")
        for batch in re.split(r"(?im)^\s*GO\s*$", script):
            batch = batch.strip()
            if batch:
                cur.execute(batch)
        conn.commit()
    return created


def validate(cur, run_id: str) -> dict:
    """Read-only operational validation over the exact functions used by the views."""
    row = cur.execute("SELECT status,reporting_cutoff_utc,extract_as_of_utc FROM ctl.refresh_run WHERE run_id=?", run_id).fetchone()
    require(row is not None, "Candidate run does not exist")
    require(row[0] in ("BRONZE_COMPLETE", "READY", "PUBLISHED"), f"Run status {row[0]} is not publishable")
    for table in ("reference_stage", "shipment_event_stage", "analytics_stage"):
        require(scalar(cur, f"SELECT status FROM ctl.{table} WHERE run_id=?", run_id) == "COMPLETE",
                f"{table} is not COMPLETE")
    files = cur.execute("SELECT source_name,parse_status,data_row_count,loaded_row_count FROM ctl.source_file WHERE run_id=?",run_id).fetchall()
    require(len(files) == 6 and {x[0] for x in files} == set(SOURCES), "Six complete source files required")
    require(all(x[1] in ("PASS","WARNING") and x[2] == x[3] for x in files), "Source file load incomplete")
    checks = dict(cur.execute("SELECT check_name,passed FROM ctl.run_check WHERE run_id=?",run_id).fetchall())
    missing = sorted(set(REQUIRED_CHECKS) - checks.keys())
    require(not missing, f"Missing required checks: {missing}")
    failed = sorted(name for name, passed in checks.items() if not passed)
    require(not failed, f"Failed checks: {failed}")
    dimensions = {}
    for name, (function, key, unknown) in DIMENSIONS.items():
        rows = cur.execute(f"""SELECT COUNT_BIG(*),COUNT(DISTINCT [{key}]),
            SUM(CASE WHEN is_unknown=1 THEN 1 ELSE 0 END),
            SUM(CASE WHEN [{key}]=? THEN 1 ELSE 0 END),
            SUM(CASE WHEN run_id<>? OR [{key}] IS NULL THEN 1 ELSE 0 END)
            FROM gold.{function}(?)""", unknown,run_id,run_id).fetchone()
        total, distinct, flagged_unknown, reserved, malformed = rows
        require(total == distinct and flagged_unknown == reserved == 1 and malformed == 0,
                f"{name} dimension key/Unknown violation: {tuple(rows)}")
        dimensions[name] = int(total)
    expected = scalar(cur, "SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=? AND is_fact_in_scope=1",run_id)
    population = cur.execute("""SELECT COUNT_BIG(*),COUNT(DISTINCT shipment_id),
        SUM(CASE WHEN run_id<>? OR shipment_id IS NULL OR reporting_cutoff_utc<>?
                 OR extract_as_of_utc<>? THEN 1 ELSE 0 END),
        SUM(CAST(is_handed_over AS bigint)),SUM(CAST(is_due AS bigint)),
        SUM(CAST(is_confirmed_on_time AS bigint)),SUM(CAST(is_open_overdue AS bigint)),
        SUM(CAST(is_transit_eligible AS bigint)),SUM(CAST(is_first_attempt_eligible AS bigint)),
        SUM(CAST(COALESCE(is_first_attempt_success,0) AS bigint))
        FROM gold.fn_fact_shipments(?)""",run_id,row[1],row[2],run_id).fetchone()
    total, unique, bad_context, h, d, ontime, overdue, transit, eligible, first_success = [int(v or 0) for v in population]
    require(total == unique == expected and bad_context == 0, f"Fact grain/context violation: {tuple(population)}; expected {expected}")
    expected_population = cur.execute("""SELECT SUM(CAST(is_handed_over AS bigint)),SUM(CAST(is_due AS bigint)),
        SUM(CAST(is_confirmed_on_time AS bigint)),SUM(CAST(is_open_overdue AS bigint)),
        SUM(CAST(is_transit_eligible AS bigint)),SUM(CAST(is_first_attempt_eligible AS bigint)),
        SUM(CAST(COALESCE(is_first_attempt_success,0) AS bigint))
        FROM silver.shipment_summary WHERE run_id=? AND is_fact_in_scope=1""",run_id).fetchone()
    require((h,d,ontime,overdue,transit,eligible,first_success) == tuple(int(v or 0) for v in expected_population),
            "Gold metrics differ from Silver summary")
    require(0 <= ontime <= d <= h <= total and overdue <= d and transit <= h and first_success <= eligible <= h,
            "Metric population invariant failed")
    bad_metrics = scalar(cur,"""SELECT COUNT_BIG(*) FROM gold.fn_fact_shipments(?) AS f
        WHERE (f.is_due=0 AND (f.is_confirmed_on_time=1 OR f.is_open_overdue=1))
           OR (f.is_transit_eligible=1 AND f.transit_hours IS NULL)
           OR (f.is_transit_eligible=0 AND f.transit_hours IS NOT NULL)
           OR f.transit_hours<0 OR f.overdue_hours<0
           OR (f.is_first_attempt_eligible=0 AND f.is_first_attempt_success IS NOT NULL)
           OR (f.is_first_attempt_eligible=1 AND f.is_first_attempt_success IS NULL)
           OR (f.is_open_overdue=1 AND f.overdue_hours IS NULL)
           OR (f.is_open_overdue=0 AND f.overdue_hours IS NOT NULL)""",run_id)
    require(bad_metrics == 0,"Gold metric nullability/duration invariant failed")
    # A fact key present in a unique dimension has exactly one join result.
    # Testing the eight memberships avoids SQL Server repeatedly expanding all
    # eight inline functions in one large, expensive join plan.
    role_keys = {}
    for name, (function,key,_) in DIMENSIONS.items():
        role_keys[name] = {x[0] for x in cur.execute(f"SELECT [{key}] FROM gold.{function}(?)",run_id).fetchall()}
    fact_keys = cur.execute("""SELECT customer_key,service_key,origin_depot_key,destination_depot_key,
        booking_date_key,handover_date_key,promise_date_key,delivery_date_key
        FROM gold.fn_fact_shipments(?)""",run_id).fetchall()
    require(len(fact_keys)==total,"Fact keys changed during validation")
    key_sets=(role_keys["customer"],role_keys["service"],role_keys["depot"],role_keys["depot"],
              role_keys["date"],role_keys["date"],role_keys["date"],role_keys["date"])
    require(all(value in key_sets[i] for row in fact_keys for i,value in enumerate(row)),
            "One or more of the eight fact dimension roles has no member")
    joined=total  # unique dimension keys + complete membership prove a one-to-one join per role
    unknown_values=("UNK_CUSTOMER","UNK_SERVICE","UNK_DEPOT","UNK_DEPOT",0,0,0,0)
    counts=[sum(row[i]==unknown_values[i] for row in fact_keys) for i in range(8)]
    return {"run_id":run_id,"fact_rows":total,"dimensions":dimensions,
            "roles_verified":len(ROLES),"role_join_rows":int(joined),
            "unknown_role_counts":dict(zip(ROLES,counts)),
            "handed_over":h,"due":d,"confirmed_on_time":ontime,
            "open_overdue":overdue,"transit_eligible":transit,
            "first_attempt_eligible":eligible,"first_attempt_success":first_success,
            "required_check_count":len(REQUIRED_CHECKS),"all_existing_checks_pass":True}


def _audit_start(run_id: str, config: dict) -> int:
    with bronze.connect(config) as conn:
        cur=conn.cursor()
        prior=scalar(cur,"SELECT active_run_id FROM ctl.published_run WHERE singleton_id=1")
        cur.execute("""INSERT ctl.publication_attempt(candidate_run_id,prior_run_id,status)
            OUTPUT INSERTED.attempt_id VALUES (?,?,'RUNNING')""",run_id,prior)
        attempt=int(cur.fetchone()[0]);conn.commit()
        return attempt


def _audit_failure(attempt: int, status: str, exc: Exception, config: dict) -> None:
    with bronze.connect(config) as conn:
        conn.cursor().execute("""UPDATE ctl.publication_attempt SET status=?,finished_at_utc=SYSUTCDATETIME(),
            error_message=? WHERE attempt_id=? AND status='RUNNING'""",status,f"{type(exc).__name__}: {exc}"[:2048],attempt)
        conn.commit()


def publish(run_id: str, config: dict, *, fail_after_pointer: bool=False) -> dict:
    run_id=str(uuid.UUID(run_id))
    attempt=_audit_start(run_id, config)
    try:
        with bronze.connect(config) as preflight:
            candidate=validate(preflight.cursor(),run_id)
        with bronze.connect(config) as conn:
            cur=conn.cursor()
            try:
                cur.execute("SELECT 1")  # start the ODBC-managed transaction
                lock_status=scalar(cur,"""DECLARE @result int;
                    EXEC @result=sys.sp_getapplock @Resource='parcel_gold_publication',
                    @LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=10000;
                    SELECT @result""")
                require(lock_status>=0,f"Publication lock unavailable: {lock_status}")
                prior=scalar(cur,"SELECT active_run_id FROM ctl.published_run WITH (UPDLOCK,HOLDLOCK) WHERE singleton_id=1")
                require(str(prior).lower()!=run_id.lower(),"Candidate is already the active publication")
                candidate=validate(cur,run_id)
                # Publish-time checks are written only for a validated candidate, in the same transaction.
                for check_name, observed in (("GOLD_FACT_COUNT",candidate["fact_rows"]),
                                             ("GOLD_DIM_KEYS",sum(candidate["dimensions"].values())),
                                             ("GOLD_EIGHT_ROLES",candidate["role_join_rows"]),
                                             ("GOLD_METRICS",candidate["due"]),
                                             ("GOLD_CONTEXT",candidate["fact_rows"])):
                    cur.execute("""UPDATE ctl.run_check SET passed=1,observed_value=?,
                        expected_rule='Gold candidate projection satisfies invariant',
                        detail=NULL,checked_at_utc=SYSUTCDATETIME() WHERE run_id=? AND check_name=?""",
                        str(observed),run_id,check_name)
                    if cur.rowcount==0:
                        cur.execute("""INSERT ctl.run_check(run_id,check_name,passed,observed_value,expected_rule)
                            VALUES (?,?,1,?,'Gold candidate projection satisfies invariant')""",
                            run_id,check_name,str(observed))
                if prior is None:
                    cur.execute("""INSERT ctl.published_run(singleton_id,active_run_id,published_at_utc)
                        VALUES (1,?,SYSUTCDATETIME())""",run_id)
                else:
                    cur.execute("""UPDATE ctl.published_run SET active_run_id=?,
                        published_at_utc=SYSUTCDATETIME() WHERE singleton_id=1""",run_id)
                if fail_after_pointer:
                    raise RuntimeError("Controlled failure after publication-pointer update")
                cur.execute("UPDATE ctl.refresh_run SET status='PUBLISHED',finished_at_utc=SYSUTCDATETIME() WHERE run_id=?",run_id)
                cur.execute("""UPDATE ctl.publication_attempt SET status='PUBLISHED',
                    finished_at_utc=SYSUTCDATETIME(),prior_run_id=? WHERE attempt_id=?""",prior,attempt)
                conn.commit()
                return {"run_id":run_id,"prior_run_id":str(prior) if prior else None,
                        "attempt_id":attempt,"status":"PUBLISHED","validation":candidate}
            except Exception:
                conn.rollback()
                raise
    except Exception as exc:
        _audit_failure(attempt,"REJECTED" if isinstance(exc,ValueError) else "FAILED",exc,config)
        raise


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("setup","validate","publish"))
    parser.add_argument("--config",type=Path,default=DEFAULT_CONFIG_PATH,
                        help="Runtime JSON with the explicit target SQL Server and database")
    parser.add_argument("--run-id")
    parser.add_argument("--fail-after-pointer",action="store_true",help="Test-only rollback injection")
    args=parser.parse_args()
    try:
        config=bronze.config_from(args.config)
        bronze.announce_target(config, args.action)
        if args.action=="setup":
            result={"created":setup(config)}
        else:
            require(args.run_id is not None,"--run-id is required")
            rid=str(uuid.UUID(args.run_id))
            if args.action=="validate":
                with bronze.connect(config) as conn:
                    result=validate(conn.cursor(),rid)
            else:
                result=publish(rid,config,fail_after_pointer=args.fail_after_pointer)
        print(json.dumps(result,default=str))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
