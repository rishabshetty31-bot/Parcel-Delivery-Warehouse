"""Step 6C: transactional physical attempts, certificates, summaries and calendar."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

import bronze_pipeline as bronze
from silver_reference import create_if_absent
from analytics_sql import attempt_sql, business_conflict_sql, base_sql, certificate_sql, summary_sql, calendar_sql, finalize_sql


def setup(config: dict) -> list[str]:
    created=[]
    with bronze.connect(config) as conn:
        cur=conn.cursor()
        for name in ("row_resolution","shipment","tracking_event","event_hold"):
            if cur.execute("SELECT OBJECT_ID(?, 'U')",f"silver.{name}").fetchval() is None:
                raise ValueError(f"Step 6B setup required: silver.{name}")
        ddl={
            ("ctl","analytics_stage"): """CREATE TABLE ctl.analytics_stage (
                run_id uniqueidentifier NOT NULL CONSTRAINT PK_analytics_stage PRIMARY KEY,
                status varchar(12) NOT NULL,
                started_at_utc datetime2(0) NOT NULL CONSTRAINT DF_analytics_started DEFAULT SYSUTCDATETIME(),
                finished_at_utc datetime2(0) NULL,
                error_message nvarchar(2048) NULL,
                CONSTRAINT FK_analytics_stage_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_analytics_stage_status CHECK (status IN ('RUNNING','COMPLETE','FAILED'))
            )""",
            ("ctl","analytics_stage_attempt"): """CREATE TABLE ctl.analytics_stage_attempt (
                attempt_id bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_analytics_attempt PRIMARY KEY,
                run_id uniqueidentifier NOT NULL,
                status varchar(12) NOT NULL,
                started_at_utc datetime2(0) NOT NULL CONSTRAINT DF_analytics_attempt_started DEFAULT SYSUTCDATETIME(),
                finished_at_utc datetime2(0) NULL,
                error_message nvarchar(2048) NULL,
                CONSTRAINT FK_analytics_attempt_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_analytics_attempt_status CHECK (status IN ('RUNNING','COMPLETE','FAILED'))
            )""",
            ("silver","physical_attempt"): """CREATE TABLE silver.physical_attempt (
                run_id uniqueidentifier NOT NULL,
                shipment_id char(12) COLLATE Latin1_General_100_BIN2 NOT NULL,
                physical_attempt_id char(14) COLLATE Latin1_General_100_BIN2 NOT NULL,
                attempt_sequence int NULL,
                completed_at_utc datetime2(0) NULL,
                result_code varchar(18) NULL,
                is_coherent bit NOT NULL,
                message_count int NOT NULL,
                first_recorded_at_utc datetime2(0) NULL,
                conflict_code varchar(64) NULL,
                CONSTRAINT PK_silver_attempt PRIMARY KEY (run_id,shipment_id,physical_attempt_id),
                CONSTRAINT FK_silver_attempt_shipment FOREIGN KEY (run_id,shipment_id) REFERENCES silver.shipment(run_id,shipment_id),
                CONSTRAINT CK_silver_attempt_count CHECK (message_count>0)
            )""",
            ("silver","certificate_check"): """CREATE TABLE silver.certificate_check (
                run_id uniqueidentifier NOT NULL,
                shipment_id char(12) COLLATE Latin1_General_100_BIN2 NOT NULL,
                source_status varchar(11) NULL,
                watermark_at_utc datetime2(0) NULL,
                certified_attempt_count int NULL,
                observed_attempt_count_through_w int NOT NULL,
                attempt_count_by_cutoff int NOT NULL,
                is_certificate_valid bit NOT NULL,
                is_first_attempt_eligible bit NOT NULL,
                first_attempt_result varchar(18) NULL,
                primary_exclusion_code varchar(64) NULL,
                CONSTRAINT PK_silver_certificate PRIMARY KEY (run_id,shipment_id),
                CONSTRAINT FK_silver_certificate_shipment FOREIGN KEY (run_id,shipment_id) REFERENCES silver.shipment(run_id,shipment_id),
                CONSTRAINT CK_silver_certificate_counts CHECK (observed_attempt_count_through_w>=0 AND attempt_count_by_cutoff>=0)
            )""",
            ("silver","shipment_summary"): """CREATE TABLE silver.shipment_summary (
                run_id uniqueidentifier NOT NULL,
                shipment_id char(12) COLLATE Latin1_General_100_BIN2 NOT NULL,
                is_fact_in_scope bit NOT NULL,
                is_handed_over bit NOT NULL,
                is_due bit NOT NULL,
                outcome_code varchar(32) NOT NULL,
                delivered_at_utc datetime2(0) NULL,
                returned_at_utc datetime2(0) NULL,
                lost_at_utc datetime2(0) NULL,
                valid_tracking_message_count_at_cutoff int NOT NULL,
                canonical_attempt_count_at_cutoff int NULL,
                is_tracked bit NOT NULL,
                is_confirmed_on_time bit NOT NULL,
                is_open_overdue bit NOT NULL,
                is_transit_eligible bit NOT NULL,
                transit_seconds bigint NULL,
                overdue_seconds bigint NULL,
                is_first_attempt_eligible bit NOT NULL,
                is_first_attempt_success bit NULL,
                start_exclusion_code varchar(64) NULL,
                promise_exclusion_code varchar(64) NULL,
                outcome_uncertainty_code varchar(64) NULL,
                transit_exclusion_code varchar(64) NULL,
                first_attempt_exclusion_code varchar(64) NULL,
                quality_issue_count int NOT NULL,
                CONSTRAINT PK_silver_summary PRIMARY KEY (run_id,shipment_id),
                CONSTRAINT FK_silver_summary_shipment FOREIGN KEY (run_id,shipment_id) REFERENCES silver.shipment(run_id,shipment_id)
            )""",
            ("silver","calendar_date"): """CREATE TABLE silver.calendar_date (
                run_id uniqueidentifier NOT NULL,
                date_key int NOT NULL,
                calendar_date date NOT NULL,
                calendar_year smallint NOT NULL,
                calendar_month tinyint NOT NULL,
                day_of_month tinyint NOT NULL,
                iso_year smallint NOT NULL,
                iso_week tinyint NOT NULL,
                week_start_date date NOT NULL,
                month_start_date date NOT NULL,
                CONSTRAINT PK_silver_calendar PRIMARY KEY (run_id,date_key),
                CONSTRAINT UQ_silver_calendar_date UNIQUE (run_id,calendar_date),
                CONSTRAINT FK_silver_calendar_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id)
            )""",
        }
        for (schema,name),statement in ddl.items():
            if create_if_absent(cur,schema,name,statement):
                created.append(f"{schema}.{name}")
        indexes=(
            ("silver","physical_attempt","IX_attempt_ship_seq","run_id,shipment_id,attempt_sequence,completed_at_utc"),
            ("silver","certificate_check","IX_cert_eligible","run_id,is_first_attempt_eligible"),
            ("silver","shipment_summary","IX_summary_due","run_id,is_fact_in_scope,is_due,outcome_code"),
            ("silver","shipment_summary","IX_summary_handover","run_id,is_handed_over"),
            ("ctl","analytics_stage_attempt","IX_analytics_attempt_run","run_id,attempt_id"),
        )
        for schema,table,name,columns in indexes:
            if cur.execute("SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(?) AND name=?",f"{schema}.{table}",name).fetchone() is None:
                cur.execute(f"CREATE INDEX [{name}] ON {schema}.[{table}] ({columns})")
                created.append(f"index {schema}.{table}.{name}")
        conn.commit()
    return created


def prerequisites(cur,run_id: str) -> None:
    if cur.execute("SELECT status FROM ctl.refresh_run WHERE run_id=?",run_id).fetchval()!="BRONZE_COMPLETE":
        raise ValueError("Run is not BRONZE_COMPLETE")
    if cur.execute("SELECT status FROM ctl.reference_stage WHERE run_id=?",run_id).fetchval()!="COMPLETE":
        raise ValueError("Reference stage is not COMPLETE")
    if cur.execute("SELECT passed FROM ctl.run_check WHERE run_id=? AND check_name='REF_MULTIPLE_PRIMARY_LOCATION_AF'",run_id).fetchval()!=1:
        raise ValueError("Reference stage predates corrected primary-location classification")
    if cur.execute("SELECT status FROM ctl.shipment_event_stage WHERE run_id=?",run_id).fetchval()!="COMPLETE":
        raise ValueError("Shipment/event stage is not COMPLETE")
    for name in ("SHIPMENTS","TRACKING_EVENTS"):
        if cur.execute("SELECT passed FROM ctl.run_check WHERE run_id=? AND check_name=?",run_id,f"SHIPEVENT_{name}_RECON").fetchval()!=1:
            raise ValueError(f"Missing Step 6B reconciliation for {name}")


def run(config: dict,run_id: str,*,fail_after: str | None=None) -> dict:
    uuid.UUID(run_id)
    with bronze.connect(config) as conn:
        cur=conn.cursor()
        prerequisites(cur,run_id)
        current=cur.execute("SELECT status FROM ctl.analytics_stage WHERE run_id=?",run_id).fetchone()
        if current is not None and current[0]!="FAILED":
            raise ValueError(f"Analytics stage already {current[0]} for this run; no outputs changed")
        if current is None:
            cur.execute("INSERT ctl.analytics_stage(run_id,status) VALUES (?,'RUNNING')",run_id)
        else:
            cur.execute("UPDATE ctl.analytics_stage SET status='RUNNING',started_at_utc=SYSUTCDATETIME(),finished_at_utc=NULL,error_message=NULL WHERE run_id=?",run_id)
        cur.execute("INSERT ctl.analytics_stage_attempt(run_id,status) OUTPUT INSERTED.attempt_id VALUES (?,'RUNNING')",run_id)
        attempt_id=cur.fetchone()[0]
        conn.commit()
        try:
            # Local # tables created inside an ODBC parameterized batch are
            # scoped to that prepared execution. Keep all dependent stages in
            # one batch and transaction so #a/#ai/#b remain available.
            sections=[]
            for name,sql in (("attempts",attempt_sql()),("conflicts",business_conflict_sql()),
                             ("base",base_sql()),("certificates",certificate_sql()),
                             ("summaries",summary_sql()),("calendar",calendar_sql()),
                             ("finalize",finalize_sql())):
                sql=re.sub(r"(?im)^\s*SET NOCOUNT ON;\s*$", "", sql)
                sql=re.sub(r"(?im)^\s*DECLARE @run_id uniqueidentifier=CONVERT\(uniqueidentifier,\?\);\s*$", "", sql)
                sql=re.sub(r"(?im)^\s*DECLARE @[TE] datetime2\(0\)=\(SELECT [^;]+;\s*$", "", sql)
                sections.append(sql)
                if fail_after==name:
                    sections.append("THROW 51033,'Controlled analytics-stage failure',1;")
            batch=("SET NOCOUNT ON; DECLARE @run_id uniqueidentifier=CONVERT(uniqueidentifier,?); "
                   "DECLARE @T datetime2(0)=(SELECT reporting_cutoff_utc FROM ctl.refresh_run WHERE run_id=@run_id); "
                   "DECLARE @E datetime2(0)=(SELECT extract_as_of_utc FROM ctl.refresh_run WHERE run_id=@run_id);\n"
                   +"\n".join(sections))
            cur.execute(batch,run_id)
            cur.execute("UPDATE ctl.analytics_stage SET status='COMPLETE',finished_at_utc=SYSUTCDATETIME() WHERE run_id=?",run_id)
            cur.execute("UPDATE ctl.analytics_stage_attempt SET status='COMPLETE',finished_at_utc=SYSUTCDATETIME() WHERE run_id=? AND attempt_id=?",run_id,attempt_id)
            conn.commit()
            return {"run_id":run_id,"stage_status":"COMPLETE"}
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            with bronze.connect(config) as recovery:
                rc=recovery.cursor()
                detail=f"{type(exc).__name__}: {exc}"[:2048]
                rc.execute("UPDATE ctl.analytics_stage SET status='FAILED',finished_at_utc=SYSUTCDATETIME(),error_message=? WHERE run_id=? AND status='RUNNING'",detail,run_id)
                rc.execute("UPDATE ctl.analytics_stage_attempt SET status='FAILED',finished_at_utc=SYSUTCDATETIME(),error_message=? WHERE run_id=? AND attempt_id=? AND status='RUNNING'",detail,run_id,attempt_id)
                recovery.commit()
            raise


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("setup","run"))
    parser.add_argument("--config",type=Path,default=bronze.ROOT/"config"/"bronze_runtime.json",
                        help="Runtime JSON selecting the SQL Server database (default: repository config)")
    parser.add_argument("--run-id")
    parser.add_argument("--fail-after",choices=("attempts","conflicts","base","certificates","summaries","calendar","finalize"),help="Test-only injected rollback point")
    args=parser.parse_args()
    try:
        config=bronze.config_from(args.config)
        bronze.announce_target(config,f"analytics-{args.action}")
        if args.action=="setup":
            print(json.dumps({"created":setup(config)}))
        else:
            if not args.run_id:
                raise ValueError("--run-id is required")
            print(json.dumps(run(config,args.run_id,fail_after=args.fail_after)))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}",file=sys.stderr)
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
