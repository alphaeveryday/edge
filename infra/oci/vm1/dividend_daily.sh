#!/usr/bin/env bash
# 배당 레인 — 매일 03:00. apps/engine/airflow/dags/edge_dividend_daily.py 와 같은 스텝·순서다.
# 수집이 실패해도 정제는 돈다. 정제가 exit 0 또는 2 일 때만 적재한다.
# DAG 의 "03:00~03:30 에 다른 배치가 없을 때만 시작" 검사는 옮기지 않았다 — 락이 같은 일을 한다.
. "$(dirname "$0")/../lib/batch.sh"

begin_lane dividend-daily "${1:-}"

run_step_partial collect ingest-raw-dividend --run-id "$RID" --deadline-sec 600
run_step_partial normalize normalize-dividend --run-id "$RID" --input-run-id "$RID"
ok normalize && run_step load load-dividend --run-id "$RID" --input-run-id "$RID"

end_lane
