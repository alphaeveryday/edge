#!/usr/bin/env bash
# 검증 관리 태스크(dbadmin, postgres:16) 명령 — 업무 RDS 인스턴스 안에 Airflow 전용 DB·역할을 만들고 지운다(ALPHA-1119).
# run.py 가 이 파일을 gzip+base64 로 접어 RunTask override(bash -c)로 넘긴다 — 원문 그대로는 override 8,192자 제한을 넘는다.
# 비밀번호는 ECS secrets 로만 들어온다(META_PW·VERIFY_PW·PGPASSWORD, 역할 생성용 SCRAM 검증자 META_SCRAM·VERIFY_SCRAM) — 명령행·로그에 찍지 않는다(psql -e 금지, set -x 금지).
#
# 권한 분리:
# - airflow_meta: DB `airflow` 소유. 업무 DB(edge)의 테이블 권한 없음(PUBLIC 에도 테이블 권한이 없다 — privcheck 로 확인).
# - airflow_verify: DB `edge_verify`(검증 원장) 소유. 역시 업무 테이블 권한 없음.
# - 검증 자원이 꺼져 있으면(VERIFY_USER 없음 — 운영 메타DB 만, ALPHA-1141) airflow_meta·airflow 만 다룬다.
# - 두 역할 모두 CONNECTION LIMIT 10, 문장 30초·트랜잭션 유휴 60초 상한. 전역 파라미터는 건드리지 않는다(역할 수준 설정).
# - 새 DB 두 개는 PUBLIC CONNECT 를 거두고 소유 역할만 붙게 한다.
# - 마스터(PGUSER)는 두 역할의 SET 권한만 받는다(INHERIT FALSE — 권한이 마스터로 새지 않게). 소유자 권한이 필요한 문장
#   (PUBLIC CONNECT 회수·DB 삭제)은 SET ROLE 로 소유 역할이 되어 실행한다. RDS 마스터는 슈퍼유저가 아니다.
set -euo pipefail
VERIFY_USER=${VERIFY_USER:-}

q() { psql -X -v ON_ERROR_STOP=1 -At "$@"; }
# 다룰 짝 "역할:DB:비밀번호변수:SCRAM변수" — 검증 몫은 VERIFY_USER 가 있을 때만.
pairs() {
  echo "$META_USER:airflow:META_PW:META_SCRAM"
  [ -z "${VERIFY_USER:-}" ] || echo "$VERIFY_USER:edge_verify:VERIFY_PW:VERIFY_SCRAM"
}

create() {
  local u db pwv sv
  while IFS=: read -r u db pwv sv; do
    case "${!sv:-}" in SCRAM-SHA-256*) ;; *) echo "DBADMIN create: $u SCRAM 검증자 없음(run.py secrets)"; exit 1 ;; esac
    # 비밀번호는 명령행(-v)으로 넘기지 않는다(프로세스 목록에 보인다) — psql 안에서 환경변수를 읽는다(\getenv, psql 15+).
    # SQL 에는 평문이 아니라 SCRAM 검증자(run.py secrets 가 만든 *_scram)를 넣는다 — 문장이 실패하면 서버 오류 로그에 남는다.
    q -v u="$u" -v sv="$sv" -v master="$PGUSER" <<'SQL'
\getenv pw :sv
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 10 PASSWORD %L', :'u', :'pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'u') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'u', :'pw') \gexec
SELECT format('ALTER ROLE %I SET statement_timeout = %L', :'u', '30s') \gexec
SELECT format('ALTER ROLE %I SET idle_in_transaction_session_timeout = %L', :'u', '60s') \gexec
SELECT format('GRANT %I TO %I WITH INHERIT FALSE, SET TRUE', :'u', :'master') \gexec
SQL
    q <<SQL
SELECT 'CREATE DATABASE $db OWNER ' || quote_ident('$u') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$db') \gexec
SET ROLE "$u";
REVOKE CONNECT ON DATABASE $db FROM PUBLIC;
GRANT CONNECT ON DATABASE $db TO "$u";
RESET ROLE;
SQL
  done < <(pairs)
  echo "DBADMIN create ok"
}

# 검증 원장 스키마: 업무 DB 의 **스키마만** 복제한다(행 없음). 같은 인스턴스라 migrations-cloud 를 다시 적용하면
# 인스턴스 전역 역할 생성(V202609282100)이 "이미 있음"으로 실패한다. 소유·권한 구문은 빼고 airflow_verify 로 만든다.
clone_schema() {
  # 한 트랜잭션·첫 오류에서 중단 — 일부만 만들어진 원장으로 검증을 시작하지 않는다(실패는 exit≠0 으로 드러난다).
  local rc=0
  pg_dump -s --no-owner --no-privileges -d "$PGDATABASE" > /tmp/schema.sql
  PGUSER="$VERIFY_USER" PGPASSWORD="$VERIFY_PW" psql -X -q -v ON_ERROR_STOP=1 --single-transaction -d "$VERIFY_DB" \
    -f /tmp/schema.sql > /dev/null 2> /tmp/schema.err || rc=$?
  grep -E "ERROR|FATAL" /tmp/schema.err | sed 's/^/DBADMIN clone_schema error: /' | head -5 || true
  [ "$rc" = 0 ] || { echo "DBADMIN clone_schema failed rc=$rc"; exit 1; }
  echo "DBADMIN clone_schema ok tables=$(PGUSER="$VERIFY_USER" PGPASSWORD="$VERIFY_PW" q -d "$VERIFY_DB" -c "SELECT count(*) FROM pg_tables WHERE schemaname='public'")"
}

# 권한 분리 확인 — 두 역할이 업무 DB 테이블을 읽지 못하는지, 새 DB 에 다른 역할이 못 붙는지.
privcheck() {
  # 위반·확인 불가면 exit 1 — 출력만 하고 넘어가지 않는다.
  local u db pwv sv n bad=0 roles=0
  while IFS=: read -r u db pwv sv; do
    roles=$((roles + 1))
    n=$(PGUSER="$u" PGPASSWORD="${!pwv}" psql -X -At -v ON_ERROR_STOP=1 -d "$PGDATABASE" -c \
      "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND c.relkind IN ('r','v','m','p')
          AND (has_table_privilege(c.oid, 'SELECT') OR has_table_privilege(c.oid, 'INSERT')
               OR has_table_privilege(c.oid, 'UPDATE') OR has_table_privilege(c.oid, 'DELETE'))") || n=unknown
    echo "DBADMIN privcheck $u business_table_privileges=$n"
    [ "$n" = 0 ] || bad=1
  done < <(pairs)
  n=$(q -c "SELECT count(*) FROM pg_database WHERE datname IN ('airflow','edge_verify') AND has_database_privilege('public', datname, 'CONNECT')") || n=unknown
  echo "DBADMIN privcheck public_connect_dbs=$n"; [ "$n" = 0 ] || bad=1
  n=$(q -c "SELECT count(*) FROM pg_roles WHERE rolname IN ('$META_USER','$VERIFY_USER') AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND rolconnlimit = 10 AND array_to_string(rolconfig, ';') LIKE '%statement_timeout=30s%'") || n=unknown
  echo "DBADMIN privcheck compliant_roles=$n"; [ "$n" = "$roles" ] || bad=1
  n=$(q -c "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles g ON g.oid = m.member WHERE g.rolname IN ('$META_USER','$VERIFY_USER')") || n=unknown
  echo "DBADMIN privcheck memberships_of_new_roles=$n"; [ "$n" = 0 ] || bad=1
  [ "$bad" = 0 ] || { echo "DBADMIN privcheck FAILED"; exit 1; }
  echo "DBADMIN privcheck ok"
}

# 인스턴스 전체 연결·부하(마스터만 전 세션을 본다). 한 줄 JSON.
stats() {
  q <<'SQL' | sed 's/^/DBADMIN stats /'
SELECT json_build_object(
  'at', now(),
  'max_connections', current_setting('max_connections')::int,
  'sessions', (SELECT json_agg(x) FROM (SELECT usename, datname, state, wait_event_type, count(*) n
                FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY 1,2,3,4 ORDER BY 1,2,3) x),
  'longest_active_s', (SELECT max(extract(epoch FROM now() - query_start)) FROM pg_stat_activity
                        WHERE state = 'active' AND backend_type = 'client backend' AND pid <> pg_backend_pid()),
  'db', (SELECT json_agg(x) FROM (SELECT datname, numbackends, xact_commit, xact_rollback, blks_read, blks_hit,
                tup_returned, tup_fetched, tup_inserted + tup_updated + tup_deleted AS tup_written, deadlocks, temp_bytes,
                -- 새 DB 는 PUBLIC CONNECT 를 거둬 마스터가 크기를 못 볼 수 있다(권한 없음 = null, 오류로 죽지 않게)
                CASE WHEN has_database_privilege(datname, 'CONNECT') THEN pg_database_size(datname) END AS bytes FROM pg_stat_database WHERE datname IN ('edge','airflow','edge_verify')) x))::jsonb::text
SQL
}

teardown() {
  local u db pwv sv names=() dbs=()
  while IFS=: read -r u db pwv sv; do
    names+=("'$u'"); dbs+=("'$db'")
    q -v u="$u" <<SQL
SELECT 'SET ROLE ' || quote_ident(:'u') WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'u') \gexec
SELECT 'DROP DATABASE IF EXISTS $db WITH (FORCE)' \gexec
RESET ROLE;
SELECT format('DROP ROLE %I', :'u') WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'u') \gexec
SQL
  done < <(pairs)
  local IFS=,
  echo "DBADMIN teardown ok roles_left=$(q -c "SELECT count(*) FROM pg_roles WHERE rolname IN (${names[*]})") dbs_left=$(q -c "SELECT count(*) FROM pg_database WHERE datname IN (${dbs[*]})")"
}

main() {
  case "$1" in
    create|clone_schema|privcheck|stats|teardown) "$1" ;;
    *) echo "모르는 명령: $1" >&2; exit 2 ;;
  esac
}
