#!/usr/bin/env bash
# 장전 유니버스 레인 — 평일 07:00. apps/engine/airflow/dags/edge_premarket.py 와 같은 스텝·순서다.
# 어느 스텝이든 exit 0 이 아니면 뒤 스텝을 돌리지 않는다(어제 유니버스를 유지한다).
# build-minute-universe 는 거래일 07:30 뒤에는 유니버스 교체를 스스로 거부한다. 락은 07:20 까지만 기다린다 —
# 레인 전체가 실측 최대 376초(중앙 217초, edge_premarket.py 머리말의 SFN 최근 40회)라 07:20 에 시작하면 07:30 전에 끝난다.
# 07:30 뒤의 판단은 그 스텝에 맡긴다. 유니버스가 안 바뀐 날은 07:30 뒤에도 exit 0 으로 끝나는 정상 실행이라
# (무변경 검사가 시각 검사보다 앞이다) 여기서 시각으로 막으면 그런 날과 늦은 수동 재실행이 실패가 된다.
. "$(dirname "$0")/../lib/batch.sh"

begin_lane premarket "${1:-}" 07:20
DEADLINE_SEC="$(need EDGE_KRX_ETF_DEADLINE_SEC)" || exit 1
UNIVERSE_URI="$(need EDGE_MINUTE_UNIVERSE_URI)" || exit 1

run_step collect ingest-raw-etf --source krx --run-id "$RID" --deadline-sec "$DEADLINE_SEC"
ok collect && run_step normalize normalize-etf --run-id "$RID" --input-run-id "$RID"
ok normalize && run_step universe build-minute-universe --run-id "$RID" --universe "$UNIVERSE_URI"

end_lane
