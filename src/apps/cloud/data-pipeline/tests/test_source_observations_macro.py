"""매크로 원천 관측 수집·정제 계약 (ALPHA-1130). 실호출 없이 fixture 응답으로 돈다.

각 테스트는 "무엇이 틀리면 분석이 어떻게 틀리는가"를 막는다 — 가시시각·정상 0건·정정·늦은 옛 실행·
단위·재호출 금지. DB 적재·기준시각 조회는 tests/e2e/test_source_observations_pg.py 가 본다.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from data_pipeline.config import MacroObservationSource
from data_pipeline.lake import LocalStorage, canonical_macro_observation_partition, raw_run_manifest_key
from data_pipeline.sources import macro_series
from data_pipeline.sources.http import StopFetch
from data_pipeline.steps import source_observations as so

FIXTURES = Path(__file__).parent / "fixtures" / "source_observations"
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc)   # KST 07-29 10:00 → 정기 창 끝 = 07-28


def body(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeClient:
    """URL 조각 → 응답. 부른 횟수를 센다(재호출 금지 검사)."""

    def __init__(self, routes: dict):
        self.routes, self.calls = routes, []

    def request(self, method, url, *, headers=None, data=None, decode=True):
        self.calls.append(url)
        for fragment, response in self.routes.items():
            if fragment in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"route 없음: {url}")


ALL_ROUTES = {
    "historical-price-eod": body("fmp_usdkrw.json"),
    "treasury-rates": body("fmp_treasury.json"),
    "StatisticSearch": body("ecos_kr10y.json"),
    "statisticsParameterData": body("kosis_cpi.json"),
    "petroleum/pri/spt": body("eia_brent.json"),
}
KEYS = {"ecos_api_key": "E", "kosis_api_key": "K", "eia_api_key": "A"}


def source(routes=ALL_ROUTES, keys=KEYS, fmp="F"):
    client = FakeClient(dict(routes))
    return macro_series.MacroSource(MacroObservationSource(**keys), fmp_api_key=fmp, client=client), client


def canonical_rows(storage, series_id, day):
    key = f"{canonical_macro_observation_partition(series_id, day)}/part-00000.parquet"
    return so.read_rows(so.MACRO, storage.get_bytes(key))


def collect(storage, run_id, src, series=None, **kw):
    return so.collect_macro(storage, src, run_id, series_ids=series or sorted(macro_series.SERIES),
                            from_date=kw.get("from_date"), to_date=kw.get("to_date"), now=kw.get("now", NOW))


def test_regular_run_lands_every_series_with_receipt_as_visibility(tmp_path):
    # WHY: 공급자가 공표 시각을 주지 않는다. 가시시각을 관측일 기준으로 지어내면 과거 분석이 그때
    # 없던 값을 본다 — 가시시각은 실제 수신시각이어야 한다(2026-09-30 결정).
    storage = LocalStorage(tmp_path)
    src, _ = source()
    assert collect(storage, "run_raw1", src) == 0
    assert so.normalize(storage, so.MACRO, "run_norm1", "run_raw1", producer="normalize_macro") == 0

    usd = canonical_rows(storage, "usd_krw", "2026-07-27")
    assert [r["value"] for r in usd] == ["1464.671"]            # 공급자 소수 자릿수 그대로
    assert usd[0]["unit"] == "KRW_per_USD" and usd[0]["availability_basis"] == "received"
    assert usd[0]["available_at"] == usd[0]["received_at"]
    cpi = canonical_rows(storage, "kr_cpi_yoy", "2026-06-01")
    assert cpi[0]["value"] == "2.3" and cpi[0]["source_vendor"] == "kosis"   # 공표 전년동월비 그대로
    manifest = json.loads(storage.get_bytes(
        "operations_archive/canonical_run_manifests/dataset=macro_observation/run_id=run_norm1/manifest.json"))
    assert manifest["canonical_written"] is True and manifest["input_run_id"] == "run_raw1"
    assert manifest["artifact"]["partition_date"] == "ingest_date"


def test_rerun_with_the_same_run_id_does_not_call_vendors_again(tmp_path):
    # WHY: Airflow·운영자 재실행이 같은 run_id 로 공급자를 다시 부르면 한도를 태우고, 다른 바이트가
    # 같은 실행의 raw 로 섞인다. 완료 manifest 가 있으면 재수집하지 않는다.
    storage = LocalStorage(tmp_path)
    src, client = source()
    assert collect(storage, "run_same", src) == 0
    calls = len(client.calls)
    assert collect(storage, "run_same", src) == 0
    assert len(client.calls) == calls


def test_vendor_no_data_is_empty_not_failure_and_one_vendor_failure_is_partial(tmp_path):
    # WHY: "그 기간 관측 없음"(휴장·미공표)과 "받지 못함"을 섞으면 결손이 정상으로 위장되거나
    # 휴장일마다 거짓 경보가 난다. 한 공급자 장애가 다른 계열 적재를 막아서도 안 된다.
    storage = LocalStorage(tmp_path)
    routes = {**ALL_ROUTES,
              "StatisticSearch": json.dumps({"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}).encode(),
              "petroleum/pri/spt": StopFetch("HTTP 503", status=503)}
    src, _ = source(routes)
    assert collect(storage, "run_p", src) == so.PARTIAL_EXIT
    manifest = json.loads(storage.get_bytes(raw_run_manifest_key("macro_observation", "run_p")))
    by_series = {o["series_id"]: o for o in manifest["objects"]}
    assert by_series["kr_10y_yield"]["status"] == "empty"
    assert by_series["brent_spot_usd"]["status"] == "error" and "key" not in by_series["brent_spot_usd"]
    assert so.normalize(storage, so.MACRO, "run_pn", "run_p", producer="normalize_macro") == 0
    assert canonical_rows(storage, "usd_krw", "2026-07-28")[0]["value"] == "1461.35"


def test_missing_key_fails_that_series_without_calling_it(tmp_path):
    # WHY: 키 없이 부르면 4xx 가 수집 장애로 위장된다. 키 주입 누락은 그 계열의 실패로 드러낸다.
    storage = LocalStorage(tmp_path)
    src, client = source(keys={"ecos_api_key": None, "kosis_api_key": "K", "eia_api_key": "A"})
    assert collect(storage, "run_k", src) == so.PARTIAL_EXIT
    assert not any("StatisticSearch" in url for url in client.calls)
    log = json.loads(storage.get_bytes(next(k for k in storage.list_keys("operations_archive/collection_logs/")
                                            if "run_id=run_k/" in k)))
    assert log["ops"]["failed_records"] == 1 and log["failures"][0]["detail"] == "missing_credentials"


def test_value_revised_in_a_later_run_wins_even_if_the_old_run_normalizes_last(tmp_path):
    # WHY: 공급자가 값을 고치면 새 판본이 현재값이어야 하고, 늦게 끝난 옛 실행이 그걸 되덮으면 안 된다.
    # 현재값은 적재 순서가 아니라 **수신 순서**로 정한다.
    storage = LocalStorage(tmp_path)
    old_src, _ = source()
    assert collect(storage, "run_old", old_src, series=["usd_krw"]) == 0
    revised = json.loads(body("fmp_usdkrw.json"))
    revised[1]["close"] = 1465.0
    new_src, _ = source({**ALL_ROUTES, "historical-price-eod": json.dumps(revised).encode()})
    assert collect(storage, "run_new", new_src, series=["usd_krw"]) == 0
    assert so.normalize(storage, so.MACRO, "run_new_n", "run_new", producer="normalize_macro") == 0
    assert so.normalize(storage, so.MACRO, "run_old_n", "run_old", producer="normalize_macro") == 0
    rows = canonical_rows(storage, "usd_krw", "2026-07-27")
    assert [(r["value"], r["raw_run_id"]) for r in rows] == [("1465.0", "run_new")]
    # 옛 판본은 버려지지 않고 실행별 artifact 에 남는다(DB 판본 이력의 입력).
    old_manifest = json.loads(storage.get_bytes(
        "operations_archive/canonical_run_manifests/dataset=macro_observation/run_id=run_old_n/manifest.json"))
    old_rows = so.read_rows(so.MACRO, storage.get_bytes(old_manifest["artifact"]["key"]))
    assert {r["value"] for r in old_rows if r["observation_date"] == "2026-07-27"} == {"1464.671"}


def test_incomplete_and_out_of_window_observations_are_rejected_with_reasons(tmp_path):
    # WHY: 수신 당일(KST)의 관측은 아직 진행 중 세션이다 — 확정값처럼 적재하면 다음날 바뀐다.
    # 요청하지 않은 기간을 섞으면 백필 범위 감사가 거짓이 된다.
    storage = LocalStorage(tmp_path)
    today = datetime.now(KST).date().isoformat()
    rows = json.loads(body("fmp_usdkrw.json")) + [
        {"symbol": "USDKRW", "date": today, "close": 1470.0},
        {"symbol": "USDKRW", "date": "2026-01-02", "close": 1400.0}]
    src, _ = source({**ALL_ROUTES, "historical-price-eod": json.dumps(rows).encode()})
    real_now = datetime.now(timezone.utc)
    assert so.collect_macro(storage, src, "run_i", series_ids=["usd_krw"],
                            from_date="2026-07-20", to_date=(real_now.astimezone(KST).date() - timedelta(days=1)).isoformat(),
                            now=real_now) == 0
    assert so.normalize(storage, so.MACRO, "run_in", "run_i", producer="normalize_macro") == so.PARTIAL_EXIT
    log = json.loads(storage.get_bytes(next(k for k in storage.list_keys("operations_archive/data_quality_logs/")
                                            if "run_id=run_in/" in k)))
    reasons = {f["observation_date"]: f["reasons"] for f in log["failures"]}
    assert "incomplete_period" in reasons[today] and "outside_request_window" in reasons[today]
    assert reasons["2026-01-02"] == ["outside_request_window"]


def test_duplicate_rows_collapse_but_conflicting_duplicates_are_quarantined(tmp_path):
    # WHY: 같은 관측이 두 번 오면 하나로 접으면 된다. 값이 다른 중복은 어느 쪽이 맞는지 모른다 —
    # 조용히 하나를 고르면 틀린 값이 현재값이 될 수 있어 격리해 드러낸다.
    storage = LocalStorage(tmp_path)
    rows = json.loads(body("fmp_usdkrw.json"))
    rows += [dict(rows[0]), {**rows[1], "close": 1.0}]
    src, _ = source({**ALL_ROUTES, "historical-price-eod": json.dumps(rows).encode()})
    assert collect(storage, "run_d", src, series=["usd_krw"]) == 0
    assert so.normalize(storage, so.MACRO, "run_dn", "run_d", producer="normalize_macro") == so.PARTIAL_EXIT
    manifest = json.loads(storage.get_bytes(
        "operations_archive/canonical_run_manifests/dataset=macro_observation/run_id=run_dn/manifest.json"))
    assert manifest["collapsed_duplicates"] == 1 and manifest["rejected"] == 1
    assert not storage.list_keys(canonical_macro_observation_partition("usd_krw", "2026-07-27"))


def test_unit_or_series_identity_mismatch_is_rejected_not_relabelled(tmp_path):
    # WHY: 단위를 숫자로 추정하면 %·%p·원 판정이 틀린다. 다른 항목(예: 전월비)을 전년동월비로 라벨하면
    # 물가 설명이 조용히 틀린다. 기대와 다른 응답은 버리지 않고 사유로 드러낸다.
    ecos = json.loads(body("ecos_kr10y.json"))
    ecos["StatisticSearch"]["row"][0]["UNIT_NAME"] = "%"
    kosis = json.loads(body("kosis_cpi.json"))
    kosis[0]["ITM_NM"] = "전월비"
    good, bad = macro_series.parse("kr_10y_yield", json.dumps(ecos).encode())
    assert len(good) == 2 and bad[0]["reasons"] == ["unit_mismatch"]
    good, bad = macro_series.parse("kr_cpi_yoy", json.dumps(kosis).encode())
    assert len(good) == 1 and bad[0]["reasons"] == ["series_identity_mismatch"]


def test_backfill_cannot_reach_today_and_regular_window_ends_yesterday():
    # WHY: "과거 날짜 요청이 오늘 자료를 수집"하거나 진행 중 관측을 확정값으로 라벨하는 길을 막는다.
    today = date(2026, 7, 29)
    assert so.macro_window("usd_krw", today, None, None) == (date(2026, 7, 15), date(2026, 7, 28))
    with pytest.raises(SystemExit):
        so.macro_window("usd_krw", today, "2026-07-01", "2026-07-29")
    assert so.macro_window("usd_krw", today, "2026-01-01", "2026-03-31") == (date(2026, 1, 1), date(2026, 3, 31))


def test_long_backfill_is_split_by_vendor_window_limit():
    # WHY: FMP treasury-rates 는 한 요청 기간이 짧게 제한된다 — 한 번에 부르면 앞부분이 조용히 잘린다.
    windows = macro_series.request_windows(macro_series.SERIES["us_10y_yield"], date(2026, 1, 1), date(2026, 6, 30))
    assert windows[0] == (date(2026, 1, 1), date(2026, 3, 31)) and windows[-1][1] == date(2026, 6, 30)
    assert all((b - a).days < 90 for a, b in windows)


def test_request_record_never_contains_credentials(tmp_path):
    # WHY: raw manifest·로그는 만료 없이 남는다. 키가 들어가면 영구 유출이다.
    storage = LocalStorage(tmp_path)
    src, _ = source(keys={"ecos_api_key": "SECRET-E", "kosis_api_key": "SECRET-K", "eia_api_key": "SECRET-A"},
                    fmp="SECRET-F")
    assert collect(storage, "run_s", src) == 0
    for key in storage.list_keys("operations_archive/"):
        assert b"SECRET-" not in storage.get_bytes(key), key


def test_malformed_rows_and_missing_ecos_rows_are_visible_not_fatal(tmp_path):
    # WHY(리뷰): 응답 속 null 한 칸이 정제 전체를 죽이면 정상 계열까지 적재되지 않고, 완료 manifest 때문에
    # 재실행으로도 회복되지 않는다. ECOS row 누락은 "데이터 없음"(INFO-200)이 아니라 파손이다.
    storage = LocalStorage(tmp_path)
    routes = {**ALL_ROUTES,
              "historical-price-eod": json.dumps([None, *json.loads(body("fmp_usdkrw.json"))]).encode(),
              "StatisticSearch": json.dumps({"StatisticSearch": {}}).encode(),
              "statisticsParameterData": json.dumps({"RESULT": [1]}).encode()}
    src, _ = source(routes)
    assert collect(storage, "run_m", src) == so.PARTIAL_EXIT
    manifest = json.loads(storage.get_bytes(raw_run_manifest_key("macro_observation", "run_m")))
    status = {o["series_id"]: (o["status"], o["detail"]) for o in manifest["objects"]}
    assert status["kr_10y_yield"] == ("error", "missing_rows")
    assert status["kr_cpi_yoy"][0] == "error"
    assert so.normalize(storage, so.MACRO, "run_mn", "run_m", producer="normalize_macro") == so.PARTIAL_EXIT
    assert canonical_rows(storage, "usd_krw", "2026-07-27")[0]["value"] == "1464.671"


def test_vendor_decimals_survive_json_parsing():
    # WHY(리뷰): JSON 숫자를 float 로 먼저 읽으면 "공급자 자릿수 그대로" 계약이 저장 전에 깨진다.
    raw = b'[{"symbol": "USDKRW", "date": "2026-07-27", "close": 1461.1234567890123456789}]'
    good, _ = macro_series.parse("usd_krw", raw)
    assert good[0]["value"] == "1461.1234567890123456789"


def test_rerun_reports_the_original_failure_instead_of_success(tmp_path):
    # WHY(리뷰): 완료 manifest 재사용이 status=success 로 쓰면 전부 실패한 수집이 재실행 한 번으로 성공이 된다.
    storage = LocalStorage(tmp_path)
    src, _ = source({k: StopFetch("HTTP 500", status=500) for k in ALL_ROUTES})
    assert collect(storage, "run_e", src) == 1
    assert collect(storage, "run_e", src) == 1
    logs = [json.loads(storage.get_bytes(k)) for k in storage.list_keys("operations_archive/collection_logs/")
            if "run_id=run_e/" in k]
    assert {log["status"] for log in logs} == {"error"}
