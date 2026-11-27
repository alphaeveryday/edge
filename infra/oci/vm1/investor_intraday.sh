#!/usr/bin/env bash
# 장중 수급 레인 — 평일 09:35·10:05·11:25·13:25·14:35. apps/engine/airflow/dags/edge_investor_intraday.py 와 같은 스텝·순서다.
# 수집이 실패해도 정제는 돈다. 정제가 exit 0 또는 2 일 때만 적재한다.
. "$(dirname "$0")/../lib/batch.sh"

begin_lane investor-intraday "${1:-}"

run_step collect ingest-raw-investor-estimate --max-failed-symbols 1 --run-id "$RID"
run_step_partial normalize normalize-investor-estimate --run-id "$RID" --input-run-id "$RID"
ok normalize && run_step_partial load load-investor-intraday --run-id "$RID" --input-run-id "$RID"

end_lane
