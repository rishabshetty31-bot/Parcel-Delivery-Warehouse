"""Step 6A: SQL Server reference/master setup and run-scoped orchestration."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

import bronze_pipeline as bronze
from reference_sql import ORDER, primary_selection_sql, resolve_sql


def create_if_absent(cur, schema: str, name: str, statement: str) -> bool:
    if cur.execute("SELECT OBJECT_ID(?, 'U')", f"{schema}.{name}").fetchval() is not None:
        return False
    cur.execute(statement)
    return True


def setup(config: dict) -> list[str]:
    created = []
    with bronze.connect(config) as conn:
        cur = conn.cursor()
        if cur.execute("SELECT SCHEMA_ID('silver')").fetchval() is None:
            cur.execute("CREATE SCHEMA silver")
            created.append("schema silver")
        ddl = {
            ("ctl", "reference_stage"): """CREATE TABLE ctl.reference_stage (
                run_id uniqueidentifier NOT NULL CONSTRAINT PK_reference_stage PRIMARY KEY,
                status varchar(12) NOT NULL,
                started_at_utc datetime2(0) NOT NULL CONSTRAINT DF_reference_started DEFAULT SYSUTCDATETIME(),
                finished_at_utc datetime2(0) NULL,
                error_message nvarchar(2048) NULL,
                CONSTRAINT FK_reference_stage_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_reference_stage_status CHECK (status IN ('RUNNING','COMPLETE','FAILED'))
            )""",
            ("ctl", "reference_stage_attempt"): """CREATE TABLE ctl.reference_stage_attempt (
                attempt_id bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_reference_attempt PRIMARY KEY,
                run_id uniqueidentifier NOT NULL,
                status varchar(12) NOT NULL,
                started_at_utc datetime2(0) NOT NULL CONSTRAINT DF_reference_attempt_started DEFAULT SYSUTCDATETIME(),
                finished_at_utc datetime2(0) NULL,
                error_message nvarchar(2048) NULL,
                CONSTRAINT FK_reference_attempt_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_reference_attempt_status CHECK (status IN ('RUNNING','COMPLETE','FAILED'))
            )""",
            ("silver", "row_resolution"): """CREATE TABLE silver.row_resolution (
                run_id uniqueidentifier NOT NULL,
                source_name varchar(32) COLLATE Latin1_General_100_BIN2 NOT NULL,
                source_row_number int NOT NULL,
                business_key_text nvarchar(32) COLLATE Latin1_General_100_BIN2 NULL,
                disposition char(2) NOT NULL,
                representative_row_number int NULL,
                primary_reason_code varchar(64) NULL,
                CONSTRAINT PK_row_resolution PRIMARY KEY (run_id,source_name,source_row_number),
                CONSTRAINT FK_resolution_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_resolution_disposition CHECK (disposition IN ('AC','AF','DX','DE','DS','Q')),
                CONSTRAINT CK_resolution_source CHECK (source_name IN ('shipments','tracking_events','customers','customer_locations','depots','service_levels')),
                CONSTRAINT CK_resolution_row CHECK (source_row_number>=2)
            )""",
            ("silver", "customer"): """CREATE TABLE silver.customer (
                run_id uniqueidentifier NOT NULL,
                customer_id char(7) COLLATE Latin1_General_100_BIN2 NOT NULL,
                customer_name nvarchar(100) NULL,
                customer_segment varchar(12) NULL,
                account_opened_at_utc datetime2(0) NOT NULL,
                record_version int NOT NULL,
                source_updated_at_utc datetime2(0) NOT NULL,
                source_row_number int NOT NULL,
                CONSTRAINT PK_silver_customer PRIMARY KEY (run_id,customer_id),
                CONSTRAINT FK_silver_customer_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_silver_customer_version CHECK (record_version>0)
            )""",
            ("silver", "depot"): """CREATE TABLE silver.depot (
                run_id uniqueidentifier NOT NULL,
                depot_id char(5) COLLATE Latin1_General_100_BIN2 NOT NULL,
                depot_name nvarchar(80) NULL,
                city_name nvarchar(60) NULL,
                region_code varchar(12) NULL,
                record_version int NOT NULL,
                source_updated_at_utc datetime2(0) NOT NULL,
                source_row_number int NOT NULL,
                CONSTRAINT PK_silver_depot PRIMARY KEY (run_id,depot_id),
                CONSTRAINT FK_silver_depot_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_silver_depot_version CHECK (record_version>0)
            )""",
            ("silver", "service_level"): """CREATE TABLE silver.service_level (
                run_id uniqueidentifier NOT NULL,
                service_code varchar(8) COLLATE Latin1_General_100_BIN2 NOT NULL,
                service_name nvarchar(60) NULL,
                nominal_transit_hours int NULL,
                record_version int NOT NULL,
                source_updated_at_utc datetime2(0) NOT NULL,
                source_row_number int NOT NULL,
                CONSTRAINT PK_silver_service PRIMARY KEY (run_id,service_code),
                CONSTRAINT FK_silver_service_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_silver_service_version CHECK (record_version>0),
                CONSTRAINT CK_silver_service_hours CHECK (nominal_transit_hours IS NULL OR nominal_transit_hours>0)
            )""",
            ("silver", "customer_location"): """CREATE TABLE silver.customer_location (
                run_id uniqueidentifier NOT NULL,
                location_id char(8) COLLATE Latin1_General_100_BIN2 NOT NULL,
                customer_id char(7) COLLATE Latin1_General_100_BIN2 NOT NULL,
                location_name nvarchar(100) NULL,
                city_name nvarchar(60) NULL,
                region_code varchar(12) NULL,
                is_active bit NOT NULL,
                is_primary bit NOT NULL,
                record_version int NOT NULL,
                source_updated_at_utc datetime2(0) NOT NULL,
                source_row_number int NOT NULL,
                CONSTRAINT PK_silver_location PRIMARY KEY (run_id,location_id),
                CONSTRAINT FK_silver_location_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT FK_silver_location_customer FOREIGN KEY (run_id,customer_id) REFERENCES silver.customer(run_id,customer_id),
                CONSTRAINT CK_silver_location_version CHECK (record_version>0)
            )""",
            ("silver", "customer_primary_location"): """CREATE TABLE silver.customer_primary_location (
                run_id uniqueidentifier NOT NULL,
                customer_id char(7) COLLATE Latin1_General_100_BIN2 NOT NULL,
                selected_location_id char(8) COLLATE Latin1_General_100_BIN2 NULL,
                city_name nvarchar(60) NULL,
                region_code varchar(12) NULL,
                candidate_count int NOT NULL,
                selection_status varchar(32) NOT NULL,
                CONSTRAINT PK_silver_primary PRIMARY KEY (run_id,customer_id),
                CONSTRAINT FK_silver_primary_customer FOREIGN KEY (run_id,customer_id) REFERENCES silver.customer(run_id,customer_id),
                CONSTRAINT FK_silver_primary_location FOREIGN KEY (run_id,selected_location_id) REFERENCES silver.customer_location(run_id,location_id),
                CONSTRAINT CK_silver_primary_count CHECK (candidate_count>=0),
                CONSTRAINT CK_silver_primary_status CHECK (selection_status IN ('SELECTED','NO_PRIMARY','MULTIPLE_PRIMARY','INACTIVE_PRIMARY','SELECTION_HOLD','INVALID_GEOGRAPHY'))
            )""",
        }
        for (schema, name), statement in ddl.items():
            if create_if_absent(cur, schema, name, statement):
                created.append(f"{schema}.{name}")
        indexes = [
            ("ctl", "reference_stage_attempt", "IX_reference_attempt_run", "run_id,attempt_id"),
            ("silver", "row_resolution", "IX_resolution_disposition", "run_id,source_name,disposition"),
            ("silver", "row_resolution", "IX_resolution_key", "run_id,source_name,business_key_text"),
            ("silver", "customer", "IX_customer_segment", "run_id,customer_segment"),
            ("silver", "depot", "IX_depot_region", "run_id,region_code"),
            ("silver", "customer_location", "IX_location_owner_primary", "run_id,customer_id,is_active,is_primary"),
        ]
        for schema, name, index, columns in indexes:
            if cur.execute("SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(?) AND name=?", f"{schema}.{name}", index).fetchval() is None:
                cur.execute(f"CREATE INDEX [{index}] ON [{schema}].[{name}] ({columns})")
                created.append(f"index {schema}.{name}.{index}")
        # Existing objects are accepted only if their ordered columns still match.
        expected = {
            "ctl.reference_stage": ("run_id", "status", "started_at_utc", "finished_at_utc", "error_message"),
            "ctl.reference_stage_attempt": ("attempt_id", "run_id", "status", "started_at_utc", "finished_at_utc", "error_message"),
            "silver.row_resolution": ("run_id", "source_name", "source_row_number", "business_key_text", "disposition", "representative_row_number", "primary_reason_code"),
            "silver.customer": ("run_id", "customer_id", "customer_name", "customer_segment", "account_opened_at_utc", "record_version", "source_updated_at_utc", "source_row_number"),
            "silver.depot": ("run_id", "depot_id", "depot_name", "city_name", "region_code", "record_version", "source_updated_at_utc", "source_row_number"),
            "silver.service_level": ("run_id", "service_code", "service_name", "nominal_transit_hours", "record_version", "source_updated_at_utc", "source_row_number"),
            "silver.customer_location": ("run_id", "location_id", "customer_id", "location_name", "city_name", "region_code", "is_active", "is_primary", "record_version", "source_updated_at_utc", "source_row_number"),
            "silver.customer_primary_location": ("run_id", "customer_id", "selected_location_id", "city_name", "region_code", "candidate_count", "selection_status"),
        }
        for table, columns in expected.items():
            actual = tuple(row[0] for row in cur.execute("SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(?) ORDER BY column_id", table).fetchall())
            if actual != columns:
                raise RuntimeError(f"Incompatible existing {table}; no destructive migration attempted")
        conn.commit()
    return created


def check_bronze(cur, run_id: str) -> None:
    row = cur.execute("SELECT status FROM ctl.refresh_run WHERE run_id=?", run_id).fetchone()
    if row is None or row[0] != "BRONZE_COMPLETE":
        raise ValueError("Run is absent or not BRONZE_COMPLETE")
    files = cur.execute("SELECT source_name,data_row_count,loaded_row_count,parse_status FROM ctl.source_file WHERE run_id=?", run_id).fetchall()
    if len(files) != 6 or {row[0] for row in files} != set(bronze.SOURCES):
        raise ValueError("Run lacks one of the six source-file audits")
    for name, parsed, loaded, status in files:
        if status not in ("PASS", "WARNING") or parsed != loaded:
            raise ValueError(f"Incomplete Bronze file {name}")
        actual = cur.execute(f"SELECT COUNT_BIG(*) FROM bronze.[{name}] WHERE run_id=?", run_id).fetchval()
        if actual != loaded:
            raise ValueError(f"Bronze count changed for {name}")


def run_reference(config: dict, run_id: str, *, fail_after_source: str | None = None) -> dict:
    uuid.UUID(run_id)
    with bronze.connect(config) as conn:
        cur = conn.cursor()
        check_bronze(cur, run_id)
        current = cur.execute("SELECT status FROM ctl.reference_stage WHERE run_id=?", run_id).fetchone()
        if current is not None and current[0] != "FAILED":
            raise ValueError(f"Reference stage already {current[0]} for this run; no outputs changed")
        if current is None:
            cur.execute("INSERT ctl.reference_stage(run_id,status) VALUES (?,'RUNNING')", run_id)
        else:
            cur.execute("UPDATE ctl.reference_stage SET status='RUNNING',started_at_utc=SYSUTCDATETIME(),finished_at_utc=NULL,error_message=NULL WHERE run_id=?", run_id)
        cur.execute("INSERT ctl.reference_stage_attempt(run_id,status) OUTPUT INSERTED.attempt_id VALUES (?,'RUNNING')", run_id)
        attempt_id = cur.fetchone()[0]
        conn.commit()
        try:
            # The stage marker is already durable. Everything below commits or rolls back together.
            for source in ORDER:
                cur.execute(resolve_sql(source), run_id)
                if source == fail_after_source:
                    raise RuntimeError(f"Controlled reference-stage failure after {source}")
            cur.execute(primary_selection_sql(), run_id)
            counts = {}
            for source in ORDER:
                rows = cur.execute("SELECT disposition,COUNT_BIG(*) FROM silver.row_resolution WHERE run_id=? AND source_name=? GROUP BY disposition", run_id, source).fetchall()
                totals = {code.strip(): count for code, count in rows}
                actual = cur.execute(f"SELECT COUNT_BIG(*) FROM bronze.[{source}] WHERE run_id=?", run_id).fetchval()
                if sum(totals.values()) != actual:
                    raise RuntimeError(f"Disposition reconciliation failed for {source}")
                cur.execute("""INSERT ctl.run_check(run_id,check_name,passed,observed_value,expected_rule)
                    VALUES (?, ?, 1, ?, ?)""", run_id, f"REF_{source.upper()}_RECON", str(sum(totals.values())), f"Equals bronze.{source} rows")
                table = {"customers": "customer", "depots": "depot", "service_levels": "service_level", "customer_locations": "customer_location"}[source]
                accepted = cur.execute(f"SELECT COUNT_BIG(*) FROM silver.[{table}] WHERE run_id=?", run_id).fetchval()
                if accepted != totals.get("AC", 0) + totals.get("AF", 0):
                    raise RuntimeError(f"Accepted entity reconciliation failed for {source}")
                cur.execute("""INSERT ctl.run_check(run_id,check_name,passed,observed_value,expected_rule)
                    VALUES (?, ?, 1, ?, ?)""", run_id, f"REF_{source.upper()}_ACCEPTED", str(accepted), "Equals AC+AF representatives")
                counts[source] = totals
            customer_count = cur.execute("SELECT COUNT_BIG(*) FROM silver.customer WHERE run_id=?", run_id).fetchval()
            primary_count = cur.execute("SELECT COUNT_BIG(*) FROM silver.customer_primary_location WHERE run_id=?", run_id).fetchval()
            if customer_count != primary_count:
                raise RuntimeError("Primary-location enrichment does not preserve one row per customer")
            cur.execute("""INSERT ctl.run_check(run_id,check_name,passed,observed_value,expected_rule)
                VALUES (?,'REF_PRIMARY_ONE_PER_CUSTOMER',1,?,'Equals silver.customer count')""", run_id, str(primary_count))
            conflict_rows = cur.execute("""SELECT COUNT_BIG(*) FROM silver.customer_location l
                JOIN silver.customer_primary_location p ON p.run_id=l.run_id AND p.customer_id=l.customer_id
                WHERE l.run_id=? AND l.is_active=1 AND l.is_primary=1
                  AND p.selection_status='MULTIPLE_PRIMARY'""", run_id).fetchval()
            correctly_flagged = cur.execute("""SELECT COUNT_BIG(*) FROM silver.customer_location l
                JOIN silver.customer_primary_location p ON p.run_id=l.run_id AND p.customer_id=l.customer_id
                JOIN silver.row_resolution rr ON rr.run_id=l.run_id
                  AND rr.source_name='customer_locations' AND rr.source_row_number=l.source_row_number
                WHERE l.run_id=? AND l.is_active=1 AND l.is_primary=1
                  AND p.selection_status='MULTIPLE_PRIMARY' AND rr.disposition='AF'
                  AND EXISTS (SELECT 1 FROM ctl.quality_issue q WHERE q.run_id=l.run_id
                    AND q.source_name='customer_locations' AND q.source_row_number=l.source_row_number
                    AND q.scope='INTEGRATION' AND q.reason_code='MULTIPLE_PRIMARY')""", run_id).fetchval()
            if conflict_rows != correctly_flagged:
                raise RuntimeError("Multiple-primary location rows lack AF or row-linked integration reason")
            cur.execute("""INSERT ctl.run_check(run_id,check_name,passed,observed_value,expected_rule)
                VALUES (?,'REF_MULTIPLE_PRIMARY_LOCATION_AF',1,?,'All current conflicting primary sites are AF with row-linked issue')""",
                run_id, str(correctly_flagged))
            cur.execute("UPDATE ctl.reference_stage SET status='COMPLETE',finished_at_utc=SYSUTCDATETIME() WHERE run_id=?", run_id)
            cur.execute("UPDATE ctl.reference_stage_attempt SET status='COMPLETE',finished_at_utc=SYSUTCDATETIME() WHERE attempt_id=? AND run_id=?", attempt_id, run_id)
            conn.commit()
            return {"run_id": run_id, "stage_status": "COMPLETE", "dispositions": counts, "customers": customer_count, "primary_rows": primary_count}
        except Exception as exc:
            conn.rollback()
            cur.execute("UPDATE ctl.reference_stage SET status='FAILED',finished_at_utc=SYSUTCDATETIME(),error_message=? WHERE run_id=?", f"{type(exc).__name__}: {exc}"[:2048], run_id)
            cur.execute("UPDATE ctl.reference_stage_attempt SET status='FAILED',finished_at_utc=SYSUTCDATETIME(),error_message=? WHERE attempt_id=? AND run_id=?", f"{type(exc).__name__}: {exc}"[:2048], attempt_id, run_id)
            conn.commit()
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "run"))
    parser.add_argument("--config", type=Path, default=bronze.ROOT / "config" / "bronze_runtime.json",
                        help="Runtime JSON selecting the SQL Server database (default: repository config)")
    parser.add_argument("--run-id", help="Existing BRONZE_COMPLETE run ID; required for run")
    parser.add_argument("--fail-after-source", choices=ORDER, help="Test-only injected rollback point")
    args = parser.parse_args()
    try:
        config = bronze.config_from(args.config)
        bronze.announce_target(config, f"reference-{args.action}")
        if args.action == "setup":
            print(json.dumps({"created": setup(config)}))
        else:
            if not args.run_id:
                raise ValueError("--run-id is required")
            print(json.dumps(run_reference(config, args.run_id, fail_after_source=args.fail_after_source)))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
