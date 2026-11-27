#!/usr/bin/env bash
# 원천 관측 레인 — 매일 05:20. apps/engine/airflow/dags/edge_source_daily.py 와 같은 스텝이다.
# 계열 셋(매크로·업종·재무 지표)은 서로 기다리지 않는다 — 한 계열이 실패해도 다음 계열은 돈다.
# 계열 안에서는 수집이 실패해도 정제는 돌고, 정제가 exit 0 또는 2 일 때만 적재한다.
# DAG 는 백필 인자를 빈 값으로 넘겼다(--from "" --to "" = 정기 창). 같은 명령을 쓴다.
. "$(dirname "$0")/../lib/batch.sh"

begin_lane source-daily "${1:-}"

run_step_partial macro_collect ingest-raw-macro --run-id "$RID" --from "" --to ""
run_step_partial macro_normalize normalize-macro --run-id "$RID" --input-run-id "$RID"
ok macro_normalize && run_step macro_load load-macro --run-id "$RID" --input-run-id "$RID"

run_step_partial sector_collect ingest-raw-sector --run-id "$RID"
run_step_partial sector_normalize normalize-sector --run-id "$RID" --input-run-id "$RID"
ok sector_normalize && run_step sector_load load-sector --run-id "$RID" --input-run-id "$RID"

run_step_partial financial_collect ingest-raw-financial-metric --run-id "$RID" --from "" --to ""
run_step_partial financial_normalize normalize-financial-metric --run-id "$RID" --input-run-id "$RID"
ok financial_normalize && run_step financial_load load-financial-metric --run-id "$RID" --input-run-id "$RID"

end_lane
