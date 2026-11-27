#!/usr/bin/env bash
# 공시 보충 레인 — 평일 19:30. apps/engine/airflow/dags/edge_disclosure.py 와 같은 스텝·순서다.
# 수집이 실패해도 정제 둘은 돈다. 정제 둘이 모두 exit 0 또는 2 일 때만 적재한다.
. "$(dirname "$0")/../lib/batch.sh"

begin_lane disclosure "${1:-}"

run_step collect ingest-raw-disclosure --run-id "$RID"
run_step_partial normalize normalize-disclosure --run-id "$RID" --input-run-id "$RID"
run_step_partial normalize_segment normalize-disclosure-segment --run-id "$RID" --input-run-id "$RID"
ok normalize normalize_segment && run_step load load-disclosure --run-id "$RID" --input-run-id "$RID"

end_lane
