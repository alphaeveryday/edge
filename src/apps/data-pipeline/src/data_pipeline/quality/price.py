"""가격 OHLCV 정합성 게이트 (ALPHA-133 / S032).

정규화된 일봉 행 하나가 **물리적으로 성립하는 봉인지** 검사한다 — high 가 최고가고
low 가 최저가며 가격이 양수인지 등. 잘못된 봉이 canonical 로 흘러 분석을 오염시키지
않도록 막는 게이트다(스토리: "잘못된 가격 데이터가 분석에 사용되지 않도록").

이 함수는 **벤더 무관 순수 함수**다 — 입력은 이미 정규화(문자열→수치)된 행이라
open/high/low/close/volume 이 수치(None 아님)라고 가정한다. 결측·비수치(schema drift)
판정은 그 이전 정규화 단계가 `missing_field`·`non_numeric` 사유로 따로 잡는다.

사유는 **전부 수집**한다(첫 실패에서 멈추지 않음) — 어떤 규칙들이 왜 깨졌는지 다
드러나야 운영에서 소스 문제를 추적한다(AGENTS Rule 12: fail loud).
"""

from __future__ import annotations

# 정규화 표준행에서 게이트가 보는 가격 4필드(양수여야 하고 정합 관계를 이룬다).
_PRICE_FIELDS = ("open", "high", "low", "close")


def validate_ohlcv(row: dict) -> list[str]:
    """정규화된 일봉 행의 OHLCV 물리 정합성 검사. 위반 사유 코드 리스트(정상=[]).

    입력 계약: open/high/low/close/volume 은 정규화로 수치가 보장된 값(None 아님).
    결측·비수치는 호출 전 정규화가 잡으므로 여기서 재검하지 않는다(단일 책임).

    사유(전부 수집, 결정적 순서):
      - non_positive_price : O/H/L/C 중 0 이하 (가격은 양수여야 함)
      - negative_volume    : 거래량 < 0 (거래정지일 0 은 정상 — 음수만 위반)
      - high_lt_low        : high < low (고가가 저가보다 낮음 — 물리적 모순)
      - high_not_max       : high < max(open, close) (고가가 시/종가보다 낮음)
      - low_not_min        : low > min(open, close) (저가가 시/종가보다 높음)
    """
    o, h, l, c = (row["open"], row["high"], row["low"], row["close"])
    reasons: list[str] = []

    if any(row[f] <= 0 for f in _PRICE_FIELDS):
        reasons.append("non_positive_price")
    if row["volume"] < 0:
        reasons.append("negative_volume")
    if h < l:
        reasons.append("high_lt_low")
    if h < max(o, c):
        reasons.append("high_not_max")
    if l > min(o, c):
        reasons.append("low_not_min")
    return reasons
