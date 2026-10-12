"""Read-only operational database queries extracted from analysis v1."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

from .config import PgConfig, PipelineError
from .logging import log

# 상한의 기본값. 컨테이너 env 로 덮는다(task-def 가 EDGE_QUERY_* 를 준다).
_ROW_CAP = 2000
_TIMEOUT_MS = 30_000

# 접속 직후 내려앉을 읽기전용 롤. 상수다 - env 로 두면 RunTask env 오버라이드로
# 강등을 끌 수 있다(connect_readonly 주석 참조).
_QUERY_ROLE = "agent_ro"

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
    """Connect with read-only defaults and the fixed agent_ro role."""
    import psycopg2

    timeout = timeout_ms if timeout_ms is not None else _int_env("EDGE_QUERY_TIMEOUT_MS", _TIMEOUT_MS)
    password = pg.password or auth_token(pg)
    # 마스터 시크릿으로 붙되 권한은 읽기전용 롤로 내려앉는다(ALPHA-933) - SELECT
    # 가드를 통과하는 부작용 함수(pg_terminate_backend 류)를 서버 권한이 막는다.
    # 접속 파라미터라 첫 SQL 이전에 적용된다(GRANT agent_ro TO 마스터, V202608111500).
    # **env 로 열지 않는다** - ECS RunTask 의 ContainerOverride.environment 가 task
    # 정의 env 를 덮을 수 있어, env 기반 강등은 RunTask 권한만으로 무력화된다(봇 P1).
    # 마이그레이션이 안 간 DB(agent_ro 부재·비멤버)에서는 접속이 시끄럽게 실패한다 -
    # 그게 계약이다: 이 경로는 마이그레이션 적용된 원장 전용이다.
    options = (f"-c default_transaction_read_only=on -c statement_timeout={timeout}"
               f" -c role={_QUERY_ROLE}")
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
        options=options,
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
