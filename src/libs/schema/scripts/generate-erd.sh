#!/usr/bin/env bash
# libs/schema Flyway 마이그레이션을 임시 Postgres(pg18) 클러스터에 순서대로 적용하고,
# 최종 물리 스키마를 결정적 DBML(ERD)로 추출한다(scripts/gen-erd.sql).
#
# Flyway 가 DB 스키마 SSOT 이며 이 산출물(generated/*.dbml)은 파생물이다 — 사람이 편집하지 않는다.
# 집행은 CI 가 한다(ALPHA-783): `schema-validate` 가 `src/libs/schema/**` 변경 PR 에서 이 스크립트를
# 다시 돌려 커밋본과 대조하고, 어긋나면 빨간불을 낸다. `.githooks/pre-commit` 도 같은 일을 하지만
# opt-in 이고 pg18 이 없으면 경고만 하므로 방어선이 아니라 편의다.
# ⚠️ 이 레포는 branch protection 이 없어 그 체크가 required 가 아니다 — 빨간불이 머지를 막지는
# 못한다. 마이그레이션을 추가했으면 직접 돌려 확인하라(pg18 없으면 docker — `chmod` 을 빼면
# 리눅스에서 Permission denied 다. 컨테이너가 postgres uid 로 남의 파일을 truncate 하지 못한다):
#  `chmod -R a+w src/libs/schema/generated && \
#   docker run --rm -v "$PWD:/repo" -w /repo --user postgres postgres:18 \
#   bash src/libs/schema/scripts/generate-erd.sh`
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
# `-1` 로 파일 하나를 한 트랜잭션에 넣는다 — 배포 경로(Flyway)가 마이그레이션마다
# 트랜잭션을 여는 것과 같은 조건으로 적용해야, 그 전제를 쓰는 문장이 여기서만 다르게
# 동작하지 않는다. 없으면 autocommit 문장 단위라 `SET LOCAL`(락 예산, README 참고)이
# "can only be used in transaction blocks" 경고와 함께 no-op 이 된다.
# ⚠️ 트랜잭션 밖에서만 되는 DDL(`CREATE INDEX CONCURRENTLY` 등)을 쓰는 마이그레이션이
# 생기면 이 플래그가 그것을 막는다 — 그때는 그 파일만 갈라야 한다(현재 사용 0건).
apply() {  # $1=migration dir  $2=db
  local f
  for f in "$1"/V*.sql; do
    psql -v ON_ERROR_STOP=1 -q -1 -d "$2" -f "$f" >/dev/null
  done
}
apply "$HERE/migrations-cloud"  edge
apply "$HERE/migrations-onprem" edge_onprem

mkdir -p "$OUT"
# ON_ERROR_STOP=1 은 apply() 와 같은 이유로 필수다 — 없으면 gen-erd.sql 이 깨져도 psql 이 0 으로
# 끝나 **빈 파일**이 산출물로 남는다. CI 대조는 훅과 같은 SQL 을 돌리므로 양쪽 다 비어 서로
# 일치하고, ERD 가 파괴된 채 게이트가 초록이 된다(집행이 자기 실패를 못 보는 자리).
psql -v ON_ERROR_STOP=1 -tA -q -d edge        -f "$SQL" > "$OUT/physical-erd.dbml"
psql -v ON_ERROR_STOP=1 -tA -q -d edge_onprem -f "$SQL" > "$OUT/physical-erd-onprem.dbml"
echo "generated: $OUT/physical-erd.dbml, $OUT/physical-erd-onprem.dbml"
