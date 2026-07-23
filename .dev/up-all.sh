#!/usr/bin/env bash
# edge 로컬 전체 스택 한 줄 기동 (ALPHA-516)
#
# 백엔드(cloud+onprem+mock-broker 데모)는 docker compose 로, 콘솔 UI 2종은 vite dev
# 로 함께 띄운다. UI 를 compose 에 넣지 않는 이유: docker-compose.yml 의 계약은
# ECS Service Connect 토폴로지 재현인데 UI 는 prod 에서 서버로 존재하지 않고
# (빌드 산출물 납품·정적 배포), prod 번들은 로그인 화면이 없어 정적 서빙이
# 동작하지 않으며, dev 모드 컨테이너는 볼륨 파일 감시 폴링으로 HMR 이 느려진다.
#
# 사용:
#   .dev/up-all.sh             # 전체 기동 — Ctrl-C 로 UI 종료 (백엔드 컨테이너는 유지)
#   .dev/up-all.sh down [-v]   # compose 백엔드 정리 (-v 는 DB 볼륨까지)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/docker-compose.yml")

if [[ "${1:-}" == "down" ]]; then
  shift
  exec "${COMPOSE[@]}" down "$@"
fi
if [[ $# -gt 0 ]]; then
  echo "사용법: .dev/up-all.sh [down [-v]]" >&2
  exit 2
fi

# UI 포트 프리플라이트 — 선점돼 있으면 vite 가 다른 포트로 옮겨 앉아 아래 안내
# URL 이 거짓이 된다. 여기서 막고, 경쟁 상황은 vite --strictPort 가 마저 막는다.
for port in 5174 5175; do
  if lsof -ti ":$port" > /dev/null 2>&1; then
    echo "✗ 포트 $port 가 이미 사용 중이다 — 기존 UI dev 서버를 내리고 다시 실행" >&2
    exit 1
  fi
done

echo "▶ 백엔드 기동 — docker compose up --build -d (첫 실행은 gradle 빌드로 느리다)"
"${COMPOSE[@]}" up --build -d

# API health 대기 — 컨테이너가 뜨다 죽으면 UI 만 떠서 빈 화면이 되므로,
# 여기서 기다렸다 실패를 드러낸다(타임아웃 시 로그 안내 후 종료).
wait_health() {
  local name="$1" url="$2" deadline=$((SECONDS + 180))
  until curl -fsS "$url" > /dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
      echo "✗ $name 이 180초 안에 뜨지 않았다 — 'docker compose logs $name' 으로 원인 확인" >&2
      exit 1
    fi
    sleep 2
  done
  echo "  ✓ $name"
}
wait_health tenant-console-api "http://localhost:18081/actuator/health"
wait_health super-admin-api "http://127.0.0.1:18082/actuator/health"
wait_health publication-api "http://localhost:18084/actuator/health"

# health 엔드포인트가 없는 서비스(sync 경로·mock-broker·PG)도 생존을 확인한다 —
# 시작 직후 죽은 컨테이너를 여기서 드러내지 않으면 동기화·데모만 조용히 빠진
# "성공"이 된다. flyway* 는 마이그레이션 후 정상 종료라 제외.
# 열거를 대입으로 분리 — `for x in $(cmd)` 형태는 cmd 실패를 삼켜 빈 목록으로
# 검사를 건너뛴 채 ✓ 를 찍는다(set -e 는 대입 형태에서만 전파된다).
services=$("${COMPOSE[@]}" config --services | grep -v '^flyway')
if [[ -z "$services" ]]; then
  echo "✗ compose 서비스 열거가 비었다 — docker compose config 확인" >&2
  exit 1
fi
for svc in $services; do
  state=$("${COMPOSE[@]}" ps --format '{{.State}}' "$svc" 2> /dev/null || true)
  if [[ "$state" != "running" ]]; then
    echo "✗ $svc 컨테이너가 running 이 아니다(현재: ${state:-없음}) — 'docker compose logs $svc' 확인" >&2
    exit 1
  fi
done
echo "  ✓ 전 서비스 컨테이너 생존"

echo "▶ UI 의존성 확인 — pnpm install"
pnpm -C "$ROOT/src" install

echo "▶ 콘솔 UI 2종 기동 — vite dev (Ctrl-C 로 함께 내려간다)"
# --strictPort: 프리플라이트와 기동 사이에 포트를 뺏겨도 다른 포트로 옮겨 앉지
# 않고 죽는다 — 아래 생존 루프가 그 죽음을 드러낸다.
#
# 정리는 프로세스 트리 단위 — pnpm 래퍼만 죽이면 vite 자식이 살아남아 포트를
# 계속 점유한다(실증). 터미널 Ctrl-C 는 포그라운드 그룹 전체에 INT 가 가지만,
# 스크립트 내부 실패 경로(TERM·exit 1)는 트리로 내려가야 한다.
kill_tree() {
  local child
  for child in $(pgrep -P "$1" 2> /dev/null); do
    kill_tree "$child"
  done
  kill "$1" 2> /dev/null || true
}
cleanup() {
  local pid
  for pid in ${UI_PIDS[@]+"${UI_PIDS[@]}"}; do
    kill_tree "$pid"
  done
  echo
  echo "◼ UI 종료. 백엔드는 유지 중 — 정리는 .dev/up-all.sh down"
}
# INT/TERM 은 정리 후 즉시 종료해야 한다 — cleanup 만 걸면 트랩 반환 후 원래
# 흐름(리슨 대기·폴링)이 이어져 신호를 삼키고, 이후 EXIT 트랩이 cleanup 을
# 한 번 더 돌린다. 트랩 해제 후 관례적 종료 코드(128+signo)로 끝낸다.
trap 'trap - INT TERM EXIT; cleanup; exit 130' INT
trap 'trap - INT TERM EXIT; cleanup; exit 143' TERM
trap cleanup EXIT
# 주의: `dev -- --strictPort` 로 쓰면 pnpm 이 `--` 를 vite 에 그대로 전달해
# strictPort 가 무시된다(실증) — 구분자 없이 붙여야 vite 옵션으로 파싱된다.
pnpm -C "$ROOT/src" --filter tenant-console-ui dev --strictPort &
UI_PIDS=($!)
pnpm -C "$ROOT/src" --filter super-admin-ui dev --strictPort &
UI_PIDS+=($!)

# 배너 전에 UI 리슨을 기다린다 — 안 기다리면 "기동 완료" 직후 접속이 실패할 수
# 있고, 시작 실패(strictPort 충돌 등)도 배너 뒤에야 드러난다.
for port in 5174 5175; do
  deadline=$((SECONDS + 60))
  until lsof -ti ":$port" > /dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
      echo "✗ UI(:$port)가 60초 안에 리슨하지 않았다 — 위 vite 로그 확인" >&2
      exit 1
    fi
    sleep 1
  done
done

cat << 'EOF'

전체 스택 기동 완료
  Tenant Console UI   http://localhost:5174   (dev 자동 로그인)
  Super Admin UI      http://localhost:5175   (dev 자동 로그인)
  가상 MTS 데모        http://localhost:18090  (AI 분석 탭 = publication-api 실호출)
  tenant-console-api  http://localhost:18081/actuator/health
  super-admin-api     http://127.0.0.1:18082/actuator/health

종료: Ctrl-C (UI) → .dev/up-all.sh down [-v] (백엔드[·DB 볼륨])
EOF

# 편측 사망 감시 — 인자 없는 wait 는 한쪽이 먼저 죽어도 0 으로 끝나 실패를
# 숨긴다(macOS 기본 bash 3.2 라 wait -n 도 없다). 생존 폴링으로 어느 쪽이든
# 죽는 즉시 실패로 끝내고, EXIT trap 이 나머지를 정리한다.
while :; do
  for pid in "${UI_PIDS[@]}"; do
    if ! kill -0 "$pid" 2> /dev/null; then
      echo "✗ UI dev 서버(pid $pid)가 종료됐다 — 위 로그 확인" >&2
      exit 1
    fi
  done
  sleep 2
done
