#!/usr/bin/env bash
# libs/schema Flyway 마이그레이션을 임시 Postgres(pg18) 클러스터에 순서대로 적용하고,
# 최종 물리 스키마를 결정적 DBML(ERD)로 추출한다(scripts/gen-erd.sql).
#
# Flyway 가 DB 스키마 SSOT 이며 이 산출물(generated/*.dbml)은 파생물이다 — 사람이 편집하지 않는다.
# ⚠️ 재생성을 강제하는 CI 게이트는 없다. `.githooks/pre-commit` 이 마이그레이션 커밋에서 이 스크립트를
# 돌리지만 pg18 이 없으면 경고만 하고 통과시키므로(훅 자체도 core.hooksPath 설정이 있어야 돈다),
# 낡은 dbml 이 조용히 머지될 수 있다 — 마이그레이션을 추가했으면 이 스크립트를 직접 돌려 확인하라
# (pg18 이 없으면 docker: `docker run --rm -v "$PWD:/repo" -w /repo --user postgres postgres:18 \
#  bash src/libs/schema/scripts/generate-erd.sh`).
#
# 의존: initdb · pg_ctl · createdb · psql (PostgreSQL 18 클라이언트+서버). 외부/npm 도구 없음.
# 결정성: 임시 클러스터를 --no-locale(C 로케일)로 만들고 gen-erd.sql 의 ORDER BY 를 COLLATE "C"
# 로 고정하므로 OS/로케일과 무관하게 바이트 동일한 산출물을 낸다(로컬=CI).
#
# 사용:
#   bash src/libs/schema/scripts/generate-erd.sh
# CI(ubuntu)는 먼저 postgresql-18 을 설치한다(PATH 에 없으면 /usr/lib/postgresql/18/bin 을 얹는다).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"   # src/libs/schema
OUT="$HERE/generated"
SQL="$HERE/scripts/gen-erd.sql"

# pg18 바이너리가 PATH 에 없으면 CI 표준 설치 경로를 얹는다.
if ! command -v initdb >/dev/null 2>&1; then
  export PATH="/usr/lib/postgresql/18/bin:$PATH"
fi

PORT="${ERD_PGPORT:-55444}"
DATA="$(mktemp -d)"
export PGHOST=localhost PGPORT="$PORT" PGUSER=edge PGPASSWORD=

cleanup() { pg_ctl -D "$DATA" stop -m immediate >/dev/null 2>&1 || true; rm -rf "$DATA"; }
trap cleanup EXIT

initdb -D "$DATA" -U edge -A trust --encoding=UTF8 --no-locale >/dev/null
pg_ctl -D "$DATA" -l "$DATA/pg.log" \
  -o "-p $PORT -c listen_addresses=localhost -c unix_socket_directories=/tmp" -w start >/dev/null

createdb edge
createdb edge_onprem

# 파일명 timestamp 버전이 고정폭이라 glob 정렬 = Flyway 적용 순서. 세트별 독립.
apply() {  # $1=migration dir  $2=db
  local f
  for f in "$1"/V*.sql; do
    psql -v ON_ERROR_STOP=1 -q -d "$2" -f "$f" >/dev/null
  done
}
apply "$HERE/migrations-cloud"  edge
apply "$HERE/migrations-onprem" edge_onprem

mkdir -p "$OUT"
psql -tA -q -d edge        -f "$SQL" > "$OUT/physical-erd.dbml"
psql -tA -q -d edge_onprem -f "$SQL" > "$OUT/physical-erd-onprem.dbml"
echo "generated: $OUT/physical-erd.dbml, $OUT/physical-erd-onprem.dbml"
