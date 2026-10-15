"""quality/etf.validate_etf_holding 게이트 테스트 — 정체성 blocking / 값 이상 경고.

각도 H(coerce-to-passing 방지)와 KRX 해외기초 결측(null) 허용을 인코딩한다 — 정규화가
finite-or-null 로 정리한 참고 수치를 받아 범위 이상만 경고로 표면화하고, 정체성 결측은 막는다.
"""

from data_pipeline.quality import BLOCKING_REASONS_ETF, validate_etf_holding

_MAX = "2026-07-16"


def _row(**over) -> dict:
    row = {"market": "KR", "etf_id": "069500", "constituent_ticker": "005930",
           "constituent_isin": "KR7005930003", "constituent_name": "삼성전자",
           "weight_pct": 30.5, "shares": 1000.0, "market_value": 5.0e9,
           "currency": "KRW", "as_of_date": "2026-07-14",
           "source_vendor": "krx", "fetched_at": "2026-07-14T00:00:00+00:00"}
    row.update(over)
    return row


def test_valid_row_passes():
    # WHY: 정체성·시간축이 온전하고 값이 정상 범위면 통과(빈 사유)해야 canonical 로 간다.
    assert validate_etf_holding(_row(), max_as_of_date=_MAX) == []


def test_null_reference_fields_pass_without_warning():
    # WHY: KRX 해외기초 ETF 는 비중·평가금액을 대시(-)로 줘 정규화가 null 로 정리한다 —
    #      결측(null)은 구조적 특성이라 경고 없이 통과해야 한다(구성종목·주식수는 보존).
    #      해외 ETF 한 종목당 수백 행을 결측 경고로 채우지 않는다(사용자 결정).
    row = _row(weight_pct=None, market_value=None)
    assert validate_etf_holding(row, max_as_of_date=_MAX) == []


def test_missing_identity_is_blocking():
    # WHY: (market,etf_id,constituent_ticker)는 canonical 행키다 — 하나라도 없으면 fact 를
    #      식별할 수 없어 blocking(canonical 제외). 공백만 문자열도 결측으로 본다(NonBlankStr).
    for field, reason in [("market", "missing_market"), ("etf_id", "missing_etf_id"),
                          ("constituent_ticker", "missing_constituent")]:
        reasons = validate_etf_holding(_row(**{field: "  "}), max_as_of_date=_MAX)
        assert reason in reasons and reason in BLOCKING_REASONS_ETF


def test_unsupported_market_is_blocking():
    # WHY: 정규화는 US/KR 만 지원한다 — 비어있진 않지만 미지원 market('JP')은 통화 계약을
    #      만들 수 없어 blocking(present 라고 passed 로 위장하지 않음, Rule 12).
    reasons = validate_etf_holding(_row(market="JP"), max_as_of_date=_MAX)
    assert "unsupported_market" in reasons and "unsupported_market" in BLOCKING_REASONS_ETF


def test_missing_and_bad_as_of_date_are_blocking():
    # WHY: as_of_date 는 시간축 파티션 키다 — 결측(정규화 실패=None)은 파티션을 못 만들어
    #      blocking, 범위 밖 미래('20991231')는 엉뚱한 파티션을 만들어 blocking(사업부문 동형).
    missing = validate_etf_holding(_row(as_of_date=None), max_as_of_date=_MAX)
    assert "missing_as_of_date" in missing and "missing_as_of_date" in BLOCKING_REASONS_ETF
    future = validate_etf_holding(_row(as_of_date="2099-12-31"), max_as_of_date=_MAX)
    assert "bad_as_of_date" in future and "bad_as_of_date" in BLOCKING_REASONS_ETF


def test_weight_out_of_range_is_warning_not_blocking():
    # WHY: 한 종목 비중이 ≤0·>100%면 파싱/단위 이상 신호로 표면화하되(coerce-to-passing
    #      방지), 실재 구성종목이 무효란 뜻은 아니라 경고로만 남기고 행은 통과시킨다.
    for bad in (0.0, -5.0, 150.0):
        reasons = validate_etf_holding(_row(weight_pct=bad), max_as_of_date=_MAX)
        assert reasons == ["weight_out_of_range"]
        assert "weight_out_of_range" not in BLOCKING_REASONS_ETF


def test_negative_shares_and_value_are_warnings():
    # WHY: 음수 주식수·평가금액은 파싱 이상 신호라 경고로 표면화하되(Rule 12) 행은 통과시킨다
    #      (참고 필드 — 구성종목 멤버십은 유효).
    assert validate_etf_holding(_row(shares=-1.0), max_as_of_date=_MAX) == ["negative_shares"]
    assert validate_etf_holding(_row(market_value=-1.0), max_as_of_date=_MAX) == ["negative_market_value"]


def test_all_reasons_collected_not_short_circuited():
    # WHY: 첫 실패에서 멈추면 어떤 규칙들이 왜 깨졌는지 다 안 드러난다 — 정체성 결측과 값
    #      이상이 동시에 있으면 둘 다 수집돼야 운영이 소스 문제를 추적한다(Rule 12).
    reasons = validate_etf_holding(_row(etf_id="", weight_pct=200.0), max_as_of_date=_MAX)
    assert "missing_etf_id" in reasons and "weight_out_of_range" in reasons
