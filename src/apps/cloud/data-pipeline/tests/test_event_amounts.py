"""events/amounts KR 금액 파서 테스트 (ALPHA-545, v4 이식).

WHY: event_measure.value/unit 은 LLM 이 아니라 이 파서가 소유한다(Rule 5). 파서가
자릿수(억/조/만)를 틀리면 계약·배당 규모가 자릿수째 틀린 채 적재되고, 문법 밖 표기에
값을 지어내면 UNRESOLVED 정직 규칙이 깨진다 — 둘 다 다운스트림 설명이 그대로 믿는다.
"""

from data_pipeline.events.amounts import (
    FLAG_APPROX_OR_RANGE,
    FLAG_CALENDAR_YEAR,
    FLAG_NO_NUMBER,
    FLAG_OK,
    parse_amount,
    parse_basis,
)


def test_eok_amount_with_comma_parses_to_krw():
    """1,883억원 — 콤마 자릿수 + 억 단위 + 원 명시. 파싱 실패나 자릿수 오류가 나면
    계약 규모가 1/10⁸ 로 실린다."""
    parsed = parse_amount("1,883억원")
    assert (parsed.value, parsed.unit, parsed.parse_flag) == (188_300_000_000.0, "KRW", FLAG_OK)
    assert parsed.currency_marked is True


def test_jo_eok_mixed_run_sums_place_values():
    """1조2000억원 — 조/억 혼합 run 은 자릿수 합산이다(1.2e12). 첫 세그먼트만 읽으면 1조,
    붙는 세그먼트를 다른 금액으로 읽으면 두 값이 나온다 — 둘 다 오적재."""
    parsed = parse_amount("1조2000억원")
    assert (parsed.value, parsed.unit) == (1_200_000_000_000.0, "KRW")


def test_grouped_small_places_scale_with_trailing_big_place():
    """1천200억원 — 천백십은 그룹 안 가수라 뒤따르는 억이 그룹 전체(1200)를 곱해야 한다.
    세그먼트 독립 합산은 1천+200억=200억1천으로 계약 규모를 6배 가까이 깎는다(Codex #255 P2)."""
    parsed = parse_amount("1천200억원")
    assert (parsed.value, parsed.unit, parsed.parse_flag) == (120_000_000_000.0, "KRW", FLAG_OK)

    parsed = parse_amount("1천2백만원")
    assert (parsed.value, parsed.unit) == (12_000_000.0, "KRW")

    # 조 그룹 뒤 억 그룹 — 그룹 경계(조억만)는 종전대로 합산이다.
    parsed = parse_amount("1조2천억원")
    assert (parsed.value, parsed.unit) == (1_200_000_000_000.0, "KRW")


def test_bare_place_value_implies_krw():
    """단위 토큰 없는 '5000억' — 만 이상 자릿수는 KR 뉴스 관례상 원화로 읽는다
    (currency_marked=False 로 명시/관례를 구분해 남긴다)."""
    parsed = parse_amount("5000억")
    assert (parsed.value, parsed.unit) == (500_000_000_000.0, "KRW")
    assert parsed.currency_marked is False


def test_range_takes_midpoint_and_inherits_right_unit():
    """3~4조원 — 좌측 경계가 우측의 자릿수·단위를 물려받아 중간값(3.5조)이 된다.
    좌측을 '3원'으로 읽으면 중간값이 2조원으로 붕괴한다."""
    parsed = parse_amount("3~4조원")
    assert (parsed.value, parsed.unit, parsed.parse_flag) == (
        3_500_000_000_000.0, "KRW", FLAG_APPROX_OR_RANGE)


def test_approx_marker_flags_but_keeps_value():
    """'약 5000억원' — 근사 표지는 값을 버리지 않고 플래그로 드러낸다(버리면 손실,
    조용히 ok 면 정밀도 과장)."""
    parsed = parse_amount("약 5000억원")
    assert (parsed.value, parsed.parse_flag) == (500_000_000_000.0, FLAG_APPROX_OR_RANGE)


def test_usd_is_never_converted_to_krw():
    """'3억달러' — 단위 패밀리 간 환산 금지: USD 는 USD 로 남는다(환율 추정은 조작)."""
    parsed = parse_amount("3억달러")
    assert (parsed.value, parsed.unit) == (300_000_000.0, "USD")


def test_no_number_refuses_to_invent_value():
    """숫자 없는 표기('대규모 수주')는 value=None + no_number — 호출부가 UNRESOLVED 로
    기록한다. 여기서 값을 지어내면 정직 규칙 전체가 무너진다."""
    parsed = parse_amount("대규모 수주")
    assert (parsed.value, parsed.unit, parsed.parse_flag) == (None, None, FLAG_NO_NUMBER)


def test_multiple_unrelated_amounts_are_refused():
    """무관한 금액이 여럿('매출 1조원 영업익 500억원')이면 거부 — 어느 쪽인지 추측해
    하나를 고르면 절반 확률로 틀린 값이 계약 컬럼에 실린다."""
    parsed = parse_amount("매출 1조원 영업익 500억원")
    assert parsed.value is None and parsed.parse_flag == FLAG_NO_NUMBER


def test_basis_only_from_explicit_markers():
    """basis 는 원문 명시(총/연간)에서만 나온다 — 그 외는 UNKNOWN(계약 CHECK 어휘).
    TOTAL/ANNUAL 을 추측하면 연간화 산식(annualized_value)이 최대 계약기간 배수로 틀린다."""
    assert parse_basis("총 2조원 규모") == "TOTAL"
    assert parse_basis("연간 3000억원") == "ANNUAL"
    assert parse_basis("2조원 규모") == "UNKNOWN"
    assert parse_basis(None) == "UNKNOWN"


def test_calendar_year_is_not_a_duration():
    """'2028년 만기' — 역년은 만기일이지 기간이 아니다. YEARS 로 통과시키면 unit_family 가
    DURATION_DAYS 와 맞아떨어져 2028년짜리 기간이 event_measure 에 적재되고(수천 배 오염)
    만기 분석이 무의미해진다(Codex #255 P2). 실제 기간 표기는 그대로 살아야 한다."""
    parsed = parse_amount("2028년 만기")
    assert (parsed.value, parsed.unit, parsed.parse_flag) == (None, None, FLAG_CALENDAR_YEAR)

    # 역년 범위(1900~2999)로 읽히는 range 중앙값도 거부한다.
    assert parse_amount("2028~2030년").parse_flag == FLAG_CALENDAR_YEAR

    # 진짜 기간은 보존 — 3년·10년·120년 계약은 정상 파싱이다.
    assert (parse_amount("3년").value, parse_amount("3년").unit) == (3.0, "YEARS")
    assert parse_amount("10년").value == 10.0
    assert parse_amount("120년").value == 120.0
