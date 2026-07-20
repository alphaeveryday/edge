"""ETF 프로필 게이트 (ALPHA-462 / ETF 마스터 정제).

정규화된 ETF 프로필 행이 **마스터를 만들 최소 요건**을 갖췄는지 검사한다. 이 canonical 의
소비자는 `load_instruments` 이고, 그 스텝은 `entity`·`instrument` 행을 만든다 — 즉 여기서
통과시킨 행은 곧 **마스터가 된다**. 잘못된 행 하나가 엉뚱한 ETF 마스터를 만들면 그 ID 를
참조하는 NAV·구성종목·트리거가 전부 그 위에 쌓이므로, 다른 게이트보다 보수적으로 막는다.

전 사유가 blocking 이다 — 참고 필드가 없다. 이름이 없으면 `entity.display_name`(NOT NULL)을
채울 수 없고, 시장·티커가 없으면 자연키 `(market_code, ticker)` 를 만들 수 없다.
"""

from __future__ import annotations

BLOCKING_REASONS_ETF_PROFILE = frozenset(
    {"missing_market", "unsupported_market", "missing_etf_id",
     "missing_display_name", "not_an_etf"}
)

# ETF 마스터는 국내만 만든다(ADR-0024 MVP=국내 ETF, 수집도 KIS 국내 종목정보).
_SUPPORTED_MARKETS = frozenset({"KR"})


def _blank(value: object) -> bool:
    """사실상 빈 값인가 — None·비문자열·공백만 문자열."""
    return not (isinstance(value, str) and value.strip())


def validate_etf_profile(row: dict) -> list[str]:
    """정규화된 ETF 프로필 행 검사. 위반 사유 코드 리스트(정상=[]).

    사유(전부 수집, 결정적 순서):
      - missing_market / unsupported_market : market 결측/미지원(KR 밖)
      - missing_etf_id      : etf_id(자연키의 ticker) 결측/공백
      - missing_display_name: 표시명 결측 — entity.display_name 이 NOT NULL 이라 마스터 불가
      - not_an_etf          : 상품구분이 ETF 가 아님. 유니버스 오설정(주식 코드가 etf_map 에
                              들어온 경우)을 **마스터를 만들기 전에** 잡는다 — 잘못 만들면
                              instrument_type='ETF' 인 가짜 ETF 가 생기고 그 위에 NAV·구성종목이
                              쌓인다. 되돌리려면 FK 참조를 전부 걷어내야 한다.
    """
    reasons: list[str] = []

    market = row.get("market")
    if _blank(market):
        reasons.append("missing_market")
    elif market not in _SUPPORTED_MARKETS:
        reasons.append("unsupported_market")

    if _blank(row.get("etf_id")):
        reasons.append("missing_etf_id")
    if _blank(row.get("display_name")):
        reasons.append("missing_display_name")

    product_class = row.get("product_class")
    if not (isinstance(product_class, str) and product_class.strip().upper() == "ETF"):
        reasons.append("not_an_etf")

    return reasons
