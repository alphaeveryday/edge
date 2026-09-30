"""DART 재무 지표 수집·정제 계약 (ALPHA-1130). 기준시각 조회는 e2e(PostgreSQL)가 본다."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from data_pipeline.lake import LocalStorage
from data_pipeline.sources import dart_fundamental
from data_pipeline.steps import source_observations as so
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


def chain(tmp_path, dart, *, from_date="2025-10-01", to_date="2026-08-20", holdings=("005930", "000660", "005935")):
    storage = LocalStorage(tmp_path)
    write_holdings(storage, "2026-08-14", list(holdings))
    code = so.collect_financial(storage, dart, "run_f", etf_ids=["091160"], from_date=from_date, to_date=to_date,
                                now=NOW)
    return storage, code


def rows_by(storage, run_id="run_fn"):
    manifest = json.loads(storage.get_bytes(
        f"operations_archive/canonical_run_manifests/dataset=financial_metric/run_id={run_id}/manifest.json"))
    rows = so.read_rows(so.FINANCIAL, storage.get_bytes(manifest["artifact"]["key"]))
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
    assert so.normalize(storage, so.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 0
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
    so.normalize(storage, so.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
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
    so.normalize(storage, so.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    row = rows_by(storage)[("005930", 2026, "Q2", "revenue", "QUARTER", "CFS")]
    assert row["availability_basis"] == "received" and row["rcept_date"] is None
    assert row["available_at"] == row["received_at"]


def test_company_without_consolidated_statements_keeps_standalone_only(tmp_path):
    # WHY: 연결이 없는 회사(013 조회 데이터 없음)는 수집 실패가 아니다. 별도만 남기고 연결 행을 만들지 않는다.
    storage, code = chain(tmp_path, default_dart())
    assert code == 0
    so.normalize(storage, so.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
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
    tickers, coverage = so.constituents_between(storage, ["091160"], datetime(2026, 6, 10).date(),
                                                datetime(2026, 7, 31).date())
    assert tickers == ["005930"] and coverage["snapshots"] == ["2026-06-01"]
    assert coverage["uncovered_before"] is None
    tickers, coverage = so.constituents_between(storage, ["091160"], datetime(2026, 1, 1).date(),
                                                datetime(2026, 6, 30).date())
    assert coverage["uncovered_before"] == "2026-06-01"     # 그 앞 기간 구성은 모른다고 드러낸다


def test_no_snapshot_in_the_period_fails_without_calling_dart(tmp_path):
    storage = LocalStorage(tmp_path)
    dart = default_dart()
    assert so.collect_financial(storage, dart, "run_x", etf_ids=["091160"], from_date="2025-01-01",
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
    assert so.normalize(storage, so.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 2
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
    targets, rejects = dart_fundamental.plan_reports(
        [{"report_nm": "사업보고서 (2026.03)", "rcept_dt": "20260601", "corp_code": "1"},
         {"report_nm": "사업보고서 (2025.12)", "rcept_dt": "20260310", "corp_code": "2"}],
        datetime(2026, 1, 1).date(), datetime(2026, 12, 31).date())
    assert targets == {("2025", "11011"), ("2025", "11014")}      # 사업보고서엔 같은 해 3분기를 붙인다
    assert rejects[0]["reasons"] == ["non_december_fiscal_year"]


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
    tickers, coverage = so.constituents_between(storage, ["091160"], datetime(2026, 6, 3).date(),
                                                datetime(2026, 6, 4).date())
    assert tickers == ["005930"] and coverage["snapshots"] == ["2026-06-01"]
    assert coverage["etfs_without_snapshot_at_start"] == []


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
    so.normalize(storage, so.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    bps = rows_by(storage)[("005930", 2026, "Q2", "bps", "POINT", "CFS")]
    assert bps["availability_basis"] == "received" and bps["available_at"] == bps["received_at"]


def test_unsupported_fiscal_calendar_is_reported_not_silently_skipped(tmp_path):
    # WHY(리뷰, Rule 12): 비12월 결산을 계획에서 빼기만 하면 수집·정제가 exit 0 으로 끝나 누락이 안 보인다.
    march = {("2026", "11011"): ("사업보고서 (2026.03)", "20260601000505", "20260601")}
    dart = DartFake([SAMSUNG], {}, {SAMSUNG["corp_code"]: filing_list(SAMSUNG, march)})
    storage, _ = chain(tmp_path, dart, holdings=("005930",))
    assert so.normalize(storage, so.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 2


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
    assert so.normalize(storage, so.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 2
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
    assert so.normalize(storage, so.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 2
    rows = rows_by(storage)
    assert ("005930", 2026, "Q2", "bps", "POINT", "CFS") in rows         # 합계 행은 그대로 읽힌다
    assert ("005930", 2026, "Q1", "revenue", "QUARTER", "CFS") not in rows


def test_damaged_filing_list_rows_are_reported_and_page_counts_must_be_positive_integers(tmp_path):
    # WHY(검증 라운드 잔여): 파손 목록 행을 조용히 빼면 "그 기간 보고서 없음"처럼 보이고, 문자열이 아닌
    # 접수번호는 정제 전체를 멈추며, 0·소수 페이지 수는 뒤 페이지 누락을 완료로 확정한다.
    good = json.loads(filing_list(SAMSUNG))
    good["list"] += [None, {"rcept_no": ["20260930000001"], "rcept_dt": "20260930", "report_nm": "x"}]
    storage, code = chain(tmp_path, DartFake([SAMSUNG], full_responses(SAMSUNG),
                                             {SAMSUNG["corp_code"]: json.dumps(good).encode()}),
                          holdings=("005930",))
    assert so.normalize(storage, so.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric") == 2
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
    assert any("bps_blocked_preferred_shares" in r["reasons"] for r in rejects)

    negative = json.loads(shares(SAMSUNG, "2026", "11012"))
    for r in negative["list"]:
        if r["se"] == "우선주":
            r["istc_totqy"] = "-5"
    rows, rejects = _extract_bps(negative)
    assert [r["metric"] for r in rows if r["metric"].startswith("bps")] == ["bps_total_shares"]
    assert any("bps_blocked_preferred_shares" in r["reasons"] for r in rejects)

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
    so.normalize(storage, so.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    rows = rows_by(storage)
    assert rows[("005930", 2026, "Q2", "bps_total_shares", "POINT", "CFS")]["received_at"] == \
        rows[("005930", 2026, "Q2", "bps", "POINT", "CFS")]["received_at"]
    assert rows[("005930", 2026, "Q2", "bps_total_shares", "POINT", "CFS")]["received_at"].startswith("2026-12-31")
