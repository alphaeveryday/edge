"""KIS 지수업종 분류 수집·정제 계약 (ALPHA-1130).

마스터 ZIP 은 KIS 공식 헤더의 고정폭 형식으로 테스트 안에서 만든다(실파일 미확인 — 모듈 도크스트링).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from data_pipeline import run as run_module
from data_pipeline.lake import LocalStorage, canonical_sector_classification_partition
from data_pipeline.sources import kis_sector_master
from data_pipeline.sources.http import StopFetch
from data_pipeline.steps import source_observations as so, source_observations_sector as so_sector
from source_observation_fakes import KOSDAQ, KOSPI, NAMES, SectorClient as Client, filler, master_line, zipped
from source_observation_fakes import SECTOR_ROUTES as ROUTES


def rows_of(storage, market):
    keys = [k for k in storage.list_keys(f"canonical/reference/sector_classification/market={market}/")]
    return {r["instrument_code"]: r for k in keys for r in so.read_rows(so_sector.SECTOR, storage.get_bytes(k))}


WEDNESDAY = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)


def run_chain(tmp_path, routes, run_id="run_s", now=WEDNESDAY):
    storage = LocalStorage(tmp_path)
    code = so_sector.collect_sector(storage, Client(routes), "https://example.invalid/master", run_id, now=now)
    return storage, code




def test_three_levels_with_names_and_0000_as_no_classification(tmp_path):
    # WHY: `0000` 을 코드로 남기면 "분류 없음"이 가짜 업종 하나로 묶여 동종 비교가 오염된다.
    storage, code = run_chain(tmp_path, ROUTES)
    assert code == 0
    assert so.normalize(storage, so_sector.SECTOR, "run_sn", "run_s", producer="normalize_sector") == 0
    samsung = rows_of(storage, "KOSPI")["005930"]
    assert (samsung["large_code"], samsung["large_name"]) == ("0013", "전기·전자")
    assert (samsung["medium_code"], samsung["medium_name"]) == ("0027", "제조")
    assert samsung["small_code"] is None and samsung["raw_small_code"] == "0000"
    assert samsung["taxonomy"] == "KIS_INDEX_SECTOR" and samsung["security_group"] == "ST"
    etf = rows_of(storage, "KOSPI")["091160"]
    assert etf["large_code"] is None and etf["security_group"] == "EF"
    assert rows_of(storage, "KOSDAQ")["058470"]["large_name"] == "전기·전자"


def test_shifted_layout_is_refused_not_loaded(tmp_path):
    # WHY: 고정폭이 한 칸만 밀려도 모든 종목의 업종 코드가 조용히 틀린다. 반쪽·밀린 파일은 싣지 않는다.
    shifted = zipped("kospi_code.mst", [line + "X" for line in
                                        [master_line("005930", "KR7005930003", "삼성전자", "ST", "0013", "0027",
                                                     "0000", 227), *filler(227)]])
    storage, code = run_chain(tmp_path, {**ROUTES, "kospi_code.mst.zip": shifted})
    assert code == 0
    assert so.normalize(storage, so_sector.SECTOR, "run_sn", "run_s", producer="normalize_sector") == so.PARTIAL_EXIT
    assert rows_of(storage, "KOSPI") == {}
    assert "058470" in rows_of(storage, "KOSDAQ")


def test_name_table_layout_mismatch_keeps_codes_but_not_wrong_names(tmp_path):
    # WHY: 공식 헤더와 샘플이 이름 위치에서 어긋난다. 틀린 이름을 붙이느니 이름을 비우고 드러낸다.
    names, warnings = kis_sector_master.parse_sector_names(zipped("idxcode.mst", ["00013AB", "00027CD"]))
    assert names == {} and warnings == ["sector_name_layout_mismatch"]


def test_one_market_download_failure_is_partial_and_other_market_lands(tmp_path):
    storage, code = run_chain(tmp_path, {**ROUTES, "kosdaq_code.mst.zip": StopFetch("HTTP 404", status=404)})
    assert code == so.PARTIAL_EXIT
    assert so.normalize(storage, so_sector.SECTOR, "run_sn", "run_s", producer="normalize_sector") == 0
    assert "005930" in rows_of(storage, "KOSPI") and rows_of(storage, "KOSDAQ") == {}


def test_sector_collection_refuses_past_dates():
    # WHY: 원천은 현재 분류만 준다 — 과거 날짜를 달면 오늘 분류를 과거로 라벨한다(복원 주장 금지).
    with pytest.raises(SystemExit, match="현재 분류만"):
        run_module.main(["ingest-raw-sector", "--from", "2026-01-01", "--to", "2026-01-31"])


def test_non_trading_day_downloads_nothing_and_downstream_is_a_clean_no_op(tmp_path, monkeypatch):
    # WHY: 원장은 이 작업을 비거래일 SKIPPED 로 계획한다(kr_trading_calendar). 그날 실제로 받으면 그 실행이
    # SKIPPED 뒤로 사라진다 — 휴장일엔 일을 하지 않고, 정제·적재는 실패가 아닌 "할 일 없음"으로 끝난다.
    monkeypatch.setenv("OPS_KR_HOLIDAYS", "2026-10-05")
    client = Client(ROUTES)
    storage = LocalStorage(tmp_path)
    holiday = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)      # 월요일 공휴일(KST 09:00)
    assert so_sector.collect_sector(storage, client, "https://example.invalid", "run_h", now=holiday) == 0
    assert client.calls == []
    assert so.normalize(storage, so_sector.SECTOR, "run_hn", "run_h", producer="normalize_sector") == 0
    manifest = json.loads(storage.get_bytes(
        "operations_archive/canonical_run_manifests/dataset=sector_classification/run_id=run_hn/manifest.json"))
    assert manifest["rows"] == 0 and manifest["rejected"] == 0


def test_corrupt_name_table_keeps_codes_and_other_markets(tmp_path):
    # WHY(리뷰): 업종명 ZIP 하나가 깨졌다고 두 시장 분류 적재가 통째로 멈추면 안 된다(HTTP 실패와 같은 부분 처리).
    storage, code = run_chain(tmp_path, {**ROUTES, "idxcode.mst.zip": b"not a zip"})
    assert code == 0
    assert so.normalize(storage, so_sector.SECTOR, "run_sn", "run_s", producer="normalize_sector") == so.PARTIAL_EXIT
    samsung = rows_of(storage, "KOSPI")["005930"]
    assert samsung["large_code"] == "0013" and samsung["large_name"] is None


def test_named_rows_are_not_visible_before_the_name_table_arrives():
    # WHY: 업종명은 마스터와 따로 받는 표에서 온다. 행의 가시시각이 마스터 수신시각이면, 이름 표를 받기 전 시각의
    # 기준시각 조회가 아직 받지 않은 이름을 보게 된다. 이름을 붙인 행은 두 입력 중 늦게 받은 시각부터 보여야 한다.
    master_at, names_at = "2026-09-30T00:00:01+00:00", "2026-09-30T00:00:09+00:00"
    objects = [
        {"request": {"file": "kospi_code.mst.zip"}, "body": KOSPI, "key": "raw/kospi", "sha256": "a", "fetched_at": master_at},
        {"request": {"file": "idxcode.mst.zip"}, "body": NAMES, "key": "raw/names", "sha256": "b", "fetched_at": names_at},
    ]
    rows, _ = so_sector._normalize_sector(objects, {})
    assert rows and {(r["received_at"], r["available_at"]) for r in rows} == {(names_at, names_at)}
    assert {r["raw_key"] for r in rows} == {"raw/kospi"}                       # 근거는 행을 만든 마스터
    assert any(r["large_name"] for r in rows)
    # 이름 표를 못 받은 실행은 마스터 수신시각 그대로다(붙인 이름이 없다).
    rows, _ = so_sector._normalize_sector(objects[:1], {})
    assert {r["available_at"] for r in rows} == {master_at}


def test_snapshot_date_follows_the_name_table_across_kst_midnight():
    # WHY(봇 P2): DB CHECK 은 as_of_date = received_at 의 KST 날짜다. 마스터는 23:59 KST, 이름 표는 00:00 KST 뒤에
    # 받으면 received_at 만 다음날로 가고 as_of_date 가 전날에 남아 load 가 그 실행 전체를 거부한다.
    master_at, names_at = "2026-09-30T14:59:50+00:00", "2026-09-30T15:00:05+00:00"
    objects = [
        {"request": {"file": "kospi_code.mst.zip"}, "body": KOSPI, "key": "raw/kospi", "sha256": "a", "fetched_at": master_at},
        {"request": {"file": "idxcode.mst.zip"}, "body": NAMES, "key": "raw/names", "sha256": "b", "fetched_at": names_at},
    ]
    rows, _ = so_sector._normalize_sector(objects, {})
    assert rows and {(r["as_of_date"], r["received_at"]) for r in rows} == {("2026-10-01", names_at)}
