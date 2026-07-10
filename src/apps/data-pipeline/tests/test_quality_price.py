"""OHLCV 정합성 게이트 테스트 — ALPHA-133/S032: 잘못된 봉이 canonical 로 새지 않게."""

from data_pipeline.quality import validate_ohlcv

# 물리적으로 성립하는 정상 봉 — high=최고, low=최저, 가격 양수, 거래량 비음수.
GOOD = {"open": 9.0, "high": 11.0, "low": 8.5, "close": 10.0, "volume": 100}


def test_valid_bar_passes():
    # WHY: 정상 봉이 게이트에 걸리면 멀쩡한 시세가 분석에서 누락된다 — 통과가 기본.
    assert validate_ohlcv(GOOD) == []


def test_high_below_low_is_physical_contradiction():
    # WHY: high < low 는 봉이 존재할 수 없는 상태다 — 소스 오류이므로 반드시 걸러야
    #      잘못된 수익률·변동성이 다운스트림 모델로 흘러가지 않는다.
    assert "high_lt_low" in validate_ohlcv({**GOOD, "high": 8.0, "low": 9.0})


def test_high_must_be_the_max_of_open_close():
    # WHY: 고가가 시가·종가보다 낮으면 그날 최고가 정의가 깨진 것 — 정합성 위반.
    assert validate_ohlcv({**GOOD, "high": 9.5, "close": 10.0}) == ["high_not_max"]


def test_low_must_be_the_min_of_open_close():
    # WHY: 저가가 시가·종가보다 높으면 최저가 정의가 깨진 것 — 정합성 위반.
    assert validate_ohlcv({**GOOD, "low": 9.5, "open": 9.0}) == ["low_not_min"]


def test_non_positive_price_rejected():
    # WHY: 0/음수 가격은 실재하지 않는다 — 결측을 0으로 채운 소스 오류를 걸러낸다.
    assert "non_positive_price" in validate_ohlcv({**GOOD, "low": 0.0})


def test_zero_volume_allowed_but_negative_rejected():
    # WHY: 거래정지·비유동일의 거래량 0 은 정상이라 통과시켜야 하지만(정상 봉 유실 방지),
    #      음수 거래량은 물리적으로 불가능하므로 걸러야 한다.
    assert validate_ohlcv({**GOOD, "volume": 0}) == []
    assert "negative_volume" in validate_ohlcv({**GOOD, "volume": -1})


def test_all_violations_collected_not_short_circuited():
    # WHY: 첫 위반에서 멈추면 남은 문제가 숨는다(Rule 12) — 사유는 전부 드러나야
    #      운영이 소스 품질 문제를 한 번에 파악한다.
    bad = {"open": -1.0, "high": 1.0, "low": 5.0, "close": 2.0, "volume": -3}
    reasons = validate_ohlcv(bad)
    assert set(reasons) == {"non_positive_price", "negative_volume", "high_lt_low", "high_not_max", "low_not_min"}
