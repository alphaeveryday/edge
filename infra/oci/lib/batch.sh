# 배치 레인 공통 함수 — 레인 스크립트가 source 한다(직접 실행하지 않는다).
#
# 한 레인의 흐름: begin_lane(슬롯 확인·run_id·락) → run_step …(스텝을 한 줄로) → end_lane(성공 행 또는 실패 통보).
# 같은 VM 의 모든 레인이 락 하나를 나눠 쓴다 — 한 번에 컨테이너 하나만 돈다.
# 스텝 판정은 Airflow DAG 와 같다: 0 성공, 2 부분 실패, 그 밖은 실패. 하나라도 0 이 아니면 레인은 실패다.
#
# 설정 파일(EDGE_CONF_DIR, 기본 /etc/edge)은 읽는 쪽이 갈린다:
#   batch.env     호스트 스크립트만 읽는다(이미지·명령 인자·상한). 컨테이너에 넘기지 않는다
#   pipeline.env  스텝 컨테이너의 env(docker --env-file)
#   mail.env      통보 메일 계정 — lib/edge_cron.py 만 읽는다
#   retention.env 분석 스냅샷 정리 스텝에만 더 넘긴다(vm1/snapshot_retention.sh)

set -u
EDGE_CONF_DIR="${EDGE_CONF_DIR:-/etc/edge}"
EDGE_LOCK_FILE="${EDGE_LOCK_FILE:-/var/lock/edge-batch.lock}"
EDGE_LOCK_WAIT_SEC="${EDGE_LOCK_WAIT_SEC:-3600}"
# ponytail: 2g 는 정해진 기본값이다. 로컬 태거(tag-news)는 Fargate 실측 최대 2.0~2.2GB 였다 — exit 137 이면 batch.env 에서 올린다.
EDGE_STEP_MEMORY_DEFAULT=2g
# 스텝 하나의 시간 상한(초). DAG 는 레인 전체 상한이 25~45분이었다 — 한 줄로 돌려도 스텝 하나가 90분을 넘으면 멈춘 것으로 본다.
EDGE_STEP_TIMEOUT_DEFAULT=5400
export EDGE_CONF_DIR
PIPELINE_ENV="$EDGE_CONF_DIR/pipeline.env"
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LANE=batch
SLOT_AT=""
RID=""
IMAGE=""
TOKEN_DIR=""
STEP_DOCKER_OPTS=""   # 레인이 스텝에 더 주는 docker 옵션(공백으로 나뉜다 — 공백 든 값은 못 쓴다)
STEP_DEADLINE=""      # 레인이 정하는 스텝 마감 시각(HH:MM KST). 시간 상한이 이 시각을 넘지 않게 줄인다
CODES=""    # "스텝=exit …" — 판정과 실패 통보에 쓴다
PASSED=" "  # 하류로 넘겨도 되는 스텝 이름
FAILED=0
NOTE=""     # 실패 통보에 덧붙일 말(시간 상한 초과)

# 로그는 stderr 로 낸다 — `X="$(need KEY)"` 처럼 명령 치환 안에서 불려도 출력이 변수로 들어가 사라지지 않게.
log() { printf '%s [%s] %s\n' "$(TZ=Asia/Seoul date +%FT%T%z)" "$LANE" "$*" >&2; }

# conf <KEY> [파일] — 설정 파일의 값(docker --env-file 과 같은 규칙: KEY=VALUE 그대로). 없으면 빈 값. 파일 기본은 batch.env.
conf() { sed -n "s/^$1=//p" "${2:-$EDGE_CONF_DIR/batch.env}" | tail -n 1; }

# 통보 메일. 발송이 실패해도 로그에는 남는다.
alert() {
  log "통보: $1 — $2"
  printf '%s\n' "$2" | python3 "$LIB_DIR/edge_cron.py" mail "$1" || log "통보 메일 발송 실패"
}

die() {
  alert "[$LANE] FAILED — ${SLOT_AT:-슬롯 미상}" "$1"
  exit 1
}

# need <KEY> [파일] — 꼭 있어야 하는 설정값. 호출부는 `X="$(need KEY)" || exit 1` 로 쓴다(없으면 통보하고 끝낸다).
need() {
  local value
  value="$(conf "$@")"
  [ -n "$value" ] || die "${2:-$EDGE_CONF_DIR/batch.env} 에 $1 이 없다"
  printf '%s' "$value"
}

# begin_lane <레인> <HH:MM> [락 대기 마감 HH:MM] — 슬롯을 확인하고 run_id 를 만든 뒤 락을 잡는다.
# 하나라도 안 되면 통보하고 끝낸다. 마감을 주면 락을 그 시각까지만 기다린다(그 뒤에 시작해 봐야 소용없는 레인).
begin_lane() {
  LANE="$1"
  local out lock_wait="$EDGE_LOCK_WAIT_SEC" left
  out="$(python3 "$LIB_DIR/edge_cron.py" run-id "$LANE" "${2:-}" 2>&1)" || die "슬롯 확인 실패(${2:-슬롯 인자 없음}): $out"
  RID="${out%% *}"
  SLOT_AT="${out##* }"
  IMAGE="$(need EDGE_PIPELINE_IMAGE)" || exit 1
  [ -r "$PIPELINE_ENV" ] || die "스텝 env 파일을 읽을 수 없다: $PIPELINE_ENV"
  # KIS 토큰 파일 캐시(선택). 없는 디렉터리를 마운트하면 docker 가 root 소유로 만들어 컨테이너(uid 1001)가 못 쓴다 —
  # 그러면 스텝마다 토큰을 새로 받게 되므로 시작 전에 막는다.
  TOKEN_DIR="$(conf KIS_TOKEN_CACHE_DIR "$PIPELINE_ENV")"
  [ -z "$TOKEN_DIR" ] || [ -d "$TOKEN_DIR" ] || die "KIS_TOKEN_CACHE_DIR 디렉터리가 없다: $TOKEN_DIR"
  exec 9>"$EDGE_LOCK_FILE" || die "락 파일을 열지 못했다: $EDGE_LOCK_FILE"
  if [ -n "${3:-}" ]; then
    left="$(python3 "$LIB_DIR/edge_cron.py" seconds-until "$3")" || die "락 대기 마감($3)까지 남은 시간을 계산하지 못했다"
    [ "$left" -ge "$lock_wait" ] || lock_wait="$left"
  fi
  log "시작 run_id=$RID — 락 대기(최대 ${lock_wait}초)"
  flock -w "$lock_wait" 9 \
    || die "락을 ${lock_wait}초 안에 잡지 못했다 — 앞선 레인이 아직 돈다. 이 슬롯은 한 스텝도 실행하지 않았다(run_id=$RID)"
}

# run_step <이름> <data_pipeline.run 인자…> — 스텝 하나를 실행한다. exit 0 일 때만 하류로 넘긴다.
run_step() { _run_step 0 "$@"; }
# run_step_partial — 부분 실패(exit 2)도 하류로 넘긴다(DAG 의 partial_exit_codes=(2,)). 레인 판정은 그래도 실패다.
run_step_partial() { _run_step 2 "$@"; }

_run_step() {
  local partial="$1" name="$2" code memory limit left container started
  shift 2
  memory="$(conf EDGE_STEP_MEMORY)"
  # 시간 상한: 스텝별 값(EDGE_TIMEOUT_SEC_<레인>_<스텝>, 예 EDGE_TIMEOUT_SEC_NEWS_TAG) → 공통 값 → 기본값.
  limit="$(conf "EDGE_TIMEOUT_SEC_$(printf '%s_%s' "$LANE" "$name" | tr 'a-z-' 'A-Z_')")"
  limit="${limit:-$(conf EDGE_STEP_TIMEOUT_SEC)}"
  limit="${limit:-$EDGE_STEP_TIMEOUT_DEFAULT}"
  case "$limit" in '' | 0 | *[!0-9]*) FAILED=1; die "$name 의 시간 상한이 양의 정수(초)가 아니다: $limit — 이 스텝부터 실행하지 않았다:$CODES" ;; esac
  if [ -n "$STEP_DEADLINE" ]; then
    left="$(python3 "$LIB_DIR/edge_cron.py" seconds-until "$STEP_DEADLINE")" || left=0
    if [ "$left" -eq 0 ]; then
      FAILED=1
      die "$name 을 시작하지 않았다 — 마감 $STEP_DEADLINE 이 지났다:$CODES"
    fi
    [ "$left" -ge "$limit" ] || limit="$left"
  fi
  container="edge-$LANE-$name-$$"
  log "스텝 시작: $name(상한 ${limit}초) — $*"
  started=$SECONDS
  # 토큰 캐시 디렉터리는 호스트와 같은 경로로 붙인다 — 스텝들이 KIS 토큰 하나를 나눠 쓴다(KIS 는 잦은 재발급을 막는다).
  # 9>&-: 컨테이너 쪽 프로세스가 락을 물려받지 않게 한다.
  timeout -s KILL "$limit" docker run --rm --name "$container" --env-file "$PIPELINE_ENV" \
    --memory "${memory:-$EDGE_STEP_MEMORY_DEFAULT}" \
    ${TOKEN_DIR:+-v} ${TOKEN_DIR:+"$TOKEN_DIR:$TOKEN_DIR"} $STEP_DOCKER_OPTS "$IMAGE" "$@" 9>&-
  code=$?
  log "스텝 끝: $name exit=$code"
  CODES="$CODES $name=$code"
  if [ "$code" -eq 0 ] || [ "$code" -eq "$partial" ]; then
    PASSED="$PASSED$name "
  fi
  [ "$code" -eq 0 ] || FAILED=1
  # 124 이상은 컨테이너가 뜨지 못했거나(125~127) 신호로 죽은 것(137 메모리 초과·시간 상한)이다. 어디까지 썼는지 모르므로
  # 뒤 스텝을 돌리지 않는다(Airflow 의 "결과 미상 → 보류"와 같은 뜻).
  if [ "$code" -ge 124 ]; then
    [ $((SECONDS - started)) -lt "$limit" ] || NOTE=" · $name 이 시간 상한 ${limit}초를 넘겼다"
    # 상한에 걸리면 timeout 은 docker 명령만 죽인다. 컨테이너는 이름으로 따로 지운다. 삭제가 실패해도 컨테이너가
    # 이미 없으면(스스로 죽어 --rm 이 치운 경우) 된 것이다. 남아 있거나 확인하지 못하면 지웠다고 말하지 않는다 —
    # 이 레인이 락을 놓은 뒤에도 그 컨테이너가 돌 수 있다.
    if docker rm -f "$container" >/dev/null 2>&1 \
      || { left="$(docker ps -aq --filter "name=^${container}\$" 2>/dev/null)" && [ -z "$left" ]; }; then
      [ -z "$NOTE" ] || NOTE="$NOTE(컨테이너는 지웠다)"
    else
      NOTE="$NOTE · 컨테이너 $container 를 지우지 못했다 — 아직 돌 수 있다. docker ps 로 확인하고 손으로 지운다"
    fi
    end_lane
  fi
}

# ok <스텝…> — 적은 스텝이 모두 하류로 넘겨도 되는 결과로 끝났을 때만 참. 거짓이면 뒤 스텝을 건너뛰게 되므로
# 레인을 실패로 표시한다(건너뛴 스텝이 있는 레인이 성공 행을 쓰지 못하게).
ok() {
  local name
  for name in "$@"; do
    case "$PASSED" in *" $name "*) ;; *) FAILED=1; return 1 ;; esac
  done
}

# end_lane — 모든 스텝이 exit 0 이면 성공 행을 쓰고, 아니면 통보하고 실패로 끝낸다. 끝나면 락이 풀린다.
end_lane() {
  if [ "$FAILED" -ne 0 ]; then
    die "exit 0 이 아닌 스텝이 있다(목록에 없는 스텝은 실행하지 않았다):$CODES$NOTE · run_id=$RID"
  fi
  "$LIB_DIR/edge_sql.sh" \
    "INSERT INTO batch_lane_success (lane, slot_at) VALUES (%s, %s) ON CONFLICT (lane, slot_at) DO UPDATE SET finished_at = now()" \
    "$LANE" "$SLOT_AT" \
    || die "스텝은 모두 성공했으나 성공 행을 쓰지 못했다 — 감시가 이 슬롯을 누락으로 통보한다(run_id=$RID)"
  log "레인 성공:$CODES"
}
