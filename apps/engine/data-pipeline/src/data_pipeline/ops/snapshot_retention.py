"""분석 내부 스냅샷 보존 — ETF 체인마다 다음 분석이 읽는 최신 스냅샷만 남기고 지난 것을 지운다.

분석 엔진(analysis-engine-v2)은 분석 1건마다 그 ETF 의 누적 조사 상태 전체(`record_research_state`)와
뉴스 기준선(`capture_news_snapshot`)을 `tool_runs` 1행씩으로 남긴다. 다음 분석은 체인(종류·ETF·data_source,
가격변동은 거래일까지)의 최신 completed 분석의 행만 읽으므로(`storage/research.py` load_research,
`storage/news_snapshots.py` read_snapshot) 지난 행은 다시 읽히지 않는다. 그런데 행마다 상태 전체를 다시 적어
전망 1건에 약 4MB, 하루 약 250건이면 하루 약 1GB 씩 DB 가 불었다(2026-11-24 표본 실측·추정).

지우는 행(전부 만족):
- 스냅샷 2종, 행 status completed, 7일 지남(엔진의 재시도·재실행 창보다 넉넉하게).
- 부모 분석이 끝남(completed/failed). 실행 중인 분석의 행은 그 분석이 곧 읽을 수 있다.
- 부모가 체인의 최근 completed 분석 2건이 아님. 정렬은 엔진의 직전 분석 선택(`service._previous`)과 같다.
  최신 건은 다음 분석이, 그 앞 건은 최신 건과 같은 분석 시각으로 다시 돌리는 재실행이 읽는다.
- 부모가 그 스냅샷 종류를 가진 최신 completed 분석이 아님(load_research 는 스냅샷 없는 부모를 건너 거슬러 올라간다).
- 부모가 실행 중인 분석의 직전 분석(previous_analysis_id)이 아님.
- 어떤 발행 항목도 인용하지 않음(스냅샷은 근거가 아니지만 인용된 행은 배달·검증이 읽는다).
대시보드에서 직전 분석을 임의로 골라 다시 돌리는 실험은 보호하지 않는다 — 그 경우 load_research 가 지난 감사 기록을
다시 모아 상태를 복구하고 gaps 에 누락을 적는다.

보관하지 않는다: 지운 행의 내용은 파생물이다. 도구 출력 원문은 각자의 `tool_runs` 행에, 메모·질문은 최신
스냅샷에 그대로 남는다. 2026-11-16·11-22 수동 정리분은 `operations_archive/tool_runs_snapshots/` 에 있다.

삭제는 WAL 을 만들어 RDS 디스크를 잠시 먹는다(11-22: 2,415행에 여유 4.35→2.66GiB). 그래서 묶음마다 RDS
FreeStorageSpace 를 보고, 여유가 PACE_FREE_GIB 아래면 회복을 기다리고 MIN_FREE_GIB 아래면 멈춘다.

실행: edge-dev-db-maintenance 태스크(마스터 계정만 tool_runs 를 지울 수 있다).
  MODE=plan(기본): 읽기만 — 후보 수·크기를 로그로 낸다.  MODE=run: 지우고 VACUUM (ANALYZE) tool_runs.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

SNAPSHOT_FUNCTIONS = ("record_research_state", "capture_news_snapshot")
BATCH_ROWS = 20
GIB = 1024**3

ELIGIBLE = """
WITH a AS (
  SELECT 'movement' AS kind, analysis_id, etf_code, data_source, trading_date, status, analysis_at, published_at,
         previous_analysis_id
  FROM movement_analyses
  UNION ALL SELECT 'outlook', analysis_id, etf_code, data_source, NULL::date, status, analysis_at, published_at,
         previous_analysis_id
  FROM outlook_analyses
), s AS (
  SELECT r.tool_run_id, r.status AS run_status, r.started_at, pg_column_size(r.output) AS bytes, d.function_name AS fn, a.*
  FROM tool_runs r JOIN tool_definitions d USING (tool_id)
  JOIN a ON a.analysis_id = coalesce(r.movement_analysis_id, r.outlook_analysis_id)
  WHERE d.function_name = ANY(%(functions)s) AND (%(etf_codes)s::text[] IS NULL OR a.etf_code = ANY(%(etf_codes)s))
), keep AS (
  SELECT DISTINCT ON (fn, kind, etf_code, data_source, trading_date) fn, analysis_id FROM s
  WHERE status = 'completed' AND run_status = 'completed'
  ORDER BY fn, kind, etf_code, data_source, trading_date, analysis_at DESC, published_at DESC, analysis_id DESC
), recent AS (
  SELECT analysis_id FROM (
    SELECT analysis_id, row_number() OVER (PARTITION BY kind, etf_code, data_source, trading_date
                                           ORDER BY analysis_at DESC, published_at DESC, analysis_id DESC) AS n
    FROM a WHERE status = 'completed') ranked
  WHERE n <= 2
  UNION SELECT previous_analysis_id FROM a WHERE status = 'running' AND previous_analysis_id IS NOT NULL
), cited AS MATERIALIZED (
  SELECT unnest(tool_run_ids) AS id FROM movement_items
  UNION SELECT unnest(tool_run_ids) FROM outlook_items
  UNION SELECT unnest(tool_run_ids) FROM outlook_issue_items
  UNION SELECT unnest(tool_run_ids) FROM outlook_factor_metrics
  UNION SELECT unnest(tool_run_ids) FROM outlook_conclusion_keywords
)
SELECT s.tool_run_id, s.bytes FROM s
WHERE s.run_status = 'completed' AND s.started_at < now() - %(age)s::interval
  AND s.status IN ('completed', 'failed')
  AND NOT EXISTS (SELECT 1 FROM keep k WHERE k.fn = s.fn AND k.analysis_id = s.analysis_id)
  AND NOT EXISTS (SELECT 1 FROM recent r WHERE r.analysis_id = s.analysis_id)
  AND NOT EXISTS (SELECT 1 FROM cited c WHERE c.id = s.tool_run_id)
ORDER BY s.started_at, s.tool_run_id
"""

# 후보는 한 번만 계산한다(위 쿼리는 표 전체를 본다). 실행 중 새로 끝난 분석은 옛 행을 덜 보호할 뿐이지만,
# 새로 시작한 분석이 직전 분석으로 고른 행은 보호해야 하므로 삭제 문장에서 그것만 다시 본다.
DELETE = """
DELETE FROM tool_runs r USING tool_definitions d
WHERE r.tool_id = d.tool_id AND d.function_name = ANY(%(functions)s) AND r.tool_run_id = ANY(%(ids)s)
  AND r.status = 'completed' AND r.started_at < now() - %(age)s::interval
  AND NOT EXISTS (
    SELECT 1 FROM movement_analyses m WHERE m.status = 'running' AND m.previous_analysis_id = r.movement_analysis_id
    UNION ALL SELECT 1 FROM outlook_analyses o WHERE o.status = 'running' AND o.previous_analysis_id = r.outlook_analysis_id)
"""


def log(event: str, **fields) -> None:
    """CloudWatch 로그에 한 줄 JSON 이벤트를 남긴다."""
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str), flush=True)


def candidates(conn, age: str, etf_codes: list[str] | None = None) -> list[tuple[str, int]]:
    """지울 수 있는 스냅샷 행의 (tool_run_id, output 바이트) 목록. 오래된 것부터. etf_codes 는 시험용 범위 제한."""
    return conn.execute(ELIGIBLE, {"functions": list(SNAPSHOT_FUNCTIONS), "age": age, "etf_codes": etf_codes}).fetchall()


def delete_batch(conn, ids: list[str], age: str) -> int:
    """ids 중 지금도 지울 수 있는 행만 한 트랜잭션으로 지우고 지운 행 수를 돌려준다."""
    with conn.transaction():
        return conn.execute(DELETE, {"functions": list(SNAPSHOT_FUNCTIONS), "age": age, "ids": ids}).rowcount


def rds_free_bytes(cloudwatch, instance_id: str) -> float | None:
    """최근 15분 중 가장 최신 1분의 FreeStorageSpace. 지표가 없으면 None(판단 보류, 진행)."""
    now = datetime.now(timezone.utc)
    result = cloudwatch.get_metric_data(StartTime=now - timedelta(minutes=15), EndTime=now, MetricDataQueries=[{
        "Id": "f", "MetricStat": {"Metric": {"Namespace": "AWS/RDS", "MetricName": "FreeStorageSpace",
                                             "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": instance_id}]},
                                  "Period": 60, "Stat": "Minimum"}}])
    values = result["MetricDataResults"][0]["Values"]
    return values[0] if values else None


class DiskGuard:
    """삭제 묶음 사이에 RDS 여유 공간을 본다. free=None 이면 지표 없이 진행한다(로컬·테스트)."""

    def __init__(self, free, *, pace_bytes: float, min_bytes: float, sleep=time.sleep, waits: int = 30):
        self.free, self.pace_bytes, self.min_bytes, self.sleep, self.waits = free, pace_bytes, min_bytes, sleep, waits

    def check(self) -> bool:
        """계속해도 되면 True. 여유가 최저선 아래거나 기다려도 회복하지 않으면 False."""
        for _ in range(self.waits):
            value = self.free()
            if value is None or value >= self.pace_bytes:
                return True
            if value < self.min_bytes:
                log("stop.free_storage", free_gib=round(value / GIB, 2))
                return False
            log("pace.wait", free_gib=round(value / GIB, 2))
            self.sleep(60)
        log("stop.free_storage_not_recovered")
        return False


def run(conn, *, mode: str, age: str, guard: DiskGuard, pause: float = 0, etf_codes: list[str] | None = None) -> dict:
    """후보를 세고, run 모드면 묶음마다 디스크를 확인하며 지운 뒤 VACUUM 한다. 요약을 돌려준다."""
    rows = candidates(conn, age, etf_codes)
    summary = {"mode": mode, "candidates": len(rows), "candidate_bytes": sum(b or 0 for _, b in rows), "deleted": 0}
    log("candidates", rows=summary["candidates"], output_bytes=summary["candidate_bytes"])
    if mode != "run":
        return summary
    ids = [rid for rid, _ in rows]
    for start in range(0, len(ids), BATCH_ROWS):
        if not guard.check():
            summary["stopped"] = True
            break
        summary["deleted"] += delete_batch(conn, ids[start:start + BATCH_ROWS], age)
        if pause:
            time.sleep(pause)
    log("deleted", rows=summary["deleted"])
    if summary["deleted"] and not summary.get("stopped") and guard.check():
        conn.execute("SET statement_timeout = 0")
        conn.execute("SET vacuum_cost_delay = 2")  # 수동 VACUUM 기본값 0(무제한) → 자동 VACUUM 정도로 느리게
        started = time.time()
        conn.execute("VACUUM (ANALYZE) tool_runs")
        log("vacuum.done", seconds=round(time.time() - started))
    return summary


def main() -> int:
    """태스크 진입점. 종료 코드: 0 정상, 2 잘못된 MODE, 3 디스크 여유 부족으로 중간에 멈춤."""
    import boto3
    import psycopg

    mode = os.environ.get("MODE", "plan")
    if mode not in ("plan", "run"):
        log("error.mode", mode=mode)
        return 2
    instance = os.environ.get("RDS_INSTANCE_ID")
    cloudwatch = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION") or "ap-northeast-2")
    guard = DiskGuard((lambda: rds_free_bytes(cloudwatch, instance)) if instance else (lambda: None),
                      pace_bytes=float(os.environ.get("PACE_FREE_GIB", "2.5")) * GIB,
                      min_bytes=float(os.environ.get("MIN_FREE_GIB", "1.5")) * GIB)
    with psycopg.connect(autocommit=True, application_name="db-maintenance-snapshot-retention") as conn:
        conn.execute("SET lock_timeout = '5s'")
        conn.execute("SET statement_timeout = '600s'")
        summary = run(conn, mode=mode, age=os.environ.get("MIN_AGE", "7 days"), guard=guard, pause=5)
    log("done", **summary)
    return 3 if summary.get("stopped") else 0


if __name__ == "__main__":
    sys.exit(main())
