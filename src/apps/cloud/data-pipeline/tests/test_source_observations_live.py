"""2026-09-30 소량 실응답(축약 원문, `fixtures/source_observations/live/`) 위에서 파서·정제 의미를 고정한다.

기대값은 fixture 에서 베끼지 않고 공식 설명과 대조했다: ECOS 항목명, KOSIS 항목 메타, EIA series-description,
FMP treasury-rates 필드. 실응답 모듈 도크스트링(설계 §10.8)이 근거다.
"""

from __future__ import annotations

import json
from pathlib import Path

from data_pipeline.sources import macro_series

LIVE = Path(__file__).parent / "fixtures" / "source_observations" / "live"


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


def test_fmp_treasury_live_row_is_percent_per_maturity():
    good, bad = macro_series.parse("us_10y_yield", live("fmp_treasury_rates.json"))
    assert bad == [] and good[0] == {"observation_date": "2026-09-25", "value": "5.17"}
    assert macro_series.SERIES["us_10y_yield"].unit == "percent"
