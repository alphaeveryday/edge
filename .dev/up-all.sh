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

echo "▶ UI 의존성 확인 — pnpm install"
pnpm -C "$ROOT/src" install

echo "▶ 콘솔 UI 2종 기동 — vite dev (Ctrl-C 로 함께 내려간다)"
# UI 는 이 스크립트의 자식으로 포그라운드에 묶는다 — 한쪽이 죽으면 wait 가
# 그 실패 코드로 끝나고 trap 이 나머지를 정리한다(반쪽 기동을 숨기지 않음).
trap 'kill $(jobs -p) 2> /dev/null; echo; echo "◼ UI 종료. 백엔드는 유지 중 — 정리는 .dev/up-all.sh down"' INT TERM EXIT
pnpm -C "$ROOT/src" --filter tenant-console-ui dev &
pnpm -C "$ROOT/src" --filter super-admin-ui dev &

cat << 'EOF'

전체 스택 기동 완료
  Tenant Console UI   http://localhost:5174   (dev 자동 로그인)
  Super Admin UI      http://localhost:5175   (dev 자동 로그인)
  가상 MTS 데모        http://localhost:18090  (AI 분석 탭 = publication-api 실호출)
  tenant-console-api  http://localhost:18081/actuator/health
  super-admin-api     http://127.0.0.1:18082/actuator/health

종료: Ctrl-C (UI) → .dev/up-all.sh down [-v] (백엔드[·DB 볼륨])
EOF
wait
