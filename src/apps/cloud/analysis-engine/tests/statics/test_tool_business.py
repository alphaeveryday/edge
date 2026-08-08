"""business_mix 계약 검증 — 이 도구가 조용히 거짓말을 시작하는 두 지점만 친다.

  1) 산술: 정정공시를 접고, 미개시/종료 계약을 빼고, 거래처를 합쳐야 집중도가
     맞다. 하나라도 틀리면 "매출의 몇 %가 이 고객사"라는 문장이 근거를 잃는다.
  2) 커버리지 정직성: 이 표는 26종목만 덮는다(2026-08-04 실측 53행). 덮지
     않는 종목에 0 이나 빈 결과를 주면 "집중도 낮음"으로 읽힌다 — 판정불가와
     **몇 종목만 덮는지**가 사유에 들어가야 한다.
"""
from edge_analysis.statics.tool_business import _business_mix

DAY = "2026-08-04"

# (corp_code, corp_name, counterparty, withheld, object, amount, ratio, start, end,
#  report_date, rcept_no) — s3_supply_fact 실제 열 순서.
BOOK = [
    ("00100000", "가나전자", "갑", False, "P1", 1000, 10.0, "2025-01-01", "2027-12-31",
     "2026-01-10", "r1"),                                   # r6 이 정정한다
    ("00100000", "가나전자", "갑", False, "P2", 500, 5.0, "2025-06-01", "2026-09-30",
     "2026-02-10", "r2"),                                   # 유효 · 90일 내 만료
    ("00100000", "가나전자", "을", False, "Q1", 400, 4.0, "2024-01-01", "2026-07-31",
     "2026-03-10", "r3"),                                   # 이미 종료
    ("00100000", "가나전자", "병", False, "R1", 300, 3.0, "2027-01-01", "2028-01-01",
     "2026-04-10", "r4"),                                   # 미개시 (수주잔고)
    ("00100000", "가나전자", "을", False, "Q2", 200, 2.0, "2025-01-01", None,
     "2026-05-10", "r5"),                                   # 유효 · 기한 미확정
    ("00100000", "가나전자", "갑", False, "P1", 1200, 12.0, "2025-01-01", "2027-12-31",
     "2026-06-10", "r6"),                                   # r1 의 증액 정정
    ("00100000", "가나전자", "정", False, "S1", 100, 1.0, "2025-01-01", "2026-12-31",
     "2026-09-01", "r7"),                                   # 오늘 이후 공시 (선견 금지)
]


class _Lake:
    """질의 대상 표로 갈라 주는 가짜 레이크. 순서 주의 — `_base(day)` 가 만드는
    CTE 안에도 `v_instrument` 가 들어 있으므로 구체적인 표를 먼저 본다."""

    def __init__(self, contracts, revenue=(), inst=(("005930", "가나전자 보통주"),)):
        self.contracts, self.revenue, self.inst = contracts, revenue, inst

    def sql(self, q):
        if "s3_supply_fact" in q:
            return list(self.contracts)
        if "s3_statement_line" in q:
            return list(self.revenue)
        if "v_instrument" in q:
            return list(self.inst)
        raise AssertionError(f"예상 못 한 질의: {q[:120]}")


def test_concentration_and_expiry_math():
    r = _business_mix(_Lake(BOOK), day=DAY, instrument_id="inst_x")

    assert r["verdict"] == "계산됨", r["reason"]
    # 정정 1건을 접고(r1←r6), 종료 1건·미개시 1건·미공시 1건을 뺀 3건이 유효하다.
    assert (r["n_contracts"], r["restated"], r["backlog"]) == (3, 1, 1)
    # 거래처 '갑' 은 별건 2개를 들고 있다 — 합쳐야 집중이 보인다 (12.0 + 5.0).
    assert (r["top_counterparty"], r["top_share"]) == ("갑", 17.0)
    assert (r["n_counterparties"], r["concentration"]) == (2, 19.0)
    # 책 내부 HHI: (17/19)² + (2/19)² = 0.81163…
    assert r["hhi"] == 8116.3
    assert r["live_amount_krw"] == 1900.0        # 정정 후 1200 + 500 + 200
    # 만기 지평 = day + 90d = 2026-11-02. 2026-09-30 건만 걸린다.
    assert (r["expiring_90d"], r["expiring_90d_ratio_pct"]) == (1, 5.0)
    assert r["expiring_90d_amount_krw"] == 500.0
    # 매출 교차검증 부재는 침묵이 아니라 문장으로 나온다.
    assert r["revenue_covered"] is False and r["contract_to_revenue"] is None
    assert "교집합이 0" in r["note"]


def test_uncovered_instrument_is_undecidable_and_names_the_coverage():
    r = _business_mix(_Lake([]), day=DAY, instrument_id="inst_없는종목")

    assert r["verdict"] == "판정불가"
    assert "26종목" in r["reason"] and "s3_supply_fact" in r["reason"]
    # 부재를 0 으로 흘리면 '집중도 낮음'으로 읽힌다 — 수치 자리는 전부 None 이다.
    for k in ("n_contracts", "top_share", "concentration", "hhi", "expiring_90d"):
        assert r[k] is None, k


def test_all_backlog_is_undecidable_not_zero():
    # 실측 삼성전기: 공시된 3건이 전부 2027-01-01 개시다. 오늘의 집중도는 0 이
    # 아니라 '못 쟀다' — 0.0 을 내면 "특정 고객사에 안 걸려 있다"가 된다.
    only_backlog = [r for r in BOOK if r[10] == "r4"]
    r = _business_mix(_Lake(only_backlog), day=DAY, instrument_id="inst_x")

    assert r["verdict"] == "판정불가" and r["concentration"] is None
    assert "유효한 것이 0건" in r["reason"]
    assert "3.0%" in r["note"]      # 잔고 정보를 잃지 않는다
