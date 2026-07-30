"""Cloud Event Store(Postgres) 커넥션 — 적재 스텝(load-*)의 유일한 DB 경계 (ALPHA-372).

레이크(`lake/storage.py`)가 S3 경로 규약의 SSOT 이듯, 이 모듈이 **DB 접속의 SSOT** 다.
스텝은 여기서 커넥션을 받아 쓰고 접속 문자열을 직접 조립하지 않는다.

**ID 발번 규약은 ADR-0027** — 도메인 ID 는 `<타입접두사>_<ULID>` 불투명 서로게이트다. 외부
식별자(ticker·dart_corp_code)를 ID 에 인코딩하지 않는다: 티커는 회사명이 바뀌면 함께 바뀌고
죽은 티커는 재사용되므로, 파생 ID 는 영구히 잘못된 ID 로 남거나 참조 전부를 마이그레이션하게
만든다. 서로게이트는 `instrument.ticker` 한 컬럼만 UPDATE 하면 참조가 전부 살아 있다.

**예외 — 결정적 계열**(`stable_domain_id`): document/assertion/event/thread 계보 ID 는 자연키
해시라 ULID 가 아니고 **시간 정렬이 안 된다**(hex 26자). ADR 이 자연키 파생을 배격한 이유는
*가변 외부 식별자*(티커) 인코딩이었는데 이 계열의 재료는 전부 내부·불변값이라 그 위험이
없고, 불투명성은 유지된다(해시라 파싱해도 의미가 안 나온다). 대신 **같은 사건이 재실행·다른
writer 에서도 같은 ID 로 수렴**하는 걸 얻는다. 시간 정렬이 필요하면 `available_at` 을 쓴다.

psycopg 는 **지연 import** 한다 — 레이크만 쓰는 스텝(수집·정제)과 그 단위테스트가 DB 드라이버
없이 돌아야 한다(pyarrow·boto3 와 같은 관례).
"""

from __future__ import annotations

import hashlib
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


# ⚠️ 결정적 ID 의 네임스페이스 — 분석엔진 daily_pipeline.PIPELINE_ID 와 **동일해야 한다**.
# 다르면 같은 사건이 다른 ID 로 갈려 이행기 멱등 수렴이 깨진다(steps/assemble_events 독스트링).
PIPELINE_ID = "alphamale-etf-daily-v1"


def stable_domain_id(prefix: str, *parts: object) -> str:
    """자연키에서 **결정적으로** 파생하는 도메인 ID — 같은 재료면 언제 어디서 불러도 같은 값.

    `domain_id`(랜덤 ULID)와 쓰임이 갈린다. 행의 정체성이 **이미 자연키로 정해져 있는데**
    서로게이트가 랜덤이면, 그 ID 를 재료로 삼는 다운스트림 ID 까지 랜덤을 상속한다. 특히
    같은 테이블에 writer 가 둘일 때(load-assertions·assemble-events 의 `document_assertion`)
    산식이 갈리면 먼저 쓴 쪽 값이 남아 "결정적 ID" 계약이 조용히 깨진다(ALPHA-456).

    그래서 **두 writer 는 반드시 이 함수를 공유해야 한다** — 각자 같은 모양의 해시를 따로
    구현하면 salt 나 구분자 하나만 어긋나도 같은 자연키에 다른 ID 가 나온다.

    `\\u0001` 구분자는 재료 경계를 고정한다(`("ab","c")` 와 `("a","bc")` 가 같은 문자열로
    합쳐지지 않게). 26자는 ULID 길이와 맞춘 것으로, 두 계열이 컬럼 폭을 공유한다.
    """
    material = "".join([PIPELINE_ID, *(str(p) for p in parts)])
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:26]}"


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


def ensure_etf_profile(conn, instrument_id: str) -> bool:
    """ETF 계열 FK 의 선행 행(`etf_profile`)을 보장한다. 새로 만들었으면 True.

    **여기 있는 이유는 순서 의존을 없애기 위해서다.** `etf_nav_daily`·`etf_holding_snapshot`·
    `price_movement_trigger` 가 전부 `etf_profile(instrument_id)` 를 참조하는데, 이들을 쓰는
    로더는 SFN feature 페이즈의 **같은 Parallel 브랜치들**이라 실행 순서가 없다. 한 스텝만
    프로필을 만들면 다른 스텝이 먼저 도는 런에서 FK 위반으로 비결정적으로 실패한다
    (edge-review 지적). 각 스텝이 자기 선행을 스스로 보장하면 순서가 무관해진다 —
    `ON CONFLICT DO NOTHING` 이라 동시에 들어와도 늦은 쪽이 False 를 받을 뿐이다.

    `etf_type` 은 채우지 않는다 — 허용 어휘가 미확정이라 ALPHA-378 이 NOT NULL 을 풀었고,
    임의 값을 넣으면 그게 사실상 계약이 돼 나중에 적재분이 오염된다. 다른 프로필 속성도
    canonical 에 없어 비워 둔다(프로필 채우기는 별건 — 여기 일은 FK 를 세우는 것뿐이다).
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO etf_profile (instrument_id) VALUES (%s)"
            " ON CONFLICT (instrument_id) DO NOTHING",
            (instrument_id,),
        )
        return cur.rowcount > 0
