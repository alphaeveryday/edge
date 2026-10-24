#!/usr/bin/env bash
# ECS 태스크 정의 헬스체크의 두 방식. MICRO_HEALTH 로 고른다(실험 조건).
# - cli(기본, #981): api curl + `airflow jobs check`(scheduler·dag-processor) 동시 실행 — ECS 는 컨테이너별로 60초마다
#   돌리므로 겹침은 최악의 경우다. jobs check 한 번이 Airflow 를 import 하는 파이썬 프로세스(약 110MiB)다.
# - light: api-server `/api/v2/monitor/health` 하나로 metadatabase·scheduler·dag_processor 가 모두 "healthy" 인지
#   본다. 같은 heartbeat(메타DB job 표)를 읽는 판정이다(scheduler_health_check_threshold 30초). 응답 코드는 부분
#   장애에도 200 이라 본문의 status 셋을 센다. 파이썬을 띄우지 않는다.
if [ "${MICRO_HEALTH:-cli}" = light ]; then
  body=$(curl -fs --max-time 20 http://localhost:8080/api/v2/monitor/health) || exit 1
  [ "$(printf '%s' "$body" | grep -o '"status":"healthy"' | wc -l)" -ge 3 ] || exit 1
  exit 0
fi
curl -fs http://localhost:8080/api/v2/monitor/health >/dev/null & a=$!
PGAPPNAME=af-healthcheck airflow jobs check --job-type SchedulerJob --local >/dev/null 2>&1 & b=$!
PGAPPNAME=af-healthcheck airflow jobs check --job-type DagProcessorJob --local >/dev/null 2>&1 & c=$!
rc=0; for p in $a $b $c; do wait $p || rc=1; done; exit $rc
