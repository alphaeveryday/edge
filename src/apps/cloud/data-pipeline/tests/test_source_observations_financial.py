"""DART 재무 지표 수집·정제 계약 (ALPHA-1130). 기준시각 조회는 e2e(PostgreSQL)가 본다."""

from __future__ import annotations

import json

import pytest
from datetime import datetime, timezone
from decimal import Decimal

from data_pipeline.lake import LocalStorage
from data_pipeline.sources import dart_fundamental
from data_pipeline.steps import source_observations as so, source_observations_financial as so_fin
from source_observation_fakes import (FILINGS, HYNIX, NO_DATA, SAMSUNG, DartFake, filing_list, shares, statement,
                                      write_holdings)

NOW = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)


def full_responses(corp, *, cfs=True):
    responses = {}
    for year, code in FILINGS:
        responses[("statement", corp["corp_code"], year, code, "OFS")] = statement(corp, year, code, "OFS")
        if cfs:
            responses[("statement", corp["corp_code"], year, code, "CFS")] = statement(corp, year, code, "CFS")
        responses[("shares", corp["corp_code"], year, code)] = shares(corp, year, code, treasury=50)
    return responses


def chain(tmp_path, dart, *, from_date="2025-10-01", to_date="2026-08-20", holdings=("005930", "000660", "005935"),
          now=None):
    storage = LocalStorage(tmp_path)
    write_holdings(storage, "2026-08-14", list(holdings))
    code = so_fin.collect_financial(storage, dart, "run_f", etf_ids=["091160"], from_date=from_date, to_date=to_date,
                                now=now or NOW)
    return storage, code


def rows_by(storage, run_id="run_fn"):
    manifest = json.loads(storage.get_bytes(
        f"operations_archive/canonical_run_manifests/dataset=financial_metric/run_id={run_id}/manifest.json"))
    rows = so.read_rows(so_fin.FINANCIAL, storage.get_bytes(manifest["artifact"]["key"]))
    return {(r["instrument_code"], r["fiscal_year"], r["fiscal_period"], r["metric"], r["period_kind"],
             r["fs_basis"]): r for r in rows}


def default_dart():
    return DartFake([SAMSUNG, HYNIX], {**full_responses(SAMSUNG), **full_responses(HYNIX, cfs=False)},
                    {SAMSUNG["corp_code"]: filing_list(SAMSUNG), HYNIX["corp_code"]: filing_list(HYNIX)})


def test_quarterly_values_q4_derivation_and_bps_with_evidence(tmp_path):
    # WHY: v2 PER 는 '해당 분기 EPS 4개 합'이다. 누적을 분기로, 4분기를 공시값처럼 다루면 TTM 이 틀린다.
    # Q4 는 FY−9M 유도임을 행에 남기고, BPS 는 계산식·입력 접수번호를 남긴다.
    storage, code = chain(tmp_path, default_dart())
    assert code == 0
    assert so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 0
    rows = rows_by(storage)
    q2 = rows[("005930", 2026, "Q2", "revenue", "QUARTER", "CFS")]
    assert q2["value"] == "90000" and q2["derivation"] == "REPORTED" and q2["unit"] == "KRW"
    assert rows[("005930", 2026, "Q2", "revenue", "CUMULATIVE", "CFS")]["value"] == "175000"
    q4 = rows[("005930", 2025, "Q4", "eps_basic", "QUARTER", "CFS")]
    assert q4["value"] == "1100" and q4["derivation"] == "FY_MINUS_9M" and q4["formula"] == dart_fundamental.Q4_FORMULA
    assert {i["rcept_no"] for i in json.loads(q4["inputs"])} == {"20260310000202", "20251114000101"}
    assert rows[("005930", 2025, "Q4", "revenue", "QUARTER", "CFS")]["value"] == "90000"   # 330000−240000
    bps = rows[("005930", 2026, "Q2", "bps", "POINT", "CFS")]
    # 계약: 지배지분 ÷ (합계 발행 − 자기주식), 소수 6자리 반올림.
    assert bps["value"] == str((Decimal("3200000") / Decimal(1000 - 50)).quantize(Decimal("0.000001")))
    inputs = json.loads(bps["inputs"])
    assert inputs[1]["istc_totqy"] == "1000" and inputs[1]["tesstk_co"] == "50" and inputs[1]["se"] == "보통주"
    assert bps["derivation"] == "EQUITY_OVER_SHARES" and "보통주" in bps["formula"]
    assert rows[("005930", 2026, "Q2", "bps_total_shares", "POINT", "CFS")]["value"] == bps["value"]
    # 사업보고서 기말 BPS 는 Q4 시점이다(연간 누적과 가른다).
    assert ("005930", 2025, "Q4", "bps", "POINT", "CFS") in rows


def test_release_date_sets_visibility_without_inventing_a_time(tmp_path):
    # WHY: DART 는 접수일만 준다. 접수 당일 몇 시에 보였는지 모르므로 다음날 00:00 KST 부터 보이게 한다
    # (2026-09-30 결정). 실제 수신이 더 이르면 그때부터다.
    storage, _ = chain(tmp_path, default_dart())
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    fy = rows_by(storage)[("005930", 2025, "FY", "revenue", "CUMULATIVE", "CFS")]
    assert fy["rcept_date"] == "2026-03-10" and fy["availability_basis"] == "provider_release_date"
    assert datetime.fromisoformat(fy["available_at"]) == datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)
    q4 = rows_by(storage)[("005930", 2025, "Q4", "revenue", "QUARTER", "CFS")]
    assert q4["rcept_date"] == "2026-03-10"          # 유도값은 입력 중 늦은 공개일


def test_unknown_release_date_falls_back_to_receipt(tmp_path):
    # WHY: 목록에서 접수일을 못 찾은 판본(정정 직후 목록 창 밖 등)의 공개일을 추정하지 않는다.
    lists = {SAMSUNG["corp_code"]: filing_list(SAMSUNG), HYNIX["corp_code"]: filing_list(HYNIX)}
    responses = full_responses(SAMSUNG)
    responses[("statement", SAMSUNG["corp_code"], "2026", "11012", "CFS")] = statement(
        SAMSUNG, "2026", "11012", "CFS", rcept_no="20260901000999")        # 목록에 없는 접수번호
    storage, _ = chain(tmp_path, DartFake([SAMSUNG, HYNIX], {**responses, **full_responses(HYNIX, cfs=False)}, lists))
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    row = rows_by(storage)[("005930", 2026, "Q2", "revenue", "QUARTER", "CFS")]
    assert row["availability_basis"] == "received" and row["rcept_date"] is None
    assert row["available_at"] == row["received_at"]


def test_company_without_consolidated_statements_keeps_standalone_only(tmp_path):
    # WHY: 연결이 없는 회사(013 조회 데이터 없음)는 수집 실패가 아니다. 별도만 남기고 연결 행을 만들지 않는다.
    storage, code = chain(tmp_path, default_dart())
    assert code == 0
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    hynix = {k for k in rows_by(storage) if k[0] == "000660"}
    assert hynix and {k[5] for k in hynix} == {"OFS"}
    manifest = json.loads(storage.get_bytes(
        "operations_archive/raw_run_manifests/dataset=financial_metric/run_id=run_f/manifest.json"))
    assert manifest["counts"]["error"] == 0 and manifest["counts"]["empty"] >= 4
    assert manifest["request_scope"]["unmapped"] == ["005935"]   # 우선주는 DART 공시 주체가 아니다


def test_constituents_come_from_snapshots_of_the_period_not_the_current_list(tmp_path):
    # WHY: 현재 구성종목을 과거 전체 기간에 적용하면 그때 없던 종목의 재무를 소비자가 편입 종목으로 읽는다.
    storage = LocalStorage(tmp_path)
    write_holdings(storage, "2026-06-01", ["005930"])
    write_holdings(storage, "2026-09-01", ["005930", "000660"])
    tickers, coverage = so_fin.constituents_between(storage, ["091160"], datetime(2026, 6, 10).date(),
                                                datetime(2026, 7, 31).date())
    assert tickers == ["005930"] and coverage["snapshots"] == ["2026-06-01"]
    assert coverage["uncovered_before"] is None
    tickers, coverage = so_fin.constituents_between(storage, ["091160"], datetime(2026, 1, 1).date(),
                                                datetime(2026, 6, 30).date())
    assert coverage["uncovered_before"] == "2026-06-01"     # 그 앞 기간 구성은 모른다고 드러낸다


def test_no_snapshot_in_the_period_fails_without_calling_dart(tmp_path):
    storage = LocalStorage(tmp_path)
    dart = default_dart()
    assert so_fin.collect_financial(storage, dart, "run_x", etf_ids=["091160"], from_date="2025-01-01",
                                to_date="2025-03-31", now=NOW) == 1
    assert dart.calls == []


def test_same_run_id_with_a_different_filing_window_fails_instead_of_skipping(tmp_path):
    # WHY(봇 P1): 같은 분에 trigger 한 백필 청크는 run_id 가 같다. '이미 수집'으로 성공하면 뒤 청크의 접수일
    # 범위는 DART 를 한 번도 부르지 않은 채 끝난다 — 범위가 다르면 거부하고, 같은 범위 재시도만 건너뛴다.
    storage = LocalStorage(tmp_path)
    write_holdings(storage, "2026-08-14", ["005930"])
    dart = default_dart()
    assert so_fin.collect_financial(storage, dart, "run_x", etf_ids=["091160"], from_date="2025-01-01",
                                    to_date="2025-03-31", now=NOW) == 1      # 스냅샷 없음 — 결과와 무관하게 완료
    with pytest.raises(SystemExit, match="다른 요청 범위"):
        so_fin.collect_financial(storage, dart, "run_x", etf_ids=["091160"], from_date="2024-01-01",
                                 to_date="2024-12-31", now=NOW)
    assert so_fin.collect_financial(storage, dart, "run_x", etf_ids=["091160"], from_date="2025-01-01",
                                    to_date="2025-03-31", now=NOW) == 1
    assert dart.calls == []


def test_missing_q3_blocks_q4_derivation_and_non_krw_is_rejected(tmp_path):
    # WHY: 9개월 누적 없이 Q4 를 만들면 연간값이 한 분기로 둔갑한다. 통화가 원이 아니면 단위가 섞인다.
    responses = full_responses(SAMSUNG)
    responses[("statement", SAMSUNG["corp_code"], "2025", "11014", "CFS")] = NO_DATA
    responses[("statement", SAMSUNG["corp_code"], "2026", "11013", "CFS")] = statement(
        SAMSUNG, "2026", "11013", "CFS", currency="USD")
    storage, _ = chain(tmp_path, DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)}),
                       holdings=("005930",))
    assert so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 2
    rows = rows_by(storage)
    assert ("005930", 2025, "Q4", "eps_basic", "QUARTER", "CFS") not in rows
    assert ("005930", 2025, "Q4", "eps_basic", "QUARTER", "OFS") in rows          # 별도는 3분기가 있다
    assert ("005930", 2026, "Q1", "revenue", "QUARTER", "CFS") not in rows
    log = json.loads(storage.get_bytes(next(k for k in storage.list_keys("operations_archive/data_quality_logs/")
                                            if "run_id=run_fn/" in k)))
    reasons = {r for f in log["failures"] for r in f["reasons"]}
    assert {"q4_derivation_input_missing", "non_krw_currency"} <= reasons


def test_report_names_map_to_periods_and_reject_non_december_years():
    assert dart_fundamental.report_of("[기재정정]반기보고서 (2026.06)") == ("2026", "11012", 6)
    assert dart_fundamental.report_of("분기보고서 (2026.09)") == ("2026", "11014", 9)
    # plan_reports 는 한 회사의 목록을 받는다(수집이 회사별로 부른다).
    window = (datetime(2026, 1, 1).date(), datetime(2026, 12, 31).date())
    targets, rejects = dart_fundamental.plan_reports(
        [{"report_nm": "사업보고서 (2025.12)", "rcept_dt": "20260310", "corp_code": "2"}], *window)
    assert targets == {("2025", "11011"), ("2025", "11014")} and rejects == []   # 사업보고서엔 같은 해 3분기를 붙인다
    targets, rejects = dart_fundamental.plan_reports(
        [{"report_nm": "사업보고서 (2026.03)", "rcept_dt": "20260601", "corp_code": "1"}], *window)
    assert targets == set() and rejects[0]["reasons"] == ["non_december_fiscal_year"]


def test_ambiguous_eps_lines_pick_common_share_or_refuse():
    lines = [{"sj_div": "IS", "account_id": "ifrs-full_BasicEarningsLossPerShare", "account_nm": n}
             for n in ("보통주 기본주당이익", "우선주 기본주당이익")]
    line, problem = dart_fundamental._pick_line(lines, "ifrs-full_BasicEarningsLossPerShare", ("IS",))
    assert line["account_nm"] == "보통주 기본주당이익" and problem is None
    line, problem = dart_fundamental._pick_line(lines[:1] * 2, "ifrs-full_BasicEarningsLossPerShare", ("IS",))
    assert line is None and problem == "ambiguous_account_line"


def test_in_force_snapshot_is_chosen_per_etf(tmp_path):
    # WHY(리뷰): 다른 ETF 만 갱신된 날짜를 대상 ETF 의 스냅샷으로 쓰면 대상 구성종목이 빈 목록이 되고
    # 재무 수집이 "스냅샷 없음"으로 실패한다(완료 manifest 라 재실행도 회복하지 못한다).
    storage = LocalStorage(tmp_path)
    write_holdings(storage, "2026-06-01", ["005930"])
    write_holdings(storage, "2026-06-02", ["035420"], etf_id="069500")
    tickers, coverage = so_fin.constituents_between(storage, ["091160"], datetime(2026, 6, 3).date(),
                                                datetime(2026, 6, 4).date())
    assert tickers == ["005930"] and coverage["snapshots"] == ["2026-06-01"]
    assert coverage["etfs_without_snapshot_at_start"] == []


@pytest.mark.parametrize("sj_divs,metric", [(("IS", "CIS"), "revenue"), (("BS",), "bps")])
def test_lines_without_a_currency_are_rejected_not_assumed_krw(sj_divs, metric):
    # WHY(봇 P2): 금액 단위는 응답의 currency=KRW 가 세운다. 필드가 빠진 줄을 원으로 가정하면 단위를 확인하지
    # 않은 값이 원 단위 매출·EPS·BPS 로 적힌다 — 실 응답은 모든 줄에 KRW 를 싣는다(live 픽스처 6건 전수).
    corp = {"corp_code": SAMSUNG["corp_code"], "stock_code": "005930"}
    body = json.loads(statement(SAMSUNG, "2026", "11012", "CFS"))
    for line in body["list"]:
        if line["sj_div"] in sj_divs:
            del line["currency"]
    rows, rejects = dart_fundamental.extract(corp, "2026", "11012", "CFS", {"body_json": body},
                                             json.loads(shares(SAMSUNG, "2026", "11012")))
    assert not [r for r in rows if r["metric"] == metric]
    assert any("non_krw_currency" in r["reasons"] and r["metric"] == metric for r in rejects)


def test_bps_refuses_non_krw_equity_and_bad_receipt_numbers():
    # WHY(리뷰): 달러 자본을 원/주로 적으면 단위가 조용히 틀린다. 형식이 틀린 접수번호 한 줄은 DB CHECK 에서
    # 그 실행의 적재 전체(다른 회사 포함)를 롤백시킨다 — 정제에서 걸러야 한다.
    corp = {"corp_code": SAMSUNG["corp_code"], "stock_code": "005930"}
    body = json.loads(statement(SAMSUNG, "2026", "11012", "CFS"))
    for line in body["list"]:
        if line["sj_div"] == "BS":
            line["currency"] = "USD"
    rows, rejects = dart_fundamental.extract(corp, "2026", "11012", "CFS", {"body_json": body},
                                             json.loads(shares(SAMSUNG, "2026", "11012")))
    assert not [r for r in rows if r["metric"] == "bps"]
    assert any("non_krw_currency" in r["reasons"] and r["metric"] == "bps" for r in rejects)
    body = json.loads(statement(SAMSUNG, "2026", "11012", "CFS", rcept_no="bad"))
    rows, rejects = dart_fundamental.extract(corp, "2026", "11012", "CFS", {"body_json": body}, None)
    assert rows == [] and any("bad_rcept_no" in r["reasons"] for r in rejects)


def test_bps_with_an_unverifiable_share_filing_is_not_backdated(tmp_path):
    # WHY(리뷰): 계산 입력 하나의 접수일을 모르면 공개일 기준으로 과거에 보이게 할 근거가 없다.
    responses = full_responses(SAMSUNG)
    body = json.loads(shares(SAMSUNG, "2026", "11012", treasury=50))
    for row in body["list"]:
        row["rcept_no"] = "20260901000999"      # 형식은 맞지만 목록에 없는 접수번호 — 공개일을 확인할 수 없다
    responses[("shares", SAMSUNG["corp_code"], "2026", "11012")] = json.dumps(body).encode()
    storage, _ = chain(tmp_path, DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)}),
                       holdings=("005930",))
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    bps = rows_by(storage)[("005930", 2026, "Q2", "bps", "POINT", "CFS")]
    assert bps["availability_basis"] == "received" and bps["available_at"] == bps["received_at"]


def test_unsupported_fiscal_calendar_is_reported_not_silently_skipped(tmp_path):
    # WHY(리뷰, Rule 12): 비12월 결산을 계획에서 빼기만 하면 수집·정제가 exit 0 으로 끝나 누락이 안 보인다.
    march = {("2026", "11011"): ("사업보고서 (2026.03)", "20260601000505", "20260601")}
    dart = DartFake([SAMSUNG], {}, {SAMSUNG["corp_code"]: filing_list(SAMSUNG, march)})
    storage, _ = chain(tmp_path, dart, holdings=("005930",))
    assert so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 2


def test_bad_share_receipt_number_is_rejected_not_loaded():
    # WHY(리뷰 2차): BPS 분모의 접수번호도 DB CHECK 대상이다 — 통과시키면 그 실행의 적재 전체가 롤백된다.
    corp = {"corp_code": SAMSUNG["corp_code"], "stock_code": "005930"}
    share_body = json.loads(shares(SAMSUNG, "2026", "11012"))
    for row in share_body["list"]:
        row["rcept_no"] = "bad"
    rows, rejects = dart_fundamental.extract(corp, "2026", "11012", "CFS",
                                             {"body_json": json.loads(statement(SAMSUNG, "2026", "11012", "CFS"))},
                                             share_body)
    assert not [r for r in rows if r["metric"] == "bps"]
    assert any(r["metric"] == "bps" and "bad_rcept_no" in r["reasons"] for r in rejects)


def test_malformed_filing_list_fails_one_company_and_keeps_the_rest(tmp_path):
    # WHY(리뷰 2차): 목록의 파손 행 하나가 수집을 죽이면 앞 회사에서 받은 raw 까지 저장되지 않는다.
    lists = {SAMSUNG["corp_code"]: filing_list(SAMSUNG),
             HYNIX["corp_code"]: json.dumps({"status": "000", "total_page": "x", "list": [None]}).encode()}
    dart = DartFake([SAMSUNG, HYNIX], full_responses(SAMSUNG), lists)
    storage, code = chain(tmp_path, dart)
    assert code == 0            # 파손 행이 수집을 멈추지 않는다(페이지 수 검증은 _source 기반 테스트가 본다)
    # 파손 행은 거부로 드러난다(부분 실패) — 정상 회사는 그대로 적재된다.
    assert so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 2
    assert ("005930", 2026, "Q2", "revenue", "QUARTER", "CFS") in rows_by(storage)


def test_q3_correction_refetches_the_annual_report_to_rederive_q4():
    # WHY(리뷰 2차): 3분기만 정정되면 FY 를 다시 받지 않아 Q4=FY−9M 이 옛 9M 으로 남는다.
    rows = [{"report_nm": "사업보고서 (2025.12)", "rcept_dt": "20260310", "rcept_no": "1"},
            {"report_nm": "[기재정정]분기보고서 (2025.09)", "rcept_dt": "20260915", "rcept_no": "2"}]
    targets, _ = dart_fundamental.plan_reports(rows, datetime(2026, 9, 1).date(), datetime(2026, 9, 30).date())
    assert targets == {("2025", "11014"), ("2025", "11011")}


class _PagedClient:
    """DartFundamentalSource 운반 대역 — page_no 별 응답. 요청 시작 시각을 남긴다."""

    def __init__(self, pages):
        self.pages, self.started = pages, []

    def request(self, method, url, *, headers=None, data=None, decode=True):
        self.started.append(datetime.now(timezone.utc))
        page = int(url.split("page_no=")[1].split("&")[0]) if "page_no=" in url else 1
        return self.pages[page]


def _source(pages):
    from data_pipeline.config import DartFinancialSource

    client = _PagedClient(pages)
    return dart_fundamental.DartFundamentalSource(DartFinancialSource(api_key="k"), client), client


def test_filing_list_paging_keeps_received_pages_and_never_calls_a_broken_count_complete():
    # WHY(리뷰 3차): 페이지 수를 못 읽었는데 마지막 페이지로 치면 뒤 보고서가 빠진 채 "완료"가 된다.
    # 한도 초과(020) 같은 중단 응답이 2쪽에서 오면 이미 받은 1쪽까지 버리면 안 된다.
    ok = lambda total: json.dumps({"status": "000", "total_page": total, "list": [{"rcept_no": "1"}]}).encode()
    src, _ = _source({1: ok("broken")})
    pages = src.filings("00126380", datetime(2026, 1, 1).date(), datetime(2026, 1, 31).date())
    assert [(p.status, p.detail) for p in pages] == [("error", "bad_total_page")] and pages[0].body
    stop = json.dumps({"status": "020", "message": "한도 초과"}).encode()
    src, _ = _source({1: ok(2), 2: stop})
    pages = src.filings("00126380", datetime(2026, 1, 1).date(), datetime(2026, 1, 31).date())
    assert [p.status for p in pages] == ["ok", "error"] and pages[1].stop and pages[1].body


def test_receipt_time_is_taken_after_the_response_arrives():
    # WHY(리뷰 3차): received_at 이 곧 가시시각이다. 요청 전에 찍으면 재시도·지연 동안 받기 전부터 보인다.
    src, client = _source({1: json.dumps({"status": "000", "list": []}).encode()})
    result = src.statement("00126380", "2026", "11012", "CFS")
    assert datetime.fromisoformat(result.fetched_at) >= client.started[-1]


def test_damaged_share_rows_and_foreign_period_responses_are_rejected_not_fatal(tmp_path):
    # WHY(리뷰 3차): 한 보고서의 파손(se 숫자)이 정제 전체를 죽이면 다른 회사도 적재되지 않는다. 요청과 다른
    # 연도·보고서의 행을 요청 기간으로 라벨하면 연간값이 분기값이 된다.
    responses = full_responses(SAMSUNG)
    share = json.loads(shares(SAMSUNG, "2026", "11012"))
    share["list"].append({"se": 123})
    responses[("shares", SAMSUNG["corp_code"], "2026", "11012")] = json.dumps(share).encode()
    wrong = json.loads(statement(SAMSUNG, "2026", "11013", "CFS"))
    for line in wrong["list"]:
        line["bsns_year"], line["reprt_code"] = "2025", "11011"
    responses[("statement", SAMSUNG["corp_code"], "2026", "11013", "CFS")] = json.dumps(wrong).encode()
    storage, _ = chain(tmp_path, DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)}),
                       holdings=("005930",))
    assert so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 2
    rows = rows_by(storage)
    assert ("005930", 2026, "Q2", "bps", "POINT", "CFS") in rows         # 합계 행은 그대로 읽힌다
    assert ("005930", 2026, "Q1", "revenue", "QUARTER", "CFS") not in rows


def test_damaged_filing_list_rows_are_reported_and_page_counts_must_be_positive_integers(tmp_path):
    # WHY(검증 라운드 잔여): 파손 목록 행을 조용히 빼면 "그 기간 보고서 없음"처럼 보이고, 문자열이 아닌
    # 접수번호는 정제 전체를 멈추며, 0·소수 페이지 수는 뒤 페이지 누락을 완료로 확정한다.
    good = json.loads(filing_list(SAMSUNG))
    good["list"] += [None, {"rcept_no": ["20260814000001"], "rcept_dt": "20260814", "report_nm": "x"}]   # 창 안의 파손 행
    storage, code = chain(tmp_path, DartFake([SAMSUNG], full_responses(SAMSUNG),
                                             {SAMSUNG["corp_code"]: json.dumps(good).encode()}),
                          holdings=("005930",))
    assert so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 2
    log = json.loads(storage.get_bytes(next(k for k in storage.list_keys("operations_archive/data_quality_logs/")
                                            if "run_id=run_fn/" in k)))
    reasons = {r for f in log["failures"] for r in f["reasons"]}
    assert {"malformed_list_row", "bad_rcept_no"} <= reasons
    assert ("005930", 2026, "Q2", "revenue", "QUARTER", "CFS") in rows_by(storage)   # 나머지는 적재된다
    for total in (0, 1.5, -1, True):
        body = json.dumps({"status": "000", "total_page": total, "list": [{"rcept_no": "1"}]}).encode()
        src, _ = _source({1: body})
        pages = src.filings("00126380", datetime(2026, 1, 1).date(), datetime(2026, 1, 31).date())
        assert pages[-1].detail == "bad_total_page", total


def _extract_bps(share_body):
    corp = {"corp_code": SAMSUNG["corp_code"], "stock_code": "005930"}
    return dart_fundamental.extract(corp, "2026", "11012", "CFS",
                                    {"body_json": json.loads(statement(SAMSUNG, "2026", "11012", "CFS"))}, share_body)


def test_share_table_damage_blocks_common_bps_instead_of_assuming_no_preferred():
    # WHY(리뷰 4차): 우선주 행이 없거나 음수이거나 종류별 합이 합계와 다르면 "우선주 없음"이 아니라 "모름"이다 —
    # 그때 보통주 BPS 를 만들면 파손 응답이 정상 보통주 기준값으로 적재된다. 틀리면 막는 쪽으로 틀려야 한다.
    missing = json.loads(shares(SAMSUNG, "2026", "11012"))
    missing["list"] = [r for r in missing["list"] if r["se"] != "우선주"]
    rows, rejects = _extract_bps(missing)
    assert [r["metric"] for r in rows if r["metric"].startswith("bps")] == ["bps_total_shares"]
    assert any("bps_share_rows_unreadable" in r["reasons"] for r in rejects)

    negative = json.loads(shares(SAMSUNG, "2026", "11012"))
    for r in negative["list"]:
        if r["se"] == "우선주":
            r["istc_totqy"] = "-5"
    rows, rejects = _extract_bps(negative)
    assert [r["metric"] for r in rows if r["metric"].startswith("bps")] == ["bps_total_shares"]
    assert any("bps_share_rows_unreadable" in r["reasons"] for r in rejects)

    inconsistent = json.loads(shares(SAMSUNG, "2026", "11012"))
    for r in inconsistent["list"]:
        if r["se"] == "보통주":
            r["istc_totqy"] = "1"
    rows, rejects = _extract_bps(inconsistent)
    assert not [r for r in rows if r["metric"].startswith("bps")]
    assert any("share_rows_inconsistent" in r["reasons"] for r in rejects)


def test_numeric_zero_share_count_is_zero_not_missing():
    # WHY(리뷰 4차): 자기주식 없음이 문자열 '-' 대신 숫자 0 으로 오면 결측으로 읽혀 유효한 분모가 거부됐다.
    assert dart_fundamental._share_count({"tesstk_co": 0}, "tesstk_co") == 0
    assert dart_fundamental._share_count({"tesstk_co": None}, "tesstk_co") is None
    assert dart_fundamental._share_count({"tesstk_co": "-3"}, "tesstk_co") is None


def test_total_share_bps_receipt_time_includes_the_share_response(tmp_path):
    # WHY(리뷰 4차): 두 BPS 지표의 분모는 주식총수 응답이다 — 그 응답을 받기 전 시점에 값이 보이면 안 된다.
    dart = DartFake([SAMSUNG], full_responses(SAMSUNG), {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
    storage, _ = chain(tmp_path, dart, holdings=("005930",))
    manifest = json.loads(storage.get_bytes(so.raw_run_manifest_key("financial_metric", "run_f")))
    for obj in manifest["objects"]:
        if obj.get("kind") == "shares" or "shares" in obj.get("key", ""):
            obj["fetched_at"] = "2026-12-31T00:00:00+00:00"    # 주식총수만 훨씬 늦게 받은 실행
    storage.put_bytes(so.raw_run_manifest_key("financial_metric", "run_f"),
                      json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode())
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    rows = rows_by(storage)
    assert rows[("005930", 2026, "Q2", "bps_total_shares", "POINT", "CFS")]["received_at"] == \
        rows[("005930", 2026, "Q2", "bps", "POINT", "CFS")]["received_at"]
    assert rows[("005930", 2026, "Q2", "bps_total_shares", "POINT", "CFS")]["received_at"].startswith("2026-12-31")


def test_treasury_share_rows_must_also_reconcile_and_damage_is_not_a_policy_block():
    # WHY(리뷰 5차): 발행수 합만 맞추면 자기주식이 종류별로 모순돼도 두 분모가 다 만들어졌다. 그리고 주식수 파손을
    # 우선주 정책 차단과 같은 사유로 적으면 운영자가 재수집할 결함을 팀 결정 대기로 오인한다.
    body = json.loads(shares(SAMSUNG, "2026", "11012", treasury=50))
    for r in body["list"]:
        if r["se"] == "보통주":
            r["tesstk_co"] = "-"          # 합계 자기주식 50, 보통주 0, 우선주 0 → 모순
    rows, rejects = _extract_bps(body)
    assert not [r for r in rows if r["metric"].startswith("bps")]
    assert any("share_rows_inconsistent" in r["reasons"] for r in rejects)
    missing = json.loads(shares(SAMSUNG, "2026", "11012"))
    missing["list"] = [r for r in missing["list"] if r["se"] != "우선주"]
    _, rejects = _extract_bps(missing)
    assert any("bps_share_rows_unreadable" in r["reasons"] for r in rejects)
    assert not any("bps_blocked_preferred_shares" in r["reasons"] for r in rejects)


def test_preferred_treasury_damage_also_blocks_common_bps():
    # WHY(리뷰 6차): 우선주 자기주식이 결측·음수면 종류별 대조를 건너뛰고 보통주 BPS 가 정상으로 나갔다.
    body = json.loads(shares(SAMSUNG, "2026", "11012", treasury=50))
    for r in body["list"]:
        if r["se"] == "우선주":
            r["tesstk_co"] = "-5"
    rows, rejects = _extract_bps(body)
    assert [r["metric"] for r in rows if r["metric"].startswith("bps")] == ["bps_total_shares"]
    assert any("bps_share_rows_unreadable" in r["reasons"] for r in rejects)


def test_treasury_above_issued_within_a_share_class_rejects_both_denominators():
    # WHY(리뷰 7차): 합계는 맞는데 우선주 0주에 우선주 자기주식 5주 같은 종류별 모순은 두 합 검사를 다 통과했다.
    body = json.loads(shares(SAMSUNG, "2026", "11012", treasury=55))
    for r in body["list"]:
        if r["se"] == "보통주":
            r["tesstk_co"] = "50"
        if r["se"] == "우선주":
            r["tesstk_co"] = "5"           # 발행 0, 자기주식 5
    rows, rejects = _extract_bps(body)
    assert not [r for r in rows if r["metric"].startswith("bps")]
    assert any("share_rows_inconsistent" in r["reasons"] for r in rejects)


def test_conflicting_duplicate_share_class_rows_reject_instead_of_taking_the_first():
    # WHY(리뷰 8차): 우선주 0주 행과 50주 행이 함께 오면 첫 행만 골라 정상 보통주 BPS 가 나갔다 — 순서가 판정을 갈랐다.
    body = json.loads(shares(SAMSUNG, "2026", "11012"))
    extra = dict(next(r for r in body["list"] if r["se"] == "우선주"))
    extra["istc_totqy"] = "50"
    body["list"].append(extra)
    rows, rejects = _extract_bps(body)
    assert not [r for r in rows if r["metric"].startswith("bps")]
    assert any("share_rows_inconsistent" in r["reasons"] for r in rejects)
    body["list"][-1] = dict(next(r for r in body["list"] if r["se"] == "우선주"))   # 같은 내용의 중복은 허용
    rows, _ = _extract_bps(body)
    assert [r["metric"] for r in rows if r["metric"].startswith("bps")] == ["bps_total_shares", "bps"]


def test_share_class_rows_with_different_receipts_are_one_table_or_none():
    # WHY(리뷰 9차): 보통주 행의 접수번호가 합계 행과 다르면 보통주 BPS 의 근거·공개일이 합계 행 것으로 적혔다.
    body = json.loads(shares(SAMSUNG, "2026", "11012"))
    for r in body["list"]:
        if r["se"] == "보통주":
            r["rcept_no"] = "20260929000001"
    rows, rejects = _extract_bps(body)
    assert not [r for r in rows if r["metric"].startswith("bps")]
    assert any("share_rows_inconsistent" in r["reasons"] for r in rejects)


def test_share_table_must_be_dated_at_the_report_period_end_and_integer():
    # WHY(리뷰 10차): 반기 재무제표에 12-31 기준 주식수 표가 붙어도 종류별 기준일이 서로 같으면 통과했다 —
    # 다른 기말의 분모로 만든 BPS 가 분기값이 된다. 소수 주식수도 정상 분모로 흘렀다.
    body = json.loads(shares(SAMSUNG, "2026", "11012"))
    for r in body["list"]:
        r["stlm_dt"] = "2025-12-31"
    rows, rejects = _extract_bps(body)
    assert not [r for r in rows if r["metric"].startswith("bps")]
    assert any("share_rows_inconsistent" in r["reasons"] for r in rejects)
    assert dart_fundamental._share_count({"istc_totqy": "100.5"}, "istc_totqy") is None
    assert dart_fundamental._share_count({"istc_totqy": "1,000"}, "istc_totqy") == 1000


def test_share_counts_are_normalized_to_integer_strings_and_defect_beats_policy():
    # WHY(리뷰 11차): "100.0" 이 inputs 에 그대로 남으면 DB 조회가 우선주 정책 차단을 파손으로 읽는다. 그리고 우선주가 있어도
    # 종류별 수를 못 읽었으면 파손이 먼저다.
    assert str(dart_fundamental._share_count({"istc_totqy": "100.0"}, "istc_totqy")) == "100"
    body = json.loads(shares(SAMSUNG, "2026", "11012", preferred=100))
    for r in body["list"]:
        if r["se"] == "우선주":
            r["tesstk_co"] = "-5"
    _, rejects = _extract_bps(body)
    assert any("bps_share_rows_unreadable" in r["reasons"] for r in rejects)
    assert not any("bps_blocked_preferred_shares" in r["reasons"] for r in rejects)


def test_common_bps_verdict_is_stored_on_the_total_shares_evidence_line():
    # WHY(리뷰 12차): DB 조회가 우선주 수만 보고 사유를 다시 추론하면 "우선주 있음 + 종류별 수 파손"이 정책 차단으로
    # 읽힌다. 정제가 내린 판정 하나를 근거 줄에 남기고 조회는 그것만 읽는다.
    body = json.loads(shares(SAMSUNG, "2026", "11012", preferred=100))
    for r in body["list"]:
        if r["se"] == "우선주":
            r["tesstk_co"] = "-5"
    rows, _ = _extract_bps(body)
    total = next(r for r in rows if r["metric"] == "bps_total_shares")
    assert total["inputs"][1]["common_bps"] == "bps_share_rows_unreadable"
    rows, _ = _extract_bps(json.loads(shares(SAMSUNG, "2026", "11012", preferred=100)))
    assert next(r for r in rows if r["metric"] == "bps_total_shares")["inputs"][1]["common_bps"] == "bps_blocked_preferred_shares"
    rows, _ = _extract_bps(json.loads(shares(SAMSUNG, "2026", "11012")))
    assert next(r for r in rows if r["metric"] == "bps_total_shares")["inputs"][1]["common_bps"] == "computed"


def _versions(storage, run_norm="run_fn"):
    manifest = json.loads(storage.get_bytes(
        f"operations_archive/canonical_run_manifests/dataset=financial_metric/run_id={run_norm}/manifest.json"))
    rows = so.read_rows(so_fin.FINANCIAL_VERSION, storage.get_bytes(manifest["companion"]["key"]))
    return manifest, {(r["fiscal_year"], r["reprt_code"], r["fs_basis"]): r for r in rows}


def test_report_versions_distinguish_confirmed_partial_empty_and_unconfirmed(tmp_path):
    # WHY(리뷰 7차 잔여 → 이번 수정): 지표 행만 저장하면 "정정 보고서를 확인했는데 쓸 지표가 0개"인 실행이 흔적 없이
    # 사라져 옛 값이 최신처럼 남는다. 판본 행은 지표와 독립으로 실행마다 남고, 확인(CONFIRMED)과 확정 실패(UNCONFIRMED)를
    # 가른다 — 조회는 확인된 최신 판본만 권위로 본다.
    responses = full_responses(SAMSUNG)
    # 2026 반기 CFS: 본문은 정상이나 쓸 계정 줄이 없다(전부 거부) → CONFIRMED, metrics=[]
    broken = json.loads(statement(SAMSUNG, "2026", "11012", "CFS"))
    for line in broken["list"]:
        line["account_id"] = "x_unknown"
    responses[("statement", SAMSUNG["corp_code"], "2026", "11012", "CFS")] = json.dumps(broken).encode()
    # 2026 1분기 CFS: HTTP 본문 파손 → UNCONFIRMED
    responses[("statement", SAMSUNG["corp_code"], "2026", "11013", "CFS")] = b"<html>502</html>"
    # 2025 3분기: 주식총수 응답 파손 → 판본은 확정, 분모만 미확정(detail.shares=error)
    responses[("shares", SAMSUNG["corp_code"], "2025", "11014")] = b"<html>502</html>"
    dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
    storage, _ = chain(tmp_path, dart, holdings=("005930",))
    assert so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 2
    manifest, versions = _versions(storage)
    assert manifest["companion"]["dataset"] == "financial_report_version" and manifest["companion"]["rows"] == len(versions)
    empty = versions[(2026, "11012", "CFS")]
    assert empty["status"] == "CONFIRMED" and json.loads(empty["metrics"]) == [] and json.loads(empty["rejected"])
    assert empty["rcept_no"] == "20260814000404" and empty["availability_basis"] == "provider_release_date"
    assert versions[(2026, "11013", "CFS")]["status"] == "UNCONFIRMED"
    assert json.loads(versions[(2026, "11013", "CFS")]["detail"])["statement"] == "error"
    q3 = versions[(2025, "11014", "CFS")]
    assert q3["status"] == "CONFIRMED" and json.loads(q3["detail"])["shares"] == "error"
    assert "eps_basic/QUARTER/Q3" in json.loads(q3["metrics"]) and not any(m.startswith("bps") for m in json.loads(q3["metrics"]))
    annual = versions[(2025, "11011", "CFS")]
    assert "eps_basic/QUARTER/Q4" in json.loads(annual["metrics"])          # Q4 유도 행은 사업보고서 판본의 것
    assert json.loads(versions[(2026, "11012", "OFS")]["detail"])["statement"] == "ok"
    assert versions[(2026, "11013", "OFS")]["status"] == "CONFIRMED"        # OFS 응답은 멀쩡했다 — 기준별로 따로 확정
    # 판본 행의 raw 근거: 파손 본문도 raw 객체로 남아 그 키를 가리킨다(본문이 아예 없는 실패만 raw manifest)
    assert versions[(2026, "11013", "CFS")]["raw_key"].endswith("00126380-2026-11013-CFS-39b659fd9260d48f.json")


def test_companion_artifact_is_required_at_load_time(tmp_path):
    # WHY: 판본 없는 지표 행은 조회 계약 밖이다 — 옛 형태(companion 없는) manifest 를 조용히 싣지 않는다.
    dart = DartFake([SAMSUNG], full_responses(SAMSUNG), {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
    storage, _ = chain(tmp_path, dart, holdings=("005930",))
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    key = "operations_archive/canonical_run_manifests/dataset=financial_metric/run_id=run_fn/manifest.json"
    manifest = json.loads(storage.get_bytes(key))
    manifest.pop("companion")
    storage.put_bytes(key, json.dumps(manifest).encode())
    with pytest.raises(ValueError, match="companion"):
        so._companion_params(storage, so_fin.FINANCIAL, manifest, "run_fn")


def test_malformed_rows_and_rejected_share_responses_are_not_confirmed(tmp_path):
    # WHY(리뷰 14차): 파손 행이 섞인 재무제표를 남은 행으로 정제하면 "확인했는데 지표 없음"이 되어 정상 확정값을 NULL 로
    # 갈아치운다. 정제가 거부한 분모 응답(다른 회사 응답)을 ok 로 적으면 분모 부재가 "확정된 부재"로 읽힌다.
    responses = full_responses(SAMSUNG)
    body = json.loads(statement(SAMSUNG, "2026", "11012", "CFS"))
    body["list"].append(42)
    responses[("statement", SAMSUNG["corp_code"], "2026", "11012", "CFS")] = json.dumps(body).encode()
    other = json.loads(shares(SAMSUNG, "2026", "11013"))
    for row in other["list"]:
        row["corp_code"] = "00000009"          # 다른 회사의 주식총수 응답이 온 경우
    responses[("shares", SAMSUNG["corp_code"], "2026", "11013")] = json.dumps(other).encode()
    dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
    storage, _ = chain(tmp_path, dart, holdings=("005930",))
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    _, versions = _versions(storage)
    assert versions[(2026, "11012", "CFS")]["status"] == "UNCONFIRMED"
    assert json.loads(versions[(2026, "11012", "CFS")]["detail"])["statement_detail"] == "malformed_list_row"
    q1 = json.loads(versions[(2026, "11013", "CFS")]["detail"])
    assert q1["shares"] == "error" and q1["shares_detail"] == "response_identity_mismatch"


def test_rejected_responses_feed_nothing_downstream(tmp_path):
    # WHY(리뷰 15차): 거부한 응답의 남은 행으로 지표·분모·Q4 유도를 만들면 미확정 값이 확정 판본에 실린다.
    # 파손 주식총수 → BPS 없음, 접수번호 파손 재무제표 → 판본 UNCONFIRMED·지표 0, 미확정 Q3 → Q4 유도 없음.
    responses = full_responses(SAMSUNG)
    bad_shares = json.loads(shares(SAMSUNG, "2026", "11012"))
    bad_shares["list"].append(42)
    responses[("shares", SAMSUNG["corp_code"], "2026", "11012")] = json.dumps(bad_shares).encode()
    bad_q1 = json.loads(statement(SAMSUNG, "2026", "11013", "CFS"))
    bad_q1["list"][0]["rcept_no"] = "bad"
    responses[("statement", SAMSUNG["corp_code"], "2026", "11013", "CFS")] = json.dumps(bad_q1).encode()
    bad_q3 = json.loads(statement(SAMSUNG, "2025", "11014", "CFS"))
    bad_q3["list"].append(None)
    responses[("statement", SAMSUNG["corp_code"], "2025", "11014", "CFS")] = json.dumps(bad_q3).encode()
    dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
    storage, _ = chain(tmp_path, dart, holdings=("005930",))
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    rows = rows_by(storage)
    _, versions = _versions(storage)
    assert ("005930", 2026, "Q2", "bps", "POINT", "CFS") not in rows and ("005930", 2026, "Q2", "bps_total_shares", "POINT", "CFS") not in rows
    assert json.loads(versions[(2026, "11012", "CFS")]["detail"])["shares"] == "error"
    assert versions[(2026, "11013", "CFS")]["status"] == "UNCONFIRMED" and json.loads(versions[(2026, "11013", "CFS")]["metrics"]) == []
    assert not any(k[1:3] == (2026, "Q1") and k[5] == "CFS" for k in rows)
    assert versions[(2025, "11014", "CFS")]["status"] == "UNCONFIRMED"
    assert ("005930", 2025, "Q4", "eps_basic", "QUARTER", "CFS") not in rows            # 미확정 9M → Q4 유도 없음
    # 유도 입력이 미확정이면 사업보고서 판본도 미확정 — 그 실행의 FY·Q4 행(BPS 포함)은 싣지 않는다(리뷰 21차)
    assert versions[(2025, "11011", "CFS")]["status"] == "UNCONFIRMED"
    assert ("005930", 2025, "Q4", "bps", "POINT", "CFS") not in rows


def test_unconfirmed_attempts_are_visible_from_receipt_and_empty_statements_keep_share_status(tmp_path):
    # WHY(리뷰 16차): 실패한 재수집에 섞인 정상 접수번호로 실패를 공시일로 소급하면 8월 기준 조회에 9월 실패가 보인다.
    # 그리고 013(empty) 재무제표 판본에 분모 상태가 비면 정상 확인된 부재가 "분모 미확정"으로 읽힌다.
    responses = full_responses(SAMSUNG)
    mixed = json.loads(statement(SAMSUNG, "2026", "11012", "CFS"))
    mixed["list"][-1]["rcept_no"] = "bad"                 # 앞 줄들은 정상 접수번호
    responses[("statement", SAMSUNG["corp_code"], "2026", "11012", "CFS")] = json.dumps(mixed).encode()
    responses[("statement", SAMSUNG["corp_code"], "2026", "11013", "CFS")] = NO_DATA      # 013 + 주식총수 정상
    dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
    dart.fetched_at = "2026-09-20T00:00:00+00:00"
    storage, _ = chain(tmp_path, dart, holdings=("005930",))
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    _, versions = _versions(storage)
    failed = versions[(2026, "11012", "CFS")]
    assert failed["status"] == "UNCONFIRMED" and failed["availability_basis"] == "received"
    assert failed["available_at"] == failed["received_at"] and failed["available_at"].startswith("2026-09-20")
    empty = versions[(2026, "11013", "CFS")]
    assert empty["status"] == "CONFIRMED" and json.loads(empty["metrics"]) == []
    assert json.loads(empty["detail"]) == {"statement": "empty", "statement_detail": "013", "shares": "ok", "shares_detail": None}


def test_broken_share_table_unconfirms_only_the_denominator(tmp_path):
    # WHY(리뷰 17차): 주식총수 표의 접수번호·종류별 합 파손을 재무제표 파손으로 취급하면 정정된 손익 지표까지 버려
    # 옛 값이 남는다. 분모만 미확정(shares=error → BPS_UNCONFIRMED)이고 손익·판본은 확정이다.
    responses = full_responses(SAMSUNG)
    body = json.loads(shares(SAMSUNG, "2026", "11012"))
    for row in body["list"]:
        row["rcept_no"] = "bad"
    responses[("shares", SAMSUNG["corp_code"], "2026", "11012")] = json.dumps(body).encode()
    dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
    storage, _ = chain(tmp_path, dart, holdings=("005930",))
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    rows = rows_by(storage)
    _, versions = _versions(storage)
    v = versions[(2026, "11012", "CFS")]
    assert v["status"] == "CONFIRMED" and json.loads(v["detail"])["shares"] == "error"
    assert json.loads(v["detail"])["shares_detail"] == "bad_rcept_no"
    assert ("005930", 2026, "Q2", "eps_basic", "QUARTER", "CFS") in rows
    assert not any(k[1:3] == (2026, "Q2") and k[3].startswith("bps") for k in rows)


def test_share_table_validity_is_judged_independently_of_the_equity_line():
    # WHY(리뷰 18차): 분모 표의 파손 판정이 자본 계정 추출 성공에 묶이거나 특정 파손 종류만 보면, 숫자 파손·접수번호 파손이
    # "확정된 BPS 부재"로 읽힌다. 한 규칙(share_table_problem)을 정제의 판본 상태와 _bps 가 함께 쓴다.
    ok = json.loads(shares(SAMSUNG, "2026", "11012"))
    assert dart_fundamental.share_table_problem(ok, "2026-06-30") is None
    assert dart_fundamental.share_table_problem(None, "2026-06-30") is None
    broken = json.loads(shares(SAMSUNG, "2026", "11012"))
    next(r for r in broken["list"] if r["se"] == "합계")["istc_totqy"] = "broken"
    assert dart_fundamental.share_table_problem(broken, "2026-06-30") == "share_count_unreadable"
    assert dart_fundamental.share_table_problem(ok, "2025-12-31") == "share_rows_inconsistent"
    # 자본 계정이 없는 재무제표 + 파손 분모: 판본 shares=error (자본 계정과 무관)
    responses = full_responses(SAMSUNG)
    body = json.loads(statement(SAMSUNG, "2026", "11012", "CFS"))
    body["list"] = [ln for ln in body["list"] if ln["sj_div"] != "BS"]
    responses[("statement", SAMSUNG["corp_code"], "2026", "11012", "CFS")] = json.dumps(body).encode()
    responses[("shares", SAMSUNG["corp_code"], "2026", "11012")] = json.dumps(broken).encode()
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
        storage, _ = chain(pathlib.Path(tmp), dart, holdings=("005930",))
        so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
        _, versions = _versions(storage)
        detail = json.loads(versions[(2026, "11012", "CFS")]["detail"])
        assert detail["shares"] == "error" and detail["shares_detail"] == "share_count_unreadable"
        assert versions[(2026, "11012", "CFS")]["status"] == "CONFIRMED"


def test_share_sums_are_checked_per_column_and_for_empty_statement_versions(tmp_path):
    # WHY(리뷰 19차): 자기주식 한 칸 파손이 발행수 합 불일치 검사를 끄면 모순된 분모로 통상 BPS 가 나간다. 그리고
    # 013 재무제표 판본도 분모 표 파손은 shares=error 여야 한다(재무제표 유무와 무관).
    body = json.loads(shares(SAMSUNG, "2026", "11012", preferred=20))
    for r in body["list"]:
        if r["se"] == "보통주":
            r["istc_totqy"] = "90"            # 90 + 20 ≠ 1000
        if r["se"] == "우선주":
            r["tesstk_co"] = "broken"
    assert dart_fundamental.share_table_problem(body, "2026-06-30") == "share_rows_inconsistent"
    responses = full_responses(SAMSUNG)
    responses[("statement", SAMSUNG["corp_code"], "2026", "11012", "CFS")] = NO_DATA
    broken = json.loads(shares(SAMSUNG, "2026", "11012"))
    for r in broken["list"]:
        r["stlm_dt"] = "2025-12-31"
    responses[("shares", SAMSUNG["corp_code"], "2026", "11012")] = json.dumps(broken).encode()
    dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
    storage, _ = chain(tmp_path, dart, holdings=("005930",))
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    _, versions = _versions(storage)
    detail = json.loads(versions[(2026, "11012", "CFS")]["detail"])
    assert detail["statement"] == "empty" and detail["shares"] == "error" and detail["shares_detail"] == "share_rows_inconsistent"


def test_version_is_visible_only_when_all_its_metrics_are(tmp_path):
    # WHY(리뷰 20차): Q3 정정(새 접수번호)으로 재유도한 Q4 는 정정 공개일부터 보이는데 사업보고서 판본이 원 공개일부터
    # 보이면, 정정 전 기준시각에서 새 판본이 뽑히고 Q4 는 걸러져 NULL 이 된다 — 판본 가시시각은 자기 지표의 최대다.
    # 그리고 한 재무제표에 접수번호가 둘 섞이면 어느 공개일의 값인지 정할 수 없어 UNCONFIRMED 다.
    corrected = {**FILINGS, ("2025", "11014"): ("[기재정정]분기보고서 (2025.09)", "20260901000777", "20260901")}
    responses = full_responses(SAMSUNG)
    for fs in ("CFS", "OFS"):
        responses[("statement", SAMSUNG["corp_code"], "2025", "11014", fs)] = statement(SAMSUNG, "2025", "11014", fs, rcept_no="20260901000777")
    responses[("shares", SAMSUNG["corp_code"], "2025", "11014")] = shares(SAMSUNG, "2025", "11014")
    body = json.loads(responses[("shares", SAMSUNG["corp_code"], "2025", "11014")])
    for r in body["list"]:
        r["rcept_no"] = "20260901000777"
    responses[("shares", SAMSUNG["corp_code"], "2025", "11014")] = json.dumps(body).encode()
    mixed = json.loads(statement(SAMSUNG, "2026", "11012", "CFS"))
    mixed["list"][0]["rcept_no"] = "20260929000540"
    responses[("statement", SAMSUNG["corp_code"], "2026", "11012", "CFS")] = json.dumps(mixed).encode()
    dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG, corrected)})
    dart.fetched_at = "2026-09-20T00:00:00+00:00"
    storage, _ = chain(tmp_path, dart, holdings=("005930",), from_date="2025-10-01", to_date="2026-09-20",
                       now=datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc))
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    rows = rows_by(storage)
    _, versions = _versions(storage)
    q4 = rows[("005930", 2025, "Q4", "eps_basic", "QUARTER", "CFS")]
    annual = versions[(2025, "11011", "CFS")]
    assert q4["rcept_date"] == "2026-09-01" and annual["rcept_date"] == "2026-09-01"
    assert annual["available_at"] == q4["available_at"] and annual["rcept_no"] == FILINGS[("2025", "11011")][1]
    assert versions[(2026, "11012", "CFS")]["status"] == "UNCONFIRMED"
    assert json.loads(versions[(2026, "11012", "CFS")]["detail"])["statement_detail"] == "mixed_rcept_no"


def test_annual_version_is_unconfirmed_when_its_q4_input_was_not_confirmed(tmp_path):
    # WHY(리뷰 21차): FY 는 멀쩡한데 Q3 응답만 실패한 재수집이 FY 판본을 "Q4 없음"으로 확정하면, 그 판본이 옛 공개일부터
    # 보여 과거 조회의 Q4 가 NULL 로 바뀐다. 유도 입력이 미확정이면 사업보고서 판본도 미확정이고 행을 갖지 않는다.
    responses = full_responses(SAMSUNG)
    responses[("statement", SAMSUNG["corp_code"], "2025", "11014", "CFS")] = b"<html>502</html>"
    dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
    storage, _ = chain(tmp_path, dart, holdings=("005930",))
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    rows = rows_by(storage)
    _, versions = _versions(storage)
    annual = versions[(2025, "11011", "CFS")]
    assert annual["status"] == "UNCONFIRMED" and json.loads(annual["detail"])["statement_detail"] == "q4_input_unconfirmed"
    assert not any(k[1:3] in ((2025, "Q4"), (2025, "FY")) and k[5] == "CFS" for k in rows)
    assert versions[(2025, "11011", "OFS")]["status"] == "CONFIRMED"      # 별도는 Q3 도 멀쩡 — 기준별로 따로
    assert ("005930", 2025, "Q4", "eps_basic", "QUARTER", "OFS") in rows


def test_annual_version_is_unconfirmed_when_the_q3_request_never_happened(tmp_path):
    # WHY(리뷰 22차): 수집이 사업보고서(11011)를 받은 뒤 한도로 멈추면 Q3(11014) 판본 자체가 없다 — 그 부분 실행이
    # FY 판본을 "Q4 없음"으로 확정하면 옛 Q4 가 과거 조회에서 사라진다. 부재도 미확정 입력이다.
    from data_pipeline.sources.dart_fundamental import DartResult

    responses = full_responses(SAMSUNG)
    dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
    real_statement = dart.statement

    def stopping(corp_code, year, code, fs_div):
        result = real_statement(corp_code, year, code, fs_div)
        if (year, code) == ("2025", "11014"):     # Q3 요청에서 한도 초과 — 이후 호출 중단
            return DartResult(result.kind, result.request, "error", "dart_020", None, result.fetched_at, stop=True)
        return result
    dart.statement = stopping
    storage, code = chain(tmp_path, dart, holdings=("005930",))
    assert code == 2
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    rows = rows_by(storage)
    _, versions = _versions(storage)
    annual = versions[(2025, "11011", "CFS")]
    assert annual["status"] == "UNCONFIRMED" and json.loads(annual["detail"])["statement_detail"] == "q4_input_unconfirmed"
    assert not any(k[1:3] in ((2025, "Q4"), (2025, "FY")) for k in rows)


def test_late_q3_correction_refetches_the_annual_report_outside_the_list_window(tmp_path):
    # WHY(봇 P1): 3분기 정정이 사업보고서보다 LIST_LOOKBACK_DAYS(400일)보다 늦게 오면 목록에 사업보고서가 없어 Q4 를
    # 재유도하지 못하고 옛 Q4 가 남는다. 그 해의 다음 해 목록을 한 번 더 받아 사업보고서를 다시 계획한다.
    corrected = {**FILINGS, ("2025", "11014"): ("[기재정정]분기보고서 (2025.09)", "20270601000777", "20270601")}
    responses = full_responses(SAMSUNG)
    for fs in ("CFS", "OFS"):       # 정정본 재무제표·주식총수는 정정 접수번호를 싣는다
        responses[("statement", SAMSUNG["corp_code"], "2025", "11014", fs)] = statement(SAMSUNG, "2025", "11014", fs, rcept_no="20270601000777")
    body = json.loads(shares(SAMSUNG, "2025", "11014"))
    for r in body["list"]:
        r["rcept_no"] = "20270601000777"
    responses[("shares", SAMSUNG["corp_code"], "2025", "11014")] = json.dumps(body).encode()
    dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG, corrected)})
    dart.fetched_at = "2027-06-05T00:00:00+00:00"
    storage, _ = chain(tmp_path, dart, holdings=("005930",), from_date="2027-05-01", to_date="2027-06-05",
                       now=datetime(2027, 6, 5, 1, 0, tzinfo=timezone.utc))
    lists = [c for c in dart.calls if c[0] == "list"]
    assert len(lists) == 2 and lists[1][2:] == ("2026-01-01", "2026-12-31")      # 사업보고서 해(2026) 목록을 한 번 더
    assert ("statement", SAMSUNG["corp_code"], "2025", "11011", "CFS") in dart.calls
    so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    rows = rows_by(storage)
    assert ("005930", 2025, "Q4", "eps_basic", "QUARTER", "CFS") in rows          # Q4 재유도
    q4 = rows[("005930", 2025, "Q4", "eps_basic", "QUARTER", "CFS")]
    assert q4["rcept_date"] == "2027-06-01" and q4["availability_basis"] == "provider_release_date"   # 두 입력 접수일 모두 확인


def test_http_key_or_quota_error_stops_the_collection():
    # WHY(봇 P2): 4xx·429 는 키·한도·차단이라 남은 호출도 같은 답이다 — 회사마다 계속 부르면 한도만 태운다.
    from data_pipeline.config import DartFinancialSource as Cfg
    from data_pipeline.sources.http import StopFetch

    class Client:
        def request(self, *a, **k):
            raise StopFetch("429", status=429)
    result = dart_fundamental.DartFundamentalSource(Cfg(api_key="k"), Client()).shares("00126380", "2026", "11012")
    assert result.status == "error" and result.detail == "http_429" and result.stop is True


def test_non_string_receipt_dates_reject_the_row_not_the_company():
    # WHY(봇 P2): 접수일이 숫자·배열인 파손 행이 비교에서 TypeError 를 내면 회사 전체 계획이 사라진다 — 행만 거부한다.
    from datetime import date
    rows = json.loads(filing_list(SAMSUNG))["list"]
    rows[0]["rcept_dt"] = 20260814
    rows[1]["rcept_dt"] = ["20260515"]
    targets, rejects = dart_fundamental.plan_reports(rows, date(2025, 10, 1), date(2026, 8, 20))
    assert {tuple(r["reasons"]) for r in rejects} == {("bad_rcept_dt",)} and len(rejects) == 2
    assert targets                                                     # 나머지 행은 정상 계획된다


def test_non_string_report_names_reject_the_row_not_the_company():
    # WHY(봇 P2): 보고서명이 숫자·배열인 파손 행이 정규식에서 TypeError 를 내면 회사 전체 계획이 사라진다 — 행만 거부한다.
    from datetime import date
    rows = json.loads(filing_list(SAMSUNG))["list"]
    rows[0]["report_nm"] = 1
    rows[2]["report_nm"] = ["분기보고서 (2026.03)"]           # 사업보고서 행(rows[1])은 남겨 결산월이 확인되게
    targets, rejects = dart_fundamental.plan_reports(rows, date(2025, 10, 1), date(2026, 8, 20))
    assert [r["reasons"] for r in rejects] == [["bad_report_nm"], ["bad_report_nm"]] and targets
    assert dart_fundamental.report_of(1) is None


def test_corp_map_transport_failure_is_recorded_as_a_collection_error(tmp_path):
    # WHY(봇 P2): corpCode.xml 재시도 소진(SafeFailureError)이 새면 raw manifest·collection_log 없이 죽는다.
    from data_pipeline.sources.http import SafeFailureError

    dart = DartFake([SAMSUNG], full_responses(SAMSUNG), {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})

    def broken():
        raise SafeFailureError("corpCode.xml retries exhausted")
    dart.corp_map = broken
    storage, code = chain(tmp_path, dart, holdings=("005930",))
    assert code == 1
    manifest = json.loads(storage.get_bytes(so.raw_run_manifest_key("financial_metric", "run_f")))
    assert manifest["completed"] is True
    assert [o["request"]["kind"] for o in manifest["objects"]] == ["corp_map"] and manifest["objects"][0]["status"] == "error"


def test_interim_reports_of_a_non_december_company_are_rejected_even_when_months_match():
    # WHY(봇 P2): 6월 결산 회사의 9월 분기보고서는 1분기인데 월만 보면 3분기(11014)로 통과한다 — 결산월은 사업보고서가 정한다.
    from datetime import date
    june = [{"corp_code": "00000009", "report_nm": "분기보고서 (2026.09)", "rcept_no": "20261114000009", "rcept_dt": "20261114"},
            {"corp_code": "00000009", "report_nm": "사업보고서 (2026.06)", "rcept_no": "20260915000009", "rcept_dt": "20260915"}]
    targets, rejects = dart_fundamental.plan_reports(june, date(2026, 11, 1), date(2026, 11, 20))
    assert targets == set() and {r["reasons"][0] for r in rejects} == {"non_december_fiscal_year"} and len(rejects) == 2
    no_annual = june[:1]
    targets, rejects = dart_fundamental.plan_reports(no_annual, date(2026, 11, 1), date(2026, 11, 20))
    assert targets == set() and rejects[0]["reasons"] == ["fiscal_calendar_unconfirmed"]


def test_disabled_dart_source_is_not_called_even_with_a_key(tmp_path, monkeypatch):
    # WHY: 운영자가 dart_financial.source.enabled=false 로 끈 공급자를 새 재무 수집이 키만 보고 부르면
    # 끄는 스위치가 이 경로에서 무력하다(한도·장애 대응). 비활성은 호출 없이 거부한다.
    from types import SimpleNamespace

    import pytest

    from data_pipeline import run as run_module
    from data_pipeline.config import DartFinancialSource, SourceObservationsConfig

    called = []
    monkeypatch.setattr(so_fin, "collect_financial", lambda *a, **k: called.append(1) or 0)
    settings = SimpleNamespace(
        source_observations=SourceObservationsConfig(etf_ids=["091160"]),
        dart_financial=SimpleNamespace(source=DartFinancialSource(enabled=False, api_key="k")))
    args = SimpleNamespace(step="ingest-raw-financial-metric", input_run_id=None, from_date=None, to_date=None,
                           series=None, all_partitions=False)
    with pytest.raises(SystemExit, match="비활성"):
        run_module._dispatch_observation(args, settings, LocalStorage(tmp_path), "run_off")
    assert called == []


def test_preferred_share_eps_candidates_through_extract_synthetic():
    # 합성 표본(실응답에는 우선주 EPS 줄이 없었다 — 삼성·하이닉스·고려제강 모두 기본·희석 한 줄씩, §10.8).
    # WHY: 보통주 EPS 자리에 우선주 EPS 가 들어가면 PER 이 조용히 틀린다. 보통주 줄이 있으면 그 줄, 우선주 줄만 있거나
    # 구분 안 되는 줄이 둘이면 거부한다 — 한 줄뿐이라는 이유로 우선주 줄을 받지 않는다.
    corp = {"corp_code": SAMSUNG["corp_code"], "stock_code": "005930"}

    def with_eps_lines(names_values):
        body = json.loads(statement(SAMSUNG, "2026", "11012", "CFS"))
        body["list"] = [ln for ln in body["list"] if ln["account_id"] != "ifrs-full_BasicEarningsLossPerShare"]
        rcept_no = body["list"][0]["rcept_no"]
        body["list"] += [{"rcept_no": rcept_no, "sj_div": "IS", "account_id": "ifrs-full_BasicEarningsLossPerShare",
                          "account_nm": n, "thstrm_amount": v, "thstrm_add_amount": v, "currency": "KRW"}
                         for n, v in names_values]
        rows, rejects = dart_fundamental.extract(corp, "2026", "11012", "CFS", {"body_json": body}, None)
        eps = [r for r in rows if r["metric"] == "eps_basic" and r["period_kind"] == "QUARTER"]
        return eps, [r["reasons"] for r in rejects if r.get("metric") == "eps_basic"]

    eps, bad = with_eps_lines([("보통주 기본주당이익", "1000"), ("우선주 기본주당이익", "1001")])
    assert [r["value"] for r in eps] == ["1000"] and bad == []
    eps, bad = with_eps_lines([("우선주 기본주당이익", "1001")])
    assert eps == [] and bad == [["preferred_share_line_only"]]
    eps, bad = with_eps_lines([("기본주당이익", "1000"), ("기본주당이익(계속영업)", "990")])
    assert eps == [] and bad == [["ambiguous_account_line"]]


def test_macro_only_config_loads_and_financial_collection_requires_etf_ids(tmp_path):
    # WHY(봇 P2): 재무 대상 ETF 를 설정 전체의 필수값으로 두면 매크로만 쓰는 설정이 로드부터 실패한다(무관한 스텝까지).
    # 비어 있어도 설정은 로드되고, 재무 수집 스텝만 대상이 없다고 거부한다.
    from types import SimpleNamespace

    from data_pipeline import run as run_module
    from data_pipeline.config.models import SourceObservationsConfig

    config = SourceObservationsConfig.model_validate({"macro": {}})
    assert config.etf_ids == []
    args = SimpleNamespace(step="ingest-raw-financial-metric", input_run_id=None, from_date=None, to_date=None,
                           series=None, all_partitions=False, source=None)
    settings = SimpleNamespace(source_observations=config, dart_financial=None)
    with pytest.raises(SystemExit, match="etf_ids"):
        run_module._dispatch_observation(args, settings, LocalStorage(tmp_path), "run_etf")
