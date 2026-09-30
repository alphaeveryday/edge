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
from data_pipeline.steps import source_observations as so, source_observations_macro as so_macro

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
    "731Y003": body("ecos_usdkrw.json"),
    "treasury-rates": body("fmp_treasury.json"),
    "817Y002": body("ecos_kr10y.json"),
    "statisticsParameterData": body("kosis_cpi.json"),
    "petroleum/pri/spt": body("eia_brent.json"),
}
KEYS = {"ecos_api_key": "E", "kosis_api_key": "K", "eia_api_key": "A"}


def source(routes=ALL_ROUTES, keys=KEYS, fmp="F"):
    client = FakeClient(dict(routes))
    return macro_series.MacroSource(MacroObservationSource(**keys), fmp_api_key=fmp, client=client), client


def canonical_rows(storage, series_id, day):
    key = f"{canonical_macro_observation_partition(series_id, day)}/part-00000.parquet"
    return so.read_rows(so_macro.MACRO, storage.get_bytes(key))


def collect(storage, run_id, src, series=None, **kw):
    return so_macro.collect_macro(storage, src, run_id, series_ids=series or sorted(macro_series.SERIES),
                            from_date=kw.get("from_date"), to_date=kw.get("to_date"), now=kw.get("now", NOW))


def test_regular_run_lands_every_series_with_receipt_as_visibility(tmp_path):
    # WHY: 공급자가 공표 시각을 주지 않는다. 가시시각을 관측일 기준으로 지어내면 과거 분석이 그때
    # 없던 값을 본다 — 가시시각은 실제 수신시각이어야 한다(2026-09-30 결정).
    storage = LocalStorage(tmp_path)
    src, _ = source()
    assert collect(storage, "run_raw1", src) == 0
    assert so.normalize(storage, so_macro.MACRO, "run_norm1", "run_raw1", producer="normalize_macro") == 0

    usd = canonical_rows(storage, "usd_krw", "2026-07-27")
    assert [r["value"] for r in usd] == ["1464.671"]            # 공급자 소수 자릿수 그대로
    assert usd[0]["unit"] == "KRW_per_USD" and usd[0]["source_vendor"] == "ecos"
    assert usd[0]["availability_basis"] == "received"
    assert usd[0]["available_at"] == usd[0]["received_at"]
    cpi = canonical_rows(storage, "kr_cpi_yoy", "2026-06-01")
    assert cpi[0]["value"] == "3.2" and cpi[0]["source_vendor"] == "kosis"   # 공표 전년동월비 그대로(실응답 2026-06)
    manifest = json.loads(storage.get_bytes(
        "operations_archive/canonical_run_manifests/dataset=macro_observation/run_id=run_norm1/manifest.json"))
    assert manifest["canonical_written"] is True and manifest["input_run_id"] == "run_raw1"
    assert manifest["artifact"]["partition_date"] == "ingest_date"
    # 복구 계약: 어떤 코드 판이 이 정본을 만들었는지 manifest 가 말해야 재현 범위를 주장할 수 있다.
    assert manifest["code_version"] == "unknown"   # 테스트는 GIT_SHA 미주입
    raw_manifest = json.loads(storage.get_bytes(so.raw_run_manifest_key("macro_observation", "run_raw1")))
    assert raw_manifest["code_version"] == "unknown"


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
              "817Y002": json.dumps({"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}).encode(),
              "petroleum/pri/spt": StopFetch("HTTP 503", status=503)}
    src, _ = source(routes)
    assert collect(storage, "run_p", src) == so.PARTIAL_EXIT
    manifest = json.loads(storage.get_bytes(raw_run_manifest_key("macro_observation", "run_p")))
    by_series = {o["series_id"]: o for o in manifest["objects"]}
    assert by_series["kr_10y_yield"]["status"] == "empty"
    assert by_series["brent_spot_usd"]["status"] == "error" and "key" not in by_series["brent_spot_usd"]
    assert so.normalize(storage, so_macro.MACRO, "run_pn", "run_p", producer="normalize_macro") == 0
    assert canonical_rows(storage, "usd_krw", "2026-07-28")[0]["value"] == "1461.35"


def test_missing_key_fails_that_series_without_calling_it(tmp_path):
    # WHY: 키 없이 부르면 4xx 가 수집 장애로 위장된다. 키 주입 누락은 그 계열의 실패로 드러낸다.
    storage = LocalStorage(tmp_path)
    src, client = source(keys={"ecos_api_key": None, "kosis_api_key": "K", "eia_api_key": "A"})
    assert collect(storage, "run_k", src) == so.PARTIAL_EXIT
    assert not any("ecos" in url for url in client.calls)
    log = json.loads(storage.get_bytes(next(k for k in storage.list_keys("operations_archive/collection_logs/")
                                            if "run_id=run_k/" in k)))
    # ECOS 키 하나가 두 계열(국고채 10년·원/달러)을 막는다.
    assert log["ops"]["failed_records"] == 2 and {f["detail"] for f in log["failures"]} == {"missing_credentials"}


def test_value_revised_in_a_later_run_wins_even_if_the_old_run_normalizes_last(tmp_path):
    # WHY: 공급자가 값을 고치면 새 판본이 현재값이어야 하고, 늦게 끝난 옛 실행이 그걸 되덮으면 안 된다.
    # 현재값은 적재 순서가 아니라 **수신 순서**로 정한다.
    storage = LocalStorage(tmp_path)
    old_src, _ = source()
    assert collect(storage, "run_old", old_src, series=["usd_krw"]) == 0
    revised = json.loads(body("ecos_usdkrw.json"))
    revised["StatisticSearch"]["row"][1]["DATA_VALUE"] = "1465.0"
    new_src, _ = source({**ALL_ROUTES, "731Y003": json.dumps(revised).encode()})
    assert collect(storage, "run_new", new_src, series=["usd_krw"]) == 0
    assert so.normalize(storage, so_macro.MACRO, "run_new_n", "run_new", producer="normalize_macro") == 0
    assert so.normalize(storage, so_macro.MACRO, "run_old_n", "run_old", producer="normalize_macro") == 0
    rows = canonical_rows(storage, "usd_krw", "2026-07-27")
    assert [(r["value"], r["raw_run_id"]) for r in rows] == [("1465.0", "run_new")]
    # 옛 판본은 버려지지 않고 실행별 artifact 에 남는다(DB 판본 이력의 입력).
    old_manifest = json.loads(storage.get_bytes(
        "operations_archive/canonical_run_manifests/dataset=macro_observation/run_id=run_old_n/manifest.json"))
    old_rows = so.read_rows(so_macro.MACRO, storage.get_bytes(old_manifest["artifact"]["key"]))
    assert {r["value"] for r in old_rows if r["observation_date"] == "2026-07-27"} == {"1464.671"}


def test_incomplete_and_out_of_window_observations_are_rejected_with_reasons(tmp_path):
    # WHY: 수신 당일(KST)의 관측은 아직 진행 중 세션이다 — 확정값처럼 적재하면 다음날 바뀐다.
    # 요청하지 않은 기간을 섞으면 백필 범위 감사가 거짓이 된다.
    storage = LocalStorage(tmp_path)
    today = datetime.now(KST).date().isoformat()
    doc = json.loads(body("ecos_usdkrw.json"))
    tmpl = doc["StatisticSearch"]["row"][0]
    doc["StatisticSearch"]["row"] += [{**tmpl, "TIME": today.replace("-", ""), "DATA_VALUE": "1470"},
                                      {**tmpl, "TIME": "20260102", "DATA_VALUE": "1400"}]
    src, _ = source({**ALL_ROUTES, "731Y003": json.dumps(doc).encode()})
    real_now = datetime.now(timezone.utc)
    assert so_macro.collect_macro(storage, src, "run_i", series_ids=["usd_krw"],
                            from_date="2026-07-20", to_date=(real_now.astimezone(KST).date() - timedelta(days=1)).isoformat(),
                            now=real_now) == 0
    assert so.normalize(storage, so_macro.MACRO, "run_in", "run_i", producer="normalize_macro") == so.PARTIAL_EXIT
    log = json.loads(storage.get_bytes(next(k for k in storage.list_keys("operations_archive/data_quality_logs/")
                                            if "run_id=run_in/" in k)))
    reasons = {f["observation_date"]: f["reasons"] for f in log["failures"]}
    assert "incomplete_period" in reasons[today] and "outside_request_window" in reasons[today]
    assert reasons["2026-01-02"] == ["outside_request_window"]


def test_duplicate_rows_collapse_but_conflicting_duplicates_are_quarantined(tmp_path):
    # WHY: 같은 관측이 두 번 오면 하나로 접으면 된다. 값이 다른 중복은 어느 쪽이 맞는지 모른다 —
    # 조용히 하나를 고르면 틀린 값이 현재값이 될 수 있어 격리해 드러낸다.
    storage = LocalStorage(tmp_path)
    doc = json.loads(body("ecos_usdkrw.json"))
    rows = doc["StatisticSearch"]["row"]
    rows += [dict(rows[0]), {**rows[1], "DATA_VALUE": "1.0"}]
    src, _ = source({**ALL_ROUTES, "731Y003": json.dumps(doc).encode()})
    assert collect(storage, "run_d", src, series=["usd_krw"]) == 0
    assert so.normalize(storage, so_macro.MACRO, "run_dn", "run_d", producer="normalize_macro") == so.PARTIAL_EXIT
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
    kosis[0]["ITM_NM"] = "전월비(%)"      # 단위 표기는 같고 항목만 다른 행 — 실응답엔 UNIT_NM 이 없다
    good, bad = macro_series.parse("kr_10y_yield", json.dumps(ecos).encode())
    assert len(good) == 2 and bad[0]["reasons"] == ["unit_mismatch"]
    good, bad = macro_series.parse("kr_cpi_yoy", json.dumps(kosis).encode())
    assert len(good) == 1 and bad[0]["reasons"] == ["series_identity_mismatch"]


def test_backfill_cannot_reach_today_and_regular_window_ends_yesterday():
    # WHY: "과거 날짜 요청이 오늘 자료를 수집"하거나 진행 중 관측을 확정값으로 라벨하는 길을 막는다.
    today = date(2026, 7, 29)
    assert so_macro.macro_window("usd_krw", today, None, None) == (date(2026, 7, 15), date(2026, 7, 28))
    with pytest.raises(SystemExit):
        so_macro.macro_window("usd_krw", today, "2026-07-01", "2026-07-29")
    assert so_macro.macro_window("usd_krw", today, "2026-01-01", "2026-03-31") == (date(2026, 1, 1), date(2026, 3, 31))


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
    doc = json.loads(body("ecos_usdkrw.json"))
    doc["StatisticSearch"]["row"].insert(0, None)
    routes = {**ALL_ROUTES,
              "731Y003": json.dumps(doc).encode(),
              "817Y002": json.dumps({"StatisticSearch": {}}).encode(),
              "statisticsParameterData": json.dumps({"RESULT": [1]}).encode()}
    src, _ = source(routes)
    assert collect(storage, "run_m", src) == so.PARTIAL_EXIT
    manifest = json.loads(storage.get_bytes(raw_run_manifest_key("macro_observation", "run_m")))
    status = {o["series_id"]: (o["status"], o["detail"]) for o in manifest["objects"]}
    assert status["kr_10y_yield"] == ("error", "missing_rows")
    assert status["kr_cpi_yoy"][0] == "error"
    assert so.normalize(storage, so_macro.MACRO, "run_mn", "run_m", producer="normalize_macro") == so.PARTIAL_EXIT
    assert canonical_rows(storage, "usd_krw", "2026-07-27")[0]["value"] == "1464.671"


def test_vendor_decimals_survive_json_parsing():
    # WHY(리뷰): JSON 숫자를 float 로 먼저 읽으면 "공급자 자릿수 그대로" 계약이 저장 전에 깨진다.
    raw = b'[{"date": "2026-07-27", "year10": 4.1234567890123456789}]'
    good, _ = macro_series.parse("us_10y_yield", raw)
    assert good[0]["value"] == "4.1234567890123456789"


def test_rerun_reports_the_original_failure_instead_of_success(tmp_path):
    # WHY(리뷰): 완료 manifest 재사용이 status=success 로 쓰면 전부 실패한 수집이 재실행 한 번으로 성공이 된다.
    storage = LocalStorage(tmp_path)
    src, _ = source({k: StopFetch("HTTP 500", status=500) for k in ALL_ROUTES})
    assert collect(storage, "run_e", src) == 1
    assert collect(storage, "run_e", src) == 1
    logs = [json.loads(storage.get_bytes(k)) for k in storage.list_keys("operations_archive/collection_logs/")
            if "run_id=run_e/" in k]
    assert {log["status"] for log in logs} == {"error"}


def test_monthly_window_starts_on_the_first_so_the_first_requested_month_is_kept():
    # WHY(리뷰 2차): KOSIS 요청은 월 단위인데 창 시작이 월 중간이면 요청한 첫 달(관측일=1일)이 정제에서
    # "창 밖"으로 떨어져 정기 실행이 매번 부분 실패가 된다.
    start, end = so_macro.macro_window("kr_cpi_yoy", date(2026, 9, 30), None, None)
    assert start.day == 1 and start <= date(2026, 5, 29)
    assert so_macro.macro_window("kr_cpi_yoy", date(2026, 9, 30), "2026-02-15", "2026-06-30")[0] == date(2026, 2, 1)


def test_ecos_rows_from_another_statistics_table_are_rejected():
    # WHY(리뷰 11차): 항목 코드·이름·단위가 같아도 다른 통계표(STAT_CODE)의 행은 요청한 계열이 아니다.
    body = json.loads((FIXTURES / "ecos_usdkrw.json").read_bytes())
    for row in body["StatisticSearch"]["row"]:
        row["STAT_CODE"] = "731Y001"
    good, bad = macro_series.parse("usd_krw", json.dumps(body).encode())
    assert good == [] and all("series_identity_mismatch" in r["reasons"] for r in bad) and bad


def test_reversed_monthly_backfill_is_rejected_before_month_alignment():
    # WHY(봇 P2): 월초 맞춤 뒤에 검사하면 02-20~02-01 같은 역전 창이 2월 한 달 수집으로 바뀐다.
    with pytest.raises(SystemExit, match="역전"):
        so_macro.macro_window("kr_cpi_yoy", date(2026, 9, 30), "2026-02-20", "2026-02-01")


def test_quality_log_failure_leaves_the_normalize_run_incomplete(tmp_path, monkeypatch):
    # WHY: 완료 manifest 가 검증 기록(quality_log)보다 먼저 서면, 기록이 실패한 실행도 `load --all` 이 싣고 소비
    # 마커를 남긴다 — 빠진 검증 기록이 영영 드러나지 않는다. 기록이 실패하면 그 정제는 미완료로 남아야 한다.
    storage = LocalStorage(tmp_path)
    src, _ = source()
    assert collect(storage, "q_raw", src) == 0
    original = storage.put_bytes

    def failing(key, data, *a, **k):
        if key.startswith("operations_archive/data_quality_logs/"):
            raise OSError("quality log write failed")
        return original(key, data, *a, **k)

    monkeypatch.setattr(storage, "put_bytes", failing)
    assert so.normalize(storage, so_macro.MACRO, "q_norm", "q_raw", producer="normalize_macro") == 1
    monkeypatch.setattr(storage, "put_bytes", original)
    manifest = json.loads(storage.get_bytes(
        "operations_archive/canonical_run_manifests/dataset=macro_observation/run_id=q_norm/manifest.json"))
    assert manifest["canonical_written"] is False
    # 적재의 미완료 회수 경로도 이 실행을 싣지 않는다(소비 마커 없음 → 검증 기록을 다시 만들 기회가 남는다).
    assert so._completed_manifest(storage, so_macro.MACRO, "q_norm") is None


def test_quality_log_failure_after_rejected_rows_is_a_hard_failure(tmp_path, monkeypatch):
    # WHY(봇 P2): 거부 행으로 이미 PARTIAL_EXIT(2)인 정제에서 검증 기록까지 실패하면 완료 manifest 가 없다.
    # 2 는 DAG 가 '충족'으로 넘기고 `load --all` 은 미완료 실행을 건너뛰어 성공 — 받은 행이 영영 안 실린다.
    storage = LocalStorage(tmp_path)
    doc = json.loads(body("ecos_usdkrw.json"))
    doc["StatisticSearch"]["row"].append({**doc["StatisticSearch"]["row"][0], "TIME": "20260102"})
    src, _ = source({**ALL_ROUTES, "731Y003": json.dumps(doc).encode()})
    real_now = datetime.now(timezone.utc)
    assert so_macro.collect_macro(storage, src, "qp_raw", series_ids=["usd_krw"], from_date="2026-07-20",
                                  to_date=(real_now.astimezone(KST).date() - timedelta(days=1)).isoformat(),
                                  now=real_now) == 0
    original = storage.put_bytes

    def failing(key, data, *a, **k):
        if key.startswith("operations_archive/data_quality_logs/"):
            raise OSError("quality log write failed")
        return original(key, data, *a, **k)

    monkeypatch.setattr(storage, "put_bytes", failing)
    assert so.normalize(storage, so_macro.MACRO, "qp_norm", "qp_raw", producer="normalize_macro") == 1


@pytest.mark.parametrize("extra", [{"from_date": "2026-01-01"}, {"to_date": "2026-01-31"}, {"series": "usd_krw"}])
@pytest.mark.parametrize("step", ["normalize-macro", "load-macro"])
def test_normalize_and_load_refuse_collection_scope_arguments(tmp_path, step, extra):
    # WHY(봇 P2): 정제·적재는 입력 실행 전체를 처리한다. 복구 창을 받아 두고 버리면 운영자가 요청한 것보다
    # 넓은 범위를 처리하고도 성공으로 끝난다 — 쓰지 않는 인자는 거부한다.
    from types import SimpleNamespace

    from data_pipeline import run as run_module

    args = SimpleNamespace(step=step, input_run_id="r", from_date=None, to_date=None, series=None,
                           all_partitions=False)
    for k, v in extra.items():
        setattr(args, k, v)
    settings = SimpleNamespace(source_observations=SimpleNamespace())  # 거부는 설정 내용을 읽기 전에 난다
    with pytest.raises(SystemExit, match="쓰지 않는다"):
        run_module._dispatch_observation(args, settings, LocalStorage(tmp_path), "run_scope")


def test_same_run_id_with_a_different_backfill_window_fails_instead_of_skipping(tmp_path):
    # WHY(봇 P1): 1년 단위 백필을 같은 분에 여럿 trigger 하면 슬롯이 같아 run_id 가 겹친다. 완료 manifest 만 보고
    # '이미 수집'으로 성공하면 뒤 범위는 공급자를 한 번도 부르지 않은 채 DAG 가 성공한다 — 범위가 다르면 거부한다.
    storage = LocalStorage(tmp_path)
    src, client = source()
    assert collect(storage, "run_bf", src, series=["usd_krw"], from_date="2025-01-01", to_date="2025-12-31") == 0
    calls = len(client.calls)
    with pytest.raises(SystemExit, match="다른 요청 범위"):
        collect(storage, "run_bf", src, series=["usd_krw"], from_date="2024-01-01", to_date="2024-12-31")
    assert len(client.calls) == calls
    # 같은 범위의 재시도는 그대로 '이미 수집'이다(재호출 없음).
    assert collect(storage, "run_bf", src, series=["usd_krw"], from_date="2025-01-01", to_date="2025-12-31") == 0
