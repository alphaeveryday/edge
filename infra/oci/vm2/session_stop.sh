#!/usr/bin/env bash
# VM-2 세션 종료 — 거래일 16:10. drain → 큐 게이트 → QC → 장중 설명 분석 대기(기본 상한 2시간).
#
# 성공(exit 0)하면 stop-minute-session 이 batch_lane_success 에 (minute_session_stop, 그날 16:10 KST)
# 행을 쓴다. VM-1 감시가 그 행을 보고 VM-2 를 내린다 — 이 스크립트는 VM 을 내리지 않는다.
#
# 성공했을 때만 컨테이너를 내린다(docker compose down). 남겨 두면 다음 부팅 때 docker 가 그날 세션이
# 서기도 전에 워커를 되살려 재시작을 되풀이한다. 실패했으면 그대로 둔다 — 아직 처리 중인 일감이 있거나
# 상태를 모르는 것이라, 내리면 그 일감이 잘린다.
set -u
cd "$(dirname "$0")" || exit 1
SLOT="${1:-}"
log() { printf '%s [minute-session-stop] %s\n' "$(TZ=Asia/Seoul date +%FT%T%z)" "$*"; }

log "시작 slot=${SLOT:-수동}"
docker compose run --rm session-stop
code=$?
if [ "$code" -ne 0 ]; then
  log "stop-minute-session exit=$code — 컨테이너를 내리지 않는다(성공 행도 없다)"
  exit "$code"
fi
log "stop-minute-session exit=0 — 컨테이너를 내린다"
docker compose down
code=$?
log "docker compose down exit=$code"
exit "$code"
