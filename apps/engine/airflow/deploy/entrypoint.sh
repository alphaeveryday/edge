#!/usr/bin/env bash
# ECS 태스크 정의가 주입한 값으로 Airflow 설정을 완성한 뒤 공식 entrypoint 로 넘긴다.
# - 메타DB 연결 문자열: 비밀번호는 RDS 관리형 시크릿(ECS secrets)에서 온다. 특수문자가 있을 수 있어 URL 인코딩한다.
#   (RDS 비밀번호 로테이션 — 토 09:00~12:00 KST — 뒤에는 새 연결이 실패해 헬스체크가 태스크를 교체하고, 새 태스크가 새 값을 받는다.)
# - UI 비밀번호 파일: SimpleAuthManager 는 파일에 없는 사용자의 비밀번호를 **새로 만들어 파일에 쓴다** — 태스크마다
#   바뀌지 않도록 시크릿 값으로 먼저 채운다.
set -euo pipefail

if [[ -n "${EDGE_AIRFLOW_DB_HOST:-}" ]]; then
  # 파일로 쓰고 Airflow 는 AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_CMD(=cat 이 파일)로 읽는다 — env 로 export 하면
  # 이 entrypoint 를 거치지 않는 프로세스(ECS 헬스체크 = docker exec)가 연결을 못 보고 sqlite 로 떨어진다.
  : "${EDGE_AIRFLOW_DB_PASSWORD:?메타DB 비밀번호(EDGE_AIRFLOW_DB_PASSWORD) 없음}"
  python - <<'PY'
import os
from urllib.parse import quote
e = os.environ
conn = (f"postgresql+psycopg2://{quote(e['EDGE_AIRFLOW_DB_USER'], safe='')}:{quote(e['EDGE_AIRFLOW_DB_PASSWORD'], safe='')}"
        f"@{e['EDGE_AIRFLOW_DB_HOST']}:{e.get('EDGE_AIRFLOW_DB_PORT', '5432')}/{e['EDGE_AIRFLOW_DB_NAME']}?sslmode=require")
with open(os.open("/opt/airflow/sql_alchemy_conn", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fp:
    fp.write(conn)
PY
  unset EDGE_AIRFLOW_DB_PASSWORD
fi

if [[ -n "${AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE:-}" ]]; then
  : "${EDGE_AIRFLOW_ADMIN_PASSWORD:?UI 비밀번호(EDGE_AIRFLOW_ADMIN_PASSWORD) 없음}"
  python - <<'PY'
import json, os
path = os.environ["AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE"]
with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fp:
    json.dump({"admin": os.environ["EDGE_AIRFLOW_ADMIN_PASSWORD"]}, fp)
PY
  unset EDGE_AIRFLOW_ADMIN_PASSWORD
fi

exec /entrypoint "$@"
