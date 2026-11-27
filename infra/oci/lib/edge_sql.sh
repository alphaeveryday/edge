#!/usr/bin/env bash
# SQL 한 문장을 파이프라인 이미지 안에서 실행한다. 결과 행은 탭으로 구분해 출력한다.
#
#   edge_sql.sh '<SQL — 값 자리는 %s>' [값…]
#
# 호스트에 psql 과 DB 접속값 사본을 따로 두지 않으려고 이미지의 psycopg 와 적재 스텝이 쓰는 DB 설정
# (env 파일의 DATA_PIPELINE_DB__*)을 그대로 쓴다. 레인의 성공 행 기록과 감시의 조회가 이 스크립트를 부른다.
set -u
ENV_FILE="${EDGE_ENV_FILE:-/etc/edge/batch.env}"
IMAGE="$(sed -n 's/^EDGE_PIPELINE_IMAGE=//p' "$ENV_FILE" | tail -n 1)"
[ -n "$IMAGE" ] || { echo "$ENV_FILE 에 EDGE_PIPELINE_IMAGE 가 없다" >&2; exit 1; }

exec docker run --rm --env-file "$ENV_FILE" --entrypoint python "$IMAGE" -c '
import sys
from data_pipeline.config import load_settings
from data_pipeline.db import connect, db_config_from_env

with connect(db_config_from_env(load_settings().db)) as conn:
    cursor = conn.execute(sys.argv[1], sys.argv[2:])
    for row in cursor.fetchall() if cursor.description else ():
        print(*row, sep="\t")
' "$@"
