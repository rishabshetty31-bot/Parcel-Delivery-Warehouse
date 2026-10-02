"""Step 6B: run-scoped SQL Server shipment/event Silver processing only."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

import bronze_pipeline as bronze
from event_sql import event_sql
from reference_sql import resolve_sql
from silver_reference import create_if_absent


SOURCES = ("shipments", "tracking_events")


def setup(config: dict) -> list[str]:
    created: list[str] = []
    with bronze.connect(config) as conn:
        cur = conn.cursor()
        for name in ("row_resolution", "customer", "depot", "service_level"):
            if cur.execute("SELECT OBJECT_ID(?, 'U')", f"silver.{name}").fetchval() is None:
                raise ValueError(f"Step 6A setup required: silver.{name}")
        ddl = {
            ("ctl", "shipment_event_stage"): """CREATE TABLE ctl.shipment_event_stage (
                run_id uniqueidentifier NOT NULL CONSTRAINT PK_shipment_event_stage PRIMARY KEY,
                status varchar(12) NOT NULL,
                started_at_utc datetime2(0) NOT NULL CONSTRAINT DF_ship_event_started DEFAULT SYSUTCDATETIME(),
                finished_at_utc datetime2(0) NULL,
                error_message nvarchar(2048) NULL,
                CONSTRAINT FK_ship_event_stage_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_ship_event_stage_status CHECK (status IN ('RUNNING','COMPLETE','FAILED'))
            )""",
            ("ctl", "shipment_event_stage_attempt"): """CREATE TABLE ctl.shipment_event_stage_attempt (
                attempt_id bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_ship_event_attempt PRIMARY KEY,
                run_id uniqueidentifier NOT NULL,
                status varchar(12) NOT NULL,
                started_at_utc datetime2(0) NOT NULL CONSTRAINT DF_ship_event_attempt_started DEFAULT SYSUTCDATETIME(),
                finished_at_utc datetime2(0) NULL,
                error_message nvarchar(2048) NULL,
                CONSTRAINT FK_ship_event_attempt_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_ship_event_attempt_status CHECK (status IN ('RUNNING','COMPLETE','FAILED'))
            )""",
            ("silver", "shipment"): """CREATE TABLE silver.shipment (
                run_id uniqueidentifier NOT NULL,
                shipment_id char(12) COLLATE Latin1_General_100_BIN2 NOT NULL,
                customer_id char(7) COLLATE Latin1_General_100_BIN2 NULL,
                service_code varchar(8) COLLATE Latin1_General_100_BIN2 NULL,
                origin_depot_id char(5) COLLATE Latin1_General_100_BIN2 NULL,
                destination_depot_id char(5) COLLATE Latin1_General_100_BIN2 NULL,
                booked_at_utc datetime2(0) NOT NULL,
                handed_over_at_utc datetime2(0) NULL,
                promised_delivery_at_utc datetime2(0) NULL,
                cancelled_at_utc datetime2(0) NULL,
                handover_value_state varchar(8) NOT NULL,
                promise_value_state varchar(8) NOT NULL,
                cancellation_value_state varchar(8) NOT NULL,
                attempt_history_status varchar(11) NULL,
                attempt_history_through_at_utc datetime2(0) NULL,
                attempt_history_attempt_count int NULL,
                attempt_history_attested_at_utc datetime2(0) NULL,
                record_version int NOT NULL,
                source_updated_at_utc datetime2(0) NOT NULL,
                source_row_number int NOT NULL,
                CONSTRAINT PK_silver_shipment PRIMARY KEY (run_id,shipment_id),
                CONSTRAINT FK_silver_shipment_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_silver_shipment_version CHECK (record_version>0),
                CONSTRAINT CK_silver_ship_handover_state CHECK (handover_value_state IN ('VALID','MISSING','INVALID')),
                CONSTRAINT CK_silver_ship_promise_state CHECK (promise_value_state IN ('VALID','MISSING','INVALID')),
                CONSTRAINT CK_silver_ship_cancel_state CHECK (cancellation_value_state IN ('VALID','MISSING','INVALID'))
            )""",
            ("silver", "tracking_event"): """CREATE TABLE silver.tracking_event (
                run_id uniqueidentifier NOT NULL,
                event_id char(14) COLLATE Latin1_General_100_BIN2 NOT NULL,
                shipment_id char(12) COLLATE Latin1_General_100_BIN2 NOT NULL,
                event_type varchar(18) COLLATE Latin1_General_100_BIN2 NOT NULL,
                occurred_at_utc datetime2(0) NOT NULL,
                recorded_at_utc datetime2(0) NOT NULL,
                event_depot_id char(5) COLLATE Latin1_General_100_BIN2 NULL,
                physical_attempt_id char(14) COLLATE Latin1_General_100_BIN2 NULL,
                attempt_sequence int NULL,
                is_late_arrival bit NOT NULL,
                is_after_cutoff bit NOT NULL,
                has_valid_handover_context bit NOT NULL,
                source_row_number int NOT NULL,
                CONSTRAINT PK_silver_event PRIMARY KEY (run_id,event_id),
                CONSTRAINT FK_silver_event_shipment FOREIGN KEY (run_id,shipment_id) REFERENCES silver.shipment(run_id,shipment_id)
            )""",
            ("silver", "event_hold"): """CREATE TABLE silver.event_hold (
                run_id uniqueidentifier NOT NULL,
                source_row_number int NOT NULL,
                shipment_id char(12) COLLATE Latin1_General_100_BIN2 NOT NULL,
                hold_kind varchar(16) NOT NULL,
                affects_cutoff bit NOT NULL,
                affects_certificate bit NOT NULL,
                reason_code varchar(64) NOT NULL,
                CONSTRAINT PK_silver_event_hold PRIMARY KEY (run_id,source_row_number,shipment_id,hold_kind),
                CONSTRAINT FK_silver_event_hold_shipment FOREIGN KEY (run_id,shipment_id) REFERENCES silver.shipment(run_id,shipment_id),
                CONSTRAINT CK_silver_event_hold_kind CHECK (hold_kind IN ('TERMINAL','ATTEMPT'))
            )""",
        }
        for (schema,name),statement in ddl.items():
            if create_if_absent(cur,schema,name,statement):
                created.append(f"{schema}.{name}")
        indexes = (
            ("silver", "shipment", "IX_ship_booking", "run_id,booked_at_utc"),
            ("silver", "shipment", "IX_ship_customer", "run_id,customer_id"),
            ("silver", "shipment", "IX_ship_service", "run_id,service_code"),
            ("silver", "shipment", "IX_ship_destination", "run_id,destination_depot_id"),
            ("silver", "tracking_event", "IX_event_parent_time", "run_id,shipment_id,occurred_at_utc,event_id"),
            ("silver", "tracking_event", "IX_event_attempt", "run_id,physical_attempt_id"),
            ("silver", "event_hold", "IX_event_hold_parent", "run_id,shipment_id,affects_cutoff,affects_certificate"),
            ("ctl", "shipment_event_stage_attempt", "IX_ship_event_attempt_run", "run_id,attempt_id"),
        )
        for schema,table,name,columns in indexes:
            if cur.execute("SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(?) AND name=?",
                           f"{schema}.{table}",name).fetchone() is None:
                cur.execute(f"CREATE INDEX [{name}] ON {schema}.[{table}] ({columns})")
                created.append(f"index {schema}.{table}.{name}")
        conn.commit()
    return created


def check_prerequisites(cur, run_id: str) -> None:
    row = cur.execute("SELECT status FROM ctl.refresh_run WHERE run_id=?",run_id).fetchone()
    if row is None or row[0] != "BRONZE_COMPLETE":
        raise ValueError("Run is not BRONZE_COMPLETE")
    if cur.execute("SELECT status FROM ctl.reference_stage WHERE run_id=?",run_id).fetchval() != "COMPLETE":
        raise ValueError("Reference stage is not COMPLETE")
    if cur.execute("""SELECT passed FROM ctl.run_check WHERE run_id=?
        AND check_name='REF_MULTIPLE_PRIMARY_LOCATION_AF'""",run_id).fetchval() != 1:
        raise ValueError("Reference stage predates corrected multiple-primary classification")
    for source in bronze.SOURCES:
        row = cur.execute("SELECT parse_status,data_row_count,loaded_row_count FROM ctl.source_file WHERE run_id=? AND source_name=?",run_id,source).fetchone()
        if row is None or row[0] not in ("PASS","WARNING") or row[1] != row[2]:
            raise ValueError(f"Incomplete Bronze source {source}")


def run(config: dict, run_id: str, *, fail_after_source: str | None = None,
        query_timeout_seconds: int | None = None) -> dict:
    uuid.UUID(run_id)
    if query_timeout_seconds is not None and query_timeout_seconds <= 0:
        raise ValueError("query_timeout_seconds must be positive")
    with bronze.connect(config) as conn:
        if query_timeout_seconds is not None:
            conn.timeout=query_timeout_seconds
        cur=conn.cursor()
        check_prerequisites(cur,run_id)
        current=cur.execute("SELECT status FROM ctl.shipment_event_stage WHERE run_id=?",run_id).fetchone()
        if current is not None and current[0] != "FAILED":
            raise ValueError(f"Shipment/event stage already {current[0]} for this run; no outputs changed")
        if current is None:
            cur.execute("INSERT ctl.shipment_event_stage(run_id,status) VALUES (?,'RUNNING')",run_id)
        else:
            cur.execute("UPDATE ctl.shipment_event_stage SET status='RUNNING',started_at_utc=SYSUTCDATETIME(),finished_at_utc=NULL,error_message=NULL WHERE run_id=?",run_id)
        cur.execute("INSERT ctl.shipment_event_stage_attempt(run_id,status) OUTPUT INSERTED.attempt_id VALUES (?,'RUNNING')",run_id)
        attempt_id=cur.fetchone()[0]
        conn.commit()
        try:
            cur.execute(resolve_sql("shipments"),run_id)
            if fail_after_source=="shipments":
                raise RuntimeError("Controlled shipment/event-stage failure after shipments")
            cur.execute(event_sql(),run_id)
            if fail_after_source=="tracking_events":
                raise RuntimeError("Controlled shipment/event-stage failure after tracking_events")
            # Integration flags available without canonical attempt aggregation.
            for reason, condition in (
                ("CERT_NOT_COMPLETE_WITH_ATTEMPT",
                 "s.attempt_history_status IS NULL OR s.attempt_history_status<>'COMPLETE'"),
                ("HANDOVER_MISSING_WITH_EVENTS", "s.handover_value_state='MISSING'"),
            ):
                event_filter = "AND e.event_type IN ('DELIVERY_FAILED','DELIVERY_SUCCEEDED')" if reason.startswith("CERT_") else ""
                condition_sql = f"({condition}) AND EXISTS (SELECT 1 FROM silver.tracking_event e WHERE e.run_id=s.run_id AND e.shipment_id=s.shipment_id {event_filter})"
                cur.execute(f"""INSERT ctl.quality_issue
                    (run_id,source_name,source_row_number,entity_type,entity_key,scope,severity,reason_code)
                    SELECT ?, 'shipments',s.source_row_number,'SHIPMENT',s.shipment_id,'INTEGRATION','WARN','{reason}'
                    FROM silver.shipment s WHERE s.run_id=? AND {condition_sql}""",run_id,run_id)
                cur.execute(f"""UPDATE rr SET disposition='AF',
                    primary_reason_code=COALESCE(rr.primary_reason_code,'{reason}')
                    FROM silver.row_resolution rr JOIN silver.shipment s ON s.run_id=rr.run_id
                      AND s.source_row_number=rr.source_row_number
                    WHERE rr.run_id=? AND rr.source_name='shipments' AND rr.disposition IN ('AC','AF')
                      AND {condition_sql}""",run_id)
            counts={}
            for source,table in (("shipments","shipment"),("tracking_events","tracking_event")):
                pairs=cur.execute("SELECT disposition,COUNT_BIG(*) FROM silver.row_resolution WHERE run_id=? AND source_name=? GROUP BY disposition",run_id,source).fetchall()
                totals={code.strip():n for code,n in pairs}
                raw=cur.execute(f"SELECT COUNT_BIG(*) FROM bronze.[{source}] WHERE run_id=?",run_id).fetchval()
                accepted=cur.execute(f"SELECT COUNT_BIG(*) FROM silver.[{table}] WHERE run_id=?",run_id).fetchval()
                if sum(totals.values())!=raw or accepted!=totals.get("AC",0)+totals.get("AF",0):
                    raise RuntimeError(f"Disposition or accepted-entity reconciliation failed for {source}")
                cur.execute("""INSERT ctl.run_check(run_id,check_name,passed,observed_value,expected_rule)
                    VALUES (?,?,1,?,?)""",run_id,f"SHIPEVENT_{source.upper()}_RECON",str(raw),"Raw=AC+AF+DX+DE+DS+Q; accepted=AC+AF")
                counts[source]={"raw":raw,"accepted":accepted,**totals}
            cur.execute("UPDATE ctl.shipment_event_stage SET status='COMPLETE',finished_at_utc=SYSUTCDATETIME() WHERE run_id=?",run_id)
            cur.execute("UPDATE ctl.shipment_event_stage_attempt SET status='COMPLETE',finished_at_utc=SYSUTCDATETIME() WHERE run_id=? AND attempt_id=?",run_id,attempt_id)
            conn.commit()
            return {"run_id":run_id,"stage_status":"COMPLETE","dispositions":counts}
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            # A killed SQL session can invalidate the original connection even
            # when rollback itself appears to succeed. Persist stage failure
            # through a fresh connection after the transaction has ended.
            with bronze.connect(config) as recovery:
                rc=recovery.cursor()
                rc.execute("UPDATE ctl.shipment_event_stage SET status='FAILED',finished_at_utc=SYSUTCDATETIME(),error_message=? WHERE run_id=? AND status='RUNNING'",f"{type(exc).__name__}: {exc}"[:2048],run_id)
                rc.execute("UPDATE ctl.shipment_event_stage_attempt SET status='FAILED',finished_at_utc=SYSUTCDATETIME(),error_message=? WHERE run_id=? AND attempt_id=? AND status='RUNNING'",f"{type(exc).__name__}: {exc}"[:2048],run_id,attempt_id)
                recovery.commit()
            raise


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("setup","run"))
    parser.add_argument("--config",type=Path,default=bronze.ROOT/"config"/"bronze_runtime.json",
                        help="Runtime JSON selecting the SQL Server database (default: repository config)")
    parser.add_argument("--run-id")
    parser.add_argument("--fail-after-source",choices=SOURCES,help="Test-only injected rollback point")
    parser.add_argument("--query-timeout-seconds",type=int,
                        help="Bound each SQL statement; timeout fails the stage and records its audit")
    args=parser.parse_args()
    try:
        config=bronze.config_from(args.config)
        bronze.announce_target(config,f"shipments-events-{args.action}")
        if args.action=="setup":
            print(json.dumps({"created":setup(config)}))
        else:
            if not args.run_id:
                raise ValueError("--run-id is required")
            print(json.dumps(run(config,args.run_id,fail_after_source=args.fail_after_source,
                                 query_timeout_seconds=args.query_timeout_seconds)))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}",file=sys.stderr)
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
