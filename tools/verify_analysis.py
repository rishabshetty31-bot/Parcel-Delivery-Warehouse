"""Execute and independently reconcile the twelve public-Gold analysis queries."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import traceback
import uuid
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import bronze_pipeline as bronze


ROOT = bronze.ROOT
DEFAULT_CONFIG_PATH = ROOT / "config" / "bronze_runtime.json"
QUERY_DIR = ROOT / "sql" / "analytics"
TARGET = ROOT / "evidence" / "step8_results.json"
FILES = (
    "01_overall_scorecard.sql", "02_due_outcomes.sql", "03_weekly_service_performance.sql",
    "04_service_comparison.sql", "05_origin_depot_comparison.sql",
    "06_destination_depot_comparison.sql", "07_origin_destination_lanes.sql",
    "08_customer_performance.sql", "09_overdue_backlog_aging.sql",
    "10_first_attempt_performance.sql", "11_tracking_coverage.sql",
    "12_returns_losses_uncertainty.sql",
)
RATE_TOLERANCE = Decimal("0.00000051")
LIMITATIONS = (
    "Public Gold exposes six-decimal hours, so its views alone cannot reproduce exact integer-second duration sums; Step 7 checked exact seconds.",
    "The active demonstration run has no fact referencing UNK_SERVICE or UNK_DEPOT; Step 7 isolated variants checked those mappings.",
    "An empty published fact run was not created in Step 8; read-only empty-population variants of the analysis SQL were checked instead.",
    "No dashboard, external publication, causal delay analysis, or historical as-known-at-week-end series was built.",
)


def serial(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat() + ("Z" if isinstance(value, dt.datetime) else "")
    return value


def get_sql(path: Path) -> str:
    sql = path.read_text(encoding="utf-8")
    body = re.sub(r"(?m)^\s*--.*$", "", sql).strip()
    if re.search(r"\b(bronze|silver|ctl)\s*\.|\bgold\s*\.\s*fn_", body, re.I):
        raise AssertionError(f"Reporting SQL uses non-public objects: {path.name}")
    if re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|EXEC|OPENROWSET)\b", body, re.I):
        raise AssertionError(f"Reporting SQL is not read-only: {path.name}")
    allowed = {"gold.fact_shipments", "gold.dim_customers", "gold.dim_services",
               "gold.dim_depots", "gold.dim_date"}
    used = {x.lower() for x in re.findall(r"\bgold\s*\.\s*\w+", body, re.I)}
    used = {re.sub(r"\s+", "", x) for x in used}
    if not used or not used <= allowed:
        raise AssertionError(f"Unexpected reporting object in {path.name}: {used}")
    if not body.upper().startswith(("WITH ", "SELECT ")):
        raise AssertionError(f"Reporting SQL has no SELECT entry point: {path.name}")
    return sql


def execute(cur, path: Path) -> tuple[list[dict], float]:
    started = time.perf_counter()
    cur.execute(get_sql(path))
    names = [x[0] for x in cur.description]
    rows = [dict(zip(names, row)) for row in cur.fetchall()]
    return rows, round(time.perf_counter()-started, 3)


def dec(value) -> Decimal:
    return Decimal(str(value))


def rate(row: dict, field: str, numerator: str, denominator: str) -> None:
    n,d = dec(row[numerator]),dec(row[denominator])
    value=row[field]
    if d==0:
        assert value is None,(field,n,d,value)
    else:
        assert value is not None and abs(dec(value)-n/d)<=RATE_TOLERANCE,(field,n,d,value)


def average(row: dict, field: str, total: str, denominator: str) -> None:
    d=dec(row[denominator]);value=row[field]
    if d==0:
        assert value is None,(field,value)
    else:
        assert value is not None and abs(dec(value)-dec(row[total])/d)<=RATE_TOLERANCE,(field,value)


def sum_field(rows: list[dict], field: str) -> Decimal:
    return sum((dec(row[field] or 0) for row in rows), Decimal(0))


def oracle_duration(oracle: dict, field: str) -> tuple[int, Decimal]:
    """Recreate view precision from independent per-shipment expected hours."""
    seconds = [round(row[field] * 3600) for row in oracle["per_shipment"].values()
               if row["fact"] and row[field] is not None]
    rounded_hours = sum((Decimal(value).scaleb(0) / Decimal(3600)).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP) for value in seconds)
    return sum(seconds), rounded_hours


def check_dimension_membership(cur, expected_count: int) -> dict:
    roles=("customer_key","service_key","origin_depot_key","destination_depot_key",
           "booking_date_key","handover_date_key","promise_date_key","delivery_date_key")
    mappings=("dim_customers","dim_services","dim_depots","dim_depots",
              "dim_date","dim_date","dim_date","dim_date")
    keys={}
    for view,key in (("dim_customers","customer_key"),("dim_services","service_key"),
                     ("dim_depots","depot_key"),("dim_date","date_key")):
        values=[row[0] for row in cur.execute(f"SELECT {key} FROM gold.{view}").fetchall()]
        assert len(values)==len(set(values))
        keys[view]=set(values)
    for view,reserved in (("dim_customers","UNK_CUSTOMER"),("dim_services","UNK_SERVICE"),
                          ("dim_depots","UNK_DEPOT"),("dim_date",0)):
        assert reserved in keys[view]
    fact=cur.execute("SELECT "+",".join(roles)+" FROM gold.fact_shipments").fetchall()
    assert len(fact)==expected_count
    assert all(row[i] in keys[mappings[i]] for row in fact for i in range(8))
    return {"fact_rows":len(fact),"roles_checked":8,
            "dimension_key_counts":{name:len(values) for name,values in keys.items()}}


def checks(cur, queries: dict[str,list[dict]], oracle: dict) -> dict:
    score=queries["01_overall_scorecard"][0]
    expected=oracle["expected"]
    compare={
        "shipment_count":expected["fact_rows"],"handed_over_count":expected["handed_over"],
        "due_count":expected["due"],"on_time_count":expected["confirmed_on_time"]["numerator"],
        "transit_eligible_count":expected["transit"]["eligible"],
        "open_overdue_count":expected["open_overdue"]["shipments"],
        "first_attempt_eligible_count":expected["first_attempt_success"]["denominator"],
        "first_attempt_success_count":expected["first_attempt_success"]["numerator"],
        "tracked_count":expected["tracking_coverage"]["numerator"],
        "returned_count":expected["return_rate"]["numerator"],
        "lost_count":expected["loss_rate"]["numerator"],
    }
    for field,value in compare.items():
        assert score[field]==value,(field,score[field],value)
    transit_seconds, transit_gold_hours = oracle_duration(oracle, "transit_hours")
    overdue_seconds, overdue_gold_hours = oracle_duration(oracle, "overdue_hours")
    assert transit_seconds==round(expected["transit"]["hours_sum"]*3600)
    assert overdue_seconds==round(expected["open_overdue"]["hours_sum"]*3600)
    assert dec(score["transit_hours_sum"])==transit_gold_hours
    assert dec(score["overdue_hours_sum"])==overdue_gold_hours
    for field,n,d in (("on_time_rate","on_time_count","due_count"),
                      ("first_attempt_success_rate","first_attempt_success_count","first_attempt_eligible_count"),
                      ("tracking_coverage_rate","tracked_count","handed_over_count"),
                      ("return_rate","returned_count","due_count"),("loss_rate","lost_count","due_count")):
        rate(score,field,n,d)
    average(score,"average_transit_hours","transit_hours_sum","transit_eligible_count")
    average(score,"average_overdue_hours","overdue_hours_sum","open_overdue_count")

    outcomes=queries["02_due_outcomes"]
    assert len(outcomes)==7 and sum_field(outcomes,"outcome_count")==score["due_count"]
    assert {r["outcome_code"]:r["outcome_count"] for r in outcomes}=={
        **expected["due_outcomes"],"OPEN_AT_DEADLINE":0}
    for row in outcomes:
        assert row["due_count"]==score["due_count"]
        rate(row,"outcome_share","outcome_count","due_count")

    weekly=queries["03_weekly_service_performance"]
    assert sum_field(weekly,"due_count")==score["due_count"]
    assert sum_field(weekly,"on_time_count")==score["on_time_count"]
    assert len({(r["iso_year"],r["iso_week"],r["service_key"]) for r in weekly})==len(weekly)
    for row in weekly:rate(row,"on_time_rate","on_time_count","due_count")

    partitions={
        "04_service_comparison":{"shipment_count":"shipment_count","handed_over_count":"handed_over_count",
            "due_count":"due_count","on_time_count":"on_time_count","transit_eligible_count":"transit_eligible_count",
            "transit_hours_sum":"transit_hours_sum","first_attempt_eligible_count":"first_attempt_eligible_count",
            "first_attempt_success_count":"first_attempt_success_count"},
        "05_origin_depot_comparison":{"shipment_count":"shipment_count","handed_over_count":"handed_over_count",
            "due_count":"due_count","on_time_count":"on_time_count","tracked_count":"tracked_count"},
        "06_destination_depot_comparison":{"shipment_count":"shipment_count","due_count":"due_count",
            "on_time_count":"on_time_count","open_overdue_count":"open_overdue_count",
            "overdue_hours_sum":"overdue_hours_sum"},
        "07_origin_destination_lanes":{"shipment_count":"shipment_count","due_count":"due_count",
            "on_time_count":"on_time_count","transit_eligible_count":"transit_eligible_count",
            "transit_hours_sum":"transit_hours_sum"},
        "08_customer_performance":{"shipment_count":"shipment_count","due_count":"due_count",
            "on_time_count":"on_time_count","open_overdue_count":"open_overdue_count",
            "overdue_hours_sum":"overdue_hours_sum"},
    }
    for name,mapping in partitions.items():
        for component,overall in mapping.items():
            assert sum_field(queries[name],component)==dec(score[overall]),(name,component)
    for name in partitions:
        for row in queries[name]:
            rate(row,"on_time_rate","on_time_count","due_count")
            if "average_transit_hours" in row:
                average(row,"average_transit_hours","transit_hours_sum","transit_eligible_count")
            if "first_attempt_success_rate" in row:
                rate(row,"first_attempt_success_rate","first_attempt_success_count","first_attempt_eligible_count")
            if "tracking_coverage_rate" in row:
                rate(row,"tracking_coverage_rate","tracked_count","handed_over_count")
            if "average_overdue_hours" in row:
                average(row,"average_overdue_hours","overdue_hours_sum","open_overdue_count")
            if "has_comparison_sample" in row:
                assert bool(row["has_comparison_sample"])==(row["due_count"]>=30)
    customers=queries["08_customer_performance"]
    unknown_customer=[r for r in customers if r["customer_key"]=="UNK_CUSTOMER"]
    assert len(unknown_customer)==1 and unknown_customer[0]["shipment_count"]==3

    bands=queries["09_overdue_backlog_aging"]
    assert [r["band_order"] for r in bands]==[1,2,3,4]
    assert sum_field(bands,"open_overdue_count")==score["open_overdue_count"]
    assert sum_field(bands,"overdue_hours_sum")==dec(score["overdue_hours_sum"])
    ages=[r[0] for r in cur.execute("SELECT overdue_hours FROM gold.fact_shipments WHERE is_open_overdue=1").fetchall()]
    assert len(ages)==score["open_overdue_count"]
    expected_bands={i:{"count":0,"hours":Decimal(0)} for i in range(1,5)}
    for age in ages:
        age=dec(age)
        assert age>=0
        band=1 if age<24 else 2 if age<72 else 3 if age<168 else 4
        expected_bands[band]["count"]+=1;expected_bands[band]["hours"]+=age
    for row in bands:
        want=expected_bands[row["band_order"]]
        assert row["open_overdue_count"]==want["count"]
        assert dec(row["overdue_hours_sum"])==want["hours"]
        average(row,"average_overdue_hours","overdue_hours_sum","open_overdue_count")

    attempts=queries["10_first_attempt_performance"]
    totals=[r for r in attempts if r["is_total"]==1]
    assert len(totals)==1
    total=totals[0]
    assert (total["handed_over_count"],total["eligible_count"],total["successful_count"])==(
        score["handed_over_count"],score["first_attempt_eligible_count"],score["first_attempt_success_count"])
    detail=[r for r in attempts if r["is_total"]==0]
    for field in ("handed_over_count","eligible_count","successful_count","excluded_count"):
        assert sum_field(detail,field)==dec(total[field])
    assert sum(r["category"]=="ELIGIBLE" for r in detail)==1
    assert all(r["category"]!="UNSPECIFIED_EXCLUSION" for r in detail)
    for row in attempts:
        rate(row,"success_rate","successful_count","eligible_count")
        assert row["handed_over_count"]==row["eligible_count"]+row["excluded_count"]

    coverage=queries["11_tracking_coverage"]
    assert sum_field(coverage,"handed_over_count")==score["handed_over_count"]
    assert sum_field(coverage,"covered_count")==score["tracked_count"]
    assert sum_field(coverage,"uncovered_count")==score["handed_over_count"]-score["tracked_count"]
    for row in coverage:
        assert row["handed_over_count"]==row["covered_count"]+row["uncovered_count"]
        rate(row,"coverage_rate","covered_count","handed_over_count")

    terminal=queries["12_returns_losses_uncertainty"][0]
    assert terminal["due_count"]==score["due_count"]
    assert terminal["returned_count"]==score["returned_count"]
    assert terminal["lost_count"]==score["lost_count"]
    assert terminal["unknown_due_count"]==expected["due_outcomes"]["UNKNOWN"]
    assert terminal["due_with_uncertainty_code_count"]>=terminal["unknown_due_count"]
    for field,n in (("return_rate","returned_count"),("loss_rate","lost_count"),
                    ("unknown_due_share","unknown_due_count")):
        rate(terminal,field,n,"due_count")
    dimensions=check_dimension_membership(cur,score["shipment_count"])

    # Read-only boundary and empty-cohort probes; no pointer changes or fixture publication.
    probe=cur.execute("""SELECT v.age,
        CASE WHEN v.age<24 THEN 1 WHEN v.age<72 THEN 2 WHEN v.age<168 THEN 3 ELSE 4 END AS band
        FROM (VALUES (CAST(0 AS decimal(19,6))),(CAST(23.999999 AS decimal(19,6))),
        (CAST(24 AS decimal(19,6))),(CAST(71.999999 AS decimal(19,6))),
        (CAST(72 AS decimal(19,6))),(CAST(167.999999 AS decimal(19,6))),
        (CAST(168 AS decimal(19,6)))) AS v(age) ORDER BY v.age""").fetchall()
    assert [r[1] for r in probe]==[1,1,2,2,3,3,4]
    score_sql=get_sql(QUERY_DIR/FILES[0])
    empty_score_sql=score_sql.replace("FROM gold.fact_shipments\n",
        "FROM (SELECT * FROM gold.fact_shipments WHERE 1=0) AS empty_fact\n",1)
    assert empty_score_sql!=score_sql
    cur.execute(empty_score_sql)
    empty_score=dict(zip((c[0] for c in cur.description),cur.fetchone()))
    assert (empty_score["shipment_count"],empty_score["due_count"],empty_score["on_time_rate"],
            empty_score["average_transit_hours"])==(0,0,None,None)
    week_sql=get_sql(QUERY_DIR/FILES[2])
    empty_week_sql=week_sql.replace("WHERE f.is_due=1","WHERE f.is_due=1 AND 1=0",1)
    assert empty_week_sql!=week_sql
    assert cur.execute(empty_week_sql).fetchall()==[]
    band_sql=get_sql(QUERY_DIR/FILES[8])
    empty_band_sql=band_sql.replace("FROM gold.fact_shipments WHERE is_open_overdue=1",
        "FROM gold.fact_shipments WHERE is_open_overdue=1 AND 1=0",1)
    assert empty_band_sql!=band_sql
    empty_bands=cur.execute(empty_band_sql).fetchall()
    assert len(empty_bands)==4 and all(row[5]==0 and row[7] is None for row in empty_bands)
    return {"overall_vs_independent_oracle":"PASS",
            "oracle_duration_seconds":{"transit":transit_seconds,"overdue":overdue_seconds},
            "complete_group_partitions":list(partitions)+[
                "02_due_outcomes","03_weekly_service_performance","09_overdue_backlog_aging",
                "10_first_attempt_performance","11_tracking_coverage"],
            "dimension_membership":dimensions,"unknown_customer_shipments":3,
            "backlog_boundary_probe":[[str(a),b] for a,b in probe],
            "empty_subset":{"scorecard_shipments":0,"scorecard_due":0,"on_time_rate":None,
                            "transit_average":None,"weekly_rows":0,"backlog_band_rows":4,
                            "backlog_band_counts":[int(row[5]) for row in empty_bands]},
            "decimal_rates_checked":True,"grouped_rates_checked":True}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--expected-run-id",help="Require this active demo publication for an isolated replay")
    parser.add_argument("--output",type=Path,default=TARGET)
    args=parser.parse_args()
    config_path=args.config.resolve()
    config=bronze.config_from(config_path)
    bronze.announce_target(config,"analysis verification (read-only)")
    expected_run_id=str(uuid.UUID(args.expected_run_id)) if args.expected_run_id else None
    result={"status":"RUNNING","active_run_id":None,"query_files":list(FILES),
            "queries":{},"checks":{},"limitations":list(LIMITATIONS),
            "target_database":config["connection"]["database"],
            "expected_run_id":expected_run_id}
    try:
        present={p.name for p in QUERY_DIR.glob("*.sql")}
        assert present==set(FILES),(present^set(FILES))
        oracle=json.loads((ROOT/"tests"/"expected"/"demo.json").read_text(encoding="utf-8"))
        if expected_run_id is None:
            reviewed=json.loads((ROOT/"evidence"/"step7_results.json").read_text(encoding="utf-8"))
            assert reviewed["status"]=="PASS"
            expected_run_id=reviewed["final_published_run"]
        result["expected_run_id"]=expected_run_id
        with bronze.connect(config) as conn:
            conn.timeout=60
            cur=conn.cursor()
            cur.execute("SELECT 1")
            lock=cur.execute("""DECLARE @status int;
                EXEC @status=sys.sp_getapplock @Resource='parcel_gold_publication',
                @LockMode='Shared',@LockOwner='Transaction',@LockTimeout=10000;
                SELECT @status""").fetchval()
            assert lock>=0,lock
            pointer=cur.execute("SELECT CONVERT(varchar(36),active_run_id) FROM ctl.published_run WHERE singleton_id=1").fetchval()
            assert pointer is not None
            run_id=pointer.lower();result["active_run_id"]=run_id
            assert run_id==expected_run_id
            expected_t=dt.datetime.strptime(config["reporting_cutoff_utc"],"%Y-%m-%dT%H:%M:%SZ")
            expected_e=dt.datetime.strptime(config["extract_as_of_utc"],"%Y-%m-%dT%H:%M:%SZ")
            data={}
            for name in FILES:
                rows,seconds=execute(cur,QUERY_DIR/name)
                for row in rows:
                    assert row["run_id"] is not None and str(row["run_id"]).lower()==run_id,(name,row.get("run_id"))
                    assert row["reporting_cutoff_utc"]==expected_t and row["extract_as_of_utc"]==expected_e
                stem=Path(name).stem
                data[stem]=rows
                result["queries"][stem]={"row_count":len(rows),"elapsed_seconds":seconds,
                                          "columns":list(rows[0]) if rows else [],
                                          "rows":[{k:serial(v) for k,v in row.items()} for row in rows]}
                print(json.dumps({"query":stem,"rows":len(rows),"seconds":seconds}),flush=True)
            result["checks"]=checks(cur,data,oracle)
            pointer_after=cur.execute("SELECT CONVERT(varchar(36),active_run_id) FROM ctl.published_run WHERE singleton_id=1").fetchval()
            assert pointer_after and pointer_after.lower()==run_id
            result["consistent_publication"]={"lock_mode":"Shared transaction application lock",
                                               "run_id_before":run_id,"run_id_after":pointer_after.lower()}
            conn.rollback()
        result["status"]="PASS"
        return 0
    except Exception as exc:
        result["status"]="FAIL"
        result["error"]=f"{type(exc).__name__}: {exc}"
        result["traceback"]=traceback.format_exc()
        print(result["traceback"],file=sys.stderr,flush=True)
        return 1
    finally:
        args.output.write_text(json.dumps(result,indent=2,default=serial,ensure_ascii=False)+"\n",encoding="utf-8")


if __name__=="__main__":
    raise SystemExit(main())
