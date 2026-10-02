"""Run one or all published-Gold business queries without changing publication."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bronze_pipeline as bronze
import verify_analysis as analysis


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=bronze.ROOT / "config" / "bronze_runtime.json",
                        help="Runtime JSON with the explicit target SQL Server and database")
    parser.add_argument("--query",required=True,help="01..12 or all")
    parser.add_argument("--limit",type=int,default=20,help="Rows printed per query; 0 prints all")
    args=parser.parse_args()
    if args.limit<0:
        parser.error("--limit must be nonnegative")
    if args.query=="all":
        names=analysis.FILES
    else:
        matches=[name for name in analysis.FILES if name.startswith(args.query.zfill(2)+"_")]
        if len(matches)!=1:
            parser.error("--query must be 01..12 or all")
        names=matches
    try:
        config=bronze.config_from(args.config)
        bronze.announce_target(config,"analysis queries (read-only)")
        output={"active_run_id":None,"queries":{}}
        with bronze.connect(config) as conn:
            conn.timeout=60
            cur=conn.cursor()
            cur.execute("SELECT 1")
            lock=cur.execute("""DECLARE @status int;
                EXEC @status=sys.sp_getapplock @Resource='parcel_gold_publication',
                @LockMode='Shared',@LockOwner='Transaction',@LockTimeout=10000;
                SELECT @status""").fetchval()
            if lock<0:
                raise RuntimeError(f"Could not hold published run: {lock}")
            rid=cur.execute("SELECT CONVERT(varchar(36),active_run_id) FROM ctl.published_run WHERE singleton_id=1").fetchval()
            if rid is None:
                raise RuntimeError("No locally published run")
            output["active_run_id"]=rid.lower()
            for name in names:
                rows,seconds=analysis.execute(cur,analysis.QUERY_DIR/name)
                selected=rows if args.limit==0 else rows[:args.limit]
                output["queries"][name]={"row_count":len(rows),"elapsed_seconds":seconds,
                                          "rows":[{k:analysis.serial(v) for k,v in row.items()} for row in selected]}
            conn.rollback()
        print(json.dumps(output,indent=2,ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
