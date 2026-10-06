# db-query

VPC 안에서 PostgreSQL을 조회하는 일회성 ECS 태스크용 명령입니다. v1 분석엔진의 조회 기능을 분리했습니다. 분석·DuckDB·적재 코드는 포함하지 않습니다.

```bash
uv run --package db-query python -m edge_db_query --sql "SELECT 1 AS ok"
```

- 접속: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSCHEMA`.
- 기존과 동일하게 `agent_ro` 역할과 읽기전용 트랜잭션 기본값으로 접속합니다. 접속 기본값 자체가 변경 불가능한 보안 경계는 아닙니다.
- 출력: 조회 감사 이벤트와 행별 JSON Lines. 날짜·소수는 문자열로 표시합니다.
- 제한: 기본 2,000행·30초. `EDGE_QUERY_ROW_CAP`, `EDGE_QUERY_TIMEOUT_MS`로 조정합니다.
- SQL은 SELECT 하위 쿼리로 감쌀 수 있어야 합니다. 기존 가드는 SHOW·EXPLAIN도 허용하지만 실행 시 거부되므로 SELECT를 사용합니다.
- `deploy-db-query`는 이미지 게시만 수행합니다. 조회 태스크 정의(`infra/terraform/modules/db-query`)는 이미 이 이미지(`db-query-latest`)와 `edge_db_query` 진입점을 씁니다.

검증: 단위 테스트는 `uv run --package db-query pytest apps/cloud/db-query/tests/test_readonly.py -q`. 실제 PostgreSQL 검사는 CI의 일회용 DB에서 실행하며, 운영 DB에서는 실행하지 않습니다.
