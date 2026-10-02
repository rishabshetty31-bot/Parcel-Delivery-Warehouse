"""Step 5 only: safe SQL Server setup and raw six-file Bronze ingestion."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import pyodbc


ROOT = Path(__file__).resolve().parents[1]
DATABASE_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}\Z", re.ASCII)
SYSTEM_DATABASES = frozenset(("master", "model", "msdb", "tempdb"))
HEADERS = {
    "shipments": ("shipment_id", "customer_id", "service_code", "origin_depot_id", "destination_depot_id", "booked_at_utc", "handed_over_at_utc", "promised_delivery_at_utc", "cancelled_at_utc", "attempt_history_status", "attempt_history_through_at_utc", "attempt_history_attempt_count", "attempt_history_attested_at_utc", "record_version", "source_updated_at_utc"),
    "tracking_events": ("event_id", "shipment_id", "event_type", "occurred_at_utc", "recorded_at_utc", "depot_id", "physical_attempt_id", "attempt_sequence"),
    "customers": ("customer_id", "customer_name", "customer_segment", "account_opened_at_utc", "record_version", "source_updated_at_utc"),
    "customer_locations": ("location_id", "customer_id", "location_name", "city_name", "region_code", "is_active", "is_primary", "record_version", "source_updated_at_utc"),
    "depots": ("depot_id", "depot_name", "city_name", "region_code", "record_version", "source_updated_at_utc"),
    "service_levels": ("service_code", "service_name", "nominal_transit_hours", "record_version", "source_updated_at_utc"),
}
SOURCES = tuple(HEADERS)


@dataclass
class FileSnapshot:
    name: str
    path: Path
    sha256: bytes | None = None
    byte_count: int | None = None
    rows: list[tuple[str, ...]] | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None


def validate_database_name(name: object) -> str:
    """Permit only a simple SQL Server database identifier for dynamic setup DDL."""
    if not isinstance(name, str) or DATABASE_IDENTIFIER.fullmatch(name) is None:
        raise ValueError("connection.database must be a 1-128 character ASCII SQL identifier starting with a letter")
    if name.casefold() in SYSTEM_DATABASES:
        raise ValueError("A SQL Server system database cannot be the warehouse target")
    return name


def config_from(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Runtime configuration must be a JSON object")
    if set(config) != {"connection", "input_root", "reporting_cutoff_utc", "extract_as_of_utc"}:
        raise ValueError("Runtime configuration must have only connection, input_root, T and E")
    c = config["connection"]
    if not isinstance(c, dict):
        raise ValueError("connection must be a JSON object")
    if set(c) != {"server", "database", "driver", "trusted_connection", "encrypt", "trust_server_certificate"}:
        raise ValueError("Connection settings do not match the documented local profile")
    validate_database_name(c["database"])
    if c["trusted_connection"] is not True:
        raise ValueError("Only Windows integrated authentication is supported")
    if any(type(c[field]) is not bool for field in ("encrypt", "trust_server_certificate")):
        raise ValueError("encrypt and trust_server_certificate must be JSON booleans")
    for field in ("server", "driver"):
        value = c[field]
        if not isinstance(value, str) or not value.strip() or ";" in value or any(ord(char) < 32 for char in value):
            raise ValueError(f"connection.{field} must be a nonempty connection-string-safe value")
    if "{" in c["driver"] or "}" in c["driver"]:
        raise ValueError("connection.driver must not contain ODBC brace delimiters")
    if c["driver"] not in pyodbc.drivers():
        raise ValueError(f"ODBC driver unavailable: {c['driver']}")
    if not isinstance(config["input_root"], str) or not config["input_root"].strip():
        raise ValueError("input_root must be a nonempty path")
    from datetime import datetime
    for field in ("reporting_cutoff_utc", "extract_as_of_utc"):
        value = config[field]
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError(f"{field} must be a UTC Z timestamp")
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    if config["extract_as_of_utc"] < config["reporting_cutoff_utc"]:
        raise ValueError("Export timestamp must not precede reporting cutoff")
    return config


def announce_target(config: dict, action: str) -> None:
    """Show the selected target before any setup/load/stage mutation."""
    c = config["connection"]
    print("TARGET " + json.dumps({"server": c["server"], "database": c["database"], "action": action}), file=sys.stderr)


def connect(config: dict, database: str | None = None, *, autocommit: bool = False):
    c = config["connection"]
    # Only the setup/probe path may override the validated warehouse target with master.
    target = "master" if database == "master" else validate_database_name(database if database is not None else c["database"])
    # Local Express uses a self-signed certificate. Transport remains encrypted;
    # the explicit client-only trust exception is documented in the evidence.
    parts = [
        f"DRIVER={{{c['driver']}}}", f"SERVER={c['server']}",
        f"DATABASE={target}", "Trusted_Connection=yes",
        f"Encrypt={'yes' if c['encrypt'] else 'no'}",
        f"TrustServerCertificate={'yes' if c['trust_server_certificate'] else 'no'}",
        "Connection Timeout=5",
    ]
    conn = pyodbc.connect(";".join(parts), autocommit=autocommit)
    try:
        actual = conn.cursor().execute("SELECT DB_NAME()").fetchval()
        if actual != target:
            raise RuntimeError(f"Connected database {actual!r} does not match selected target {target!r}")
        return conn
    except Exception:
        conn.close()
        raise


def table_exists(cursor, schema: str, name: str) -> bool:
    return cursor.execute("SELECT OBJECT_ID(?, 'U')", f"{schema}.{name}").fetchval() is not None


def create_if_absent(cursor, schema: str, name: str, ddl: str) -> bool:
    if table_exists(cursor, schema, name):
        return False
    cursor.execute(ddl)
    return True


def create_index_if_absent(cursor, schema: str, table: str, index: str, columns: str) -> bool:
    present = cursor.execute("SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(?) AND name=?", f"{schema}.{table}", index).fetchval()
    if present:
        return False
    cursor.execute(f"CREATE INDEX [{index}] ON [{schema}].[{table}] ({columns})")
    return True


def setup(config: dict) -> list[str]:
    database = validate_database_name(config["connection"]["database"])
    with connect(config, "master", autocommit=True) as master:
        cur = master.cursor()
        exists = cur.execute("SELECT DB_ID(?)", database).fetchval()
        if exists is None:
            cur.execute(f"CREATE DATABASE [{database}]")
    created = []
    with connect(config, autocommit=False) as conn:
        cur = conn.cursor()
        for schema in ("ctl", "bronze"):
            if cur.execute("SELECT SCHEMA_ID(?)", schema).fetchval() is None:
                cur.execute(f"CREATE SCHEMA [{schema}]")
                created.append(f"schema {schema}")
        control = {
            "refresh_run": """CREATE TABLE ctl.refresh_run (
                run_id uniqueidentifier NOT NULL CONSTRAINT DF_refresh_run_id DEFAULT NEWID() CONSTRAINT PK_refresh_run PRIMARY KEY,
                input_root nvarchar(1024) NOT NULL,
                reporting_cutoff_utc datetime2(0) NOT NULL,
                extract_as_of_utc datetime2(0) NOT NULL,
                status varchar(16) NOT NULL CONSTRAINT DF_refresh_status DEFAULT 'CREATED',
                started_at_utc datetime2(0) NOT NULL CONSTRAINT DF_refresh_started DEFAULT SYSUTCDATETIME(),
                finished_at_utc datetime2(0) NULL,
                failed_stage varchar(64) NULL,
                error_message nvarchar(2048) NULL,
                CONSTRAINT CK_refresh_dates CHECK (extract_as_of_utc >= reporting_cutoff_utc),
                CONSTRAINT CK_refresh_status CHECK (status IN ('CREATED','PREFLIGHT','LOADING','BRONZE_COMPLETE','BUILDING','VALIDATING','READY','PUBLISHED','FAILED'))
            )""",
            "source_file": """CREATE TABLE ctl.source_file (
                file_id bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_source_file PRIMARY KEY,
                run_id uniqueidentifier NOT NULL,
                source_name varchar(32) COLLATE Latin1_General_100_BIN2 NOT NULL,
                file_path nvarchar(1024) NOT NULL,
                sha256 varbinary(32) NULL,
                byte_count bigint NULL,
                data_row_count int NULL,
                loaded_row_count int NOT NULL CONSTRAINT DF_source_loaded DEFAULT 0,
                parse_status varchar(16) NOT NULL,
                checked_at_utc datetime2(0) NOT NULL CONSTRAINT DF_source_checked DEFAULT SYSUTCDATETIME(),
                failure_detail nvarchar(1000) NULL,
                CONSTRAINT FK_source_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT UQ_source_run_name UNIQUE (run_id,source_name),
                CONSTRAINT UQ_source_file_run_name UNIQUE (file_id,run_id,source_name),
                CONSTRAINT CK_source_name CHECK (source_name IN ('shipments','tracking_events','customers','customer_locations','depots','service_levels')),
                CONSTRAINT CK_source_parse CHECK (parse_status IN ('PASS','WARNING','FAIL')),
                CONSTRAINT CK_source_counts CHECK (loaded_row_count >= 0 AND (data_row_count IS NULL OR data_row_count >= 0)),
                CONSTRAINT CK_source_pass_metadata CHECK (parse_status='FAIL' OR (sha256 IS NOT NULL AND byte_count IS NOT NULL AND data_row_count IS NOT NULL))
            )""",
            "published_run": """CREATE TABLE ctl.published_run (
                singleton_id tinyint NOT NULL CONSTRAINT PK_published_run PRIMARY KEY,
                active_run_id uniqueidentifier NOT NULL,
                published_at_utc datetime2(0) NOT NULL,
                CONSTRAINT CK_published_singleton CHECK (singleton_id=1),
                CONSTRAINT FK_published_run FOREIGN KEY (active_run_id) REFERENCES ctl.refresh_run(run_id)
            )""",
            "quality_issue": """CREATE TABLE ctl.quality_issue (
                issue_id bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_quality_issue PRIMARY KEY,
                run_id uniqueidentifier NOT NULL,
                source_name varchar(32) NULL,
                source_row_number int NULL,
                entity_type varchar(32) NULL,
                entity_key nvarchar(32) NULL,
                scope varchar(16) NOT NULL,
                severity varchar(8) NOT NULL,
                reason_code varchar(64) NOT NULL,
                detail nvarchar(1000) NULL,
                created_at_utc datetime2(0) NOT NULL CONSTRAINT DF_issue_created DEFAULT SYSUTCDATETIME(),
                CONSTRAINT FK_issue_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT CK_issue_scope CHECK (scope IN ('FILE','SOURCE','INTEGRATION','REPORTING')),
                CONSTRAINT CK_issue_severity CHECK (severity IN ('INFO','WARN','ERROR'))
            )""",
            "run_check": """CREATE TABLE ctl.run_check (
                run_id uniqueidentifier NOT NULL,
                check_name varchar(100) NOT NULL,
                passed bit NOT NULL,
                observed_value nvarchar(200) NULL,
                expected_rule nvarchar(300) NOT NULL,
                detail nvarchar(1000) NULL,
                checked_at_utc datetime2(0) NOT NULL CONSTRAINT DF_check_checked DEFAULT SYSUTCDATETIME(),
                CONSTRAINT PK_run_check PRIMARY KEY (run_id,check_name),
                CONSTRAINT FK_check_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id)
            )""",
        }
        for name, ddl in control.items():
            if create_if_absent(cur, "ctl", name, ddl):
                created.append(f"ctl.{name}")
        for name, fields in HEADERS.items():
            raw = ",\n".join(f"[{field}] nvarchar(max) COLLATE Latin1_General_100_BIN2 NOT NULL" for field in fields)
            ddl = f"""CREATE TABLE bronze.[{name}] (
                run_id uniqueidentifier NOT NULL,
                source_file_id bigint NOT NULL,
                source_name varchar(32) COLLATE Latin1_General_100_BIN2 NOT NULL,
                source_row_number int NOT NULL,
                loaded_at_utc datetime2(0) NOT NULL CONSTRAINT DF_bronze_{name}_loaded DEFAULT SYSUTCDATETIME(),
                {raw},
                CONSTRAINT PK_bronze_{name} PRIMARY KEY (run_id,source_row_number),
                CONSTRAINT FK_bronze_{name}_run FOREIGN KEY (run_id) REFERENCES ctl.refresh_run(run_id),
                CONSTRAINT FK_bronze_{name}_file FOREIGN KEY (source_file_id,run_id,source_name) REFERENCES ctl.source_file(file_id,run_id,source_name),
                CONSTRAINT CK_bronze_{name}_source CHECK (source_name='{name}'),
                CONSTRAINT CK_bronze_{name}_row CHECK (source_row_number>=2)
            )"""
            if create_if_absent(cur, "bronze", name, ddl):
                created.append(f"bronze.{name}")
        indexes = [
            ("ctl", "refresh_run", "IX_refresh_status", "status,started_at_utc"),
            ("ctl", "quality_issue", "IX_issue_source", "run_id,source_name,source_row_number"),
            ("ctl", "quality_issue", "IX_issue_entity", "run_id,entity_type,entity_key"),
            ("ctl", "run_check", "IX_run_check_passed", "run_id,passed"),
        ] + [("bronze", name, f"IX_bronze_{name}_file_row", "source_file_id,source_row_number") for name in SOURCES]
        for schema, table, index, columns in indexes:
            if create_index_if_absent(cur, schema, table, index, columns):
                created.append(f"index {schema}.{table}.{index}")
        # Refuse an incompatible pre-existing table instead of altering or dropping it.
        for name, fields in HEADERS.items():
            rows = cur.execute("""SELECT c.name,t.name,c.max_length,c.is_nullable,c.collation_name
                FROM sys.columns c JOIN sys.types t ON c.user_type_id=t.user_type_id
                WHERE c.object_id=OBJECT_ID(?) ORDER BY c.column_id""", f"bronze.{name}").fetchall()
            expected = ("run_id", "source_file_id", "source_name", "source_row_number", "loaded_at_utc") + fields
            if tuple(row[0] for row in rows) != expected or any(row[1] != "nvarchar" or row[2] != -1 or row[3] for row in rows[5:]):
                raise RuntimeError(f"Incompatible existing bronze.{name} schema; setup made no destructive repair")
            if any(row[4] != "Latin1_General_100_BIN2" for row in rows[5:]):
                raise RuntimeError(f"bronze.{name} raw collation differs from BIN2 design")
        for name in control:
            if not table_exists(cur, "ctl", name):
                raise RuntimeError(f"Missing ctl.{name}")
        if not cur.execute("""SELECT 1 FROM sys.default_constraints d JOIN sys.columns c
                ON d.parent_object_id=c.object_id AND d.parent_column_id=c.column_id
                WHERE d.parent_object_id=OBJECT_ID('ctl.refresh_run') AND c.name='run_id'""").fetchval():
            cur.execute("ALTER TABLE ctl.refresh_run ADD CONSTRAINT DF_refresh_run_id DEFAULT NEWID() FOR run_id")
            created.append("default ctl.refresh_run.run_id")
        if not cur.execute("SELECT 1 FROM sys.check_constraints WHERE parent_object_id=OBJECT_ID('ctl.source_file') AND name='CK_source_pass_metadata'").fetchval():
            cur.execute("""ALTER TABLE ctl.source_file ADD CONSTRAINT CK_source_pass_metadata
                CHECK (parse_status='FAIL' OR (sha256 IS NOT NULL AND byte_count IS NOT NULL AND data_row_count IS NOT NULL))""")
            created.append("check ctl.source_file.CK_source_pass_metadata")
        control_fields = {
            "refresh_run": ("run_id", "input_root", "reporting_cutoff_utc", "extract_as_of_utc", "status", "started_at_utc", "finished_at_utc", "failed_stage", "error_message"),
            "source_file": ("file_id", "run_id", "source_name", "file_path", "sha256", "byte_count", "data_row_count", "loaded_row_count", "parse_status", "checked_at_utc", "failure_detail"),
            "published_run": ("singleton_id", "active_run_id", "published_at_utc"),
            "quality_issue": ("issue_id", "run_id", "source_name", "source_row_number", "entity_type", "entity_key", "scope", "severity", "reason_code", "detail", "created_at_utc"),
            "run_check": ("run_id", "check_name", "passed", "observed_value", "expected_rule", "detail", "checked_at_utc"),
        }
        for name, expected in control_fields.items():
            actual = tuple(row[0] for row in cur.execute("SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(?) ORDER BY column_id", f"ctl.{name}").fetchall())
            if actual != expected:
                raise RuntimeError(f"Incompatible existing ctl.{name} columns; setup made no destructive repair")
        conn.commit()
    return created


def strict_quote_check(text: str) -> None:
    """Reject quote syntax that csv.reader tolerates outside the CSV contract."""
    state = "START"
    for char in text:
        if state == "START":
            if char == '"':
                state = "QUOTED"
            elif char == ",":
                pass
            elif char == "\n":
                pass
            else:
                state = "UNQUOTED"
        elif state == "UNQUOTED":
            if char == '"':
                raise ValueError("Quote within unquoted field")
            if char == "," or char == "\n":
                state = "START"
        elif state == "QUOTED":
            if char == '"':
                state = "CLOSED"
            elif char == "\n":
                raise ValueError("Embedded newline within quoted field")
        else:  # CLOSED
            if char == '"':
                state = "QUOTED"  # escaped pair of quotes
            elif char == "," or char == "\n":
                state = "START"
            else:
                raise ValueError("Text after closing quote")
    if state == "QUOTED":
        raise ValueError("Unclosed quoted field")


def snapshot(name: str, root: Path) -> FileSnapshot:
    path = root / f"{name}.csv"
    result = FileSnapshot(name, path)
    try:
        data = path.read_bytes()
        result.sha256 = hashlib.sha256(data).digest()
        result.byte_count = len(data)
        warnings = []
        if data.startswith(b"\xef\xbb\xbf"):
            warnings.append("UTF8_BOM")
        text = data.decode("utf-8-sig", errors="strict")
        if "\r\n" in text:
            warnings.append("CRLF")
        if "\r" in text.replace("\r\n", ""):
            raise ValueError("Bare CR line ending")
        if not text.endswith("\n"):
            raise ValueError("Missing final newline")
        strict_quote_check(text.replace("\r\n", "\n"))
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"', doublequote=True, strict=True)
        header = tuple(next(reader))
        if header != HEADERS[name]:
            raise ValueError(f"Header/order mismatch: {header!r}")
        rows = []
        for line_number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise ValueError(f"Row {line_number}: expected {len(header)} fields, got {len(row)}")
            if any("\n" in value or "\r" in value for value in row):
                raise ValueError(f"Row {line_number}: embedded newline in field")
            rows.append(tuple(row))
        result.rows = rows
        result.warnings = tuple(warnings)
    except (OSError, UnicodeError, csv.Error, ValueError, StopIteration) as exc:
        result.error = f"{type(exc).__name__}: {exc}"[:1000]
    return result


def preflight(root: Path) -> list[FileSnapshot]:
    found = {p.name for p in root.glob("*.csv")} if root.is_dir() else set()
    expected = {f"{name}.csv" for name in SOURCES}
    snapshots = [snapshot(name, root) for name in SOURCES]
    extra = found - expected
    if extra:
        # Keep six manifest records while making the whole run visibly invalid.
        snapshots[0].error = f"Unexpected CSV files: {', '.join(sorted(extra))}"[:1000]
    return snapshots


def load(config: dict, input_root: Path, *, fail_source: str | None = None, fail_after_rows: int | None = None) -> tuple[uuid.UUID, bool]:
    run_id = uuid.uuid4()
    root = input_root.resolve()
    with connect(config) as conn:
        cur = conn.cursor()
        cur.execute("""INSERT ctl.refresh_run
            (run_id,input_root,reporting_cutoff_utc,extract_as_of_utc,status)
            VALUES (?, ?, CONVERT(datetime2(0), ?, 126), CONVERT(datetime2(0), ?, 126), 'PREFLIGHT')""",
            str(run_id), str(root), config["reporting_cutoff_utc"].rstrip("Z"), config["extract_as_of_utc"].rstrip("Z"))
        conn.commit()
        snapshots = preflight(root)
        file_ids = {}
        for item in snapshots:
            cur.execute("""INSERT ctl.source_file
                (run_id,source_name,file_path,sha256,byte_count,data_row_count,parse_status,failure_detail)
                OUTPUT INSERTED.file_id VALUES (?,?,?,?,?,?,?,?)""",
                str(run_id), item.name, str(item.path), item.sha256, item.byte_count,
                len(item.rows) if item.rows is not None else None,
                "FAIL" if item.error else "WARNING" if item.warnings else "PASS",
                item.error or (",".join(item.warnings) or None))
            file_ids[item.name] = cur.fetchone()[0]
        if any(item.error for item in snapshots):
            detail = "; ".join(f"{item.name}: {item.error}" for item in snapshots if item.error)
            cur.execute("""UPDATE ctl.refresh_run SET status='FAILED',failed_stage='PREFLIGHT',error_message=?,finished_at_utc=SYSUTCDATETIME() WHERE run_id=?""", detail[:2048], str(run_id))
            conn.commit()
            return run_id, False
        conn.commit()
        cur.execute("UPDATE ctl.refresh_run SET status='LOADING' WHERE run_id=?", str(run_id))
        conn.commit()
        for item in snapshots:
            try:
                # Recheck disk just before insertion. The parsed in-memory snapshot is
                # immutable even if the on-disk file changes while loading.
                if hashlib.sha256(item.path.read_bytes()).digest() != item.sha256:
                    raise RuntimeError(f"File changed after preflight: {item.name}")
                columns = ("run_id", "source_file_id", "source_name", "source_row_number") + HEADERS[item.name]
                col_sql = ",".join(f"[{col}]" for col in columns)
                marks = ",".join("?" for _ in columns)
                statement = f"INSERT bronze.[{item.name}] ({col_sql}) VALUES ({marks})"
                assert item.rows is not None
                inserted = 0
                for start in range(0, len(item.rows), 500):
                    batch = [
                        (str(run_id), file_ids[item.name], item.name, row_number, *row)
                        for row_number, row in enumerate(item.rows[start:start+500], start=start+2)
                    ]
                    if fail_source == item.name and fail_after_rows is not None and inserted < fail_after_rows <= inserted + len(batch):
                        before_failure = fail_after_rows - inserted
                        if before_failure:
                            cur.executemany(statement, batch[:before_failure])
                        raise RuntimeError(f"Controlled test failure after {fail_after_rows} inserted rows in {item.name}")
                    cur.executemany(statement, batch)
                    inserted += len(batch)
                count = cur.execute(f"SELECT COUNT_BIG(*) FROM bronze.[{item.name}] WHERE run_id=?", str(run_id)).fetchval()
                if count != len(item.rows):
                    raise RuntimeError(f"Loaded count mismatch: {item.name}: {count} != {len(item.rows)}")
                cur.execute("UPDATE ctl.source_file SET loaded_row_count=? WHERE file_id=? AND run_id=?", count, file_ids[item.name], str(run_id))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                cur.execute("""UPDATE ctl.refresh_run SET status='FAILED',failed_stage='BRONZE_LOAD',error_message=?,finished_at_utc=SYSUTCDATETIME() WHERE run_id=?""", f"{type(exc).__name__}: {exc}"[:2048], str(run_id))
                conn.commit()
                return run_id, False
        cur.execute("UPDATE ctl.refresh_run SET status='BRONZE_COMPLETE',finished_at_utc=SYSUTCDATETIME() WHERE run_id=?", str(run_id))
        conn.commit()
    return run_id, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("probe", "setup", "load"))
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "bronze_runtime.json")
    parser.add_argument("--input-root", type=Path, help="Override only the six-file input directory")
    parser.add_argument("--fail-source", choices=SOURCES, help="Test-only controlled failure source")
    parser.add_argument("--fail-after-rows", type=int, help="Test-only insert count before rollback")
    args = parser.parse_args()
    try:
        config = config_from(args.config)
        announce_target(config, args.action)
        if args.action == "probe":
            with connect(config, "master") as conn:
                row = conn.cursor().execute("SELECT @@VERSION, DB_ID(?)", config["connection"]["database"]).fetchone()
                print(json.dumps({"server_version": row[0].splitlines()[0], "database_exists": row[1] is not None, "driver": config["connection"]["driver"], "python": sys.version.split()[0], "pyodbc": pyodbc.version}))
        elif args.action == "setup":
            print(json.dumps({"created": setup(config)}))
        else:
            if (args.fail_source is None) != (args.fail_after_rows is None) or (args.fail_after_rows is not None and args.fail_after_rows < 1):
                raise ValueError("Test failure requires --fail-source and positive --fail-after-rows")
            root = args.input_root or ROOT / config["input_root"]
            run_id, ok = load(config, root, fail_source=args.fail_source, fail_after_rows=args.fail_after_rows)
            print(json.dumps({"run_id": str(run_id), "status": "BRONZE_COMPLETE" if ok else "FAILED", "input_root": str(root.resolve())}))
            return 0 if ok else 1
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
