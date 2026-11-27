# 배치 레인 공통 함수 — 레인 스크립트가 source 한다(직접 실행하지 않는다).
#
# 한 레인의 흐름: begin_lane(슬롯 확인·run_id·락) → run_step …(스텝을 한 줄로) → end_lane(성공 행 또는 실패 통보).
# 같은 VM 의 모든 레인이 락 하나를 나눠 쓴다 — 한 번에 컨테이너 하나만 돈다.
# 스텝 판정은 Airflow DAG 와 같다: 0 성공, 2 부분 실패, 그 밖은 실패. 하나라도 0 이 아니면 레인은 실패다.

set -u
EDGE_ENV_FILE="${EDGE_ENV_FILE:-/etc/edge/batch.env}"
EDGE_LOCK_FILE="${EDGE_LOCK_FILE:-/var/lock/edge-batch.lock}"
EDGE_LOCK_WAIT_SEC="${EDGE_LOCK_WAIT_SEC:-3600}"
# ponytail: 2g 는 정해진 기본값이다. 로컬 태거(tag-news)는 Fargate 실측 최대 2.0~2.2GB 였다 — exit 137 이면 env 파일에서 올린다.
EDGE_STEP_MEMORY_DEFAULT=2g
export EDGE_ENV_FILE
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LANE=batch
SLOT_AT=""
RID=""
IMAGE=""
TOKEN_DIR=""
STEP_DOCKER_OPTS=""   # 레인이 스텝에 더 주는 docker 옵션(공백으로 나뉜다 — 공백 든 값은 못 쓴다)
CODES=""    # "스텝=exit …" — 판정과 실패 통보에 쓴다
PASSED=" "  # 하류로 넘겨도 되는 스텝 이름
FAILED=0

log() { printf '%s [%s] %s\n' "$(TZ=Asia/Seoul date +%FT%T%z)" "$LANE" "$*"; }

# env 파일의 값(docker --env-file 과 같은 규칙: KEY=VALUE 그대로). 없으면 빈 값.
conf() { sed -n "s/^$1=//p" "$EDGE_ENV_FILE" | tail -n 1; }

# 통보 메일. 발송이 실패해도 로그에는 남는다.
alert() {
  log "통보: $1 — $2"
  printf '%s\n' "$2" | python3 "$LIB_DIR/edge_cron.py" mail "$1" || log "통보 메일 발송 실패"
}

die() {
  alert "[$LANE] FAILED — ${SLOT_AT:-슬롯 미상}" "$1"
  exit 1
}

# 꼭 있어야 하는 설정값. 호출부는 `X="$(need KEY)" || exit 1` 로 쓴다(없으면 통보하고 끝낸다).
need() {
  local value
  value="$(conf "$1")"
  [ -n "$value" ] || die "$EDGE_ENV_FILE 에 $1 이 없다"
  printf '%s' "$value"
}

# begin_lane <레인> <HH:MM> — 슬롯을 확인하고 run_id 를 만든 뒤 락을 잡는다. 하나라도 안 되면 통보하고 끝낸다.
begin_lane() {
  LANE="$1"
  local out
  out="$(python3 "$LIB_DIR/edge_cron.py" run-id "$LANE" "${2:-}" 2>&1)" || die "슬롯 확인 실패(${2:-슬롯 인자 없음}): $out"
  RID="${out%% *}"
  SLOT_AT="${out##* }"
  IMAGE="$(need EDGE_PIPELINE_IMAGE)" || exit 1
  # KIS 토큰 파일 캐시(선택). 없는 디렉터리를 마운트하면 docker 가 root 소유로 만들어 컨테이너(uid 1001)가 못 쓴다 —
  # 그러면 스텝마다 토큰을 새로 받게 되므로 시작 전에 막는다.
  TOKEN_DIR="$(conf KIS_TOKEN_CACHE_DIR)"
  [ -z "$TOKEN_DIR" ] || [ -d "$TOKEN_DIR" ] || die "KIS_TOKEN_CACHE_DIR 디렉터리가 없다: $TOKEN_DIR"
  exec 9>"$EDGE_LOCK_FILE" || die "락 파일을 열지 못했다: $EDGE_LOCK_FILE"
  log "시작 run_id=$RID — 락 대기(최대 ${EDGE_LOCK_WAIT_SEC}초)"
  flock -w "$EDGE_LOCK_WAIT_SEC" 9 \
    || die "락을 ${EDGE_LOCK_WAIT_SEC}초 안에 잡지 못했다 — 앞선 레인이 아직 돈다. 이 슬롯은 한 스텝도 실행하지 않았다(run_id=$RID)"
}

# run_step <이름> <data_pipeline.run 인자…> — 스텝 하나를 실행한다. exit 0 일 때만 하류로 넘긴다.
run_step() { _run_step 0 "$@"; }
# run_step_partial — 부분 실패(exit 2)도 하류로 넘긴다(DAG 의 partial_exit_codes=(2,)). 레인 판정은 그래도 실패다.
run_step_partial() { _run_step 2 "$@"; }

_run_step() {
  local partial="$1" name="$2" code memory
  shift 2
  memory="$(conf EDGE_STEP_MEMORY)"
  log "스텝 시작: $name — $*"
  # 토큰 캐시 디렉터리는 호스트와 같은 경로로 붙인다 — 스텝들이 KIS 토큰 하나를 나눠 쓴다(KIS 는 잦은 재발급을 막는다).
  docker run --rm --env-file "$EDGE_ENV_FILE" --memory "${memory:-$EDGE_STEP_MEMORY_DEFAULT}" \
    ${TOKEN_DIR:+-v} ${TOKEN_DIR:+"$TOKEN_DIR:$TOKEN_DIR"} $STEP_DOCKER_OPTS "$IMAGE" "$@"
  code=$?
  log "스텝 끝: $name exit=$code"
  CODES="$CODES $name=$code"
  if [ "$code" -eq 0 ] || [ "$code" -eq "$partial" ]; then
    PASSED="$PASSED$name "
  fi
  [ "$code" -eq 0 ] || FAILED=1
  # 124 이상은 컨테이너가 뜨지 못했거나(125~127) 신호로 죽은 것(137 메모리 초과 등)이다. 어디까지 썼는지 모르므로
  # 뒤 스텝을 돌리지 않는다(Airflow 의 "결과 미상 → 보류"와 같은 뜻).
  [ "$code" -lt 124 ] || end_lane
}

# ok <스텝…> — 적은 스텝이 모두 하류로 넘겨도 되는 결과로 끝났을 때만 참. 거짓이면 뒤 스텝을 건너뛰게 되므로
# 레인을 실패로 표시한다(건너뛴 스텝이 있는 레인이 성공 행을 쓰지 못하게).
ok() {
  local name
  for name in "$@"; do
    case "$PASSED" in *" $name "*) ;; *) FAILED=1; return 1 ;; esac
  done
}

# end_lane — 모든 스텝이 exit 0 이면 성공 행을 쓰고, 아니면 통보하고 실패로 끝낸다.
end_lane() {
  if [ "$FAILED" -ne 0 ]; then
    die "exit 0 이 아닌 스텝이 있다(목록에 없는 스텝은 실행하지 않았다):$CODES · run_id=$RID"
  fi
  "$LIB_DIR/edge_sql.sh" \
    "INSERT INTO batch_lane_success (lane, slot_at) VALUES (%s, %s) ON CONFLICT (lane, slot_at) DO UPDATE SET finished_at = now()" \
    "$LANE" "$SLOT_AT" \
    || die "스텝은 모두 성공했으나 성공 행을 쓰지 못했다 — 감시가 이 슬롯을 누락으로 통보한다(run_id=$RID)"
  log "레인 성공:$CODES"
}
