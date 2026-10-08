"""업종지수 일봉 fact 게이트 (ALPHA-1254).

정체성(market·code)·시간축(trade_date)은 NAV 게이트(quality/etf_nav.py)와 같은 규칙이고, 값은
가격 게이트 `validate_ohlcv` 의 봉 정합성을 그대로 쓴다 — 지수 봉도 고가가 시가·종가보다 낮을
수 없다. 참고 필드가 없어 전 사유가 blocking 이다.

입력 계약: open/high/low/close 는 정규화가 finite-or-None 으로 정리한 값이다(NAV 와 같은
변환 — 콤마·대시·NaN/Inf·bool 차단). None 이 하나라도 있으면 봉 정합성은 보지 않는다.
"""

from __future__ import annotations

from .etf_nav import MIN_TRADE_DATE
from .price import validate_ohlcv

BLOCKING_REASONS_SECTOR_INDEX_DAILY = frozenset(
    {"missing_market", "unsupported_market", "missing_code", "bad_code",
     "missing_trade_date", "bad_trade_date", "missing_price",
     "non_positive_price", "high_lt_low", "high_not_max", "low_not_min"}
)

# KIS 업종 일봉은 KRX 지수만 준다(수집도 KR 고정).
_SUPPORTED_MARKETS = frozenset({"KR"})
_PRICE_FIELDS = ("open", "high", "low", "close")


def validate_sector_index_daily(row: dict, *, max_trade_date: str) -> list[str]:
    """정규화된 업종지수 일봉 행의 정체성·시간축·값 검사. 위반 사유 코드 리스트(정상=[]).

    사유(전부 수집, 결정적 순서):
      - missing_market / unsupported_market : market 결측/미지원(KR 밖)
      - missing_code / bad_code : KRX 업종코드 결측 / 숫자 4자리가 아님(config 검증과 같은 형태)
      - missing_trade_date / bad_trade_date : 거래일 정규화 실패 / [MIN, max] 밖
      - missing_price : O/H/L/C 중 결측 또는 수치 변환 실패
      - non_positive_price · high_lt_low · high_not_max · low_not_min : 가격 게이트와 같다
    """
    reasons: list[str] = []

    market = row.get("market")
    if not (isinstance(market, str) and market.strip()):
        reasons.append("missing_market")
    elif market not in _SUPPORTED_MARKETS:
        reasons.append("unsupported_market")

    code = row.get("code")
    if not (isinstance(code, str) and code.strip()):
        reasons.append("missing_code")
    elif not (len(code) == 4 and code.isdigit()):
        reasons.append("bad_code")

    trade_date = row.get("trade_date")
    if not (isinstance(trade_date, str) and trade_date.strip()):
        reasons.append("missing_trade_date")
    elif not (MIN_TRADE_DATE <= trade_date[:10] <= max_trade_date):
        reasons.append("bad_trade_date")

    if any(row.get(f) is None for f in _PRICE_FIELDS):
        reasons.append("missing_price")
    else:
        # 지수 거래량은 canonical 에 싣지 않는다 — 봉 정합성만 본다. volume=0 은 가격 게이트의
        # 입력 계약을 채우는 자리일 뿐이라 negative_volume 은 여기서 나올 수 없다.
        reasons.extend(validate_ohlcv({**row, "volume": 0}))
    return reasons
