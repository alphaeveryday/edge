"""canonical 적재 진입점 — 백필과 **별도 CLI** 다.

백필(raw 수집)과 canonical(정규화)을 한 명령에 묶지 않는다. 묶으면 재적재를 하려고 수집을
다시 돌리게 되고, 그 사이 원본이 바뀌면 무엇을 재현한 것인지 알 수 없다. raw 는 한 번 쌓고,
canonical 은 그 위에서 몇 번이든 다시 만든다.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .athena import Athena
from .financials import merge_statement_line
from .reports import merge_report_current, merge_sql, staging_name
from .tables import DB_DRAFT, DB_PROD, REPORT_CURRENT, as_of_sql, latest_view


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="canonical(Iceberg) 적재")
    ap.add_argument("cmd", choices=["reports", "financials", "ddl", "asof"])
    ap.add_argument("--run-id", default="")
    ap.add_argument("--ingest-date", default="")
    ap.add_argument("--bucket", default="edge-dev-pipeline-lake")
    ap.add_argument("--draft", action="store_true",
                    help="draft DB + draft/ 접두사 (승격 전 기본)")
    ap.add_argument("--as-of", default="", help="asof: PIT 기준 시각")
    ap.add_argument("--profile", default="")
    ap.add_argument("--print-only", action="store_true", help="SQL 만 출력하고 안 돌린다")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    db = DB_DRAFT if a.draft else DB_PROD
    prefix = "draft" if a.draft else ""

    if a.cmd == "ddl" or a.print_only:
        print(REPORT_CURRENT.ddl(db, bucket=a.bucket, prefix=prefix))
        print(";\n")
        print(latest_view(REPORT_CURRENT, db))
        if a.run_id:
            print(";\n")
            print(merge_sql(db, staging_name(a.run_id), run_id=a.run_id,
                            ingest_date=a.ingest_date))
        if a.print_only:
            return 0

    ath = Athena(profile=a.profile)
    if a.cmd == "ddl":
        ath.run(f"CREATE DATABASE IF NOT EXISTS {db}")
        ath.run(REPORT_CURRENT.ddl(db, bucket=a.bucket, prefix=prefix))
        ath.run(latest_view(REPORT_CURRENT, db))
        print(json.dumps({"database": db, "table": REPORT_CURRENT.name,
                          "location": REPORT_CURRENT.location(a.bucket, prefix)},
                         ensure_ascii=False, indent=1))
        return 0

    if a.cmd == "asof":
        if not a.as_of:
            raise SystemExit("--as-of 가 필요하다")
        rows = ath.run(as_of_sql(REPORT_CURRENT, db, a.as_of) + " LIMIT 20")
        for r in rows:
            print("\t".join(r)[:200])
        return 0

    if a.cmd in ("reports", "financials"):
        if not (a.run_id and a.ingest_date):
            raise SystemExit("--run-id 와 --ingest-date 가 필요하다")
        fn = merge_report_current if a.cmd == "reports" else merge_statement_line
        out = fn(ath, run_id=a.run_id, ingest_date=a.ingest_date,
                 database=db, bucket=a.bucket, prefix=prefix)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    raise SystemExit(f"알 수 없는 명령: {a.cmd}")


if __name__ == "__main__":
    sys.exit(main())
