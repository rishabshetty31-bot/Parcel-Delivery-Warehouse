"""Fixed T-SQL batches for Step 6C; no generator or expected-result inputs."""


def attempt_sql() -> str:
    return """
    SET NOCOUNT ON;
    DECLARE @run_id uniqueidentifier=CONVERT(uniqueidentifier,?);
    SELECT e.shipment_id,e.physical_attempt_id,COUNT_BIG(*) AS message_count,
      MIN(e.attempt_sequence) AS min_sequence,MAX(e.attempt_sequence) AS max_sequence,
      MIN(e.occurred_at_utc) AS min_time,MAX(e.occurred_at_utc) AS max_time,
      MIN(e.event_type) AS min_result,MAX(e.event_type) AS max_result,
      MIN(e.recorded_at_utc) AS first_recorded,
      MIN(e.event_depot_id) AS min_depot,MAX(e.event_depot_id) AS max_depot
    INTO #ag FROM silver.tracking_event e
    WHERE e.run_id=@run_id AND e.event_type IN ('DELIVERY_FAILED','DELIVERY_SUCCEEDED')
    GROUP BY e.shipment_id,e.physical_attempt_id;
    CREATE UNIQUE CLUSTERED INDEX IX_ag_id ON #ag(shipment_id,physical_attempt_id);
    CREATE INDEX IX_ag_global ON #ag(physical_attempt_id,shipment_id);
    CREATE INDEX IX_ag_order ON #ag(shipment_id,min_sequence,min_time);
    SELECT g.*,
      CONVERT(bit,CASE WHEN g.min_sequence<>g.max_sequence OR g.min_time<>g.max_time
                        OR g.min_result<>g.max_result THEN 1 ELSE 0 END) AS intrinsic_conflict,
      CONVERT(bit,CASE WHEN EXISTS (SELECT 1 FROM #ag x WHERE x.physical_attempt_id=g.physical_attempt_id
                        AND x.shipment_id<>g.shipment_id) THEN 1 ELSE 0 END) AS global_reuse,
      CONVERT(bit,CASE WHEN g.min_sequence=g.max_sequence AND EXISTS
        (SELECT 1 FROM #ag x WHERE x.shipment_id=g.shipment_id AND x.physical_attempt_id<>g.physical_attempt_id
          AND x.min_sequence=x.max_sequence AND x.min_sequence=g.min_sequence) THEN 1 ELSE 0 END) AS sequence_collision,
      CONVERT(bit,CASE WHEN g.min_sequence=g.max_sequence AND g.min_time=g.max_time AND EXISTS
        (SELECT 1 FROM #ag x WHERE x.shipment_id=g.shipment_id AND x.physical_attempt_id<>g.physical_attempt_id
          AND x.min_sequence=x.max_sequence AND x.min_time=x.max_time AND
          ((x.min_sequence<g.min_sequence AND x.min_time>=g.min_time)
            OR (x.min_sequence>g.min_sequence AND x.min_time<=g.min_time))) THEN 1 ELSE 0 END) AS time_order_conflict
    INTO #a FROM #ag g;
    CREATE UNIQUE CLUSTERED INDEX IX_a_id ON #a(shipment_id,physical_attempt_id);
    CREATE INDEX IX_a_window ON #a(shipment_id,min_time,min_sequence);
    INSERT silver.physical_attempt
      (run_id,shipment_id,physical_attempt_id,attempt_sequence,completed_at_utc,result_code,
       is_coherent,message_count,first_recorded_at_utc,conflict_code)
    SELECT @run_id,a.shipment_id,a.physical_attempt_id,
      CASE WHEN a.intrinsic_conflict=0 THEN a.min_sequence END,
      CASE WHEN a.intrinsic_conflict=0 THEN a.min_time END,
      CASE WHEN a.intrinsic_conflict=0 THEN a.min_result END,
      CASE WHEN a.intrinsic_conflict=0 AND a.global_reuse=0 AND a.sequence_collision=0 AND a.time_order_conflict=0 THEN 1 ELSE 0 END,
      CONVERT(int,a.message_count),a.first_recorded,
      CASE WHEN a.min_result<>a.max_result THEN 'ATTEMPT_RESULT_CONFLICT'
           WHEN a.min_time<>a.max_time THEN 'ATTEMPT_TIME_CONFLICT'
           WHEN a.min_sequence<>a.max_sequence THEN 'ATTEMPT_SEQUENCE_CONFLICT'
           WHEN a.global_reuse=1 THEN 'ATTEMPT_ID_REUSE'
           WHEN a.sequence_collision=1 THEN 'ATTEMPT_SEQUENCE_COLLISION'
           WHEN a.time_order_conflict=1 THEN 'ATTEMPT_TIME_ORDER_CONFLICT' END
    FROM #a a;
    """


def business_conflict_sql() -> str:
    return """
    SET NOCOUNT ON;
    DECLARE @run_id uniqueidentifier=CONVERT(uniqueidentifier,?);
    DECLARE @T datetime2(0)=(SELECT reporting_cutoff_utc FROM ctl.refresh_run WHERE run_id=@run_id);
    SELECT s.shipment_id,s.attempt_history_through_at_utc AS W,
      s.attempt_history_attested_at_utc AS attested_at
    INTO #w FROM silver.shipment s WHERE s.run_id=@run_id;
    CREATE UNIQUE CLUSTERED INDEX IX_w ON #w(shipment_id);
    CREATE TABLE #ai (
      shipment_id char(12) COLLATE Latin1_General_100_BIN2 NOT NULL,
      physical_attempt_id char(14) COLLATE Latin1_General_100_BIN2 NOT NULL,
      reason_code varchar(64) NOT NULL,
      affects_cutoff bit NOT NULL,affects_certificate bit NOT NULL,
      terminal_risk bit NOT NULL,has_hold bit NOT NULL
    );
    INSERT #ai
    SELECT a.shipment_id,a.physical_attempt_id,v.reason_code,
      CONVERT(bit,CASE WHEN v.reason_code='ATTEMPT_ID_REUSE' THEN
        CASE WHEN EXISTS (SELECT 1 FROM #a x WHERE x.physical_attempt_id=a.physical_attempt_id
          AND x.min_time<=@T) THEN 1 ELSE 0 END
        WHEN v.reason_code IN ('ATTEMPT_SEQUENCE_COLLISION','ATTEMPT_TIME_ORDER_CONFLICT') THEN
        CASE WHEN a.min_time<=@T AND EXISTS (SELECT 1 FROM #a x
          WHERE x.shipment_id=a.shipment_id AND x.physical_attempt_id<>a.physical_attempt_id
            AND x.min_time<=@T AND
            ((v.reason_code='ATTEMPT_SEQUENCE_COLLISION' AND x.min_sequence=x.max_sequence
                AND x.min_sequence=a.min_sequence)
             OR (v.reason_code='ATTEMPT_TIME_ORDER_CONFLICT' AND x.min_sequence=x.max_sequence
                AND x.min_time=x.max_time AND
                ((x.min_sequence<a.min_sequence AND x.min_time>=a.min_time)
                 OR (x.min_sequence>a.min_sequence AND x.min_time<=a.min_time))))) THEN 1 ELSE 0 END
        ELSE CASE WHEN a.min_time<=@T THEN 1 ELSE 0 END END),
      CONVERT(bit,CASE WHEN w.W IS NULL THEN 1
        WHEN v.reason_code='ATTEMPT_ID_REUSE' THEN
          CASE WHEN EXISTS (SELECT 1 FROM #a x WHERE x.physical_attempt_id=a.physical_attempt_id
            AND x.min_time<=w.W) THEN 1 ELSE 0 END
        WHEN v.reason_code IN ('ATTEMPT_SEQUENCE_COLLISION','ATTEMPT_TIME_ORDER_CONFLICT') THEN
          CASE WHEN a.min_time<=w.W AND EXISTS (SELECT 1 FROM #a x
            WHERE x.shipment_id=a.shipment_id AND x.physical_attempt_id<>a.physical_attempt_id
              AND x.min_time<=w.W AND
              ((v.reason_code='ATTEMPT_SEQUENCE_COLLISION' AND x.min_sequence=x.max_sequence
                  AND x.min_sequence=a.min_sequence)
               OR (v.reason_code='ATTEMPT_TIME_ORDER_CONFLICT' AND x.min_sequence=x.max_sequence
                  AND x.min_time=x.max_time AND
                  ((x.min_sequence<a.min_sequence AND x.min_time>=a.min_time)
                   OR (x.min_sequence>a.min_sequence AND x.min_time<=a.min_time))))) THEN 1 ELSE 0 END
        ELSE CASE WHEN a.min_time<=w.W THEN 1 ELSE 0 END END),
      CONVERT(bit,CASE WHEN a.min_result='DELIVERY_SUCCEEDED' OR a.max_result='DELIVERY_SUCCEEDED' THEN 1 ELSE 0 END),
      CONVERT(bit,CASE WHEN v.reason_code='ATTEMPT_DEPOT_DISAGREEMENT' THEN 0 ELSE 1 END)
    FROM #a a JOIN #w w ON w.shipment_id=a.shipment_id
    CROSS APPLY (VALUES
      ('ATTEMPT_RESULT_CONFLICT',CASE WHEN a.min_result<>a.max_result THEN 1 ELSE 0 END),
      ('ATTEMPT_TIME_CONFLICT',CASE WHEN a.min_time<>a.max_time THEN 1 ELSE 0 END),
      ('ATTEMPT_SEQUENCE_CONFLICT',CASE WHEN a.min_sequence<>a.max_sequence THEN 1 ELSE 0 END),
      ('ATTEMPT_ID_REUSE',CONVERT(int,a.global_reuse)),
      ('ATTEMPT_SEQUENCE_COLLISION',CONVERT(int,a.sequence_collision)),
      ('ATTEMPT_TIME_ORDER_CONFLICT',CONVERT(int,a.time_order_conflict)),
      ('ATTEMPT_DEPOT_DISAGREEMENT',CASE WHEN a.min_depot<>a.max_depot THEN 1 ELSE 0 END)
    ) v(reason_code,applies) WHERE v.applies=1;
    CREATE INDEX IX_ai_id ON #ai(shipment_id,physical_attempt_id);

    -- Terminal conflicts are evaluated independently at T, through W, and
    -- across the complete extract. Future-only contradictions flag the source
    -- but cannot silently become a pre-T terminal outcome.
    SELECT s.shipment_id,w.W,
      t.first_terminal_all,t.first_terminal_T,t.first_terminal_W,
      t.kind_all,t.kind_T,t.kind_W,
      t.success_ids_all,t.success_ids_T,t.success_ids_W,
      t.post_terminal_all,t.post_terminal_T,t.post_terminal_W
    INTO #ts FROM silver.shipment s JOIN #w w ON w.shipment_id=s.shipment_id
    CROSS APPLY (
      SELECT
        (SELECT MIN(e.occurred_at_utc) FROM silver.tracking_event e WHERE e.run_id=@run_id
          AND e.shipment_id=s.shipment_id AND e.event_type IN ('DELIVERY_SUCCEEDED','RETURNED_TO_SENDER','LOST')) AS first_terminal_all,
        (SELECT MIN(e.occurred_at_utc) FROM silver.tracking_event e WHERE e.run_id=@run_id
          AND e.shipment_id=s.shipment_id AND e.event_type IN ('DELIVERY_SUCCEEDED','RETURNED_TO_SENDER','LOST')
          AND e.occurred_at_utc<=@T) AS first_terminal_T,
        (SELECT MIN(e.occurred_at_utc) FROM silver.tracking_event e WHERE e.run_id=@run_id
          AND e.shipment_id=s.shipment_id AND e.event_type IN ('DELIVERY_SUCCEEDED','RETURNED_TO_SENDER','LOST')
          AND e.occurred_at_utc<=w.W) AS first_terminal_W,
        (SELECT COUNT(DISTINCT e.event_type) FROM silver.tracking_event e WHERE e.run_id=@run_id
          AND e.shipment_id=s.shipment_id AND e.event_type IN ('DELIVERY_SUCCEEDED','RETURNED_TO_SENDER','LOST')) AS kind_all,
        (SELECT COUNT(DISTINCT e.event_type) FROM silver.tracking_event e WHERE e.run_id=@run_id
          AND e.shipment_id=s.shipment_id AND e.event_type IN ('DELIVERY_SUCCEEDED','RETURNED_TO_SENDER','LOST')
          AND e.occurred_at_utc<=@T) AS kind_T,
        (SELECT COUNT(DISTINCT e.event_type) FROM silver.tracking_event e WHERE e.run_id=@run_id
          AND e.shipment_id=s.shipment_id AND e.event_type IN ('DELIVERY_SUCCEEDED','RETURNED_TO_SENDER','LOST')
          AND e.occurred_at_utc<=w.W) AS kind_W,
        (SELECT COUNT(DISTINCT e.physical_attempt_id) FROM silver.tracking_event e WHERE e.run_id=@run_id
          AND e.shipment_id=s.shipment_id AND e.event_type='DELIVERY_SUCCEEDED') AS success_ids_all,
        (SELECT COUNT(DISTINCT e.physical_attempt_id) FROM silver.tracking_event e WHERE e.run_id=@run_id
          AND e.shipment_id=s.shipment_id AND e.event_type='DELIVERY_SUCCEEDED' AND e.occurred_at_utc<=@T) AS success_ids_T,
        (SELECT COUNT(DISTINCT e.physical_attempt_id) FROM silver.tracking_event e WHERE e.run_id=@run_id
          AND e.shipment_id=s.shipment_id AND e.event_type='DELIVERY_SUCCEEDED' AND e.occurred_at_utc<=w.W) AS success_ids_W,
        CONVERT(bit,0) AS post_terminal_all,CONVERT(bit,0) AS post_terminal_T,
        CONVERT(bit,0) AS post_terminal_W
    ) t WHERE s.run_id=@run_id;
    CREATE UNIQUE CLUSTERED INDEX IX_ts ON #ts(shipment_id);
    UPDATE t SET
      post_terminal_all=CASE WHEN EXISTS (SELECT 1 FROM silver.tracking_event e
        WHERE e.run_id=@run_id AND e.shipment_id=t.shipment_id
          AND e.occurred_at_utc>t.first_terminal_all
          AND e.event_type IN ('IN_TRANSIT','OUT_FOR_DELIVERY','DELIVERY_FAILED')) THEN 1 ELSE 0 END,
      post_terminal_T=CASE WHEN EXISTS (SELECT 1 FROM silver.tracking_event e
        WHERE e.run_id=@run_id AND e.shipment_id=t.shipment_id
          AND e.occurred_at_utc>t.first_terminal_T AND e.occurred_at_utc<=@T
          AND e.event_type IN ('IN_TRANSIT','OUT_FOR_DELIVERY','DELIVERY_FAILED')) THEN 1 ELSE 0 END,
      post_terminal_W=CASE WHEN EXISTS (SELECT 1 FROM silver.tracking_event e
        WHERE e.run_id=@run_id AND e.shipment_id=t.shipment_id
          AND e.occurred_at_utc>t.first_terminal_W AND e.occurred_at_utc<=t.W
          AND e.event_type IN ('IN_TRANSIT','OUT_FOR_DELIVERY','DELIVERY_FAILED')) THEN 1 ELSE 0 END
    FROM #ts t;
    CREATE TABLE #ti (
      shipment_id char(12) COLLATE Latin1_General_100_BIN2 NOT NULL,
      source_row_number int NOT NULL,
      reason_code varchar(64) NOT NULL,
      affects_cutoff bit NOT NULL,affects_certificate bit NOT NULL
    );
    INSERT #ti
    SELECT t.shipment_id,e.source_row_number,'MULTIPLE_TERMINAL_KINDS',
      CONVERT(bit,CASE WHEN t.kind_T>1 THEN 1 ELSE 0 END),
      CONVERT(bit,CASE WHEN t.kind_W>1 THEN 1 ELSE 0 END)
    FROM #ts t JOIN silver.tracking_event e ON e.run_id=@run_id AND e.shipment_id=t.shipment_id
    WHERE t.kind_all>1 AND e.event_type IN ('DELIVERY_SUCCEEDED','RETURNED_TO_SENDER','LOST');
    INSERT #ti
    SELECT t.shipment_id,e.source_row_number,'MULTIPLE_SUCCESS_ATTEMPTS',
      CONVERT(bit,CASE WHEN t.success_ids_T>1 THEN 1 ELSE 0 END),
      CONVERT(bit,CASE WHEN t.success_ids_W>1 THEN 1 ELSE 0 END)
    FROM #ts t JOIN silver.tracking_event e ON e.run_id=@run_id AND e.shipment_id=t.shipment_id
    WHERE t.success_ids_all>1 AND e.event_type='DELIVERY_SUCCEEDED';
    INSERT #ti
    SELECT t.shipment_id,e.source_row_number,'POST_TERMINAL_ACTIVITY',
      CONVERT(bit,t.post_terminal_T),CONVERT(bit,t.post_terminal_W)
    FROM #ts t JOIN silver.tracking_event e ON e.run_id=@run_id AND e.shipment_id=t.shipment_id
    WHERE t.post_terminal_all=1 AND
      (e.event_type IN ('DELIVERY_SUCCEEDED','RETURNED_TO_SENDER','LOST')
       OR (e.occurred_at_utc>t.first_terminal_all
         AND e.event_type IN ('IN_TRANSIT','OUT_FOR_DELIVERY','DELIVERY_FAILED')));
    CREATE INDEX IX_ti_row ON #ti(shipment_id,source_row_number);

    INSERT ctl.quality_issue
      (run_id,source_name,source_row_number,entity_type,entity_key,scope,severity,reason_code)
    SELECT @run_id,'tracking_events',e.source_row_number,'EVENT',e.event_id,'INTEGRATION','WARN',a.reason_code
    FROM #ai a JOIN silver.tracking_event e ON e.run_id=@run_id
      AND e.shipment_id=a.shipment_id AND e.physical_attempt_id=a.physical_attempt_id;
    INSERT ctl.quality_issue
      (run_id,source_name,source_row_number,entity_type,entity_key,scope,severity,reason_code)
    SELECT @run_id,'tracking_events',t.source_row_number,'EVENT',e.event_id,'INTEGRATION','WARN',t.reason_code
    FROM #ti t JOIN silver.tracking_event e ON e.run_id=@run_id
      AND e.shipment_id=t.shipment_id AND e.source_row_number=t.source_row_number;

    -- Stage 3's independently planned late-arrival disposition is AF. This is
    -- a context flag on an accepted message, not an event-time exclusion.
    INSERT ctl.quality_issue
      (run_id,source_name,source_row_number,entity_type,entity_key,scope,severity,reason_code)
    SELECT @run_id,'tracking_events',e.source_row_number,'EVENT',e.event_id,'INTEGRATION','INFO','LATE_ARRIVAL_CONTEXT'
    FROM silver.tracking_event e WHERE e.run_id=@run_id AND e.is_late_arrival=1;

    UPDATE rr SET disposition='AF',primary_reason_code=COALESCE(rr.primary_reason_code,
      CASE WHEN e.is_late_arrival=1 THEN 'LATE_ARRIVAL_CONTEXT' ELSE 'BUSINESS_EVENT_CONFLICT' END)
    FROM silver.row_resolution rr JOIN silver.tracking_event e ON e.run_id=rr.run_id
      AND e.source_row_number=rr.source_row_number
    WHERE rr.run_id=@run_id AND rr.source_name='tracking_events' AND rr.disposition IN ('AC','AF')
      AND (e.is_late_arrival=1 OR EXISTS (SELECT 1 FROM #ai a WHERE a.shipment_id=e.shipment_id
        AND a.physical_attempt_id=e.physical_attempt_id)
        OR EXISTS (SELECT 1 FROM #ti t WHERE t.shipment_id=e.shipment_id
          AND t.source_row_number=e.source_row_number));

    -- A contradictory terminal history is an integration quality flag on all
    -- accepted messages in that shipment, including earlier progress scans.
    -- Only the terminal rows below create uncertainty holds; this context flag
    -- does not make a progress scan malformed or erase tracking coverage.
    INSERT ctl.quality_issue
      (run_id,source_name,source_row_number,entity_type,entity_key,scope,severity,reason_code)
    SELECT @run_id,'tracking_events',e.source_row_number,'EVENT',e.event_id,
      'INTEGRATION','WARN','TERMINAL_CONFLICT_CONTEXT'
    FROM silver.tracking_event e JOIN #ts t ON t.shipment_id=e.shipment_id
    WHERE e.run_id=@run_id AND (t.kind_all>1 OR t.success_ids_all>1 OR t.post_terminal_all=1);
    UPDATE rr SET disposition='AF',primary_reason_code=COALESCE(rr.primary_reason_code,'TERMINAL_CONFLICT_CONTEXT')
    FROM silver.row_resolution rr JOIN silver.tracking_event e ON e.run_id=rr.run_id
      AND e.source_row_number=rr.source_row_number
    JOIN #ts t ON t.shipment_id=e.shipment_id
    WHERE rr.run_id=@run_id AND rr.source_name='tracking_events' AND rr.disposition='AC'
      AND (t.kind_all>1 OR t.success_ids_all>1 OR t.post_terminal_all=1);

    -- Consolidate overlapping reasons into one hold per source row and kind;
    -- all individual reasons remain in ctl.quality_issue.
    SELECT e.source_row_number,e.shipment_id,
      MAX(CONVERT(int,a.affects_cutoff)) AS cutoff_hold,
      MAX(CONVERT(int,a.affects_certificate)) AS cert_hold,
      MIN(a.reason_code) AS reason_code,
      MAX(CASE WHEN a.terminal_risk=1 THEN CONVERT(int,a.affects_cutoff) ELSE 0 END) AS terminal_cutoff,
      MAX(CASE WHEN a.terminal_risk=1 THEN CONVERT(int,a.affects_certificate) ELSE 0 END) AS terminal_cert
    INTO #ah FROM #ai a JOIN silver.tracking_event e ON e.run_id=@run_id
      AND e.shipment_id=a.shipment_id AND e.physical_attempt_id=a.physical_attempt_id
    WHERE a.has_hold=1 GROUP BY e.source_row_number,e.shipment_id;
    INSERT silver.event_hold(run_id,source_row_number,shipment_id,hold_kind,affects_cutoff,affects_certificate,reason_code)
    SELECT @run_id,h.source_row_number,h.shipment_id,'ATTEMPT',h.cutoff_hold,h.cert_hold,h.reason_code
    FROM #ah h;
    INSERT silver.event_hold(run_id,source_row_number,shipment_id,hold_kind,affects_cutoff,affects_certificate,reason_code)
    SELECT @run_id,h.source_row_number,h.shipment_id,'TERMINAL',h.terminal_cutoff,h.terminal_cert,h.reason_code
    FROM #ah h WHERE h.terminal_cutoff=1 OR h.terminal_cert=1;
    SELECT source_row_number,shipment_id,MAX(CONVERT(int,affects_cutoff)) AS cutoff_hold,
      MAX(CONVERT(int,affects_certificate)) AS cert_hold,MIN(reason_code) AS reason_code
    INTO #th FROM #ti GROUP BY source_row_number,shipment_id;
    UPDATE h SET affects_cutoff=CASE WHEN x.cutoff_hold=1 THEN 1 ELSE h.affects_cutoff END,
      affects_certificate=CASE WHEN x.cert_hold=1 THEN 1 ELSE h.affects_certificate END
    FROM silver.event_hold h JOIN #th x ON x.source_row_number=h.source_row_number
      AND x.shipment_id=h.shipment_id
    WHERE h.run_id=@run_id AND h.hold_kind='TERMINAL';
    INSERT silver.event_hold(run_id,source_row_number,shipment_id,hold_kind,affects_cutoff,affects_certificate,reason_code)
    SELECT @run_id,x.source_row_number,x.shipment_id,'TERMINAL',x.cutoff_hold,x.cert_hold,x.reason_code
    FROM #th x WHERE NOT EXISTS (SELECT 1 FROM silver.event_hold h WHERE h.run_id=@run_id
      AND h.source_row_number=x.source_row_number AND h.shipment_id=x.shipment_id AND h.hold_kind='TERMINAL');

    INSERT ctl.quality_issue
      (run_id,source_name,source_row_number,entity_type,entity_key,scope,severity,reason_code)
    SELECT DISTINCT @run_id,'shipments',s.source_row_number,'SHIPMENT',s.shipment_id,
      'INTEGRATION','WARN',x.reason_code
    FROM silver.shipment s CROSS APPLY (
      SELECT a.reason_code FROM #ai a WHERE a.shipment_id=s.shipment_id
      UNION SELECT t.reason_code FROM #ti t WHERE t.shipment_id=s.shipment_id
    ) x WHERE s.run_id=@run_id;
    UPDATE rr SET disposition='AF',primary_reason_code=COALESCE(rr.primary_reason_code,'BUSINESS_CONFLICT')
    FROM silver.row_resolution rr JOIN silver.shipment s ON s.run_id=rr.run_id
      AND s.source_row_number=rr.source_row_number
    WHERE rr.run_id=@run_id AND rr.source_name='shipments' AND rr.disposition IN ('AC','AF')
      AND (EXISTS (SELECT 1 FROM #ai a WHERE a.shipment_id=s.shipment_id)
        OR EXISTS (SELECT 1 FROM #ti t WHERE t.shipment_id=s.shipment_id));
    """


def base_sql() -> str:
    """Derive cutoff lifecycle and outcomes before evaluating the full certificate window."""
    return """
    SET NOCOUNT ON;
    DECLARE @run_id uniqueidentifier=CONVERT(uniqueidentifier,?);
    DECLARE @T datetime2(0)=(SELECT reporting_cutoff_utc FROM ctl.refresh_run WHERE run_id=@run_id);
    SELECT s.shipment_id,s.booked_at_utc,s.handed_over_at_utc,s.promised_delivery_at_utc,
      s.cancelled_at_utc,s.handover_value_state,s.promise_value_state,s.cancellation_value_state,
      CONVERT(bit,CASE WHEN s.booked_at_utc<=@T THEN 1 ELSE 0 END) AS is_fact_in_scope,
      CONVERT(varchar(64),CASE
        WHEN s.booked_at_utc>@T THEN 'FUTURE_BOOKING'
        WHEN s.cancellation_value_state='INVALID'
          OR (s.cancelled_at_utc IS NOT NULL AND s.cancelled_at_utc<=@T
              AND (s.handover_value_state='INVALID' OR (s.handed_over_at_utc IS NOT NULL AND s.handed_over_at_utc<=@T)))
          THEN 'START_CONFLICT'
        WHEN s.cancelled_at_utc IS NOT NULL AND s.cancelled_at_utc<=@T THEN 'CANCELLED'
        WHEN s.handover_value_state='INVALID' THEN 'INVALID_HANDOVER'
        WHEN s.handed_over_at_utc IS NULL THEN 'NO_HANDOVER'
        WHEN s.handed_over_at_utc>@T THEN 'FUTURE_HANDOVER'
        ELSE NULL END) AS start_exclusion_code,
      CONVERT(varchar(64),CASE WHEN s.promise_value_state<>'VALID' OR s.promised_delivery_at_utc IS NULL
        OR s.promised_delivery_at_utc<s.booked_at_utc
        OR (s.handed_over_at_utc IS NOT NULL AND s.promised_delivery_at_utc<s.handed_over_at_utc)
        THEN 'PROMISE_UNUSABLE'
        WHEN s.promised_delivery_at_utc>@T THEN 'PROMISE_FUTURE' ELSE NULL END) AS promise_exclusion_code,
      ev.valid_message_count,ev.success_at,ev.return_at,ev.loss_at,
      CONVERT(int,COALESCE(attempts.canonical_count,0)) AS canonical_attempt_count,
      CONVERT(bit,CASE WHEN EXISTS (SELECT 1 FROM silver.event_hold h WHERE h.run_id=@run_id
        AND h.shipment_id=s.shipment_id AND h.hold_kind='TERMINAL' AND h.affects_cutoff=1)
        THEN 1 ELSE 0 END) AS terminal_hold_at_cutoff
    INTO #b0 FROM silver.shipment s
    OUTER APPLY (SELECT COUNT(*) AS valid_message_count,
      MIN(CASE WHEN e.event_type='DELIVERY_SUCCEEDED' THEN e.occurred_at_utc END) AS success_at,
      MIN(CASE WHEN e.event_type='RETURNED_TO_SENDER' THEN e.occurred_at_utc END) AS return_at,
      MIN(CASE WHEN e.event_type='LOST' THEN e.occurred_at_utc END) AS loss_at
      FROM silver.tracking_event e WHERE e.run_id=@run_id AND e.shipment_id=s.shipment_id
        AND e.occurred_at_utc<=@T) ev
    OUTER APPLY (SELECT COUNT(*) AS canonical_count FROM #a a
      WHERE a.shipment_id=s.shipment_id AND a.intrinsic_conflict=0 AND a.min_time<=@T
        AND NOT EXISTS (SELECT 1 FROM #ai i WHERE i.shipment_id=a.shipment_id
          AND i.physical_attempt_id=a.physical_attempt_id AND i.has_hold=1 AND i.affects_cutoff=1)) attempts
    WHERE s.run_id=@run_id;
    CREATE UNIQUE CLUSTERED INDEX IX_b0 ON #b0(shipment_id);
    SELECT b.*,
      CONVERT(bit,CASE WHEN b.is_fact_in_scope=1 AND b.start_exclusion_code IS NULL THEN 1 ELSE 0 END) AS is_handed_over,
      CONVERT(bit,CASE WHEN b.is_fact_in_scope=1 AND b.start_exclusion_code IS NULL
        AND b.promise_exclusion_code IS NULL THEN 1 ELSE 0 END) AS is_due,
      CONVERT(varchar(64),CASE WHEN b.terminal_hold_at_cutoff=1 THEN 'TERMINAL_EVIDENCE_HOLD' ELSE NULL END) AS outcome_uncertainty_code,
      CONVERT(varchar(32),CASE
        WHEN b.is_fact_in_scope=0 THEN 'FUTURE_BOOKING'
        WHEN b.start_exclusion_code='CANCELLED' THEN 'CANCELLED'
        WHEN b.start_exclusion_code IS NOT NULL THEN 'NOT_HANDED'
        WHEN b.terminal_hold_at_cutoff=1 THEN 'UNKNOWN'
        WHEN b.success_at IS NOT NULL THEN
          CASE WHEN b.promise_exclusion_code='PROMISE_UNUSABLE' THEN 'DELIVERED_PROMISE_UNKNOWN'
            WHEN b.success_at<=b.promised_delivery_at_utc THEN 'ON_TIME' ELSE 'LATE' END
        WHEN b.return_at IS NOT NULL THEN 'RETURNED'
        WHEN b.loss_at IS NOT NULL THEN 'LOST'
        WHEN b.promise_exclusion_code='PROMISE_FUTURE' THEN 'OPEN_FUTURE'
        WHEN b.promise_exclusion_code='PROMISE_UNUSABLE' THEN 'OPEN_FUTURE'
        WHEN b.promised_delivery_at_utc=@T THEN 'OPEN_AT_DEADLINE'
        ELSE 'OPEN_OVERDUE' END) AS outcome_code
    INTO #b FROM #b0 b;
    CREATE UNIQUE CLUSTERED INDEX IX_b ON #b(shipment_id);
    """


def certificate_sql() -> str:
    """Validate source-issued completeness through W, then restrict KPI attempts to T."""
    return """
    SET NOCOUNT ON;
    DECLARE @run_id uniqueidentifier=CONVERT(uniqueidentifier,?);
    DECLARE @T datetime2(0)=(SELECT reporting_cutoff_utc FROM ctl.refresh_run WHERE run_id=@run_id);
    DECLARE @E datetime2(0)=(SELECT extract_as_of_utc FROM ctl.refresh_run WHERE run_id=@run_id);
    SELECT s.shipment_id,s.attempt_history_status AS source_status,
      s.attempt_history_through_at_utc AS W,s.attempt_history_attempt_count AS C,
      s.attempt_history_attested_at_utc AS attested_at,s.source_updated_at_utc,
      b.is_handed_over,b.terminal_hold_at_cutoff,b.canonical_attempt_count,
      x.observed_count,x.intrinsic_bad,x.sequence_gap,x.time_order_bad,x.unsupported,
      CONVERT(bit,CASE WHEN EXISTS (SELECT 1 FROM silver.event_hold h
        WHERE h.run_id=@run_id AND h.shipment_id=s.shipment_id AND h.affects_certificate=1) THEN 1 ELSE 0 END) AS certificate_hold,
      CONVERT(varchar(64),CASE
        WHEN s.attempt_history_status<>'COMPLETE' OR s.attempt_history_status IS NULL THEN 'CERTIFICATE_NOT_COMPLETE'
        WHEN s.attempt_history_through_at_utc IS NULL OR s.attempt_history_attempt_count IS NULL
          OR s.attempt_history_attested_at_utc IS NULL THEN 'CERTIFICATE_DETAIL_MISSING'
        WHEN s.attempt_history_through_at_utc<@T THEN 'CERTIFICATE_STALE'
        WHEN s.attempt_history_through_at_utc<s.booked_at_utc
          OR s.attempt_history_attested_at_utc<s.attempt_history_through_at_utc
          OR s.attempt_history_attested_at_utc>s.source_updated_at_utc
          OR s.source_updated_at_utc>@E THEN 'CERTIFICATE_CHRONOLOGY'
        WHEN EXISTS (SELECT 1 FROM silver.event_hold h WHERE h.run_id=@run_id
          AND h.shipment_id=s.shipment_id AND h.affects_certificate=1) THEN 'CERTIFICATE_EVIDENCE_HOLD'
        WHEN x.observed_count>0 AND (s.handover_value_state<>'VALID' OR s.handed_over_at_utc IS NULL)
          THEN 'CERTIFICATE_HANDOVER_UNVERIFIED'
        WHEN x.intrinsic_bad>0 OR x.sequence_gap>0 OR x.time_order_bad>0 OR x.unsupported>0 THEN 'CERTIFICATE_ATTEMPT_INCOHERENT'
        WHEN x.observed_count<>s.attempt_history_attempt_count THEN 'CERTIFICATE_COUNT_MISMATCH'
        ELSE NULL END) AS exclusion_code
    INTO #c0 FROM silver.shipment s JOIN #b b ON b.shipment_id=s.shipment_id
    OUTER APPLY (SELECT
      (SELECT COUNT(*) FROM #a a WHERE a.shipment_id=s.shipment_id
        AND a.min_time<=s.attempt_history_through_at_utc) AS observed_count,
      (SELECT COUNT(*) FROM #a a WHERE a.shipment_id=s.shipment_id
        AND a.min_time<=s.attempt_history_through_at_utc AND a.intrinsic_conflict=1) AS intrinsic_bad,
      (SELECT COUNT(*) FROM #a a WHERE a.shipment_id=s.shipment_id
        AND a.min_time<=s.attempt_history_through_at_utc AND
        (a.min_sequence<1 OR a.min_sequence>(SELECT COUNT(*) FROM #a z WHERE z.shipment_id=s.shipment_id
          AND z.min_time<=s.attempt_history_through_at_utc)
         OR EXISTS (SELECT 1 FROM #a z WHERE z.shipment_id=a.shipment_id
           AND z.physical_attempt_id<>a.physical_attempt_id AND z.min_sequence=a.min_sequence
           AND z.min_time<=s.attempt_history_through_at_utc))) AS sequence_gap,
      (SELECT COUNT(*) FROM #a a WHERE a.shipment_id=s.shipment_id
        AND a.min_time<=s.attempt_history_through_at_utc AND
        (a.min_time<s.handed_over_at_utc OR EXISTS (SELECT 1 FROM #a z
         WHERE z.shipment_id=a.shipment_id AND z.physical_attempt_id<>a.physical_attempt_id
           AND z.min_time<=s.attempt_history_through_at_utc
           AND ((z.min_sequence<a.min_sequence AND z.min_time>=a.min_time)
             OR (z.min_sequence>a.min_sequence AND z.min_time<=a.min_time))))) AS time_order_bad,
      (SELECT COUNT(*) FROM #a a WHERE a.shipment_id=s.shipment_id
        AND a.min_time<=s.attempt_history_through_at_utc AND NOT EXISTS
        (SELECT 1 FROM silver.tracking_event e WHERE e.run_id=@run_id
         AND e.shipment_id=a.shipment_id AND e.physical_attempt_id=a.physical_attempt_id
         AND e.recorded_at_utc<=s.attempt_history_attested_at_utc
         AND e.attempt_sequence=a.min_sequence AND e.occurred_at_utc=a.min_time
         AND e.event_type=a.min_result)) AS unsupported) x
    WHERE s.run_id=@run_id;
    CREATE UNIQUE CLUSTERED INDEX IX_c0 ON #c0(shipment_id);
    INSERT silver.certificate_check(run_id,shipment_id,source_status,watermark_at_utc,
      certified_attempt_count,observed_attempt_count_through_w,attempt_count_by_cutoff,
      is_certificate_valid,is_first_attempt_eligible,first_attempt_result,primary_exclusion_code)
    SELECT @run_id,c.shipment_id,c.source_status,c.W,c.C,c.observed_count,c.canonical_attempt_count,
      CONVERT(bit,CASE WHEN c.exclusion_code IS NULL THEN 1 ELSE 0 END),
      CONVERT(bit,CASE WHEN c.is_handed_over=1 AND c.terminal_hold_at_cutoff=0
        AND c.exclusion_code IS NULL AND c.canonical_attempt_count>0 THEN 1 ELSE 0 END),
      CASE WHEN c.is_handed_over=1 AND c.terminal_hold_at_cutoff=0
        AND c.exclusion_code IS NULL AND c.canonical_attempt_count>0 THEN (SELECT TOP (1) a.min_result FROM #a a
        WHERE a.shipment_id=c.shipment_id AND a.min_sequence=1 AND a.min_time<=@T
          ORDER BY a.min_time,a.physical_attempt_id) END,
      CASE WHEN c.is_handed_over=0 THEN 'LIFECYCLE_EXCLUDED'
        WHEN c.terminal_hold_at_cutoff=1 THEN 'TERMINAL_EVIDENCE_HOLD'
        WHEN c.exclusion_code IS NOT NULL THEN c.exclusion_code
        WHEN c.canonical_attempt_count=0 THEN 'NO_ATTEMPT_BY_CUTOFF' END
      FROM #c0 c;
    INSERT ctl.quality_issue(run_id,source_name,source_row_number,entity_type,entity_key,scope,severity,reason_code)
    SELECT @run_id,'shipments',s.source_row_number,'SHIPMENT',s.shipment_id,'INTEGRATION','WARN',c.exclusion_code
    FROM #c0 c JOIN silver.shipment s ON s.run_id=@run_id AND s.shipment_id=c.shipment_id
    WHERE c.exclusion_code IS NOT NULL
      AND NOT (c.is_handed_over=0 AND c.exclusion_code='CERTIFICATE_NOT_COMPLETE'
        AND c.observed_count=0);
    UPDATE rr SET disposition='AF',primary_reason_code=COALESCE(rr.primary_reason_code,c.exclusion_code)
    FROM silver.row_resolution rr JOIN silver.shipment s ON s.run_id=rr.run_id
      AND s.source_row_number=rr.source_row_number
    JOIN #c0 c ON c.shipment_id=s.shipment_id
    WHERE rr.run_id=@run_id AND rr.source_name='shipments' AND rr.disposition IN ('AC','AF')
      AND c.exclusion_code IS NOT NULL
      AND NOT (c.is_handed_over=0 AND c.exclusion_code='CERTIFICATE_NOT_COMPLETE'
        AND c.observed_count=0);
    """


def summary_sql() -> str:
    """One Silver analytical row per accepted source shipment."""
    return """
    SET NOCOUNT ON;
    DECLARE @run_id uniqueidentifier=CONVERT(uniqueidentifier,?);
    DECLARE @T datetime2(0)=(SELECT reporting_cutoff_utc FROM ctl.refresh_run WHERE run_id=@run_id);
    INSERT silver.shipment_summary
      (run_id,shipment_id,is_fact_in_scope,is_handed_over,is_due,outcome_code,
       delivered_at_utc,returned_at_utc,lost_at_utc,valid_tracking_message_count_at_cutoff,
       canonical_attempt_count_at_cutoff,is_tracked,is_confirmed_on_time,is_open_overdue,
       is_transit_eligible,transit_seconds,overdue_seconds,is_first_attempt_eligible,
       is_first_attempt_success,start_exclusion_code,promise_exclusion_code,
       outcome_uncertainty_code,transit_exclusion_code,first_attempt_exclusion_code,quality_issue_count)
    SELECT @run_id,b.shipment_id,b.is_fact_in_scope,b.is_handed_over,b.is_due,b.outcome_code,
      CASE WHEN b.outcome_code IN ('ON_TIME','LATE','DELIVERED_PROMISE_UNKNOWN') THEN b.success_at END,
      CASE WHEN b.outcome_code='RETURNED' THEN b.return_at END,
      CASE WHEN b.outcome_code='LOST' THEN b.loss_at END,
      b.valid_message_count,b.canonical_attempt_count,
      CONVERT(bit,CASE WHEN b.is_handed_over=1 AND b.valid_message_count>0 THEN 1 ELSE 0 END),
      CONVERT(bit,CASE WHEN b.is_due=1 AND b.outcome_code='ON_TIME' THEN 1 ELSE 0 END),
      CONVERT(bit,CASE WHEN b.is_due=1 AND b.outcome_code='OPEN_OVERDUE' THEN 1 ELSE 0 END),
      CONVERT(bit,CASE WHEN b.is_handed_over=1 AND b.outcome_code IN ('ON_TIME','LATE','DELIVERED_PROMISE_UNKNOWN')
        AND b.success_at>=b.handed_over_at_utc THEN 1 ELSE 0 END),
      CASE WHEN b.is_handed_over=1 AND b.outcome_code IN ('ON_TIME','LATE','DELIVERED_PROMISE_UNKNOWN')
        AND b.success_at>=b.handed_over_at_utc THEN DATEDIFF_BIG(SECOND,b.handed_over_at_utc,b.success_at) END,
      CASE WHEN b.is_due=1 AND b.outcome_code='OPEN_OVERDUE' THEN
        DATEDIFF_BIG(SECOND,b.promised_delivery_at_utc,@T) END,
      c.is_first_attempt_eligible,
      CASE WHEN c.is_first_attempt_eligible=1 THEN
        CONVERT(bit,CASE WHEN c.first_attempt_result='DELIVERY_SUCCEEDED' THEN 1 ELSE 0 END) END,
      b.start_exclusion_code,b.promise_exclusion_code,b.outcome_uncertainty_code,
      CASE WHEN b.is_handed_over=0 THEN 'NOT_HANDED_OVER'
        WHEN b.outcome_code='UNKNOWN' THEN 'OUTCOME_UNKNOWN'
        WHEN b.outcome_code NOT IN ('ON_TIME','LATE','DELIVERED_PROMISE_UNKNOWN') THEN 'NO_COHERENT_DELIVERY'
        WHEN b.success_at<b.handed_over_at_utc THEN 'NEGATIVE_TRANSIT' END,
      c.primary_exclusion_code,
      (SELECT COUNT(*) FROM ctl.quality_issue q WHERE q.run_id=@run_id
        AND q.entity_type='SHIPMENT' AND q.entity_key=b.shipment_id COLLATE DATABASE_DEFAULT)
    FROM #b b JOIN silver.certificate_check c ON c.run_id=@run_id AND c.shipment_id=b.shipment_id;
    """


def calendar_sql() -> str:
    """Stable UTC calendar keys; no generated ordinal surrogate identifiers."""
    return """
    SET NOCOUNT ON;
    DECLARE @run_id uniqueidentifier=CONVERT(uniqueidentifier,?);
    DECLARE @T datetime2(0)=(SELECT reporting_cutoff_utc FROM ctl.refresh_run WHERE run_id=@run_id);
    DECLARE @E datetime2(0)=(SELECT extract_as_of_utc FROM ctl.refresh_run WHERE run_id=@run_id);
    DECLARE @first date=(SELECT MIN(d) FROM (
      SELECT CONVERT(date,booked_at_utc) d FROM silver.shipment WHERE run_id=@run_id
      UNION ALL SELECT CONVERT(date,handed_over_at_utc) FROM silver.shipment WHERE run_id=@run_id
      UNION ALL SELECT CONVERT(date,promised_delivery_at_utc) FROM silver.shipment WHERE run_id=@run_id
      UNION ALL SELECT CONVERT(date,delivered_at_utc) FROM silver.shipment_summary WHERE run_id=@run_id
      UNION ALL SELECT CONVERT(date,@T) UNION ALL SELECT CONVERT(date,@E)) dates);
    DECLARE @last date=(SELECT MAX(d) FROM (
      SELECT CONVERT(date,booked_at_utc) d FROM silver.shipment WHERE run_id=@run_id
      UNION ALL SELECT CONVERT(date,handed_over_at_utc) FROM silver.shipment WHERE run_id=@run_id
      UNION ALL SELECT CONVERT(date,promised_delivery_at_utc) FROM silver.shipment WHERE run_id=@run_id
      UNION ALL SELECT CONVERT(date,delivered_at_utc) FROM silver.shipment_summary WHERE run_id=@run_id
      UNION ALL SELECT CONVERT(date,@T) UNION ALL SELECT CONVERT(date,@E)) dates);
    IF DATEDIFF(day,@first,@last)>4000 THROW 51031,'Calendar range exceeds 4,000 days',1;
    ;WITH nums AS (SELECT TOP (DATEDIFF(day,@first,@last)+1)
      ROW_NUMBER() OVER (ORDER BY a.object_id,b.object_id)-1 n FROM sys.all_objects a CROSS JOIN sys.all_objects b),
    days AS (SELECT DATEADD(day,n,@first) d FROM nums),
    weeks AS (SELECT d,DATEADD(day,-(DATEDIFF(day,CONVERT(date,'19000101'),d)%7),d) monday FROM days)
    INSERT silver.calendar_date(run_id,date_key,calendar_date,calendar_year,calendar_month,
      day_of_month,iso_year,iso_week,week_start_date,month_start_date)
    SELECT @run_id,YEAR(d)*10000+MONTH(d)*100+DAY(d),d,YEAR(d),MONTH(d),DAY(d),
      YEAR(DATEADD(day,3,monday)),DATEPART(ISO_WEEK,d),monday,DATEFROMPARTS(YEAR(d),MONTH(d),1)
    FROM weeks;
    """


def finalize_sql() -> str:
    """Persist independent row and population equations; fail the transaction on a breach."""
    return """
    SET NOCOUNT ON;
    DECLARE @run_id uniqueidentifier=CONVERT(uniqueidentifier,?);
    DECLARE @checks TABLE (check_name varchar(80),observed bigint,expected bigint);
    INSERT @checks VALUES
      ('ANALYTICS_SUMMARY_COUNT',(SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id),
        (SELECT COUNT_BIG(*) FROM silver.shipment WHERE run_id=@run_id)),
      ('ANALYTICS_CERTIFICATE_COUNT',(SELECT COUNT_BIG(*) FROM silver.certificate_check WHERE run_id=@run_id),
        (SELECT COUNT_BIG(*) FROM silver.shipment WHERE run_id=@run_id)),
      ('ANALYTICS_FACT_SPLIT',(SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id),
        (SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_fact_in_scope=1)+
        (SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_fact_in_scope=0)),
      ('ANALYTICS_START_SPLIT',(SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_fact_in_scope=1),
        (SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_fact_in_scope=1
          AND (is_handed_over=1 OR start_exclusion_code IS NOT NULL))),
      ('ANALYTICS_DUE_SPLIT',(SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_handed_over=1),
        (SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_handed_over=1
          AND (is_due=1 OR promise_exclusion_code IS NOT NULL))),
      ('ANALYTICS_FIRST_ATTEMPT_SPLIT',(SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_handed_over=1),
        (SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_handed_over=1
          AND (is_first_attempt_eligible=1 OR first_attempt_exclusion_code IS NOT NULL))),
      ('ANALYTICS_TRACKING_SPLIT',(SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_handed_over=1),
        (SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_handed_over=1
          AND (is_tracked=1 OR is_tracked=0))),
      ('ANALYTICS_DUE_OUTCOME_SPLIT',(SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_due=1),
        (SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND is_due=1
          AND outcome_code IN ('ON_TIME','LATE','OPEN_OVERDUE','OPEN_AT_DEADLINE','RETURNED','LOST','UNKNOWN'))),
      ('ANALYTICS_TRANSIT_NONNEGATIVE',0,
        (SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND transit_seconds<0)),
      ('ANALYTICS_OVERDUE_NONNEGATIVE',0,
        (SELECT COUNT_BIG(*) FROM silver.shipment_summary WHERE run_id=@run_id AND overdue_seconds<0));
    INSERT @checks
    SELECT CONCAT('ANALYTICS_',UPPER(raw.source_name),'_RECON'),raw.n,
      (SELECT COUNT_BIG(*) FROM silver.row_resolution rr WHERE rr.run_id=@run_id
        AND rr.source_name=raw.source_name AND rr.disposition IN ('AC','AF','DX','DE','DS','Q'))
    FROM (SELECT 'shipments' source_name,COUNT_BIG(*) n FROM bronze.shipments WHERE run_id=@run_id
      UNION ALL SELECT 'tracking_events',COUNT_BIG(*) FROM bronze.tracking_events WHERE run_id=@run_id
      UNION ALL SELECT 'customers',COUNT_BIG(*) FROM bronze.customers WHERE run_id=@run_id
      UNION ALL SELECT 'customer_locations',COUNT_BIG(*) FROM bronze.customer_locations WHERE run_id=@run_id
      UNION ALL SELECT 'depots',COUNT_BIG(*) FROM bronze.depots WHERE run_id=@run_id
      UNION ALL SELECT 'service_levels',COUNT_BIG(*) FROM bronze.service_levels WHERE run_id=@run_id) raw;
    INSERT @checks VALUES
      ('ANALYTICS_EVENT_CUTOFF_SPLIT',
        (SELECT COUNT_BIG(*) FROM silver.tracking_event WHERE run_id=@run_id),
        (SELECT COUNT_BIG(*) FROM silver.tracking_event WHERE run_id=@run_id AND is_after_cutoff=1)+
        (SELECT COUNT_BIG(*) FROM silver.tracking_event WHERE run_id=@run_id AND is_after_cutoff=0
          AND has_valid_handover_context=1)+
        (SELECT COUNT_BIG(*) FROM silver.tracking_event WHERE run_id=@run_id AND is_after_cutoff=0
          AND has_valid_handover_context=0));
    INSERT ctl.run_check(run_id,check_name,passed,observed_value,expected_rule,detail)
    SELECT @run_id,check_name,CASE WHEN observed=expected THEN 1 ELSE 0 END,
      CONVERT(nvarchar(200),observed),CONVERT(nvarchar(300),expected),'Step 6C transactional check'
    FROM @checks;
    IF EXISTS (SELECT 1 FROM @checks WHERE observed<>expected) THROW 51032,'Step 6C reconciliation failed',1;
    """
