"""Cloud Event Store(Postgres) 커넥션 — 적재 스텝(load-*)의 유일한 DB 경계 (ALPHA-372).

레이크(`lake/storage.py`)가 S3 경로 규약의 SSOT 이듯, 이 모듈이 **DB 접속의 SSOT** 다.
스텝은 여기서 커넥션을 받아 쓰고 접속 문자열을 직접 조립하지 않는다.

**ID 발번 규약은 ADR-0027** — 도메인 ID 는 `<타입접두사>_<ULID>` 불투명 서로게이트다. 외부
식별자(ticker·dart_corp_code)를 ID 에 인코딩하지 않는다: 티커는 회사명이 바뀌면 함께 바뀌고
죽은 티커는 재사용되므로, 파생 ID 는 영구히 잘못된 ID 로 남거나 참조 전부를 마이그레이션하게
만든다. 서로게이트는 `instrument.ticker` 한 컬럼만 UPDATE 하면 참조가 전부 살아 있다.

psycopg 는 **지연 import** 한다 — 레이크만 쓰는 스텝(수집·정제)과 그 단위테스트가 DB 드라이버
없이 돌아야 한다(pyarrow·boto3 와 같은 관례).
"""

from __future__ import annotations

import os
import secrets
import time
from contextlib import contextmanager

from .config import DbConfig

# Crockford Base32 — ULID 표준 알파벳(I·L·O·U 제외: 눈으로 헷갈리는 글자를 뺐다).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _b32(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_ulid() -> str:
    """ULID 1건 — 48비트 밀리초 타임스탬프(10자) + 80비트 난수(16자) = 26자.

    시간 정렬이 되는 게 UUIDv4 대비 이점이다(인덱스 지역성·로그를 눈으로 훑기). 난수는
    `secrets` 로 뽑는다 — ID 가 예측 가능할 이유가 없다.
    """
    return _b32(int(time.time() * 1000), 10) + _b32(secrets.randbits(80), 16)


def domain_id(prefix: str) -> str:
    """ADR-0027 도메인 ID — `actor_01KXJ…`·`inst_01KXJ…`.

    접두사는 서브타입을 눈으로 식별하게 한다(엔터티 서브타입 테이블은 같은 값을 공유하므로
    `entity_id` 만 봐도 ACTOR 인지 INSTRUMENT 인지 안다).
    """
    return f"{prefix}_{new_ulid()}"


def db_config_from_env(base: DbConfig | None) -> DbConfig:
    """설정 + env 로 DbConfig 를 확정한다. 미설정이면 fail-loud.

    `load_settings()` 가 `DATA_PIPELINE_DB__*` 를 이미 읽으므로 보통 base 로 충분하다. base 가
    없으면(설정에 db 섹션이 없으면) 적재를 조용히 건너뛰지 않고 여기서 드러낸다 — 적재 스텝이
    DB 없이 '성공'으로 끝나면 아무도 모른다(Rule 12).
    """
    if base is None:
        raise SystemExit(
            "db 설정이 없다 — DATA_PIPELINE_DB__HOST/PASSWORD 등을 주입한다(적재 스텝은 DB 필수)"
        )
    return base


@contextmanager
def connect(config: DbConfig):
    """커넥션 컨텍스트. 정상 종료면 commit, 예외면 rollback 한다.

    적재는 **전부 아니면 전무**여야 안전하다 — 부분 커밋은 FK 로 얽힌 마스터를 반쪽 상태로
    남겨 다음 런이 뭘 믿어야 할지 모르게 만든다.
    """
    import psycopg

    conn = psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.name,
        user=config.user,
        password=config.password,
        sslmode=config.sslmode,
        # 배포는 ECS one-off task 라 무한 대기가 곧 좀비 태스크다.
        connect_timeout=int(os.environ.get("DATA_PIPELINE_DB_CONNECT_TIMEOUT", "15")),
    )
    try:
        with conn:
            yield conn
    finally:
        conn.close()
