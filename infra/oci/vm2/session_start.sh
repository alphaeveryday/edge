#!/usr/bin/env bash
# VM-2 세션 시작 — 거래일 아침 부팅 뒤(08:05). 장중 컨테이너를 올린다.
#
# `docker compose up -d` 한 번이 순서를 지킨다: session-start(세션 계획, ECS·SQS 호출 없음)가 exit 0 으로
# 끝난 뒤에만 워커·소비자가 뜬다(compose.yaml 의 depends_on). 계획이 실패하면 워커는 뜨지 않는다.
#
# 거래일이 아니면 아무것도 올리지 않고 exit 0 이다. VM 은 거래일에만 켜지지만 여기서 한 번 더 막는다 —
# 휴장일에 올리면 세션이 없어 워커가 하루 종일 재시작을 되풀이한다. 판정은 start-minute-session 이 쓰는
# 것과 같은 함수·같은 달력(.env 의 OPS_KR_HOLIDAYS)이다.
set -u
cd "$(dirname "$0")" || exit 1
SLOT="${1:-}"
log() { printf '%s [minute-session-start] %s\n' "$(TZ=Asia/Seoul date +%FT%T%z)" "$*"; }

log "시작 slot=${SLOT:-수동}"
docker compose run --rm --no-deps --entrypoint python session-start -c '
import sys
from datetime import datetime
from data_pipeline.minute.models import KST
from data_pipeline.ops.trading_calendar import is_trading_day
sys.exit(0 if is_trading_day(datetime.now(KST).date()) else 3)'
code=$?
if [ "$code" -eq 3 ]; then
  log "비거래일이다 — 올리지 않는다"
  exit 0
fi
if [ "$code" -ne 0 ]; then
  # 달력을 못 읽었다(이미지·.env·docker). 모르는 채로 올리지 않는다 — 원인은 바로 위 출력에 있다.
  log "거래일 판정 실패 exit=$code — 올리지 않는다"
  exit "$code"
fi

docker compose up -d
code=$?
log "docker compose up -d exit=$code"
exit "$code"
