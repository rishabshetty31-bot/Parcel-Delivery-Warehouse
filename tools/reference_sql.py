"""Fixed-source T-SQL builder for Step 6A reference resolution.

All row comparisons, version decisions, type checks, and inserts execute in
SQL Server. Python supplies reviewed source metadata, not source-row answers.
"""

from __future__ import annotations

from bronze_pipeline import HEADERS


SPECS = {
    "shipments": {"key": "shipment_id", "trim": (), "immutable": ()},
    "customers": {"key": "customer_id", "trim": ("customer_name",), "immutable": ("account_opened_at_utc",)},
    "depots": {"key": "depot_id", "trim": ("depot_name", "city_name"), "immutable": ()},
    "service_levels": {"key": "service_code", "trim": ("service_name",), "immutable": ("nominal_transit_hours",)},
    "customer_locations": {"key": "location_id", "trim": ("location_name", "city_name"), "immutable": ("customer_id",)},
}
ORDER = ("customers", "depots", "service_levels", "customer_locations")
BIN = "Latin1_General_100_BIN2"


def q(name: str) -> str:
    return f"[{name}]"


def strict_time(value: str) -> str:
    pattern = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z"
    return (f"CASE WHEN DATALENGTH({value})=40 AND {value} COLLATE {BIN} LIKE N'{pattern}' "
            f"THEN TRY_CONVERT(datetime2(0), LEFT({value},19),126) END")


def positive_int(value: str) -> str:
    return (f"CASE WHEN DATALENGTH({value}) BETWEEN 2 AND 20 "
            f"AND {value} COLLATE {BIN} NOT LIKE N'%[^0-9]%' "
            f"AND TRY_CONVERT(int,{value})>0 THEN TRY_CONVERT(int,{value}) END")


def nonnegative_int(value: str) -> str:
    return (f"CASE WHEN DATALENGTH({value}) BETWEEN 2 AND 20 "
            f"AND {value} COLLATE {BIN} NOT LIKE N'%[^0-9]%' "
            f"THEN TRY_CONVERT(int,{value}) END")


def binary_equal(a: str, b: str) -> str:
    return f"(DATALENGTH({a})=DATALENGTH({b}) AND CONVERT(varbinary(max),{a})=CONVERT(varbinary(max),{b}))"


def binary_value(a: str, value: str) -> str:
    return binary_equal(a, f"N'{value}'")


def binary_one_of(a: str, values: tuple[str, ...]) -> str:
    return "(" + " OR ".join(binary_value(a, v) for v in values) + ")"


def id_valid(value: str, kind: str) -> str:
    if kind == "shipment":
        return f"(DATALENGTH({value})=24 AND {value} COLLATE {BIN} LIKE N'SHP[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]')"
    if kind == "customer":
        return f"(DATALENGTH({value})=14 AND {value} COLLATE {BIN} LIKE N'CUS[0-9][0-9][0-9][0-9]')"
    if kind == "location":
        return f"(DATALENGTH({value})=16 AND {value} COLLATE {BIN} LIKE N'LOC[0-9][0-9][0-9][0-9][0-9]')"
    if kind == "depot":
        return f"(DATALENGTH({value})=10 AND {value} COLLATE {BIN} LIKE N'DEP[0-9][0-9]')"
    return binary_one_of(value, ("ECONOMY", "STANDARD", "EXPRESS"))


def valid_text(value: str, max_chars: int) -> str:
    return f"(DATALENGTH(LTRIM(RTRIM({value}))) BETWEEN 2 AND {max_chars * 2})"


def issue(statement: str, reason: str, impact: str, priority: int, condition: str) -> str:
    return (f"INSERT #issues (source_row_number,reason_code,impact,priority) "
            f"SELECT r.source_row_number,'{reason}','{impact}',{priority} FROM #r r "
            f"JOIN bronze.[{statement}] b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number "
            f"WHERE r.disposition IN ('AC','Q') AND ({condition});")


def content_rules(source: str) -> str:
    region = binary_one_of("b.region_code", ("NORTH_REACH", "EAST_VALE", "SOUTH_MERE", "WEST_FEN"))
    rules = []
    if source == "shipments":
        booked = strict_time("b.booked_at_utc")
        handover = strict_time("b.handed_over_at_utc")
        promise = strict_time("b.promised_delivery_at_utc")
        cancel = strict_time("b.cancelled_at_utc")
        through = strict_time("b.attempt_history_through_at_utc")
        attested = strict_time("b.attempt_history_attested_at_utc")
        status = "b.attempt_history_status"
        rules += [
            issue(source,"BOOKING_INVALID","Q",30,f"{booked} IS NULL"),
            issue(source,"BOOKING_AFTER_COMMIT","Q",40,f"{booked}>r.commit_dt"),
        ]
        for field, kind, table in (("customer_id","customer","customer"),("service_code","service","service_level"),
                                   ("origin_depot_id","depot","depot"),("destination_depot_id","depot","depot")):
            key = "service_code" if kind == "service" else field
            if kind == "depot": key = "depot_id"
            rules.append(issue(source,f"{field.upper()}_INVALID","AF",60,f"NOT {id_valid('b.'+field,kind)}"))
            rules.append(issue(source,f"{field.upper()}_UNRESOLVED","AF",70,
                f"{id_valid('b.'+field,kind)} AND NOT EXISTS (SELECT 1 FROM silver.{table} m WHERE m.run_id=@run_id AND "
                f"CONVERT(varbinary(max),CONVERT(nvarchar(32),m.{key}))=CONVERT(varbinary(max),b.{field}) "
                f"AND DATALENGTH(CONVERT(nvarchar(32),m.{key}))=DATALENGTH(b.{field}))"))
        rules += [
            issue(source,"PROMISE_MISSING","AF",60,"DATALENGTH(b.promised_delivery_at_utc)=0"),
            issue(source,"PROMISE_INVALID","AF",60,f"DATALENGTH(b.promised_delivery_at_utc)>0 AND {promise} IS NULL"),
            issue(source,"PROMISE_BEFORE_BOOKING","AF",65,f"{promise}<{booked}"),
            issue(source,"PROMISE_BEFORE_HANDOVER","AF",65,f"{promise}<{handover}"),
            issue(source,"HANDOVER_INVALID","AF",60,f"DATALENGTH(b.handed_over_at_utc)>0 AND {handover} IS NULL"),
            issue(source,"HANDOVER_BEFORE_BOOKING","AF",65,f"{handover}<{booked}"),
            issue(source,"HANDOVER_AFTER_COMMIT","AF",65,f"{handover}>r.commit_dt"),
            issue(source,"CANCELLATION_INVALID","AF",60,f"DATALENGTH(b.cancelled_at_utc)>0 AND {cancel} IS NULL"),
            issue(source,"CANCELLATION_BEFORE_BOOKING","AF",65,f"{cancel}<{booked}"),
            issue(source,"CANCELLATION_AFTER_COMMIT","AF",65,f"{cancel}>r.commit_dt"),
            issue(source,"CANCELLATION_HANDOVER_CONFLICT","AF",70,f"{cancel} IS NOT NULL AND {handover} IS NOT NULL"),
            issue(source,"CERT_STATUS_INVALID","AF",60,f"NOT {binary_one_of(status,('COMPLETE','PARTIAL','UNAVAILABLE'))}"),
            issue(source,"CERT_THROUGH_INVALID","AF",60,
                  f"({binary_one_of(status,('COMPLETE','PARTIAL'))} AND {through} IS NULL) OR "
                  f"({binary_value(status,'UNAVAILABLE')} AND DATALENGTH(b.attempt_history_through_at_utc)>0)"),
            issue(source,"CERT_COUNT_INVALID","AF",60,
                  f"({binary_value(status,'COMPLETE')} AND {nonnegative_int('b.attempt_history_attempt_count')} IS NULL) OR "
                  f"(NOT {binary_value(status,'COMPLETE')} AND DATALENGTH(b.attempt_history_attempt_count)>0)"),
            issue(source,"CERT_ATTESTATION_INVALID","AF",60,
                  f"({binary_one_of(status,('COMPLETE','PARTIAL'))} AND {attested} IS NULL) OR "
                  f"({binary_value(status,'UNAVAILABLE')} AND DATALENGTH(b.attempt_history_attested_at_utc)>0)"),
            issue(source,"CERT_CHRONOLOGY_INVALID","AF",65,
                  f"({through} IS NOT NULL AND {attested} IS NOT NULL AND {through}>{attested}) OR "
                  f"({through} IS NOT NULL AND {through}<{booked}) OR "
                  f"({attested} IS NOT NULL AND ({attested}>r.commit_dt OR {attested}<{booked}))"),
        ]
    elif source == "customers":
        rules += [
            issue(source, "ACCOUNT_OPEN_INVALID", "Q", 30, f"{strict_time('b.account_opened_at_utc')} IS NULL"),
            issue(source, "ACCOUNT_OPEN_AFTER_COMMIT", "Q", 40, f"{strict_time('b.account_opened_at_utc')} > r.commit_dt"),
            issue(source, "CUSTOMER_NAME_INVALID", "AF", 60, f"NOT {valid_text('b.customer_name',100)}"),
            issue(source, "CUSTOMER_NAME_TRIMMED", "AF", 60, "DATALENGTH(b.customer_name)<>DATALENGTH(LTRIM(RTRIM(b.customer_name)))"),
            issue(source, "CUSTOMER_SEGMENT_INVALID", "AF", 60, f"NOT {binary_one_of('b.customer_segment',('SMALL','MIDMARKET','ENTERPRISE'))}"),
        ]
    elif source == "depots":
        rules += [
            issue(source, "DEPOT_NAME_INVALID", "AF", 60, f"NOT {valid_text('b.depot_name',80)}"),
            issue(source, "DEPOT_NAME_TRIMMED", "AF", 60, "DATALENGTH(b.depot_name)<>DATALENGTH(LTRIM(RTRIM(b.depot_name)))"),
            issue(source, "DEPOT_CITY_INVALID", "AF", 60, f"NOT {valid_text('b.city_name',60)}"),
            issue(source, "DEPOT_CITY_TRIMMED", "AF", 60, "DATALENGTH(b.city_name)<>DATALENGTH(LTRIM(RTRIM(b.city_name)))"),
            issue(source, "DEPOT_REGION_INVALID", "AF", 60, f"NOT {region}"),
        ]
    elif source == "service_levels":
        target = "CASE b.service_code WHEN N'ECONOMY' THEN 72 WHEN N'STANDARD' THEN 48 WHEN N'EXPRESS' THEN 24 END"
        rules += [
            issue(source, "SERVICE_NAME_INVALID", "AF", 60, f"NOT {valid_text('b.service_name',60)}"),
            issue(source, "SERVICE_NAME_TRIMMED", "AF", 60, "DATALENGTH(b.service_name)<>DATALENGTH(LTRIM(RTRIM(b.service_name)))"),
            issue(source, "SERVICE_TARGET_INVALID", "AF", 60, f"{positive_int('b.nominal_transit_hours')} IS NULL OR {positive_int('b.nominal_transit_hours')}<>{target}"),
        ]
    else:
        rules += [
            issue(source, "LOCATION_OWNER_INVALID", "Q", 30, f"NOT {id_valid('b.customer_id','customer')}"),
            issue(source, "LOCATION_OWNER_UNRESOLVED", "Q", 50, "NOT EXISTS (SELECT 1 FROM silver.customer c WHERE c.run_id=@run_id AND CONVERT(varbinary(max),CONVERT(nvarchar(7),c.customer_id))=CONVERT(varbinary(max),b.customer_id) AND DATALENGTH(CONVERT(nvarchar(7),c.customer_id))=DATALENGTH(b.customer_id))"),
            issue(source, "LOCATION_ACTIVE_INVALID", "Q", 30, f"NOT {binary_one_of('b.is_active',('true','false'))}"),
            issue(source, "LOCATION_PRIMARY_INVALID", "Q", 30, f"NOT {binary_one_of('b.is_primary',('true','false'))}"),
            issue(source, "LOCATION_NAME_TRIMMED", "AF", 60, "DATALENGTH(b.location_name)>0 AND DATALENGTH(b.location_name)<>DATALENGTH(LTRIM(RTRIM(b.location_name)))"),
            issue(source, "LOCATION_NAME_OVERLENGTH", "AF", 60, "DATALENGTH(LTRIM(RTRIM(b.location_name)))>200"),
            issue(source, "LOCATION_CITY_INVALID", "AF", 60, f"NOT {valid_text('b.city_name',60)}"),
            issue(source, "LOCATION_CITY_TRIMMED", "AF", 60, "DATALENGTH(b.city_name)<>DATALENGTH(LTRIM(RTRIM(b.city_name)))"),
            issue(source, "LOCATION_REGION_INVALID", "AF", 60, f"NOT {region}"),
            issue(source, "INACTIVE_PRIMARY", "AF", 60, f"{binary_value('b.is_primary','true')} AND {binary_value('b.is_active','false')}"),
        ]
    return "\n".join(rules)


def accepted_insert(source: str) -> str:
    if source == "shipments":
        booked, handover, promise, cancel = (strict_time(f"b.{field}") for field in
            ("booked_at_utc", "handed_over_at_utc", "promised_delivery_at_utc", "cancelled_at_utc"))
        def state(field: str, parsed: str, valid: str = "1=1") -> str:
            return (f"CASE WHEN DATALENGTH(b.{field})=0 THEN 'MISSING' "
                    f"WHEN {parsed} IS NOT NULL AND ({valid}) THEN 'VALID' ELSE 'INVALID' END")
        hv = f"{handover} >= {booked} AND {handover} <= r.commit_dt"
        pv = f"{promise} >= {booked} AND ({handover} IS NULL OR {promise} >= {handover})"
        cv = f"{cancel} >= {booked} AND {cancel} <= r.commit_dt"
        return f"""INSERT silver.shipment
          (run_id,shipment_id,customer_id,service_code,origin_depot_id,destination_depot_id,
           booked_at_utc,handed_over_at_utc,promised_delivery_at_utc,cancelled_at_utc,
           handover_value_state,promise_value_state,cancellation_value_state,
           attempt_history_status,attempt_history_through_at_utc,attempt_history_attempt_count,
           attempt_history_attested_at_utc,record_version,source_updated_at_utc,source_row_number)
        SELECT @run_id,CONVERT(char(12),b.shipment_id),
          CASE WHEN {id_valid('b.customer_id','customer')} THEN CONVERT(char(7),b.customer_id) END,
          CASE WHEN {id_valid('b.service_code','service')} THEN CONVERT(varchar(8),b.service_code) END,
          CASE WHEN {id_valid('b.origin_depot_id','depot')} THEN CONVERT(char(5),b.origin_depot_id) END,
          CASE WHEN {id_valid('b.destination_depot_id','depot')} THEN CONVERT(char(5),b.destination_depot_id) END,
          {booked},CASE WHEN {hv} THEN {handover} END,
          CASE WHEN {pv} THEN {promise} END,
          CASE WHEN {cv} THEN {cancel} END,
          {state('handed_over_at_utc',handover,hv)},
          {state('promised_delivery_at_utc',promise,pv)},
          {state('cancelled_at_utc',cancel,cv)},
          CASE WHEN {binary_one_of('b.attempt_history_status',('COMPLETE','PARTIAL','UNAVAILABLE'))}
            THEN CONVERT(varchar(11),b.attempt_history_status) END,
          {strict_time('b.attempt_history_through_at_utc')},
          {nonnegative_int('b.attempt_history_attempt_count')},
          {strict_time('b.attempt_history_attested_at_utc')},
          r.version_int,r.commit_dt,r.source_row_number
        FROM #r r JOIN bronze.shipments b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number
        WHERE r.disposition IN ('AC','AF');"""
    if source == "customers":
        return f"""INSERT silver.customer (run_id,customer_id,customer_name,customer_segment,account_opened_at_utc,record_version,source_updated_at_utc,source_row_number)
        SELECT @run_id,CONVERT(char(7),b.customer_id),
          CASE WHEN {valid_text('b.customer_name',100)} THEN CONVERT(nvarchar(100),LTRIM(RTRIM(b.customer_name))) END,
          CASE WHEN {binary_one_of('b.customer_segment',('SMALL','MIDMARKET','ENTERPRISE'))} THEN CONVERT(varchar(12),b.customer_segment) END,
          {strict_time('b.account_opened_at_utc')},r.version_int,r.commit_dt,r.source_row_number
        FROM #r r JOIN bronze.customers b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number WHERE r.disposition IN ('AC','AF');"""
    if source == "depots":
        region = binary_one_of("b.region_code", ("NORTH_REACH", "EAST_VALE", "SOUTH_MERE", "WEST_FEN"))
        return f"""INSERT silver.depot (run_id,depot_id,depot_name,city_name,region_code,record_version,source_updated_at_utc,source_row_number)
        SELECT @run_id,CONVERT(char(5),b.depot_id),
          CASE WHEN {valid_text('b.depot_name',80)} THEN CONVERT(nvarchar(80),LTRIM(RTRIM(b.depot_name))) END,
          CASE WHEN {valid_text('b.city_name',60)} THEN CONVERT(nvarchar(60),LTRIM(RTRIM(b.city_name))) END,
          CASE WHEN {region} THEN CONVERT(varchar(12),b.region_code) END,
          r.version_int,r.commit_dt,r.source_row_number
        FROM #r r JOIN bronze.depots b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number WHERE r.disposition IN ('AC','AF');"""
    if source == "service_levels":
        target = "CASE b.service_code WHEN N'ECONOMY' THEN 72 WHEN N'STANDARD' THEN 48 WHEN N'EXPRESS' THEN 24 END"
        return f"""INSERT silver.service_level (run_id,service_code,service_name,nominal_transit_hours,record_version,source_updated_at_utc,source_row_number)
        SELECT @run_id,CONVERT(varchar(8),b.service_code),
          CASE WHEN {valid_text('b.service_name',60)} THEN CONVERT(nvarchar(60),LTRIM(RTRIM(b.service_name))) END,
          CASE WHEN {positive_int('b.nominal_transit_hours')}={target} THEN {positive_int('b.nominal_transit_hours')} END,
          r.version_int,r.commit_dt,r.source_row_number
        FROM #r r JOIN bronze.service_levels b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number WHERE r.disposition IN ('AC','AF');"""
    region = binary_one_of("b.region_code", ("NORTH_REACH", "EAST_VALE", "SOUTH_MERE", "WEST_FEN"))
    return f"""INSERT silver.customer_location (run_id,location_id,customer_id,location_name,city_name,region_code,is_active,is_primary,record_version,source_updated_at_utc,source_row_number)
    SELECT @run_id,CONVERT(char(8),b.location_id),CONVERT(char(7),b.customer_id),
      CASE WHEN DATALENGTH(LTRIM(RTRIM(b.location_name))) BETWEEN 2 AND 200 THEN CONVERT(nvarchar(100),LTRIM(RTRIM(b.location_name))) END,
      CASE WHEN {valid_text('b.city_name',60)} THEN CONVERT(nvarchar(60),LTRIM(RTRIM(b.city_name))) END,
      CASE WHEN {region} THEN CONVERT(varchar(12),b.region_code) END,
      CASE WHEN {binary_value('b.is_active','true')} THEN CONVERT(bit,1) ELSE CONVERT(bit,0) END,
      CASE WHEN {binary_value('b.is_primary','true')} THEN CONVERT(bit,1) ELSE CONVERT(bit,0) END,
      r.version_int,r.commit_dt,r.source_row_number
    FROM #r r JOIN bronze.customer_locations b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number WHERE r.disposition IN ('AC','AF');"""


def resolve_sql(source: str) -> str:
    """Return one SQL Server batch for one fixed source. One ? binds the run ID."""
    if source not in SPECS:
        raise ValueError(source)
    spec = SPECS[source]
    key = spec["key"]
    fields = HEADERS[source]
    raw_cmp = " AND ".join(binary_equal(f"b.{q(f)}", f"p.{q(f)}") for f in fields)
    def norm(alias: str, field: str) -> str:
        value = f"{alias}.{q(field)}"
        return f"LTRIM(RTRIM({value}))" if field in spec["trim"] else value
    norm_cmp = " AND ".join(binary_equal(norm("b", f), norm("p", f)) for f in fields)
    key_kind = {"shipments": "shipment", "customers": "customer", "depots": "depot", "service_levels": "service", "customer_locations": "location"}[source]
    immutable = ""
    if source == "shipments":
        clauses = []
        for field,kind in (("customer_id","customer"),("service_code","service"),
                           ("origin_depot_id","depot"),("destination_depot_id","depot")):
            clauses.append(f"({id_valid('a.'+field,kind)} AND {id_valid('z.'+field,kind)} AND NOT {binary_equal('a.'+field,'z.'+field)})")
        for field in ("booked_at_utc","promised_delivery_at_utc"):
            va,vz = strict_time('a.'+field),strict_time('z.'+field)
            clauses.append(f"({va} IS NOT NULL AND {vz} IS NOT NULL AND {va}<>{vz})")
        for field in ("handed_over_at_utc","cancelled_at_utc"):
            va,vz = strict_time('a.'+field),strict_time('z.'+field)
            clauses.append(f"({va} IS NOT NULL AND {vz} IS NOT NULL AND {va}<>{vz})")
            clauses.append(f"(({positive_int('a.record_version')}<{positive_int('z.record_version')} AND {va} IS NOT NULL AND DATALENGTH(z.{field})=0) OR "
                           f"({positive_int('z.record_version')}<{positive_int('a.record_version')} AND {vz} IS NOT NULL AND DATALENGTH(a.{field})=0))")
        wa,wz = strict_time('a.attempt_history_through_at_utc'),strict_time('z.attempt_history_through_at_utc')
        clauses.append(f"(({positive_int('a.record_version')}<{positive_int('z.record_version')} AND {wa} IS NOT NULL AND {wz} IS NOT NULL AND {wa}>{wz}) OR "
                       f"({positive_int('z.record_version')}<{positive_int('a.record_version')} AND {wa} IS NOT NULL AND {wz} IS NOT NULL AND {wz}>{wa}))")
        immutable = " OR ".join(clauses)
    elif source == "customers":
        val_a, val_b = strict_time("a.account_opened_at_utc"), strict_time("z.account_opened_at_utc")
        immutable = f"{val_a} IS NOT NULL AND {val_b} IS NOT NULL AND {val_a}<>{val_b}"
    elif source == "customer_locations":
        immutable = f"{id_valid('a.customer_id','customer')} AND {id_valid('z.customer_id','customer')} AND NOT {binary_equal('a.customer_id','z.customer_id')}"
    elif source == "service_levels":
        val_a, val_b = positive_int("a.nominal_transit_hours"), positive_int("z.nominal_transit_hours")
        immutable = f"{val_a} IS NOT NULL AND {val_b} IS NOT NULL AND {val_a}<>{val_b}"
    immutable_sql = ""
    if immutable:
        immutable_sql = f"""INSERT #issues (source_row_number,reason_code,impact,priority)
        SELECT r.source_row_number,'IMMUTABLE_VALUE_CHANGED','Q',20 FROM #r r WHERE r.disposition IS NULL AND EXISTS (
          SELECT 1 FROM #r x JOIN #r y ON {binary_equal('x.business_key_text','y.business_key_text')} AND x.source_row_number<y.source_row_number
          JOIN bronze.[{source}] a ON a.run_id=@run_id AND a.source_row_number=x.source_row_number
          JOIN bronze.[{source}] z ON z.run_id=@run_id AND z.source_row_number=y.source_row_number
          WHERE x.disposition IS NULL AND y.disposition IS NULL AND x.key_valid=1 AND x.business_key_text=r.business_key_text
            AND ({immutable}));"""
    return f"""
    SET NOCOUNT ON;
    DECLARE @run_id uniqueidentifier = CONVERT(uniqueidentifier,?);
    DECLARE @E datetime2(0) = (SELECT extract_as_of_utc FROM ctl.refresh_run WHERE run_id=@run_id);
    SELECT b.source_row_number,CONVERT(nvarchar(32),LEFT(b.{q(key)},32)) AS business_key_text,
      CONVERT(bit,CASE WHEN {id_valid('b.'+q(key),key_kind)} THEN 1 ELSE 0 END) AS key_valid,
      {positive_int('b.record_version')} AS version_int,
      {strict_time('b.source_updated_at_utc')} AS commit_dt,
      CONVERT(int,NULL) AS exact_rep, CONVERT(int,NULL) AS equiv_rep,
      CONVERT(char(2),NULL) AS disposition
    INTO #r FROM bronze.[{source}] b WHERE b.run_id=@run_id;
    CREATE UNIQUE CLUSTERED INDEX IX_work_row ON #r(source_row_number);
    CREATE INDEX IX_work_key ON #r(business_key_text,source_row_number);
    CREATE TABLE #issues(source_row_number int NOT NULL,reason_code varchar(64) NOT NULL,impact char(2) NOT NULL,priority int NOT NULL);

    -- Compare every raw field as bytes, including length, before normalization.
    UPDATE r SET exact_rep=x.rep FROM #r r
      JOIN bronze.[{source}] b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number
      CROSS APPLY (SELECT MIN(p.source_row_number) AS rep FROM bronze.[{source}] p
        JOIN #r pr ON pr.source_row_number=p.source_row_number
          AND pr.business_key_text=r.business_key_text
        WHERE p.run_id=@run_id AND p.source_row_number<=r.source_row_number AND {raw_cmp}) x;
    UPDATE #r SET disposition='DX' WHERE exact_rep<>source_row_number;

    -- Only descriptive fields may be trimmed for normalized equivalence.
    UPDATE r SET equiv_rep=x.rep FROM #r r
      JOIN bronze.[{source}] b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number
      CROSS APPLY (SELECT MIN(p.source_row_number) AS rep FROM bronze.[{source}] p
        JOIN #r pr ON pr.source_row_number=p.source_row_number AND pr.exact_rep=pr.source_row_number
          AND pr.business_key_text=r.business_key_text
        WHERE p.run_id=@run_id AND p.source_row_number<=r.source_row_number AND {norm_cmp}) x
      WHERE r.disposition IS NULL;
    UPDATE #r SET disposition='DE' WHERE disposition IS NULL AND equiv_rep<>source_row_number;

    INSERT #issues SELECT source_row_number,'KEY_INVALID','Q',10 FROM #r WHERE disposition IS NULL AND key_valid=0;
    INSERT #issues SELECT r.source_row_number,'ORDER_METADATA_INVALID','Q',20 FROM #r r
      WHERE r.disposition IS NULL AND r.key_valid=1 AND EXISTS (
        SELECT 1 FROM #r x WHERE x.disposition IS NULL AND x.key_valid=1
          AND {binary_equal('x.business_key_text','r.business_key_text')}
          AND (x.version_int IS NULL OR x.commit_dt IS NULL OR x.commit_dt>@E));
    INSERT #issues SELECT r.source_row_number,'SAME_VERSION_CONFLICT','Q',20 FROM #r r
      WHERE r.disposition IS NULL AND r.key_valid=1 AND r.version_int IS NOT NULL AND EXISTS (
        SELECT 1 FROM #r x JOIN #r y ON {binary_equal('x.business_key_text','y.business_key_text')}
          AND x.version_int=y.version_int AND x.source_row_number<y.source_row_number
        WHERE x.disposition IS NULL AND y.disposition IS NULL AND x.key_valid=1
          AND {binary_equal('x.business_key_text','r.business_key_text')});
    INSERT #issues SELECT r.source_row_number,'VERSION_COMMIT_REVERSED','Q',20 FROM #r r
      WHERE r.disposition IS NULL AND r.key_valid=1 AND EXISTS (
        SELECT 1 FROM #r x JOIN #r y ON {binary_equal('x.business_key_text','y.business_key_text')}
          AND x.version_int<y.version_int AND x.commit_dt>y.commit_dt
        WHERE x.disposition IS NULL AND y.disposition IS NULL AND x.key_valid=1
          AND {binary_equal('x.business_key_text','r.business_key_text')});
    {immutable_sql}
    UPDATE r SET disposition='Q' FROM #r r WHERE r.disposition IS NULL AND EXISTS
      (SELECT 1 FROM #issues i WHERE i.source_row_number=r.source_row_number AND i.impact='Q');

    -- A coherent key chooses its source-issued maximum version. No fallback.
    UPDATE r SET disposition='DS' FROM #r r WHERE r.disposition IS NULL AND EXISTS (
      SELECT 1 FROM #r x WHERE x.disposition IS NULL AND x.key_valid=1
        AND {binary_equal('x.business_key_text','r.business_key_text')} AND x.version_int>r.version_int);
    UPDATE #r SET disposition='AC' WHERE disposition IS NULL;
    {content_rules(source)}
    UPDATE r SET disposition='Q' FROM #r r WHERE r.disposition='AC' AND EXISTS
      (SELECT 1 FROM #issues i WHERE i.source_row_number=r.source_row_number AND i.impact='Q');
    UPDATE r SET disposition='AF' FROM #r r WHERE r.disposition='AC' AND EXISTS
      (SELECT 1 FROM #issues i WHERE i.source_row_number=r.source_row_number AND i.impact='AF');

    INSERT silver.row_resolution(run_id,source_name,source_row_number,business_key_text,disposition,representative_row_number,primary_reason_code)
    SELECT @run_id,'{source}',r.source_row_number,r.business_key_text,r.disposition,
      CASE WHEN r.disposition='DX' THEN r.exact_rep WHEN r.disposition='DE' THEN r.equiv_rep
           WHEN r.disposition='DS' THEN (SELECT TOP(1) x.source_row_number FROM #r x
              WHERE x.disposition IN('AC','AF','Q') AND {binary_equal('x.business_key_text','r.business_key_text')}
              ORDER BY x.version_int DESC,x.source_row_number)
           WHEN r.disposition IN('AC','AF') THEN r.source_row_number END,
      (SELECT TOP(1) i.reason_code FROM #issues i WHERE i.source_row_number=r.source_row_number
        ORDER BY CASE WHEN i.impact='Q' THEN 0 ELSE 1 END,i.priority,i.reason_code)
    FROM #r r;
    INSERT ctl.quality_issue(run_id,source_name,source_row_number,entity_type,entity_key,scope,severity,reason_code)
    SELECT DISTINCT @run_id,'{source}',i.source_row_number,'REFERENCE',r.business_key_text,'SOURCE',
      CASE WHEN i.impact='Q' THEN 'ERROR' ELSE 'WARN' END,i.reason_code
    FROM #issues i JOIN #r r ON r.source_row_number=i.source_row_number;
    {accepted_insert(source)}
    DROP TABLE #issues;
    DROP TABLE #r;
    """


def primary_selection_sql() -> str:
    return """
    SET NOCOUNT ON;
    DECLARE @run_id uniqueidentifier=CONVERT(uniqueidentifier,?);
    ;WITH site AS (
      SELECT customer_id,
        SUM(CASE WHEN is_active=1 AND is_primary=1 THEN 1 ELSE 0 END) AS candidate_count,
        SUM(CASE WHEN is_active=0 AND is_primary=1 THEN 1 ELSE 0 END) AS inactive_primary_count
      FROM silver.customer_location WHERE run_id=@run_id GROUP BY customer_id
    ), held AS (
      SELECT CONVERT(char(7),b.customer_id) AS customer_id,COUNT_BIG(*) AS held_count
      FROM silver.row_resolution rr JOIN bronze.customer_locations b
        ON b.run_id=rr.run_id AND b.source_row_number=rr.source_row_number
      JOIN silver.customer c ON c.run_id=rr.run_id AND
        CONVERT(varbinary(max),CONVERT(nvarchar(7),c.customer_id))=CONVERT(varbinary(max),b.customer_id)
        AND DATALENGTH(CONVERT(nvarchar(7),c.customer_id))=DATALENGTH(b.customer_id)
      WHERE rr.run_id=@run_id AND rr.source_name='customer_locations' AND rr.disposition='Q'
        AND (CONVERT(varbinary(max),b.is_primary)<>CONVERT(varbinary(max),N'false')
             OR DATALENGTH(b.is_primary)<>DATALENGTH(N'false'))
      GROUP BY b.customer_id
    )
    INSERT silver.customer_primary_location
      (run_id,customer_id,selected_location_id,city_name,region_code,candidate_count,selection_status)
    SELECT @run_id,c.customer_id,
      CASE WHEN COALESCE(h.held_count,0)=0 AND COALESCE(s.inactive_primary_count,0)=0
           AND COALESCE(s.candidate_count,0)=1 THEN p.location_id END,
      CASE WHEN COALESCE(h.held_count,0)=0 AND COALESCE(s.inactive_primary_count,0)=0
           AND COALESCE(s.candidate_count,0)=1 THEN p.city_name END,
      CASE WHEN COALESCE(h.held_count,0)=0 AND COALESCE(s.inactive_primary_count,0)=0
           AND COALESCE(s.candidate_count,0)=1 THEN p.region_code END,
      COALESCE(s.candidate_count,0),
      CASE WHEN COALESCE(h.held_count,0)>0 THEN 'SELECTION_HOLD'
           WHEN COALESCE(s.inactive_primary_count,0)>0 THEN 'INACTIVE_PRIMARY'
           WHEN COALESCE(s.candidate_count,0)>1 THEN 'MULTIPLE_PRIMARY'
           WHEN COALESCE(s.candidate_count,0)=0 THEN 'NO_PRIMARY'
           WHEN p.city_name IS NULL OR p.region_code IS NULL THEN 'INVALID_GEOGRAPHY'
           ELSE 'SELECTED' END
    FROM silver.customer c LEFT JOIN site s ON s.customer_id=c.customer_id
      LEFT JOIN held h ON h.customer_id=c.customer_id
      LEFT JOIN silver.customer_location p ON p.run_id=@run_id AND p.customer_id=c.customer_id
        AND p.is_active=1 AND p.is_primary=1 AND COALESCE(s.candidate_count,0)=1
    WHERE c.run_id=@run_id;
    -- Only current accepted site representatives participate. Keep earlier
    -- content flags/reasons, and leave duplicates and quarantined rows alone.
    UPDATE rr SET disposition='AF',
      primary_reason_code=COALESCE(rr.primary_reason_code,'MULTIPLE_PRIMARY')
    FROM silver.row_resolution rr
    JOIN silver.customer_location l ON l.run_id=rr.run_id
      AND l.source_row_number=rr.source_row_number
    JOIN silver.customer_primary_location p ON p.run_id=l.run_id
      AND p.customer_id=l.customer_id
    WHERE rr.run_id=@run_id AND rr.source_name='customer_locations'
      AND rr.disposition IN ('AC','AF') AND l.is_active=1 AND l.is_primary=1
      AND p.selection_status='MULTIPLE_PRIMARY';
    INSERT ctl.quality_issue
      (run_id,source_name,source_row_number,entity_type,entity_key,scope,severity,reason_code)
    SELECT @run_id,'customer_locations',l.source_row_number,'LOCATION',l.location_id,
      'INTEGRATION','WARN','MULTIPLE_PRIMARY'
    FROM silver.customer_location l
    JOIN silver.customer_primary_location p ON p.run_id=l.run_id
      AND p.customer_id=l.customer_id
    WHERE l.run_id=@run_id AND l.is_active=1 AND l.is_primary=1
      AND p.selection_status='MULTIPLE_PRIMARY';
    INSERT ctl.quality_issue(run_id,source_name,entity_type,entity_key,scope,severity,reason_code)
    SELECT @run_id,'customer_locations','CUSTOMER',customer_id,'INTEGRATION','WARN',selection_status
    FROM silver.customer_primary_location WHERE run_id=@run_id AND selection_status<>'SELECTED';
    """
