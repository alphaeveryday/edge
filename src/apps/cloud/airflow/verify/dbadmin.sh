#!/usr/bin/env bash
# 검증 관리 태스크(dbadmin, postgres:16) 명령 — 업무 RDS 인스턴스 안에 Airflow 전용 DB·역할을 만들고 지운다(ALPHA-1119).
# run.py 가 이 파일을 읽어 RunTask override(bash -c)로 넘긴다: `<이 파일 내용>` + `main <명령>`.
# 비밀번호는 ECS secrets 로만 들어온다(META_PW·VERIFY_PW·PGPASSWORD) — 명령행·로그에 찍지 않는다(psql -e 금지, set -x 금지).
#
# 권한 분리:
# - airflow_meta: DB `airflow` 소유. 업무 DB(edge)의 테이블 권한 없음(PUBLIC 에도 테이블 권한이 없다 — privcheck 로 확인).
# - airflow_verify: DB `edge_verify`(검증 원장) 소유. 역시 업무 테이블 권한 없음.
# - 두 역할 모두 CONNECTION LIMIT 10, 문장 30초·트랜잭션 유휴 60초 상한. 전역 파라미터는 건드리지 않는다(역할 수준 설정).
# - 새 DB 두 개는 PUBLIC CONNECT 를 거두고 소유 역할만 붙게 한다.
# - 마스터(PGUSER)는 DB 소유권을 넘기려고 두 역할의 SET 권한만 받는다(INHERIT FALSE — 권한이 마스터로 새지 않게).
set -euo pipefail

q() { psql -X -v ON_ERROR_STOP=1 -At "$@"; }

create() {
  # 비밀번호는 명령행(-v)으로 넘기지 않는다(프로세스 목록에 보인다) — psql 안에서 환경변수를 읽는다(\getenv, psql 15+).
  q -v meta_user="$META_USER" -v verify_user="$VERIFY_USER" -v master="$PGUSER" <<'SQL'
\getenv meta_pw META_PW
\getenv verify_pw VERIFY_PW
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 10 PASSWORD %L', u, p)
  FROM (VALUES (:'meta_user', :'meta_pw'), (:'verify_user', :'verify_pw')) v(u, p)
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = u) \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', u, p)
  FROM (VALUES (:'meta_user', :'meta_pw'), (:'verify_user', :'verify_pw')) v(u, p) \gexec
SELECT format('ALTER ROLE %I SET statement_timeout = %L', u, '30s') FROM (VALUES (:'meta_user'), (:'verify_user')) v(u) \gexec
SELECT format('ALTER ROLE %I SET idle_in_transaction_session_timeout = %L', u, '60s') FROM (VALUES (:'meta_user'), (:'verify_user')) v(u) \gexec
SELECT format('GRANT %I TO %I WITH INHERIT FALSE, SET TRUE', u, :'master') FROM (VALUES (:'meta_user'), (:'verify_user')) v(u) \gexec
SQL
  q <<SQL
SELECT 'CREATE DATABASE airflow OWNER ' || quote_ident('$META_USER') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'airflow') \gexec
SELECT 'CREATE DATABASE edge_verify OWNER ' || quote_ident('$VERIFY_USER') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'edge_verify') \gexec
REVOKE CONNECT ON DATABASE airflow FROM PUBLIC;
REVOKE CONNECT ON DATABASE edge_verify FROM PUBLIC;
GRANT CONNECT ON DATABASE airflow TO "$META_USER";
GRANT CONNECT ON DATABASE edge_verify TO "$VERIFY_USER";
SQL
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
  local u pw
  for u in META VERIFY; do
    eval "pw=\$${u}_PW"; eval "u=\$${u}_USER"
    PGUSER="$u" PGPASSWORD="$pw" psql -X -At -d "$PGDATABASE" -c \
      "SELECT '$u', count(*) FILTER (WHERE has_table_privilege(c.oid, 'SELECT')), count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'" \
      | sed 's/^/DBADMIN privcheck readable_business_tables|total: /' || echo "DBADMIN privcheck $u connect-to-business-db failed"
  done
  q -c "SELECT datname, has_database_privilege('public', datname, 'CONNECT') FROM pg_database WHERE datname IN ('airflow','edge_verify')" | sed 's/^/DBADMIN privcheck public_connect: /'
  q -c "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolconnlimit, array_to_string(rolconfig, ';') FROM pg_roles WHERE rolname IN ('$META_USER','$VERIFY_USER')" | sed 's/^/DBADMIN privcheck role: /'
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
                pg_database_size(datname) AS bytes FROM pg_stat_database WHERE datname IN ('edge','airflow','edge_verify')) x))::jsonb::text
SQL
}

teardown() {
  q <<SQL
SELECT 'DROP DATABASE IF EXISTS airflow WITH (FORCE)' \gexec
SELECT 'DROP DATABASE IF EXISTS edge_verify WITH (FORCE)' \gexec
SQL
  q -v meta_user="$META_USER" -v verify_user="$VERIFY_USER" <<'SQL'
SELECT format('DROP ROLE %I', u) FROM (VALUES (:'meta_user'), (:'verify_user')) v(u)
 WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = u) \gexec
SQL
  echo "DBADMIN teardown ok roles_left=$(q -c "SELECT count(*) FROM pg_roles WHERE rolname IN ('$META_USER','$VERIFY_USER')") dbs_left=$(q -c "SELECT count(*) FROM pg_database WHERE datname IN ('airflow','edge_verify')")"
}

main() {
  case "$1" in
    create|clone_schema|privcheck|stats|teardown) "$1" ;;
    *) echo "모르는 명령: $1" >&2; exit 2 ;;
  esac
}
