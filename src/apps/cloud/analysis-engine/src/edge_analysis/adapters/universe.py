"""전 종목 원장(``entity``·``instrument``) 적재기.

왜 필요한가. 일봉도 산업분류도 ``instrument_id`` 에 FK 가 걸려 있다(V202607290001 의
``fk_instrument_classification_instrument``, V202607150001 의
``fk_price_observation_instrument`` — 둘 다 ``REFERENCES instrument (instrument_id)``).
시드 마이그레이션 V202607150004 는 수집 유니버스 10종만 넣었으므로 그 밖의 종목은 붙을
자리가 없다. 준거집단이 비면 인과 셀이 전부 UNCERTAIN 으로 떨어진다 - 엔진은 정상 동작하는데
결론만 사라지는 실패라 사후에 원인을 찾기 어렵다.

``classification.py`` 의 형제다. 원천 CSV 파서(``read_industry_csv``)와 커넥션
(``connect``)은 그쪽 것을 그대로 쓴다 - 같은 파일을 두 번 파싱하면 두 원장이 서로 다른
티커 집합을 갖게 되고, 그 차이는 미해소 건수로만 드러난다.

파이프라인이 아니라 별도 운영 경로다. 파이프라인은 분석 산출물의 단일 writer 이고
(ADR-0005), 참조 원장 적재는 주기도 트랜잭션 경계도 다르다.

id 는 ``observability.stable_id`` 로 티커에서 결정적으로 만든다. 실행마다 새 id 가 나오면
같은 종목이 두 행이 되고 먼저 적재된 일봉은 아무도 조회하지 않는 id 에 남는다. 모양은
시드와 같은 ``inst_`` + 26자 불투명 서로게이트다(ADR-0027 — 외부 식별자인 티커는 id 가
아니라 속성이다).

범위 밖(의도)
  * ``actor``·``company_profile``·``equity_profile`` — 발행회사는 주식과 별개 엔터티인데
    원천에 회사-주식 관계의 근거가 없다. 일봉·분류의 FK 는 instrument 행만으로 다 풀린다.
  * ``entity.display_name`` — 원천 파서가 회사명을 내지 않아 티커를 그대로 쓴다. 기존
    행은 덮지 않으므로(``DO NOTHING``) 시드가 큐레이션한 '삼성전자 보통주'는 살아남는다.
"""
from __future__ import annotations

import re
from typing import Any

from ..config import PipelineError
from ..observability import log, stable_id
from .classification import normalize_ticker

# 시드(V202607150004:91-101)는 KOSPI·KOSDAQ·ETF 를 모두 MIC 코드 XKRX 로 넣는다. 세부 시장은
# instrument_classification.listing_market 이 들고 있으므로 여기서 시장을 쪼개지 않는다 -
# 쪼개면 XKRX 만 읽는 classification.instrument_index 가 KOSDAQ 종목을 못 찾는다.
_MARKET_CODE = "XKRX"
# ck_instrument_type 은 ('ETF', 'EQUITY') 만 허용한다(V202607150001:145). 원천은 개별주식 맵이다.
_INSTRUMENT_TYPE = "EQUITY"
# ck_instrument_currency_code 는 '^[A-Z]{3}$'(V202607150001:147). KRX 상장 = KRW.
_CURRENCY_CODE = "KRW"
_TICKER_LEN = 6
_SUFFIXES = (".KS", ".KQ")  # FMP·야후 표기: KOSPI=.KS, KOSDAQ=.KQ
# ``str.isdigit()`` 은 전각 숫자('００５９３０')도 참이라 쓰지 않는다 - 그게 통과하면 그
# 문자열로 id 가 만들어져 조회되지 않는 원장 행이 조용히 생긴다.
_TICKER_RE = re.compile(r"[0-9]{1,6}")
_REJECTED_SAMPLE = 10

# entity 를 먼저 넣는다 - fk_instrument_entity 가 (instrument_id, entity_type) →
# entity(entity_id, entity_type) 다(V202607150001:1266-1270). 순서를 바꾸면 FK 위반이다.
# 기존 행은 갱신하지 않는다: 여기 display_name 은 티커라 시드의 사람이 읽는 이름을 덮으면
# 원장 품질이 조용히 나빠진다.
_UPSERT_ENTITY = (
    "INSERT INTO entity (entity_id, entity_type, display_name, status) VALUES %s"
    " ON CONFLICT (entity_id) DO NOTHING"
)
# instrument.entity_type 은 GENERATED ALWAYS STORED 라 INSERT 컬럼에 넣으면 에러다
# (V202607150001:138-139). 충돌 대상은 PK 가 아니라 자연키 uq_instrument_market_ticker
# (V202607150001:149) 다 - 이미 있는 티커에 새 id 를 붙이려 하면 PK 가 아니라 그 UNIQUE 가
# 먼저 걸리기 때문에 PK 를 충돌 대상으로 쓰면 적재가 실패한다.
_UPSERT_INSTRUMENT = (
    "INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type,"
    " currency_code) VALUES %s"
    " ON CONFLICT (market_code, ticker) DO UPDATE SET"
    " instrument_type = EXCLUDED.instrument_type,"
    " currency_code = EXCLUDED.currency_code"
    " RETURNING (xmax = 0)"
)


def bare_ticker(symbol: str) -> str:
    """원천 심볼 → 원장 티커. ``'005930.KS'`` → ``'005930'``.

    형식이 예상 밖이면 조용히 통과시키지 않고 실패한다. 통과시키면 그 문자열로
    ``instrument_id`` 가 만들어지고, 그건 FK 오류가 아니라 **원장 오염**이다 - 아무도
    조회하지 않는 종목 행이 생기고 일봉이 거기 붙는다.

    앞 0 은 채운다(``5930`` → ``005930``). 엑셀을 거친 내보내기가 앞 0 을 먹는 건 알려진
    변형이라 ``classification.normalize_ticker`` 도 같게 다룬다.
    """
    text = str(symbol or "").strip().upper()
    for suffix in _SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    if not _TICKER_RE.fullmatch(text):
        raise PipelineError(f"unexpected KR ticker: {symbol!r}")
    return text.zfill(_TICKER_LEN)


def _existing_ids(conn, market_code: str) -> dict[str, str]:
    """ticker → instrument_id(해당 시장만).

    ``classification.instrument_index`` 를 쓰지 않는다 - 그쪽은 XKRX 를 상수로 박아
    ``--market-code`` 를 받는 이 경로에서는 다른 시장의 기존 행을 놓친다. 놓치면 새 id 를
    만들어 자연키 UNIQUE 에 걸리고, 그 앞서 넣은 entity 행이 고아로 남는다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, instrument_id FROM instrument WHERE market_code = %s",
            (market_code,),
        )
        return {normalize_ticker(ticker): str(iid) for ticker, iid in cur.fetchall()}


def load_universe(
    conn,
    rows: list[dict[str, Any]],
    *,
    source: str,
    data_version: str,
    market_code: str = _MARKET_CODE,
) -> dict[str, int]:
    """``entity`` + ``instrument`` UPSERT. **커밋은 호출자 몫**이다.

    한 스냅샷은 한 트랜잭션이어야 한다 - 중간 커밋은 instrument 없는 entity(고아)나 분류가
    붙을 수 없는 절반짜리 유니버스를 남긴다.

    이미 있는 티커는 **기존 id 를 그대로 쓴다**. 새로 만들면 자연키 UNIQUE 에 걸리거나,
    걸리지 않는 시장에서는 시드가 큐레이션한 행과 갈라져 일봉이 둘로 나뉜다.

    ``source``·``data_version`` 은 로그에만 남는다 - entity·instrument 에는 출처 컬럼이
    없다(V202607150001:80-92·136-153). 그래도 받는 이유는 "이 유니버스가 어느 스냅샷에서
    왔나"가 원장 질의보다 먼저 답해져야 하기 때문이다.

    신규/기존은 ``xmax = 0`` 으로 가른다. upsert 의 ``rowcount`` 는 둘을 구분하지 못해
    "적재는 됐는데 왜 늘지 않았나"를 답할 수 없다.

    Returns:
        loaded(신규)·updated(기존)·unresolved(티커 형식 거부)·duplicate(배치 내 중복) 건수.
    """
    from psycopg2.extras import execute_values

    existing = _existing_ids(conn, market_code)

    # 같은 (market_code, ticker) 가 배치에 두 번 오면 Postgres 가 "cannot affect row a
    # second time" 로 배치 전체를 거부한다. 티커로 접어 마지막 행이 이기게 하고 건수로 남긴다.
    values: dict[str, tuple] = {}
    rejected: list[str] = []
    duplicate = 0
    for row in rows:
        try:
            ticker = bare_ticker(row.get("ticker"))
        except PipelineError:
            # 한 행의 형식 오류로 전 종목 적재를 통째로 버리지 않는다. 대신 센다 - 조용히
            # 건너뛰면 원장이 왜 짧은지 사후에 알 수 없다.
            rejected.append(str(row.get("ticker") or ""))
            continue
        if ticker in values:
            duplicate += 1
        values[ticker] = (
            existing.get(ticker) or stable_id("inst", market_code, ticker),
            market_code, ticker, _INSTRUMENT_TYPE, _CURRENCY_CODE,
        )

    counts = {"loaded": 0, "updated": 0, "unresolved": len(rejected), "duplicate": duplicate}
    if values:
        batch = list(values.values())
        with conn.cursor() as cur:
            execute_values(
                cur, _UPSERT_ENTITY,
                [(row[0], "INSTRUMENT", row[2], "ACTIVE") for row in batch],
            )
            returned = execute_values(cur, _UPSERT_INSTRUMENT, batch, fetch=True) or []
        counts["loaded"] = sum(1 for row in returned if row[0])
        counts["updated"] = len(returned) - counts["loaded"]
    if rejected:
        # 표본을 함께 남긴다 - 건수만 있으면 원천 티커 형식이 바뀐 건지 잡음 행인지 모른다.
        log("universe.rejected", count=len(rejected),
            sample=sorted(set(rejected))[:_REJECTED_SAMPLE])
    return counts
