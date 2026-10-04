"""2026-09-30 소량 실응답(축약 원문, `fixtures/source_observations/live/`) 위에서 파서·정제 의미를 고정한다.

기대값은 fixture 에서 베끼지 않고 공식 설명과 대조했다: DART 개발가이드(분·반기 손익 `thstrm_amount`=[3개월],
`thstrm_add_amount`=누적), 삼성전자 주식총수 표(보통주 5,846,278,608 / 우선주 802,371,203 / 자기주식 82,086,705),
KIS 헤더(`bstp_larg/medm/smal_div_code`), ECOS 항목명. 실응답 모듈 도크스트링(설계 §10.8)이 근거다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from data_pipeline.lake import LocalStorage
from data_pipeline.sources import dart_fundamental, kis_sector_master, macro_series
from data_pipeline.steps import source_observations as so, source_observations_financial as so_fin
from source_observation_fakes import DartFake, write_holdings

LIVE = Path(__file__).parent / "fixtures" / "source_observations" / "live"
SAMSUNG = {"corp_code": "00126380", "stock_code": "005930", "corp_name": "삼성전자"}
HYNIX = {"corp_code": "00164779", "stock_code": "000660", "corp_name": "SK하이닉스"}


def live(name: str) -> bytes:
    return (LIVE / name).read_bytes()


# ── 매크로 ───────────────────────────────────────────────────────────────

def test_ecos_live_rows_parse_with_series_identity_and_units():
    # WHY: 통계표·항목이 국고채 10년·원/달러 종가를 가리키는지는 응답의 항목명이 말한다 — 실응답에서 확인한 이름을 고정한다.
    assert macro_series.classify("kr_10y_yield", live("ecos_kr_10y_yield.json")) == ("ok", None)
    good, bad = macro_series.parse("kr_10y_yield", live("ecos_kr_10y_yield.json"))
    assert bad == [] and good[0] == {"observation_date": "2026-09-15", "value": "4.6"}
    days = {g["observation_date"] for g in good}
    assert "2026-09-29" in days and not {"2026-09-24", "2026-09-25"} & days   # 09-24·25 추석 휴장 — 채권은 행 없음
    good, bad = macro_series.parse("usd_krw", live("ecos_usd_krw_ecos_731Y003.json"))
    assert bad == [] and good[0] == {"observation_date": "2026-09-15", "value": "1359.4"}
    # 원천 사실: KRX 휴장일(09-24·25)에도 원/달러 15:30 종가 행이 있다 — KR 거래일 달력과 같다고 전제하지 않는다.
    assert {"2026-09-24", "2026-09-25"} <= {g["observation_date"] for g in good}


def test_kosis_live_t03_is_cpi_yoy_and_unit_lives_in_the_item_name():
    # WHY(실응답 2026-09-30, 발급 키): 데이터 행에도 공식 항목 메타(getMeta ITM)에도 T03 의 UNIT_NM 이 없다 —
    # 단위는 항목명 "전년동월비(%)" 끝에만 있다(T02 전월비만 UNIT_NM=%). 합성 fixture 가 UNIT_NM 을 지어내
    # 파서가 실응답 전건을 unit_mismatch 로 거부하던 것을 바로잡는다. 의미: ITM_NM_ENG "Change over the same month
    # of last year", C1_NM 총지수(CPI for all item), TBL_NM 월별 소비자물가 등락률.
    meta = {m["ITM_ID"]: m for m in json.loads(live("kosis_meta_itm.json"))}
    assert meta["T03"]["ITM_NM"] == "전년동월비(%)" and "UNIT_NM" not in meta["T03"]
    assert meta["T03"]["ITM_NM_ENG"] == "Change over the same month of last year"
    assert macro_series.classify("kr_cpi_yoy", live("kosis_cpi_yoy.json")) == ("ok", None)
    good, bad = macro_series.parse("kr_cpi_yoy", live("kosis_cpi_yoy.json"))
    assert bad == [] and good == [{"observation_date": "2026-06-01", "value": "3.2"},
                                  {"observation_date": "2026-07-01", "value": "2.8"},
                                  {"observation_date": "2026-08-01", "value": "3.1"}]   # 09 는 09-30 시점 미공표
    rows = json.loads(live("kosis_cpi_yoy.json"))
    assert all("UNIT_NM" not in r and r["C1_NM"] == "총지수" and r["ITM_ID"] == "T03" for r in rows)


def test_eia_live_rbrte_is_brent_spot_fob_in_dollars_per_barrel():
    # WHY(실응답 2026-09-30): 행마다 series-description·units 가 있다 — 계열 의미를 응답 자체가 말한다.
    body = json.loads(live("eia_brent_spot.json"))
    row = body["response"]["data"][0]
    assert row["series"] == "RBRTE" and row["series-description"] == "Europe Brent Spot Price FOB (Dollars per Barrel)"
    assert row["units"] == "$/BBL" and body["response"]["frequency"] == "daily"
    good, bad = macro_series.parse("brent_spot_usd", live("eia_brent_spot.json"))
    assert bad == [] and good[0] == {"observation_date": "2026-09-01", "value": "96.02"}
    assert good[-1]["observation_date"] == "2026-09-22"      # 09-30 조회 시 최신 관측 09-22 — 약 1주 지연


def test_fred_dgs10_live_matches_the_fmp_year10_it_replaces():
    # WHY(실응답 2026-10-01, ALPHA-1136): 공급자를 바꿔도 같은 계열이어야 us_10y_yield 를 그대로 쓸 수 있다.
    # FRED 응답 원문을 그대로 두고, 교체 전 FMP 실응답과 겹치는 날 값이 같은지 본다(09-15~25 9일 전부 일치 확인, 여기선 fixture 에 남은 날).
    body = json.loads(live("fred_dgs10.json"))
    assert body["units"] == "lin" and body["realtime_start"] == "2026-09-30"
    good, bad = macro_series.parse("us_10y_yield", live("fred_dgs10.json"))
    assert bad == [] and good[0] == {"observation_date": "2026-09-15", "value": "5"}     # 공급자 자릿수 그대로("5")
    fred = {g["observation_date"]: Decimal(g["value"]) for g in good}
    fmp = {r["date"]: Decimal(str(r["year10"])) for r in json.loads(live("fmp_treasury_rates.json"))}
    common = sorted(set(fred) & set(fmp))
    assert common and all(fred[d] == fmp[d] for d in common), common
    assert fred["2026-09-25"] == Decimal("5.17")


# ── KIS 업종 ─────────────────────────────────────────────────────────────

def test_kis_master_live_layout_names_and_levels(monkeypatch):
    monkeypatch.setattr(kis_sector_master, "MIN_ROWS_BY_MARKET", {"KOSPI": 1, "KOSDAQ": 1})   # 축약 fixture — 행수 게이트는 별도 테스트가 본다
    # WHY: 실파일로 확정한 사실 — 뒷부분 227/221자, 업종명은 헤더 `[5:45]`(공식 샘플 `[3:43]` 은 틀렸다),
    # 대분류=업종 그룹(제조·금융…), 중분류=제조 안의 산업, 소분류는 전 종목 0000.
    names, warnings = kis_sector_master.parse_sector_names(live("kis_idxcode.mst.zip"))
    assert warnings == [] and names["0027"] == "제조" and names["0013"] == "전기·전자" and names["1028"] == "전기·전자"
    rows, rejects = kis_sector_master.parse_master("kospi_code.mst.zip", live("kis_kospi_code.mst.zip"))
    assert rejects == []
    by = {r["instrument_code"]: r for r in rows}
    assert (by["005930"]["raw_large_code"], by["005930"]["raw_medium_code"], by["005930"]["raw_small_code"]) == ("0027", "0013", "0000")
    assert by["005930"]["security_group"] == "ST" and by["005930"]["standard_code"] == "KR7005930003"
    assert by["091160"]["security_group"] == "EF" and by["091160"]["raw_large_code"] == "0000"
    assert by["004970"]["security_group"] == "ST" and by["004970"]["raw_large_code"] == "0000"     # 분류 없는 주식
    rows, rejects = kis_sector_master.parse_master("kosdaq_code.mst.zip", live("kis_kosdaq_code.mst.zip"))
    by = {r["instrument_code"]: r for r in rows}
    assert rejects == [] and (by["058470"]["raw_large_code"], by["058470"]["raw_medium_code"]) == ("1009", "1028")
    assert by["0001A0"]["raw_large_code"] == "0000"            # 새 형식 단축코드, 미분류


# ── DART 재무 ─────────────────────────────────────────────────────────────

def _corp_fixtures():
    load = lambda n: live(f"dart_{n}.json")
    responses = {
        ("statement", SAMSUNG["corp_code"], "2026", "11012", "CFS"): load("stmt_samsung_2026_11012_CFS"),
        ("statement", SAMSUNG["corp_code"], "2026", "11012", "OFS"): load("stmt_samsung_2026_11012_OFS"),
        ("statement", SAMSUNG["corp_code"], "2025", "11011", "CFS"): load("stmt_samsung_2025_11011_CFS"),
        ("statement", SAMSUNG["corp_code"], "2025", "11014", "CFS"): load("stmt_samsung_2025_11014_CFS"),
        ("shares", SAMSUNG["corp_code"], "2026", "11012"): load("shares_samsung_2026_11012"),
        ("statement", HYNIX["corp_code"], "2026", "11012", "CFS"): load("stmt_hynix_2026_11012_CFS"),
        ("shares", HYNIX["corp_code"], "2026", "11012"): load("shares_hynix_2026_11012"),
    }
    lists = {SAMSUNG["corp_code"]: load("list_samsung_p1"), HYNIX["corp_code"]: load("list_hynix_p1")}
    return DartFake([SAMSUNG, HYNIX], responses, lists)


def _normalized(tmp_path, dart, from_date="2025-11-01", to_date="2026-08-20", holdings=("005930", "000660"),
                now=datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)):
    storage = LocalStorage(tmp_path)
    write_holdings(storage, "2026-08-14", list(holdings))
    assert so_fin.collect_financial(storage, dart, "run_f", etf_ids=["091160"], from_date=from_date, to_date=to_date,
                                now=now) == 0
    code = so.normalize(storage, so_fin.FINANCIAL, "run_fn", "run_f", producer="normalize_financial_metric")
    manifest = json.loads(storage.get_bytes(
        "operations_archive/canonical_run_manifests/dataset=financial_metric/run_id=run_fn/manifest.json"))
    rows = so.read_rows(so_fin.FINANCIAL, storage.get_bytes(manifest["artifact"]["key"]))
    log = json.loads(storage.get_bytes(next(k for k in storage.list_keys("operations_archive/data_quality_logs/")
                                            if "run_id=run_fn/" in k)))
    return code, {(r["instrument_code"], r["fiscal_year"], r["fiscal_period"], r["metric"], r["period_kind"],
                   r["fs_basis"]): r for r in rows}, log


def test_dart_live_quarter_vs_cumulative_currency_and_basis(tmp_path):
    # WHY: DART 가이드 — 분·반기 손익의 thstrm_amount 는 [3개월], thstrm_add_amount 는 누적. 삼성 2026 반기 매출 3개월
    # 171,499,470,000,000 < 누적 305,372,914,000,000 이고, 연간(2025) 333,605,938,000,000 > 9개월 누적 239,768,567,000,000.
    code, rows, log = _normalized(tmp_path, _corp_fixtures())
    q2 = rows[("005930", 2026, "Q2", "revenue", "QUARTER", "CFS")]
    assert q2["value"] == "171499470000000" and q2["unit"] == "KRW" and q2["derivation"] == "REPORTED"
    assert rows[("005930", 2026, "Q2", "revenue", "CUMULATIVE", "CFS")]["value"] == "305372914000000"
    assert rows[("005930", 2026, "Q2", "eps_basic", "QUARTER", "CFS")]["value"] == "10849"
    assert rows[("005930", 2026, "Q2", "eps_basic", "CUMULATIVE", "CFS")]["value"] == "17950"
    assert rows[("005930", 2026, "Q2", "eps_basic", "QUARTER", "OFS")]["value"] == "10211"      # 별도는 따로 남는다
    assert rows[("005930", 2025, "FY", "revenue", "CUMULATIVE", "CFS")]["value"] == "333605938000000"
    # Q4 = FY − 9M: 매출은 정확, EPS 는 가중평균 주식수 차이로 근사(6605 − 3724).
    assert rows[("005930", 2025, "Q4", "revenue", "QUARTER", "CFS")]["value"] == "93837371000000"
    q4 = rows[("005930", 2025, "Q4", "eps_basic", "QUARTER", "CFS")]
    assert q4["value"] == "2881" and q4["derivation"] == "FY_MINUS_9M"
    # 접수일 → 가시시각: 반기보고서 접수 2026-08-14 → 08-15 00:00 KST 부터.
    assert q2["rcept_date"] == "2026-08-14" and q2["available_at"] == "2026-08-14T15:00:00+00:00"


def test_dart_live_company_without_income_statement_uses_cis(tmp_path):
    # WHY: SK하이닉스는 IS 가 없고 CIS 에 "기본주당반기순이익" 으로 실린다 — IS→CIS 대체가 없으면 EPS 가 통째로 빠진다.
    _, rows, _ = _normalized(tmp_path, _corp_fixtures())
    assert rows[("000660", 2026, "Q2", "eps_basic", "QUARTER", "CFS")]["value"] == "132126"
    assert rows[("000660", 2026, "Q2", "revenue", "QUARTER", "CFS")]["value"] == "79318746000000"


def test_dart_live_bps_common_share_only_when_no_preferred(tmp_path):
    # WHY: 삼성전자는 우선주 802,371,203주가 있어 보통주 순수 BPS 를 만들 수 없다 — 통상값만 남기고 bps 는 막는다.
    # SK하이닉스는 우선주 '-'(0) → 보통주 기준 BPS 를 만든다. 분모는 자기주식을 뺀 발행주식수, 기준일 stlm_dt.
    code, rows, log = _normalized(tmp_path, _corp_fixtures())
    assert ("005930", 2026, "Q2", "bps", "POINT", "CFS") not in rows
    total = rows[("005930", 2026, "Q2", "bps_total_shares", "POINT", "CFS")]
    equity, shares = Decimal("565064740000000"), Decimal(6648649811 - 82086705)
    assert total["value"] == str((equity / shares).quantize(Decimal("0.000001")))
    inputs = json.loads(total["inputs"])
    assert inputs[1]["preferred_istc_totqy"] == "802371203" and inputs[1]["stlm_dt"] == "2026-06-30"
    assert inputs[1]["common_bps"] == "bps_blocked_preferred_shares"   # DB bps_note 가 읽는 판정 — 정책 차단
    # 정책 차단은 실행 실패가 아니라 분류된 결손으로 남는다(ALPHA-1169).
    assert any(f["reasons"] == ["bps_blocked_preferred_shares"] and f["class"] == "policy" for f in log["gaps"])
    assert not any("bps_blocked_preferred_shares" in f["reasons"] for f in log["failures"])
    hynix = rows[("000660", 2026, "Q2", "bps", "POINT", "CFS")]
    assert hynix["value"] == str((Decimal("262380610000000") / Decimal(712702365 - 1626865)).quantize(Decimal("0.000001")))
    assert json.loads(hynix["inputs"])[1]["se"] == "보통주"


def test_dart_live_standalone_bps_uses_total_equity_and_the_same_share_table(tmp_path):
    # WHY: 별도(OFS) 재무제표엔 비지배지분이 없어 분자가 자본총계(ifrs-full_Equity)다 — 연결(CFS)은 지배기업 소유주지분.
    # 실응답(삼성 2026 반기): 별도 자본총계 358,350,799,000,000 · 연결 자본총계 579,309,676,000,000 · 연결 지배기업
    # 소유주지분 565,064,740,000,000. 분모는 두 기준 모두 같은 주식총수 표(합계 발행 − 자기주식). 기대값은 응답 값에서
    # 직접 계산한다(코드의 계정 선택을 베끼지 않는다). 우선주가 있어 보통주 bps 는 두 기준 모두 막힌다.
    code, rows, log = _normalized(tmp_path, _corp_fixtures())
    ofs = json.loads(live("dart_stmt_samsung_2026_11012_OFS.json"))["list"]
    equity = next(Decimal(ln["thstrm_amount"]) for ln in ofs if ln["sj_div"] == "BS" and ln["account_id"] == "ifrs-full_Equity")
    assert equity == Decimal("358350799000000")
    assert not any(ln["account_id"] == "ifrs-full_EquityAttributableToOwnersOfParent" for ln in ofs)   # 별도엔 없다
    shares = Decimal(6648649811 - 82086705)
    total = rows[("005930", 2026, "Q2", "bps_total_shares", "POINT", "OFS")]
    assert total["value"] == str((equity / shares).quantize(Decimal("0.000001"))) == "54572.048302"
    inputs = json.loads(total["inputs"])
    assert inputs[0]["account_id"] == "ifrs-full_Equity" and inputs[0]["value"] == "358350799000000"
    assert (inputs[1]["istc_totqy"], inputs[1]["tesstk_co"]) == ("6648649811", "82086705")
    assert ("005930", 2026, "Q2", "bps", "POINT", "OFS") not in rows
    cfs = rows[("005930", 2026, "Q2", "bps_total_shares", "POINT", "CFS")]
    assert json.loads(cfs["inputs"])[0]["account_id"] == "ifrs-full_EquityAttributableToOwnersOfParent"


def test_dart_live_correction_receipt_is_the_one_the_api_returns(tmp_path):
    # WHY: 고려제강 반기보고서(2026.06)는 08-14 원본과 09-29 [기재정정]이 있고, 재무 API 는 정정본(20260929000540)만 준다.
    # 그 판본의 가시시각은 정정 접수일 기준(09-30 00:00 KST)이어야 한다 — 최초 공시일(08-14)에 붙이면 안 된다.
    corp = {"corp_code": "00159193", "stock_code": "002240", "corp_name": "고려제강"}
    body = json.loads(live("dart_stmt_goryeo_2026_11012_CFS.json"))
    corp["corp_code"] = body["list"][0]["corp_code"]
    dart = DartFake([corp], {("statement", corp["corp_code"], "2026", "11012", "CFS"): live("dart_stmt_goryeo_2026_11012_CFS.json")},
                    {corp["corp_code"]: live("dart_list_goryeo_p1.json")})
    dart.fetched_at = "2026-10-01T01:00:00+00:00"        # 실호출처럼 정정 뒤에 받았다
    _, rows, _ = _normalized(tmp_path, dart, from_date="2026-09-01", to_date="2026-09-30", holdings=("002240",),
                             now=datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc))
    listed = {r["rcept_no"]: r for r in json.loads(live("dart_list_goryeo_p1.json"))["list"]}
    assert listed["20260929000540"]["report_nm"].startswith("[기재정정]") and "20260814004051" in listed
    row = rows[("002240", 2026, "Q2", "revenue", "QUARTER", "CFS")]
    assert row["rcept_no"] == "20260929000540" and row["rcept_date"] == "2026-09-29"
    assert row["available_at"] == "2026-09-29T15:00:00+00:00" and row["availability_basis"] == "provider_release_date"


def test_dart_live_annual_report_share_table_is_dated_at_fiscal_year_end():
    # WHY(실응답 2026-09-30): 사업보고서(11011) 주식총수 표의 stlm_dt 가 12-31 이어야 `_bps` 의 기준일=보고기간 말
    # 검사가 실데이터에서 통과한다 — 반기(06-30)만 보고 세운 규칙을 연간 표본으로 확인했다.
    for tag, corp in (("samsung", SAMSUNG), ("hynix", HYNIX)):
        body = json.loads(live(f"dart_shares_{tag}_2025_11011.json"))
        assert {r["stlm_dt"] for r in body["list"]} == {"2025-12-31"}
    corp = {"corp_code": SAMSUNG["corp_code"], "stock_code": "005930"}
    rows, rejects = dart_fundamental.extract(
        corp, "2025", "11011", "CFS", {"body_json": json.loads(live("dart_stmt_samsung_2025_11011_CFS.json"))},
        json.loads(live("dart_shares_samsung_2025_11011.json")))
    assert [r["fiscal_period"] for r in rows if r["metric"] == "bps_total_shares"] == ["Q4"]
    assert not any("share_rows_inconsistent" in r["reasons"] for r in rejects)
