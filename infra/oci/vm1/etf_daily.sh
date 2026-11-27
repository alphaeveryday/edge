#!/usr/bin/env bash
# 시장 마감 수집 레인 — 평일 16:30(Airflow 는 15:40 이었다. 장중 레인과 겹치지 않게 옮겼다).
# apps/engine/airflow/dags/edge_etf_daily.py 의 21스텝과 같은 명령이다. DAG 가 나란히 돌리던 스텝을 한 줄로 세웠다.
#
# 순서와 관문(DAG 와 같다):
# - 수집 7개를 먼저 모두 돈다. normalize-etf 가 가격·투자자 수집보다 먼저 끝나면 수집 종목이 달라지기 때문이다.
# - 수집이 실패해도 정제는 돈다.
# - 정제 6개가 모두 exit 0 또는 2 여야 마스터 적재로 간다. 마스터 적재와 corp_code 채움은 exit 0 만 통과한다.
# - 적재 5개는 서로 기다리지 않는다. holdings 는 exit 0, 나머지는 0 또는 2 여야 가격변동 트리거를 돈다.
. "$(dirname "$0")/../lib/batch.sh"

begin_lane etf-daily "${1:-}"
DEADLINE_SEC="$(need EDGE_KRX_ETF_DEADLINE_SEC)" || exit 1

run_step collect_price ingest-price-raw --source kis --max-failed-symbols 1 --run-id "$RID"
run_step collect_investor ingest-raw-investor --max-failed-symbols 1 --run-id "$RID"
run_step collect_krx_etf ingest-raw-etf --source krx --run-id "$RID" --deadline-sec "$DEADLINE_SEC"
run_step collect_etf_profile ingest-raw-etf-profile --run-id "$RID"
run_step collect_nav ingest-raw-nav --run-id "$RID"
run_step collect_sector_index ingest-raw-sector-index-daily --run-id "$RID"
run_step collect_dart_financial ingest-raw-financial --source dart --run-id "$RID"

run_step_partial normalize_price normalize-price --run-id "$RID" --input-run-id "$RID"
run_step_partial normalize_investor normalize-investor --run-id "$RID" --input-run-id "$RID"
run_step_partial normalize_etf normalize-etf --run-id "$RID" --input-run-id "$RID"
run_step_partial normalize_etf_profile normalize-etf-profile --run-id "$RID" --input-run-id "$RID"
run_step_partial normalize_etf_nav normalize-etf-nav --run-id "$RID" --input-run-id "$RID"
run_step_partial normalize_sector_index normalize-sector-index-daily --run-id "$RID" --input-run-id "$RID"

ok normalize_price normalize_investor normalize_etf normalize_etf_profile normalize_etf_nav normalize_sector_index \
  && run_step load_instruments load-instruments --run-id "$RID" --latest-good
ok load_instruments && run_step enrich_corp_code enrich-corp-code --run-id "$RID"

if ok enrich_corp_code; then
  run_step_partial load_etf_nav load-etf-nav --run-id "$RID" --input-run-id "$RID"
  run_step_partial load_price_daily load-price-daily --run-id "$RID" --input-run-id "$RID"
  run_step load_etf_holdings load-etf-holdings --run-id "$RID" --input-run-id "$RID"
  run_step_partial load_etf_flow load-etf-flow --run-id "$RID" --input-run-id "$RID"
  run_step_partial load_market_series load-market-series-daily --run-id "$RID" --input-run-id "$RID"
fi
ok load_etf_nav load_price_daily load_etf_holdings load_etf_flow load_market_series \
  && run_step_partial load_price_triggers load-price-triggers --run-id "$RID" --input-run-id "$RID"

end_lane
