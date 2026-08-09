"""벤더 무관 분봉 정규형 — 토스·KIS 가 같은 축, 같은 불변식으로 착지한다(ALPHA-735).

토스 어댑터에서 떼어냈다. 벤더가 둘이 되면서 정합 검사가 두 벌이 되면 한쪽만 고쳐지고
그 드리프트를 canonical 이 그대로 받는다 — **무엇을 유효한 봉으로 보는가**는 여기 한 곳이고,
각 어댑터는 자기 응답 필드를 이 형으로 옮기는 매핑만 갖는다.

⚠️ 두 벤더 다 timestamp 가 **구간의 끝**이다(토스 `timestamp`·KIS `stck_cntg_hour` 실측).
원장 window 는 `[window_start, window_end)` 라 `window_start = 끝 − interval` 로 잡는다 —
뒤집으면 전 구간이 한 칸 밀린 채 조용히 커밋된다(봉 수는 그대로라 어떤 게이트도 안 걸린다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

# 가격·거래량 크기 상한 — 소스가 지수표기 거대값을 줘도 artifact 에 싣지 않는다
MAX_MAGNITUDE = Decimal("1e15")


@dataclass(frozen=True)
class Candle:
    """분봉 하나 — 원장이 쓰는 축으로 정규화한 형태."""

    symbol: str
    window_start: datetime
    window_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    currency: str | None

    @property
    def traded(self) -> bool:
        """거래가 있었나 — 거래량 0 은 정상 응답이고 no_trade 다(missing 이 아니다)."""
        return self.volume > 0


def is_stamp(raw: object, length: int) -> bool:
    """벤더 날짜·시각 문자열이 **정확히 `length` 자리 ASCII 숫자**인가.

    `strptime` 을 믿으면 안 되는 세 가지를 한 곳에서 막는다 — 셋 다 값은 **맞게** 읽혀서
    포맷 변경이 조용히 흡수된다(그래서 값이 아니라 **형상 변화의 신호**를 지키는 검사다):

    - 자리수 부족 — 연접 파싱(`"%Y%m%d%H%M%S"`)이라 한쪽이 짧으면 다른 쪽 자리를 훔친다
      (`"20260807"+"1030"` → 10:03:00).
    - 공백 패딩 — `%d` 의 `" [1-9]"` 로 `"202608 3"` 이 통과한다. `%H` 의 `" \\d"` 는
      **버전에 달렸다**(실측: 런타임 이미지 3.12 에 없고 3.14 에 있다).
    - 비-ASCII 숫자 — `\\d` 가 유니코드 Nd 라 `"٢٠٢٦0803"`·`"1٠3000"` 이 통과한다.

    ⚠️ 술어 셋이 **서로 다른 문**을 막는다. 공백은 ASCII 라 `isascii()` 로 못 막고,
    비-ASCII 숫자는 `isdecimal()` 이 True 라 그걸로 못 막는다. 하나만 두면 나머지가 샌다.

    ⚠️ **여기 한 곳인 이유**: 같은 규칙이 파서 둘·창 필터 둘에 흩어져 있었고, 파서에만
    술어를 더했다가 필터 둘이 뒤처져 그 틈으로 하루가 조용히 절단됐다(Codex P2, #647).
    """
    return isinstance(raw, str) and len(raw) == length and raw.isascii() and raw.isdecimal()


def to_decimal(raw: object, field_name: str, symbol: str) -> Decimal:
    """문자열 숫자를 Decimal 로. float 를 거치지 않는다 — 정밀도가 깨진다.

    두 벤더 다 OHLCV 를 **문자열**로 준다(실측).
    """
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise ValueError(f"{symbol} 캔들의 {field_name} 형이 예상 밖이다: {raw!r}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as error:
        raise ValueError(f"{symbol} 캔들의 {field_name} 를 못 읽는다: {raw!r}") from error
    if not value.is_finite():
        raise ValueError(f"{symbol} 캔들의 {field_name} 가 유한하지 않다: {raw!r}")
    if abs(value) >= MAX_MAGNITUDE:
        # `1E+999` 같은 값도 Decimal 로는 유한하다 — 여기서 안 막으면 artifact 에 실린
        # 뒤 NUMERIC 저장이나 분석 단계에서야 터진다(그때는 어느 window 인지 못 찾는다)
        raise ValueError(f"{symbol} 캔들의 {field_name} 가 범위를 벗어났다: {raw!r}")
    return value


def build_candle(
    symbol: str,
    *,
    window_end: datetime,
    span_seconds: int,
    values: dict[str, Decimal],
    volume: Decimal,
    currency: str | None = None,
) -> Candle:
    """정합을 통과한 봉 하나. 위반은 즉시 raise 한다(조용한 기본값 금지, Rule 12).

    `values` 는 open/high/low/close 네 키다. 조용히 0·None 으로 접히면 그 window 가
    '정상 수집'으로 커밋되고 나중에 아무도 그 자리를 다시 보지 않는다.
    """
    if window_end.tzinfo is None or window_end.tzinfo.utcoffset(window_end) is None:
        # naive 가 오면 우리가 시간대를 골라야 하는데, 그 추측이 한 칸 밀린 커밋을 만든다
        raise ValueError(f"{symbol} 캔들 시각에 오프셋이 없다: {window_end!r}")
    if volume < 0:
        raise ValueError(f"{symbol} 캔들 거래량이 음수다: {volume}")
    if min(values.values()) <= 0:
        # OHLC **상호관계**만 보면 전부 -1 인 행이 통과한다(레포 공통 게이트의
        # non_positive_price 불변식과 같은 축)
        raise ValueError(f"{symbol} 캔들 가격이 양수가 아니다: {values}")
    if not (values["low"] <= values["open"] <= values["high"]
            and values["low"] <= values["close"] <= values["high"]):
        # OHLC 정합은 소스가 깨질 수 있는 축이다 — 통과시키면 canonical 이 그대로 받는다
        raise ValueError(f"{symbol} 캔들 OHLC 정합 위반: {values}")
    return Candle(
        symbol=symbol,
        # ts 는 구간의 **끝**이다 — window_start 는 그 interval 만큼 앞이다
        window_start=window_end.__class__.fromtimestamp(
            window_end.timestamp() - span_seconds, tz=window_end.tzinfo
        ),
        window_end=window_end,
        volume=volume,
        currency=currency,
        **values,
    )
