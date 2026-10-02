"""Set-based SQL Server resolution for immutable tracking messages (Step 6B)."""

from bronze_pipeline import HEADERS
from reference_sql import BIN, binary_equal, binary_one_of, id_valid, positive_int, strict_time


def event_sql() -> str:
    fields = HEADERS["tracking_events"]
    raw_equal = " AND ".join(binary_equal(f"b.[{f}]", f"p.[{f}]") for f in fields)
    event_id = "(DATALENGTH(b.event_id)=28 AND b.event_id COLLATE Latin1_General_100_BIN2 LIKE N'EVT' + REPLICATE('[0-9]',11))"
    attempt_id = "(DATALENGTH(b.physical_attempt_id)=28 AND b.physical_attempt_id COLLATE Latin1_General_100_BIN2 LIKE N'ATT' + REPLICATE('[0-9]',11))"
    attempt_kind = binary_one_of("b.event_type", ("DELIVERY_FAILED", "DELIVERY_SUCCEEDED"))
    terminal_kind = binary_one_of("b.event_type", ("DELIVERY_SUCCEEDED", "RETURNED_TO_SENDER", "LOST"))
    known_type = binary_one_of("b.event_type", ("IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERY_FAILED",
                                                "DELIVERY_SUCCEEDED", "RETURNED_TO_SENDER", "LOST"))
    occurred, recorded = strict_time("b.occurred_at_utc"), strict_time("b.recorded_at_utc")
    depot_valid = id_valid("b.depot_id", "depot")
    parent_valid = id_valid("b.shipment_id", "shipment")
    # A shipment ID is accepted only in the fixed ASCII SHP+9-digit grammar.
    # Validate the raw text before the typed comparison; this preserves the
    # old byte-accurate parent decision for malformed/trailing-space keys while
    # allowing a seek on silver.shipment's (run_id, shipment_id) clustered PK.
    parent_join = (f"s.run_id=@run_id AND {parent_valid} AND "
                   "s.shipment_id=CONVERT(char(12),b.shipment_id) COLLATE Latin1_General_100_BIN2")
    conflict_group = ("EXISTS (SELECT 1 FROM #issues ci WHERE ci.source_row_number=r.source_row_number "
                      "AND ci.reason_code='EVENT_ID_CONFLICT')")
    group_cutoff = ("EXISTS (SELECT 1 FROM #r gx JOIN bronze.tracking_events gz "
                    "ON gz.run_id=@run_id AND gz.source_row_number=gx.source_row_number "
                    "WHERE gx.disposition='Q' AND gx.business_key_text=r.business_key_text "
                    f"AND {binary_equal('b.event_id','gz.event_id')} "
                    f"AND ({strict_time('gz.occurred_at_utc')} IS NULL "
                    f"OR {strict_time('gz.occurred_at_utc')}<=@T))")
    group_certificate = ("EXISTS (SELECT 1 FROM #r gx JOIN bronze.tracking_events gz "
                         "ON gz.run_id=@run_id AND gz.source_row_number=gx.source_row_number "
                         "WHERE gx.disposition='Q' AND gx.business_key_text=r.business_key_text "
                         f"AND {binary_equal('b.event_id','gz.event_id')} "
                         f"AND ({strict_time('gz.occurred_at_utc')} IS NULL "
                         f"OR {strict_time('gz.occurred_at_utc')}<={strict_time('p.attempt_history_through_at_utc')}))")

    def issue(reason: str, impact: str, priority: int, condition: str) -> str:
        return (f"INSERT #issues SELECT r.source_row_number,'{reason}','{impact}',{priority} "
                f"FROM #r r JOIN bronze.tracking_events b ON b.run_id=@run_id "
                f"AND b.source_row_number=r.source_row_number WHERE r.disposition IS NULL AND ({condition});")

    rules = [
        issue("EVENT_ID_INVALID", "Q", 10, f"NOT {event_id}"),
        issue("EVENT_PARENT_INVALID", "Q", 30, f"NOT {parent_valid}"),
        issue("EVENT_TYPE_INVALID", "Q", 30, f"NOT {known_type}"),
        issue("EVENT_OCCURRENCE_INVALID", "Q", 30, f"{occurred} IS NULL"),
        issue("EVENT_RECORDED_INVALID", "Q", 30, f"{recorded} IS NULL"),
        issue("EVENT_RECORDED_BEFORE_OCCURRENCE", "Q", 40, f"{recorded}<{occurred}"),
        issue("EVENT_RECORDED_AFTER_EXPORT", "Q", 40, f"{recorded}>@E"),
        issue("ATTEMPT_ID_INVALID", "Q", 30, f"{attempt_kind} AND NOT {attempt_id}"),
        issue("ATTEMPT_SEQUENCE_INVALID", "Q", 30, f"{attempt_kind} AND {positive_int('b.attempt_sequence')} IS NULL"),
        issue("NONATTEMPT_METADATA", "Q", 30,
              f"NOT {attempt_kind} AND (DATALENGTH(b.physical_attempt_id)>0 OR DATALENGTH(b.attempt_sequence)>0)"),
        issue("EVENT_PARENT_UNRESOLVED", "Q", 50,
              f"{parent_valid} AND NOT EXISTS (SELECT 1 FROM silver.shipment s WHERE {parent_join})"),
        issue("EVENT_BEFORE_BOOKING", "Q", 40,
              f"{occurred} IS NOT NULL AND EXISTS (SELECT 1 FROM silver.shipment s WHERE {parent_join} "
              f"AND {occurred}<s.booked_at_utc)"),
        issue("EVENT_BEFORE_HANDOVER", "Q", 40,
              f"{occurred} IS NOT NULL AND EXISTS (SELECT 1 FROM silver.shipment s WHERE {parent_join} "
              f"AND s.handover_value_state='VALID' AND {occurred}<s.handed_over_at_utc)"),
        issue("EVENT_DEPOT_INVALID", "AF", 60, f"DATALENGTH(b.depot_id)>0 AND NOT {depot_valid}"),
        issue("EVENT_DEPOT_UNRESOLVED", "AF", 70,
              f"{depot_valid} AND NOT EXISTS (SELECT 1 FROM silver.depot d WHERE d.run_id=@run_id "
              f"AND CONVERT(varbinary(max),CONVERT(nvarchar(5),d.depot_id))=CONVERT(varbinary(max),b.depot_id) "
              f"AND DATALENGTH(CONVERT(nvarchar(5),d.depot_id))=DATALENGTH(b.depot_id))"),
        issue("HANDOVER_CONTEXT_UNAVAILABLE", "AF", 70,
              f"EXISTS (SELECT 1 FROM silver.shipment s WHERE {parent_join} "
              f"AND s.handover_value_state<>'VALID')"),
    ]

    return f"""
    SET NOCOUNT ON;
    DECLARE @run_id uniqueidentifier=CONVERT(uniqueidentifier,?);
    DECLARE @T datetime2(0)=(SELECT reporting_cutoff_utc FROM ctl.refresh_run WHERE run_id=@run_id);
    DECLARE @E datetime2(0)=(SELECT extract_as_of_utc FROM ctl.refresh_run WHERE run_id=@run_id);
    SELECT b.source_row_number,CONVERT(nvarchar(32),LEFT(b.event_id,32)) AS business_key_text,
      CONVERT(int,NULL) AS exact_rep,CONVERT(char(2),NULL) AS disposition
    INTO #r FROM bronze.tracking_events b WHERE b.run_id=@run_id;
    CREATE UNIQUE CLUSTERED INDEX IX_work_event_row ON #r(source_row_number);
    CREATE INDEX IX_work_event_key ON #r(business_key_text,source_row_number);
    CREATE TABLE #issues(source_row_number int NOT NULL,reason_code varchar(64) NOT NULL,
      impact char(2) NOT NULL,priority int NOT NULL);

    UPDATE r SET exact_rep=x.rep FROM #r r
      JOIN bronze.tracking_events b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number
      CROSS APPLY (SELECT MIN(p.source_row_number) AS rep FROM bronze.tracking_events p
        JOIN #r pr ON pr.source_row_number=p.source_row_number
          AND pr.business_key_text=r.business_key_text
        WHERE p.run_id=@run_id AND p.source_row_number<=r.source_row_number AND {raw_equal}) x;
    UPDATE #r SET disposition='DX' WHERE exact_rep<>source_row_number;

    INSERT #issues SELECT r.source_row_number,'EVENT_ID_CONFLICT','Q',20 FROM #r r
    JOIN bronze.tracking_events b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number
    WHERE r.disposition IS NULL AND {event_id} AND EXISTS (
      SELECT 1 FROM #r x JOIN bronze.tracking_events z ON z.run_id=@run_id
        AND z.source_row_number=x.source_row_number
      WHERE x.disposition IS NULL AND x.source_row_number<>r.source_row_number
        AND x.business_key_text=r.business_key_text
        AND {binary_equal('b.event_id','z.event_id')});
    {''.join(rules)}
    UPDATE r SET disposition='Q' FROM #r r WHERE r.disposition IS NULL
      AND EXISTS (SELECT 1 FROM #issues i WHERE i.source_row_number=r.source_row_number AND i.impact='Q');
    UPDATE r SET disposition='AF' FROM #r r WHERE r.disposition IS NULL
      AND EXISTS (SELECT 1 FROM #issues i WHERE i.source_row_number=r.source_row_number AND i.impact='AF');
    UPDATE #r SET disposition='AC' WHERE disposition IS NULL;

    INSERT silver.row_resolution
      (run_id,source_name,source_row_number,business_key_text,disposition,representative_row_number,primary_reason_code)
    SELECT @run_id,'tracking_events',source_row_number,business_key_text,disposition,
      CASE WHEN disposition='DX' THEN exact_rep WHEN disposition IN ('AC','AF') THEN source_row_number END,
      (SELECT TOP(1) i.reason_code FROM #issues i WHERE i.source_row_number=r.source_row_number
        ORDER BY CASE WHEN i.impact='Q' THEN 0 ELSE 1 END,i.priority,i.reason_code)
    FROM #r r;
    INSERT ctl.quality_issue
      (run_id,source_name,source_row_number,entity_type,entity_key,scope,severity,reason_code)
    SELECT DISTINCT @run_id,'tracking_events',i.source_row_number,'EVENT',r.business_key_text,'SOURCE',
      CASE WHEN i.impact='Q' THEN 'ERROR' ELSE 'WARN' END,i.reason_code
    FROM #issues i JOIN #r r ON r.source_row_number=i.source_row_number;

    INSERT silver.tracking_event
      (run_id,event_id,shipment_id,event_type,occurred_at_utc,recorded_at_utc,event_depot_id,
       physical_attempt_id,attempt_sequence,is_late_arrival,is_after_cutoff,
       has_valid_handover_context,source_row_number)
    SELECT @run_id,CONVERT(char(14),b.event_id),CONVERT(char(12),b.shipment_id),
      CONVERT(varchar(18),b.event_type),{occurred},{recorded},
      CASE WHEN {depot_valid} THEN CONVERT(char(5),b.depot_id) END,
      CASE WHEN {attempt_kind} THEN CONVERT(char(14),b.physical_attempt_id) END,
      CASE WHEN {attempt_kind} THEN {positive_int('b.attempt_sequence')} END,
      CASE WHEN {occurred}<=@T AND {recorded}>@T THEN 1 ELSE 0 END,
      CASE WHEN {occurred}>@T THEN 1 ELSE 0 END,
      CASE WHEN s.handover_value_state='VALID' THEN 1 ELSE 0 END,
      r.source_row_number
    FROM #r r JOIN bronze.tracking_events b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number
      JOIN silver.shipment s ON {parent_join}
    WHERE r.disposition IN ('AC','AF');

    -- One rejected representative can hold two kinds and/or multiple plausible
    -- accepted parents. Exact extra copies remain DX and create no extra holds.
    INSERT silver.event_hold
      (run_id,source_row_number,shipment_id,hold_kind,affects_cutoff,affects_certificate,reason_code)
    SELECT @run_id,r.source_row_number,s.shipment_id,k.hold_kind,
      CASE WHEN {occurred} IS NULL OR {occurred}<=@T
             OR ({conflict_group} AND {group_cutoff}) THEN 1 ELSE 0 END,
      CASE WHEN {occurred} IS NULL OR {strict_time('p.attempt_history_through_at_utc')} IS NULL
                 OR {occurred}<={strict_time('p.attempt_history_through_at_utc')}
                 OR ({conflict_group} AND {group_certificate}) THEN 1 ELSE 0 END,
      COALESCE((SELECT TOP(1) i.reason_code FROM #issues i WHERE i.source_row_number=r.source_row_number
                AND i.impact='Q' ORDER BY i.priority,i.reason_code),'EVENT_QUARANTINED')
    FROM #r r JOIN bronze.tracking_events b ON b.run_id=@run_id AND b.source_row_number=r.source_row_number
      JOIN silver.shipment s ON {parent_join}
      JOIN bronze.shipments p ON p.run_id=@run_id AND p.source_row_number=s.source_row_number
      CROSS APPLY (VALUES ('TERMINAL'),('ATTEMPT')) k(hold_kind)
    WHERE r.disposition='Q' AND {parent_valid}
      AND (
        EXISTS (SELECT 1 FROM #issues i WHERE i.source_row_number=r.source_row_number
                AND i.reason_code='EVENT_ID_CONFLICT')
        OR NOT {known_type}
        OR (k.hold_kind='TERMINAL' AND {terminal_kind})
        OR (k.hold_kind='ATTEMPT' AND {attempt_kind})
      ) AND (
        k.hold_kind='TERMINAL' AND (NOT {known_type} OR {terminal_kind}
          OR EXISTS (SELECT 1 FROM #issues i WHERE i.source_row_number=r.source_row_number
            AND i.reason_code='EVENT_ID_CONFLICT'))
        OR k.hold_kind='ATTEMPT' AND (NOT {known_type} OR {attempt_kind}
          OR EXISTS (SELECT 1 FROM #issues i WHERE i.source_row_number=r.source_row_number
            AND i.reason_code='EVENT_ID_CONFLICT'))
      );
    DROP TABLE #issues;
    DROP TABLE #r;
    """
