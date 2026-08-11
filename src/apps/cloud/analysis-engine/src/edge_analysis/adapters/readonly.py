"""읽기전용 원장 질의 — 시크릿 주입 접속, 서버가 강제하는 읽기전용.

에이전트가 클라우드 원장을 확인할 경로다. private RDS 는 VPC 밖에서 닿지 않으므로 VPC
내부 ECS one-off task 안에서 이 모듈이 돈다(Flyway 가 같은 제약을 푸는 방식과 같다).

**안전을 정규식으로 주장하지 않는다.** 층은 아래 둘이고, 첫째는 애플리케이션 밖이다:

1. 접속 옵션 ``default_transaction_read_only=on`` — Postgres 가 쓰기 트랜잭션 자체를
   거부한다. 세션이 아니라 접속 파라미터라 이후 어떤 SQL 도 되돌릴 수 없다.
2. 아래 ``guard`` — SELECT 가 아닌 것을 **에러 메시지가 읽히는 형태로** 되돌린다.
   2번은 안전장치가 아니라 사용성이다. 안전은 1 이 담당한다.

접속 자격증명은 컨테이너 secrets 로 주입되는 ``PGPASSWORD``(Secrets Manager)다.
원설계는 ``agent_ro`` 롤 + RDS IAM 토큰(rds-db:connect)의 비밀번호 없는 3층이었으나,
**조직 SCP 가 rds-db:connect 를 explicitDeny** 해 이 계정에서 IAM DB 인증이 불가하다
(ALPHA-933, 2026-08-11 simulate-principal-policy 실측). ``agent_ro`` 의 읽기전용
GRANT(V202607300001)는 DB 에 남아 있다 — SCP 가 풀리면 ``PGPASSWORD`` 를 빼는 것만으로
아래 IAM 토큰 폴백(``auth_token``)이 되살아난다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

from ..config import PgConfig, PipelineError
from ..observability import log

# 상한의 기본값. 컨테이너 env 로 덮는다(task-def 가 EDGE_QUERY_* 를 준다).
_ROW_CAP = 2000
_TIMEOUT_MS = 30_000

# SELECT/WITH 로 시작하지 않으면 되돌린다. 대소문자·선행 공백·주석을 먼저 벗긴다 -
# `/* x */ DELETE` 를 통과시키지 않기 위해서다.
_LEADING_NOISE = re.compile(r"(?:\s+|--[^\n]*\n|/\*.*?\*/)+", re.DOTALL)
_READ_PREFIX = re.compile(r"^(?:select|with|table|explain|show)\b", re.IGNORECASE)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise PipelineError(f"invalid integer {name}={raw!r}") from exc
    if value <= 0:
        raise PipelineError(f"{name} must be positive, got {value}")
    return value


def guard(sql: str) -> str:
    """읽기 질의만 통과시키고 정규화한 SQL 을 돌려준다.

    Raises:
        PipelineError: 빈 질의, 읽기 아님, 또는 문장이 여럿.
    """
    text = sql.strip()
    if not text:
        raise PipelineError("empty query")
    # 후행 세미콜론 하나는 손으로 붙이기 쉬우니 받아준다. 그 이상은 문장 쌓기다.
    text = text.rstrip().removesuffix(";").rstrip()
    if ";" in _strip_literals(text):
        raise PipelineError("multiple statements are not allowed; send one query")
    head = _LEADING_NOISE.sub("", text, count=1) if _LEADING_NOISE.match(text) else text
    if not _READ_PREFIX.match(head):
        verb = head.split(None, 1)[0] if head.split() else head
        raise PipelineError(f"read-only path accepts SELECT/WITH/TABLE/EXPLAIN/SHOW, got {verb!r}")
    return text


def _strip_literals(sql: str) -> str:
    """문자열·식별자 리터럴을 비운다 — 리터럴 안 세미콜론을 문장 구분으로 오해하지 않게."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"":
            i += 1
            while i < n:
                if sql[i] == ch:
                    # 이어붙인 따옴표('' 또는 "")는 리터럴 내부의 이스케이프다.
                    if i + 1 < n and sql[i + 1] == ch:
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def auth_token(pg: PgConfig, *, region: str | None = None) -> str:
    """RDS IAM 인증 토큰(15분 유효). 태스크 역할의 서명으로 만들며 비밀은 오가지 않는다."""
    import boto3

    region = region or os.environ.get("AWS_REGION")
    if not region:
        raise PipelineError("AWS_REGION is required to sign an RDS auth token")
    client = boto3.client("rds", region_name=region)
    return client.generate_db_auth_token(
        DBHostname=pg.host, Port=pg.port, DBUsername=pg.user, Region=region,
    )


def connect_readonly(pg: PgConfig, *, timeout_ms: int | None = None):
    """읽기전용 커넥션. ``PGPASSWORD`` 가 없으면 IAM 토큰으로 붙는다.

    ``options`` 로 넘기는 두 값이 이 함수의 요점이다. 세션이 아니라 **접속 파라미터**로
    박아야 이후 어떤 SQL 도 이 설정을 되돌릴 수 없다.
    """
    import psycopg2

    timeout = timeout_ms if timeout_ms is not None else _int_env("EDGE_QUERY_TIMEOUT_MS", _TIMEOUT_MS)
    password = pg.password or auth_token(pg)
    # IAM 인증은 TLS 를 요구한다. verify-full 이 더 낫지만 RDS CA 번들을 이미지에 실어야
    # 하므로 기본은 require 로 두고 PGSSLMODE 로 올릴 수 있게 남긴다. 로컬 Postgres 는
    # SSL 이 꺼져 있어 disable 이 필요하다 - 그래서 값을 코드에 박지 않는다.
    conn = psycopg2.connect(
        host=pg.host,
        port=pg.port,
        dbname=pg.dbname,
        user=pg.user,
        password=password,
        sslmode=os.environ.get("PGSSLMODE", "require"),
        options=f"-c default_transaction_read_only=on -c statement_timeout={timeout}",
    )
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {pg.schema}")
    return conn


def run_query(conn, sql: str, *, row_cap: int | None = None) -> tuple[list[str], list[tuple]]:
    """가드를 통과한 질의를 상한 안에서 실행한다.

    상한은 SQL 을 파싱해 LIMIT 를 끼우지 않고 **감싼다**. 파싱은 틀리고, 틀린 파서는
    상한을 조용히 빠뜨린다. 감싸면 안쪽이 무엇이든 상한이 걸린다.
    """
    cap = row_cap if row_cap is not None else _int_env("EDGE_QUERY_ROW_CAP", _ROW_CAP)
    inner = guard(sql)
    started = time.monotonic()
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM ({inner}) AS _capped LIMIT {cap}")
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    # 질의 전문을 남긴다. 감사 로그가 요약이면 무엇을 봤는지 사후에 알 수 없다.
    log("query.done", sql=inner, rows=len(rows), capped=len(rows) >= cap, ms=elapsed_ms)
    return columns, rows


def emit(columns: list[str], rows: list[tuple], *, stream: Any = None) -> None:
    """행을 JSON Lines 로 낸다 — CloudWatch 에서 한 행이 한 이벤트로 잡히게."""
    out = stream if stream is not None else sys.stdout
    for row in rows:
        # date·Decimal 은 JSON 이 모른다. 에이전트가 읽는 게 목적이니 문자열로 떨어뜨린다.
        out.write(json.dumps(dict(zip(columns, row, strict=True)), ensure_ascii=False,
                             default=str) + "\n")
