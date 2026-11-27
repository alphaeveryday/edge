#!/usr/bin/env bash
# 장전 유니버스 레인 — 평일 07:00. apps/engine/airflow/dags/edge_premarket.py 와 같은 스텝·순서다.
# 어느 스텝이든 exit 0 이 아니면 뒤 스텝을 돌리지 않는다(어제 유니버스를 유지한다).
. "$(dirname "$0")/../lib/batch.sh"

begin_lane premarket "${1:-}"
DEADLINE_SEC="$(need EDGE_KRX_ETF_DEADLINE_SEC)" || exit 1
UNIVERSE_URI="$(need EDGE_MINUTE_UNIVERSE_URI)" || exit 1

run_step collect ingest-raw-etf --source krx --run-id "$RID" --deadline-sec "$DEADLINE_SEC"
ok collect && run_step normalize normalize-etf --run-id "$RID" --input-run-id "$RID"
ok normalize && run_step universe build-minute-universe --run-id "$RID" --universe "$UNIVERSE_URI"

end_lane
