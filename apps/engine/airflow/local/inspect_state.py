"""업무 결과 스냅샷 — 두 경로 비교의 재료. fake-aws 컨테이너의 업무 venv 로 실행한다(JSON 출력).

checksum 은 파일 바이트가 아니라 **정렬한 행**의 sha256 이다. parquet 메타데이터 차이가 업무 동등성을
가리지 않게 하되, 파일 바이트 해시도 따로 남긴다(바이트가 같으면 더 강한 동등이다).
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import psycopg
import pyarrow.parquet as pq

LAKE = Path("/lab-data/lake")
DEV = Path("/inputs/dev-lake")
STATE = Path("/lab-data/state")
PART = "canonical/market_data/investor_flow_intraday/market=KR"
MANIFESTS = "operations_archive/canonical_run_manifests/dataset=investor_flow_intraday"


def _rows_sha(rows: list[dict]) -> str:
    text = json.dumps(sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str)),
                      sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def _partitions(root: Path) -> dict:
    out = {}
    for f in sorted(root.glob(f"{PART}/trade_date=*/part-00000.parquet")):
        data = f.read_bytes()
        rows = pq.read_table(io.BytesIO(data)).to_pylist()
        out[f.parent.name] = {"rows": len(rows), "rows_sha": _rows_sha(rows),
                              "file_sha": hashlib.sha256(data).hexdigest()}
    return out


def _jsonl(name: str) -> list[dict]:
    path = STATE / name
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def main() -> int:
    manifests = {}
    for f in sorted(LAKE.glob(f"{MANIFESTS}/run_id=*/manifest.json")):
        body = json.loads(f.read_text())
        manifests[f.parent.name[7:]] = {
            "canonical_written": body.get("canonical_written"),
            "partitions": [(p["trade_date"], p["sha256"], len(p.get("winner_ids") or []))
                           for p in body.get("canonical_partitions", [])]}
    with psycopg.connect("host=postgres dbname=edge user=edge password=edge") as conn:
        cur = conn.cursor()
        cur.execute("SELECT instrument_id, trade_date, asof_slot, net_qty_foreign_est,"
                    " net_qty_institution_est, net_qty_total_est, available_at, data_version"
                    " FROM investor_flow_intraday")
        cols = [d.name for d in cur.description]
        db_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        by_version: dict[str, int] = {}
        for r in db_rows:
            by_version[r["data_version"]] = by_version.get(r["data_version"], 0) + 1
        cur.execute(
            "SELECT r.run_key, r.pipeline_run_id, r.orchestrator, r.orchestrator_run_ref,"
            " r.launch_status, et.task_key, et.task_outcome, et.data_status, et.records_out,"
            " et.failed_records,"
            " (SELECT json_agg(json_build_object('arn', a.ecs_task_arn, 'status', a.execution_status,"
            "   'exit', a.exit_code, 'source', a.record_source) ORDER BY a.created_at)"
            "  FROM ops_task_attempt a WHERE a.expected_task_id = et.expected_task_id)"
            " FROM ops_pipeline_run r JOIN ops_expected_task et USING (pipeline_run_id)"
            " ORDER BY r.run_key, et.task_key")
        ledger: dict[str, dict] = {}
        for (run_key, run_id, orch, ref, launch, task, outcome, data_status, rec, failed,
             attempts) in cur.fetchall():
            run = ledger.setdefault(run_key, {"run_id": run_id, "orchestrator": orch, "ref": ref,
                                              "launch": launch, "tasks": {}})
            run["tasks"][task] = {"outcome": outcome, "data_status": data_status,
                                  "records_out": rec, "failed_records": failed,
                                  "attempts": attempts or []}
        cur.execute("SELECT issue_type, dedupe_key, status FROM ops_reconciliation_issue ORDER BY 2")
        issues = [list(r) for r in cur.fetchall()]
    invocations = _jsonl("invocations.jsonl")
    calls = _jsonl("external_calls.jsonl")
    print(json.dumps({
        "canonical": _partitions(LAKE), "dev_canonical": _partitions(DEV),
        "manifests": manifests,
        "db": {"rows": len(db_rows), "rows_sha": _rows_sha(db_rows),
               "values_sha": _rows_sha([{k: v for k, v in r.items() if k != "data_version"}
                                        for r in db_rows]),
               # 업무 값 = 정체성 + 수량 3컬럼. available_at·data_version 은 "어느 run 이 먼저/마지막에
               # 적재했나"의 메타라 경로·순서에 따라 달라지는 것이 계약이다(적재는 수량 변경에만 UPDATE).
               "qty_sha": _rows_sha([{k: r[k] for k in ("instrument_id", "trade_date", "asof_slot",
                                                         "net_qty_foreign_est", "net_qty_institution_est",
                                                         "net_qty_total_est")} for r in db_rows]),
               "by_data_version": by_version},
        "ledger": ledger, "issues": issues,
        "invocations": [{k: i[k] for k in ("step", "run_id", "skip_if_succeeded")} for i in invocations],
        "external_calls": {"invocations": len(calls), "calls": sum(c["calls"] for c in calls)},
    }, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
