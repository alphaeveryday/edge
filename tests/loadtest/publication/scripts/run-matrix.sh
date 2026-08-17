#!/usr/bin/env bash
# 캐시 실험 격자 실행기 — suite 하나를 rate 스윕·반복 횟수만큼 돌리고 결과를 고정한다.
#
# WHY suite 로 묶나: 캐시 모드·인스턴스 수·시나리오·쓰기 토글은 서로 짝이 맞아야
# 비교가 성립한다(예: 읽기 비교인데 한쪽만 노출 로그가 켜져 있으면 무의미). 조합을
# 손으로 조립하지 않고 이름 하나로 고정한다.
#
# WHY 매 실행마다 컨테이너를 재생성하나: 캐시 모드·TTL 은 Spring 기동 시 바인딩되는
# env 라 재생성 없이는 반영되지 않는다(compose 가 ${VAR:-기본} 으로 읽는다).
#
# 사용: scripts/run-matrix.sh --suite T1 [--rate 200] [--rate-sweep "50 100 200 400"]
#                            [--duration 3m] [--repeat 1]
#                            [--mode two-level] [--instances 4] [--scenario hot-key]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$STACK_DIR/docker-compose.yml"
COMPOSE=(docker compose -p edge-pubcache -f "$COMPOSE_FILE")
RESULTS_DIR="$STACK_DIR/results"
K6_DIR="$STACK_DIR/k6"
PROM_URL="${PROM_URL:-http://localhost:19090}"

err() { printf '\033[31m[run-matrix] %s\033[0m\n' "$*" >&2; }
info() { printf '[run-matrix] %s\n' "$*"; }

usage() {
	cat <<'USAGE'
사용: run-matrix.sh --suite <이름> [옵션]

suite 매핑 (캐시 모드 / 인스턴스 / 시나리오 / 쓰기 경로):
  B0  none       1대  hot-key              read   기준선(단일 인스턴스)
  B1  none       4대  hot-key              read   기준선(수평 확장만)
  C1  caffeine   4대  hot-key              read   L1 로컬 캐시
  R1  redis      4대  hot-key              read   L2 공유 캐시
  T1  two-level  4대  hot-key              read   L1+L2
  E1  (--mode 필수) 4대 cold-start         read   콜드 스타트 미스 폭
  E2  two-level  4대  synchronized-expiry  read   지터 0 — 동시 만료
  E3  two-level  4대  synchronized-expiry  read   지터 2s — 만료 분산
  E4  two-level  4대  hot-key              read   측정 중 api-4 늦은 합류(스케일 아웃)
  E5  two-level  4대  hot-key              read   측정 중 redis 정지·복귀
  E6  two-level  4대  publication-change   read   측정 중 새 스냅샷·종목 차단
  F1  two-level  4대  hot-key              full   노출 로그·요청 메트릭 켠 전체 경로
  W   (--mode 필수) 4대 working-set        read   워킹셋 스윕(--working-set 필수) — L1 무릎 찾기
  S   caffeine   4대  hot-spike            read   핫키 스파이크(램프 내장, --duration 무시 가능)

옵션:
  --rate <n>            도착률 (기본 200)
  --rate-sweep "a b c"  여러 도착률을 순차 실행 (--rate 를 덮는다)
  --duration <k6기간>   측정 구간 (기본 3m)
  --repeat <n>          같은 조건 반복 (기본 1)
  --mode <none|caffeine|redis|two-level>   suite 기본 모드 덮어쓰기
  --instances <1..4>    인스턴스 수 덮어쓰기
  --scenario <이름>     k6 시나리오 파일명 덮어쓰기
  --working-set <n>     suite W 전용(필수) — 서로 다른 키 수. 합성 티커 LT00001..LT<n> 을
                        시드하고 allowlist 로 주입한다. 핫키 비율은 HOT_RATIO 로(기본 0).
  -h, --help            이 도움말

결과: results/<run_id>/{summary.json, raw.json.gz, meta.json, prom/*.json}
USAGE
}

SUITE=""
REPEAT=1
RATE=200
SWEEP=""
DURATION="3m"
MODE_OVERRIDE=""
INSTANCES_OVERRIDE=""
SCENARIO_OVERRIDE=""
WORKING_SET=""

while [ $# -gt 0 ]; do
	case "$1" in
		--suite) SUITE="${2:?--suite 에 이름이 필요하다}"; shift 2 ;;
		--repeat) REPEAT="${2:?}"; shift 2 ;;
		--rate) RATE="${2:?}"; shift 2 ;;
		--rate-sweep) SWEEP="${2:?}"; shift 2 ;;
		--duration) DURATION="${2:?}"; shift 2 ;;
		--mode) MODE_OVERRIDE="${2:?}"; shift 2 ;;
		--instances) INSTANCES_OVERRIDE="${2:?}"; shift 2 ;;
		--scenario) SCENARIO_OVERRIDE="${2:?}"; shift 2 ;;
		--working-set) WORKING_SET="${2:?--working-set 에 숫자가 필요하다}"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) err "알 수 없는 옵션: $1"; usage >&2; exit 2 ;;
	esac
done

[ -n "$SUITE" ] || { err "--suite 는 필수다"; usage >&2; exit 2; }

# ── suite → (모드, 인스턴스, 시나리오, 쓰기 토글, 지터, 특수 절차) ────────────
WRITE_SUITE="read"
JITTER="${CACHE_L2_JITTER:-0s}"
SPECIAL="none"
case "$SUITE" in
	B0) MODE=none;      N=1; SCENARIO=hot-key ;;
	B1) MODE=none;      N=4; SCENARIO=hot-key ;;
	C1) MODE=caffeine;  N=4; SCENARIO=hot-key ;;
	R1) MODE=redis;     N=4; SCENARIO=hot-key ;;
	T1) MODE=two-level; N=4; SCENARIO=hot-key ;;
	E1) MODE="";        N=4; SCENARIO=cold-start; SPECIAL=cold-start ;;
	E2) MODE=two-level; N=4; SCENARIO=synchronized-expiry; JITTER=0s ;;
	E3) MODE=two-level; N=4; SCENARIO=synchronized-expiry; JITTER=2s ;;
	E4) MODE=two-level; N=4; SCENARIO=hot-key; SPECIAL=scale-out ;;
	E5) MODE=two-level; N=4; SCENARIO=hot-key; SPECIAL=redis-bounce ;;
	E6) MODE=two-level; N=4; SCENARIO=publication-change; SPECIAL=publication-change ;;
	F1) MODE=two-level; N=4; SCENARIO=hot-key; WRITE_SUITE=full ;;
	W)  MODE="";        N=4; SCENARIO=working-set ;;
	S)  MODE=caffeine;  N=4; SCENARIO=hot-spike ;;
	*) err "알 수 없는 suite: $SUITE"; usage >&2; exit 2 ;;
esac

# E1 은 "어느 캐시 모드의 콜드 스타트인가"가 실험 대상이라 모드를 고를 수 없다.
if [ "$SUITE" = "E1" ] && [ -z "$MODE_OVERRIDE" ]; then
	err "E1 은 --mode 로 캐시 모드를 지정해야 한다 (none|caffeine|redis|two-level)"
	exit 2
fi

# W 도 같은 규율 — "어느 캐시 모드의 워킹셋 한계인가"가 실험 대상이다.
if [ "$SUITE" = "W" ] && [ -z "$MODE_OVERRIDE" ]; then
	err "W 는 --mode 로 캐시 모드를 지정해야 한다 (none|caffeine|redis|two-level)"
	exit 2
fi

[ -n "$MODE_OVERRIDE" ] && MODE="$MODE_OVERRIDE"
[ -n "$INSTANCES_OVERRIDE" ] && N="$INSTANCES_OVERRIDE"
[ -n "$SCENARIO_OVERRIDE" ] && SCENARIO="$SCENARIO_OVERRIDE"

case "$N" in 1|2|3|4) ;; *) err "--instances 는 1~4 다: $N"; exit 2 ;; esac
[ -f "$K6_DIR/$SCENARIO.js" ] || { err "시나리오 파일이 없다: $K6_DIR/$SCENARIO.js"; exit 2; }

# ── W 전용 배선: 합성 워킹셋 ─────────────────────────────────────────────────
# WHY: 워킹셋 스윕은 "서로 다른 키가 몇 개일 때 L1 이 무너지나"를 재는 실험이라
# 키 공급을 세 곳이 같은 형식으로 합의해야 한다 — ① allowlist(서빙 404 판정),
# ② DB 시드(200 판정), ③ k6 키 생성. 계약은 'LT' + 5자리 zero-pad, 1부터.
#
# 오염 방지: W 가 아닌 suite 에서는 PUB_KNOWN_TICKERS 를 반드시 비운다. 셸에 남은
# 값이 compose 의 ${PUB_KNOWN_TICKERS:-<33종>} 을 덮으면 기존 suite 가 조용히
# 다른 유니버스로 돌아간다.
SEED_ARGS="--reset-scope"
K6_EXTRA=()
if [ "$SUITE" = "W" ]; then
	[ -n "$WORKING_SET" ] || { err "W 는 --working-set <n> 이 필수다"; exit 2; }
	case "$WORKING_SET" in
		''|*[!0-9]*) err "--working-set 은 양의 정수여야 한다: $WORKING_SET"; exit 2 ;;
	esac
	[ "$WORKING_SET" -gt 0 ] || { err "--working-set 은 1 이상이어야 한다"; exit 2; }

	# 35KB 급 문자열이라 bash 루프(3.2 는 문자열 누적 O(n^2))보다 python3 가 빠르고 안전하다.
	PUB_KNOWN_TICKERS="$(python3 -c "
import sys
n = int(sys.argv[1])
print(','.join(['069500'] + ['LT%05d' % i for i in range(1, n + 1)]))
" "$WORKING_SET")"
	export PUB_KNOWN_TICKERS
	SEED_ARGS="--reset-scope --synthetic $WORKING_SET"
	K6_EXTRA=(-e "WORKING_SET=$WORKING_SET" -e "HOT_RATIO=${HOT_RATIO:-0}")
	info "워킹셋 $WORKING_SET 종(LT00001..LT$(printf '%05d' "$WORKING_SET")) + hot 069500, hot_ratio=${HOT_RATIO:-0}"
else
	[ -z "$WORKING_SET" ] || { err "--working-set 은 suite W 전용이다"; exit 2; }
	unset PUB_KNOWN_TICKERS || true
fi

# read suite 는 쓰기 경로(노출 로그·요청 메트릭)를 끈다 — 캐시가 가리는 read path 만 남긴다.
# full suite(F1)만 켠다: 캐시로 못 가리는 쓰기 잔여가 얼마인지 보는 것이 목적이다.
if [ "$WRITE_SUITE" = "full" ]; then
	EXPOSURE_ENABLED=true; REQUEST_METRIC_ENABLED=true
else
	EXPOSURE_ENABLED=false; REQUEST_METRIC_ENABLED=false
fi

CACHE_TTL="${CACHE_TTL:-3s}"
CACHE_L2_TTL="${CACHE_L2_TTL:-10s}"
WARMUP="${WARMUP:-30s}"

# k6 는 호스트 바이너리를 요구한다 — docker 폴백은 두지 않는다.
# WHY: ① 시나리오들이 `./lib.js` 상대 import 를 쓰므로 README 관례의 stdin 방식
# (`docker run -i grafana/k6 run -`)은 아예 동작하지 않는다(모듈 해석 기준 경로가 없다).
# 파일 경로로 돌리려면 k6/ 디렉터리를 볼륨 마운트해야 한다. ② macOS 에서 --network host
# 가 동작하지 않아 BASE_URL 을 host.docker.internal 로 바꿔야 한다. ③ 컨테이너 자원이
# 부하 발생기와 측정 대상 사이에 끼어 지연이 왜곡된다. 측정 실행에 쓸 수 없다.
command -v k6 >/dev/null 2>&1 || { err "k6 가 없다 — 'brew install k6' 후 다시 실행하라"; exit 1; }
command -v python3 >/dev/null 2>&1 || { err "python3 가 없다 (meta.json 생성에 필요)"; exit 1; }

# k6 기간 표기("3m","90s","500ms")를 초로 바꾼다 — 백그라운드 이벤트 시점 계산용.
to_seconds() {
	local raw="$1" n unit
	n="$(printf '%s' "$raw" | sed 's/[a-z]*$//')"
	unit="$(printf '%s' "$raw" | sed 's/^[0-9.]*//')"
	case "$unit" in
		ms) python3 -c "print(int(float('$n')/1000))" ;;
		m)  python3 -c "print(int(float('$n')*60))" ;;
		h)  python3 -c "print(int(float('$n')*3600))" ;;
		s|"") python3 -c "print(int(float('$n')))" ;;
		*) err "기간 표기를 모르겠다: $raw"; return 1 ;;
	esac
}

wait_healthy() {
	local port="$1" deadline=$(( $(date +%s) + 120 ))
	while [ "$(date +%s)" -lt "$deadline" ]; do
		if curl -sf "http://localhost:$port/actuator/health" >/dev/null 2>&1; then
			return 0
		fi
		sleep 2
	done
	err "기동 대기 타임아웃(120s): :$port"
	return 1
}

wait_all_healthy() {
	local i
	for i in $(seq 1 "$N"); do
		wait_healthy "1810$i" || return 1
	done
}

# ── 스택 기동 ────────────────────────────────────────────────────────────────
# env 는 compose 가 ${VAR:-기본} 으로 읽는다 — export 해야 컨테이너에 실린다.
export CACHE_MODE="$MODE"
export CACHE_TTL CACHE_L2_TTL
export CACHE_L2_JITTER="$JITTER"
export EXPOSURE_ENABLED REQUEST_METRIC_ENABLED

info "suite=$SUITE mode=$MODE instances=${N} scenario=$SCENARIO write=$WRITE_SUITE ttl=$CACHE_TTL l2=$CACHE_L2_TTL jitter=$JITTER"

# --force-recreate: env 만 바뀌면 compose 가 재생성하지만, 같은 값으로 재실행할 때도
# 캐시가 비워진 상태에서 시작해야 반복 실행이 서로를 오염시키지 않는다.
"${COMPOSE[@]}" up -d --force-recreate --remove-orphans
wait_all_healthy || exit 1

# 인스턴스 수를 줄이는 실험은 나머지를 내린다. nginx 업스트림은 4대 고정이라
# 1대 실험은 LB 를 우회해 api-1 직결로 때린다(부분 업스트림 장애를 재지 않기 위함).
if [ "$N" -lt 4 ]; then
	for i in $(seq $((N + 1)) 4); do
		"${COMPOSE[@]}" stop "api-$i" >/dev/null
	done
fi
if [ "$N" -eq 1 ]; then
	BASE_URL="http://localhost:18101"
else
	BASE_URL="http://localhost:18100"
fi

# --reset-scope: 앞선 suite(E6 의 --block 등)가 남긴 차단 잔재를 걷어낸다 — 잔재가 있으면
# hot 티커가 204 라 200 검증이 (정당하게) 실패한다. 모든 suite 는 깨끗한 scope 에서 시작.
# shellcheck disable=SC2086 -- SEED_ARGS 는 의도적 단어 분리(W 는 --synthetic 이 붙는다)
"$SCRIPT_DIR/prepare-data.sh" $SEED_ARGS || { err "시드 실패 — 측정을 중단한다"; exit 1; }

DUR_S="$(to_seconds "$DURATION")"
WARMUP_S="$(to_seconds "$WARMUP")"
# 이벤트는 warm-up 이 끝나고 측정 구간 중간에 터뜨린다 — 캐시가 채워진 정상 상태에서
# 무슨 일이 일어나는지가 관측 대상이다.
EVENT_AT=$((WARMUP_S + DUR_S / 2))

RATES="${SWEEP:-$RATE}"
GIT_SHA="$(git -C "$STACK_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
K6_VERSION="$(k6 version 2>/dev/null | head -1)"

for CURRENT_RATE in $RATES; do
	rep=1
	while [ "$rep" -le "$REPEAT" ]; do
		# W 는 같은 (suite, mode, 인스턴스)에서 키 수만 바뀌므로 run_id 에 -n<키수>를 넣는다.
		RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$SUITE-$MODE-${N}x${WORKING_SET:+-n$WORKING_SET}-r$rep"
		RUN_DIR="$RESULTS_DIR/$RUN_ID"
		mkdir -p "$RUN_DIR"
		info "실행 $RUN_ID (rate=$CURRENT_RATE, duration=$DURATION)"

		BG_PID=""
		case "$SPECIAL" in
			cold-start)
				# 콜드 스타트는 "빈 캐시에서 시작"이 전제다. L1 은 재기동으로, L2 는
				# redis 를 함께 재기동해 비운다(영속화가 꺼져 있어 재기동 = flush).
				info "  콜드 스타트 준비 — api·redis 재기동"
				"${COMPOSE[@]}" restart redis >/dev/null
				for i in $(seq 1 "$N"); do "${COMPOSE[@]}" restart "api-$i" >/dev/null; done
				wait_all_healthy || exit 1
				;;
			scale-out)
				# api-4 를 빼고 시작해 3대로 안정화된 뒤 측정 중간에 합류시킨다.
				# 합류 직후 api-4 의 L1 은 비어 있어 L2·DB 로 미스가 튄다 — 그 폭이 관측 대상.
				# 함정: nginx 업스트림은 4대 고정이라 정지 구간 동안 api-4 로 간 요청이
				# 주기적으로 502 가 된다(max_fails/fail_timeout 재시도). 계약 밖 응답이라
				# k6 check 가 실패로 잡히고 종료 코드가 0 이 아니게 된다 — 정상이며,
				# 판독은 합류 시점 전후의 미스 곡선으로 한다.
				"${COMPOSE[@]}" stop api-4 >/dev/null
				( sleep "$EVENT_AT"; "${COMPOSE[@]}" start api-4 >/dev/null 2>&1 ) &
				BG_PID=$!
				;;
			redis-bounce)
				# L2 소실 → 복귀. 앱이 redis 장애에 fail-open 하는지(원본 경로로 떨어지는지)와
				# 복귀 후 재적재 폭을 본다. redis 타임아웃이 150ms 라 정지 구간도 응답은 나와야 한다.
				( sleep "$EVENT_AT"
				  "${COMPOSE[@]}" stop redis >/dev/null 2>&1
				  sleep 60
				  "${COMPOSE[@]}" start redis >/dev/null 2>&1 ) &
				BG_PID=$!
				;;
			publication-change)
				# 정합성 실험: 새 스냅샷(유효 최신 승리 전환) → 종목 차단(204 전환).
				# 전환이 응답에 반영되기까지 걸리는 시간이 곧 TTL 상한의 실측치다.
				# 반복마다 이전 rep 의 --block 잔재를 걷어낸다 — 잔재가 있으면 setup 이
				# 204 를 받아 baseline 이 무너지고 이후 200 이 전부 fresh 로 집계된다.
				"$SCRIPT_DIR/prepare-data.sh" --reset-scope >/dev/null 2>&1 || true
				( sleep "$EVENT_AT"
				  "$SCRIPT_DIR/prepare-data.sh" --new-snapshot 069500 >/dev/null 2>&1
				  sleep 30
				  "$SCRIPT_DIR/prepare-data.sh" --block 069500 >/dev/null 2>&1 ) &
				BG_PID=$!
				;;
		esac

		START="$(date +%s)"
		# k6 종료 코드는 threshold 위반으로도 0 이 아니다(포화 구간에선 정상적인 결과다)
		# — 실행을 세우지 않고 meta.json 에 기록만 한다.
		set +e
		k6 run \
			-e BASE_URL="$BASE_URL" \
			-e RATE="$CURRENT_RATE" \
			-e DURATION="$DURATION" \
			-e WARMUP="$WARMUP" \
			-e TTL="$CACHE_TTL" \
			${K6_EXTRA[@]+"${K6_EXTRA[@]}"} \
			--summary-export "$RUN_DIR/summary.json" \
			--out "json=$RUN_DIR/raw.json.gz" \
			"$K6_DIR/$SCENARIO.js"
		K6_EXIT=$?
		set -e
		END="$(date +%s)"

		if [ -n "$BG_PID" ]; then
			wait "$BG_PID" 2>/dev/null || true
		fi

		"$SCRIPT_DIR/collect-result.sh" "$RUN_ID" "$START" "$END" || err "Prometheus 수집 실패(계속 진행)"

		# 컨테이너 자원 한도는 실행마다 같아야 비교가 성립한다 — 실측값을 남긴다.
		LIMITS="$(docker inspect --format '{{.Name}} nanocpus={{.HostConfig.NanoCpus}} mem={{.HostConfig.Memory}}' \
			edge-pubcache-api-1-1 edge-pubcache-postgres-1 edge-pubcache-redis-1 2>/dev/null | tr '\n' ';' || echo unknown)"
		REDIS_CONF="$("${COMPOSE[@]}" exec -T redis redis-cli config get maxmemory maxmemory-policy 2>/dev/null | tr '\n' ' ' || echo unknown)"

		RUN_ID="$RUN_ID" GIT_SHA="$GIT_SHA" SUITE="$SUITE" MODE="$MODE" N="$N" \
		SCENARIO="$SCENARIO" CURRENT_RATE="$CURRENT_RATE" DURATION="$DURATION" \
		WARMUP="$WARMUP" REP="$rep" WRITE_SUITE="$WRITE_SUITE" BASE_URL="$BASE_URL" \
		CACHE_TTL="$CACHE_TTL" CACHE_L2_TTL="$CACHE_L2_TTL" JITTER="$JITTER" \
		K6_VERSION="$K6_VERSION" K6_EXIT="$K6_EXIT" START="$START" END="$END" \
		LIMITS="$LIMITS" REDIS_CONF="$REDIS_CONF" RUN_DIR="$RUN_DIR" \
		WORKING_SET="${WORKING_SET:-}" HOT_RATIO="${HOT_RATIO:-}" \
		OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)" \
		NCPU="$(sysctl -n hw.ncpu 2>/dev/null || echo unknown)" \
		MEMSIZE="$(sysctl -n hw.memsize 2>/dev/null || echo unknown)" \
		CPU_BRAND="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)" \
		DOCKER_VERSION="$(docker --version 2>/dev/null || echo unknown)" \
		python3 - <<'PY'
import json, os

env = os.environ
run_dir = env["RUN_DIR"]

# k6 요약에서 포화 신호만 뽑아 meta 에 얹는다 — 나중에 결과를 훑을 때 summary.json 을
# 일일이 열지 않아도 "이 실행이 포화였나"를 바로 본다.
dropped = None
failed = None
try:
    with open(os.path.join(run_dir, "summary.json")) as f:
        s = json.load(f)
    metrics = s.get("metrics", {})
    dropped = metrics.get("dropped_iterations", {}).get("count")
    failed = metrics.get("http_req_failed", {}).get("value")
except (OSError, ValueError):
    pass

meta = {
    "run_id": env["RUN_ID"],
    "git_sha": env["GIT_SHA"],
    "suite": env["SUITE"],
    "mode": env["MODE"],
    "instances": int(env["N"]),
    "scenario": env["SCENARIO"],
    "rate": int(env["CURRENT_RATE"]),
    "duration": env["DURATION"],
    "warmup": env["WARMUP"],
    "repeat": int(env["REP"]),
    "write_suite": env["WRITE_SUITE"],
    "base_url": env["BASE_URL"],
    "cache": {
        "ttl": env["CACHE_TTL"],
        "l2_ttl": env["CACHE_L2_TTL"],
        "l2_jitter": env["JITTER"],
    },
    "redis_config": env["REDIS_CONF"].strip(),
    "container_limits": env["LIMITS"].strip(),
    "host": {
        "os": env["OS_VERSION"],
        "ncpu": env["NCPU"],
        "memsize": env["MEMSIZE"],
        "cpu": env["CPU_BRAND"],
        "docker": env["DOCKER_VERSION"],
    },
    "k6_version": env["K6_VERSION"],
    "k6_exit_code": int(env["K6_EXIT"]),
    "start_epoch": int(env["START"]),
    "end_epoch": int(env["END"]),
    "dropped_iterations": dropped,
    "http_req_failed_rate": failed,
}

# W 전용 축. 판독기(w_analysis.py)는 이 키의 존재로 W run 을 식별하므로
# W 가 아닌 실행에는 넣지 않는다.
if env.get("WORKING_SET"):
    meta["working_set"] = int(env["WORKING_SET"])
    meta["hot_ratio"] = float(env["HOT_RATIO"]) if env.get("HOT_RATIO") else 0.0

with open(os.path.join(run_dir, "meta.json"), "w") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

		info "완료 $RUN_ID (k6 exit=$K6_EXIT) -> $RUN_DIR"

		# in-flight 요청·비동기 쓰기가 다음 실행에 섞이지 않게 잠시 둔다(README 절차 4번과 같은 이유).
		sleep 10
		rep=$((rep + 1))
	done
done

info "suite $SUITE 종료 — 결과는 $RESULTS_DIR 아래"
