"""normalize_etf 스텝 테스트 — 벤더 이형(FMP·KRX) 흡수 + fact 게이트 + canonical 전량 스냅샷 교체
(ALPHA-1059).

핵심 시나리오: KRX 해외기초 ETF 의 대시(-) 비중이 null 로 통과해 구성종목이 보존되는 것,
FMP updatedAt(datetime)·KRX trd_dd(YYYYMMDD) 기준일이 하나의 as_of_date 로 수렴하는 것.
"""

import hashlib
import json

import pytest

from data_pipeline.lake import (
    LocalStorage,
    canonical_etf_holdings_partition,
    collection_log_key,
    latest_good_pointer_key,
    parse_raw_etf_key,
)
from data_pipeline.lake.latest_good import parse_pointer
from data_pipeline.steps import normalize_etf


def _raw_key(source: str, market: str, run_id: str = "R1", date: str = "2026-07-14") -> str:
    return (
        f"raw/source={source}/dataset=etf_holdings/market={market}"
        f"/ingest_date={date}/run_id={run_id}/part-00000.ndjson"
    )


def _write_raw(storage, key: str, rows: list[dict]) -> None:
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    storage.put_bytes(key, body.encode("utf-8"))
    parsed = parse_raw_etf_key(key)
    storage.put_bytes(
        collection_log_key(
            parsed["source"], "etf_holdings", parsed["ingest_date"], parsed["run_id"],
        ),
        json.dumps({"status": "success", "run_id": parsed["run_id"],
                    "source_vendor": parsed["source"], "records_fetched": len(rows),
                    "records_saved": len(rows), "records_failed_etfs": 0,
                    "raw_sha256": {key: hashlib.sha256(body.encode("utf-8")).hexdigest()}}).encode(),
    )


def _fmp_row(**over) -> dict:
    # FMP holdings 실측 필드 — asset(구성종목)·weightPercentage·updatedAt(datetime 기준일).
    row = {"symbol": "SPY", "asset": "NVDA", "name": "NVIDIA", "isin": "US67066G1040",
           "sharesNumber": 1000, "weightPercentage": 7.5, "marketValue": 5.0e8,
           "updatedAt": "2026-07-11 09:07:03", "our_etf_id": "SPY", "market": "US",
           "fetched_at": "2026-07-14T00:00:00+00:00"}
    row.update(over)
    return row


def _krx_row(**over) -> dict:
    # KRX MDCSTAT05001 실측 필드 — COMPST_ISU_CD(구성종목)·COMPST_RTO(비중)·trd_dd(기준일).
    # 국내기초는 값이 채워지고, 해외기초는 COMPST_RTO·VALU_AMT 가 대시(-)로 온다.
    row = {"COMPST_ISU_CD": "005930", "COMPST_ISU_CD2": "KR7005930003",
           "COMPST_ISU_NM": "삼성전자", "COMPST_ISU_CU1_SHRS": "1,000",
           "VALU_AMT": "5,000,000", "COMPST_AMT": "5,000,000", "COMPST_RTO": "30.5",
           "SECUGRP_ID": "ST", "MKT_ID": "STK",
           "our_etf_id": "069500", "market": "KR", "isin": "KR7069500007",
           "trd_dd": "20260714", "fetched_at": "2026-07-14T00:00:00+00:00"}
    row.update(over)
    return row


def _quality_log(storage) -> dict:
    keys = storage.list_keys("operations_archive/data_quality_logs/")
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def _quality_for_run(storage, run_id: str) -> dict:
    keys = [
        key for key in storage.list_keys("operations_archive/data_quality_logs/")
        if f"/run_id={run_id}/" in key
    ]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def _canonical_rows(storage, market: str, as_of_date: str) -> list[dict]:
    prefix = canonical_etf_holdings_partition(market, as_of_date)
    rows: list[dict] = []
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(normalize_etf._read_parquet_rows(storage.get_bytes(key)))
    return rows


def test_both_vendors_normalize_to_common_schema(tmp_path):
    # WHY: 정제의 존재 이유는 FMP(asset/weightPercentage/updatedAt)·KRX(COMPST_ISU_CD/
    #      COMPST_RTO/trd_dd) 이형을 하나의 공통 스키마로 수렴시키는 것 — 둘 다 정상이면
    #      같은 컬럼(etf_id·constituent_ticker·weight_pct·as_of_date·currency)으로 canonical 에 온다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row()])
    _write_raw(storage, _raw_key("krx", "KR"), [_krx_row()])

    assert normalize_etf.run(storage, "N1") == 0
    us = _canonical_rows(storage, "US", "2026-07-11")  # FMP updatedAt 날짜부
    kr = _canonical_rows(storage, "KR", "2026-07-14")  # KRX trd_dd
    assert len(us) == 1 and us[0]["etf_id"] == "SPY" and us[0]["constituent_ticker"] == "NVDA"
    assert us[0]["weight_pct"] == 7.5 and us[0]["currency"] == "USD"
    assert len(kr) == 1 and kr[0]["etf_id"] == "069500" and kr[0]["constituent_ticker"] == "005930"
    # KRX 콤마 문자열 수치 흡수 + 통화 태깅.
    assert kr[0]["weight_pct"] == 30.5 and kr[0]["shares"] == 1000.0 and kr[0]["currency"] == "KRW"
    log = _quality_log(storage)
    assert (log["records_read"], log["records_passed"], log["records_failed"]) == (2, 2, 0)
    assert log["canonical_partitions"] == [
        {"market": "KR", "as_of_date": "2026-07-14"},
        {"market": "US", "as_of_date": "2026-07-11"},
    ]


@pytest.mark.parametrize("vendor,market,day", [("krx", "KR", "2026-07-14"),
                                             ("fmp", "US", "2026-07-11")])
def test_full_snapshot_shrink_is_idempotent_and_older_run_cannot_resurrect(tmp_path, vendor, market, day):
    """WHY: 삭제는 행별 최신값으로 표현되지 않는다. 다른 ETF는 보존하고 재실행도 안전해야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    def row(ticker, weight, **extra):
        return (_krx_row(COMPST_ISU_CD=ticker, COMPST_RTO=str(weight), **extra)
                if vendor == "krx" else _fmp_row(asset=ticker, weightPercentage=weight, **extra))
    _write_raw(storage, _raw_key(vendor, market, "R1"),
               [row("A", 60), row("B", 40), row("C", 100, our_etf_id="OTHER")])
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 0
    _write_raw(storage, _raw_key(vendor, market, "R2"),
               [row("A", 100, fetched_at="2026-07-15T00:00:00+00:00")])
    assert normalize_etf.run(storage, "N2", input_run_id="R2") == 0
    expected = _canonical_rows(storage, market, day)
    assert {(r["constituent_ticker"], r["weight_pct"]) for r in expected} == {("A", 100), ("C", 100)}
    for norm, source in (("N2", "R2"), ("N3", "R1"), ("N4", None)):
        assert normalize_etf.run(storage, norm, input_run_id=source) == 0
        assert _canonical_rows(storage, market, day) == expected


@pytest.mark.parametrize("log_change", [None, {"records_saved": 0}, {"records_fetched": True},
                                      {"records_failed_etfs": 1}, {"run_id": "OTHER"},
                                      {"raw_sha256": None}, {"raw_sha256": {}}])
def test_unproven_collection_cannot_replace_or_publish_load_manifest(tmp_path, log_change):
    """WHY: 성공 문자열만으로 잘린 raw를 전량으로 승격하면 정상 구성종목을 삭제한다."""
    from data_pipeline.lake import canonical_run_manifest_key
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR", "R1"), [_krx_row()])
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 0
    previous = _canonical_rows(storage, "KR", "2026-07-14")
    key = _raw_key("krx", "KR", "R2")
    _write_raw(storage, key, [_krx_row(COMPST_RTO="100", fetched_at="2026-07-15T00:00:00+00:00")])
    log_key = collection_log_key("krx", "etf_holdings", "2026-07-14", "R2")
    if log_change is None:
        storage.delete_keys([log_key])
    else:
        payload = json.loads(storage.get_bytes(log_key))
        storage.put_bytes(log_key, json.dumps({**payload, **log_change}).encode())
    assert normalize_etf.run(storage, "N2", input_run_id="R2") == 2
    assert _canonical_rows(storage, "KR", "2026-07-14") == previous
    manifest = json.loads(storage.get_bytes(canonical_run_manifest_key("etf_holdings", "N2")))
    assert manifest["canonical_partitions"] == []


def test_recovery_uses_last_complete_run_without_unioning_partial_rows(tmp_path):
    """WHY: 전체 복구도 수집 런 경계를 지켜야 오래된 종목·부분 정정이 되살아나지 않는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR", "R1"), [_krx_row(), _krx_row(COMPST_ISU_CD="B")])
    _write_raw(storage, _raw_key("krx", "KR", "R2"),
               [_krx_row(COMPST_RTO="100", fetched_at="2026-07-15T00:00:00+00:00")])
    _write_raw(storage, _raw_key("krx", "KR", "R3"),
               [_krx_row(COMPST_RTO="20", fetched_at="2026-07-16T00:00:00+00:00"),
                _krx_row(COMPST_ISU_CD="")])
    assert normalize_etf.run(storage, "RECOVERY") == 2
    assert [(r["constituent_ticker"], r["weight_pct"]) for r in
            _canonical_rows(storage, "KR", "2026-07-14")] == [("005930", 100)]


def test_equal_revision_conflict_preserves_existing_snapshot(tmp_path):
    """WHY: 같은 수집 시각의 다른 내용을 파일 정렬 순서로 임의 채택하지 않는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR", "R1"), [_krx_row()])
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 0
    previous = _canonical_rows(storage, "KR", "2026-07-14")
    _write_raw(storage, _raw_key("krx", "KR", "R2"), [_krx_row(COMPST_RTO="100")])
    assert normalize_etf.run(storage, "N2", input_run_id="R2") == 1
    assert _canonical_rows(storage, "KR", "2026-07-14") == previous


def test_full_recovery_repairs_legacy_union_and_newer_partial_poison(tmp_path):
    """WHY: 최대 fetched_at은 기존 canonical이 전량본이라는 증거가 아니다."""
    storage = LocalStorage(tmp_path / "lake")
    first = [_krx_row(COMPST_RTO="60"), _krx_row(COMPST_ISU_CD="B", COMPST_RTO="40")]
    corrected = [_krx_row(COMPST_RTO="100", fetched_at="2026-07-15T00:00:00+00:00")]
    _write_raw(storage, _raw_key("krx", "KR", "R1"), first)
    _write_raw(storage, _raw_key("krx", "KR", "R2"), corrected)
    target = canonical_etf_holdings_partition("KR", "2026-07-14") + "/part-00000.parquet"
    for poisoned in (
        corrected + [first[1]],
        [_krx_row(COMPST_RTO="20", fetched_at="2026-07-16T00:00:00+00:00"), first[1]],
    ):
        storage.put_bytes(target, normalize_etf._write_parquet_rows(
            [normalize_etf._normalize("krx", row) for row in poisoned],
        ))
        assert normalize_etf.run(storage, "RECOVERY") == 0
        rows = _canonical_rows(storage, "KR", "2026-07-14")
        assert [(r["constituent_ticker"], r["weight_pct"]) for r in rows] == [("005930", 100)]


def test_malformed_raw_path_still_emits_quality_log(tmp_path):
    """WHY: 손상된 raw 객체 하나로 품질 기록 전 크래시가 반복되면 복구 원인을 알 수 없다."""
    storage = LocalStorage(tmp_path / "lake")
    storage.put_bytes("raw/source=krx/dataset=etf_holdings/ingest_date=2026-07-14"
                      "/run_id=R1/part-00000.ndjson", json.dumps(_krx_row()).encode())
    assert normalize_etf.run(storage, "N1") == 1
    assert _quality_log(storage)["failures"][0]["reasons"] == ["raw_read_error"]
    assert storage.list_keys("canonical/") == []


@pytest.mark.parametrize("extra", [{"COMPST_ISU_CD": " 005930 "}, {"our_etf_id": "069500 "}])
def test_loader_rejected_identity_cannot_replace_good_snapshot(tmp_path, extra):
    """WHY: 로더가 거부하는 정체성으로 전량 교체하면 기존의 정상 행까지 삭제된다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR", "R1"), [_krx_row()])
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 0
    previous = _canonical_rows(storage, "KR", "2026-07-14")
    _write_raw(storage, _raw_key("krx", "KR", "R2"),
               [_krx_row(fetched_at="2026-07-15T00:00:00+00:00", **extra)])
    assert normalize_etf.run(storage, "N2", input_run_id="R2") == 2
    assert _canonical_rows(storage, "KR", "2026-07-14") == previous


def test_canonical_CAS_conflict_does_not_publish_success(tmp_path):
    """WHY: 다른 writer의 최신 파일을 오래된 read 결과로 덮어써서는 안 된다."""
    class RacingStorage(LocalStorage):
        def put_bytes_if_version(self, key, data, version):
            if key.startswith("canonical/"):
                return False
            return super().put_bytes_if_version(key, data, version)
    storage = RacingStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR"), [_krx_row()])
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 1
    assert _quality_log(storage)["canonical_written"] is False
    assert latest_good_pointer_key("etf_holdings", "KR") not in storage.list_keys("")


def test_recovery_cannot_restamp_unproven_old_ETF_through_another_ETF(tmp_path):
    """WHY: 파티션 manifest의 로더는 모든 ETF를 읽으므로 증거 없는 잔존 ETF도 승격될 수 있다."""
    from data_pipeline.lake import canonical_run_manifest_key
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR"), [_krx_row()])
    target = canonical_etf_holdings_partition("KR", "2026-07-14") + "/part-00000.parquet"
    previous = normalize_etf._write_parquet_rows([
        normalize_etf._normalize("krx", _krx_row(our_etf_id="UNPROVEN")),
    ])
    storage.put_bytes(target, previous)
    assert normalize_etf.run(storage, "RECOVERY") == 1
    assert storage.get_bytes(target) == previous
    assert canonical_run_manifest_key("etf_holdings", "RECOVERY") not in storage.list_keys("")


def test_mixed_FMP_dates_are_not_two_complete_snapshots(tmp_path):
    """WHY: 응답에 존재하는 B가 다른 updatedAt 때문에 A의 기준일에서 삭제되면 안 된다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US", "R1"), [_fmp_row(), _fmp_row(asset="B")])
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 0
    previous = _canonical_rows(storage, "US", "2026-07-11")
    _write_raw(storage, _raw_key("fmp", "US", "R2"), [
        _fmp_row(fetched_at="2026-07-15T00:00:00+00:00"),
        _fmp_row(asset="B", updatedAt="2026-07-10 09:07:03", fetched_at="2026-07-15T00:00:00+00:00"),
    ])
    assert normalize_etf.run(storage, "N2", input_run_id="R2") == 2
    assert _canonical_rows(storage, "US", "2026-07-11") == previous
    assert _canonical_rows(storage, "US", "2026-07-10") == []


def test_full_recovery_does_not_overwrite_a_snapshot_published_after_raw_listing(tmp_path):
    """WHY: canonical 읽기의 ETag만으로는 raw 목록에 없던 최신 정정을 보호하지 못한다."""
    target = canonical_etf_holdings_partition("KR", "2026-07-14") + "/part-00000.parquet"
    newer = normalize_etf._write_parquet_rows([normalize_etf._normalize("krx", _krx_row(
        COMPST_RTO="100", fetched_at="2026-07-16T00:00:00+00:00",
    ))])
    class RacingStorage(LocalStorage):
        def list_keys(self, prefix):
            keys = super().list_keys(prefix)
            if prefix == "raw/":
                self.put_bytes(target, newer)
            return keys
    storage = RacingStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR"), [_krx_row()])
    assert normalize_etf.run(storage, "RECOVERY") == 1
    assert storage.get_bytes(target) == newer


def test_same_run_retry_finishes_stale_part_cleanup_after_delete_failure(tmp_path):
    """WHY: target 교체 후 삭제 실패가 같은 런 재시도를 영구 충돌로 막으면 안 된다."""
    class FailingOnceStorage(LocalStorage):
        failed = False
        def delete_keys(self, keys):
            if not self.failed:
                self.failed = True
                raise OSError("temporary delete failure")
            return super().delete_keys(keys)
    storage = FailingOnceStorage(tmp_path / "lake")
    prefix = canonical_etf_holdings_partition("KR", "2026-07-14")
    old = [_krx_row(COMPST_RTO="60"), _krx_row(COMPST_ISU_CD="B", COMPST_RTO="40")]
    storage.put_bytes(prefix + "/part-00001.parquet", normalize_etf._write_parquet_rows(
        [normalize_etf._normalize("krx", row) for row in old],
    ))
    _write_raw(storage, _raw_key("krx", "KR"),
               [_krx_row(COMPST_RTO="100", fetched_at="2026-07-15T00:00:00+00:00")])
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 1
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 0
    assert storage.list_keys(prefix + "/") == [prefix + "/part-00000.parquet"]
    assert [(r["constituent_ticker"], r["weight_pct"]) for r in
            _canonical_rows(storage, "KR", "2026-07-14")] == [("005930", 100)]


def test_foreign_underlying_dash_weight_nulled_but_row_preserved(tmp_path):
    # WHY: KRX 해외기초 ETF(TIGER美S&P500)는 비중·평가금액을 대시(-)로 준다 — 구성종목을
    #      버리지 않고 weight_pct=null 로 통과시켜 멤버십·주식수를 보존한다(사용자 결정). 결측은
    #      경고 없이 통과(수백 행을 결측 경고로 채우지 않음). 비중 파생은 다운스트림 소관.
    storage = LocalStorage(tmp_path / "lake")
    foreign = _krx_row(COMPST_ISU_CD="AAPL", COMPST_ISU_NM="애플",
                       COMPST_RTO="-", VALU_AMT="-", COMPST_AMT="-", COMPST_ISU_CU1_SHRS="12")
    _write_raw(storage, _raw_key("krx", "KR"), [foreign])

    assert normalize_etf.run(storage, "N1") == 0
    rows = _canonical_rows(storage, "KR", "2026-07-14")
    assert len(rows) == 1  # 버리지 않음
    assert rows[0]["constituent_ticker"] == "AAPL" and rows[0]["shares"] == 12.0
    assert rows[0]["weight_pct"] is None and rows[0]["market_value"] is None
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_warned"] == 0  # 결측 경고 없음


def test_passing_rows_idempotent_and_latest_fetched_at_wins(tmp_path):
    # WHY: canonical 은 run_id 가 없어 같은 raw 를 몇 번 정제해도 결과가 같아야 하고(멱등),
    #      같은 (etf_id,constituent)를 재적재하면 최신 fetched_at 이 이겨야 한다(정정 반영).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR", run_id="R1"),
               [_krx_row(COMPST_RTO="30.5", fetched_at="2026-07-14T00:00:00+00:00")])
    assert normalize_etf.run(storage, "N1") == 0
    _write_raw(storage, _raw_key("krx", "KR", run_id="R2"),
               [_krx_row(COMPST_RTO="31.0", fetched_at="2026-07-15T00:00:00+00:00")])
    assert normalize_etf.run(storage, "N2") == 0

    rows = _canonical_rows(storage, "KR", "2026-07-14")
    assert len(rows) == 1 and rows[0]["weight_pct"] == 31.0  # 최신 fetched_at 승리
    parts = [k for k in storage.list_keys("canonical/") if k.endswith(".parquet")]
    assert len(parts) == 1  # part 누적 없이 되쓰기


def test_blocking_identity_excluded_from_canonical(tmp_path):
    # WHY: 정체성 결측(구성종목 코드 없음)·시간축 불량(비달력일 trd_dd → 정규화 실패)은
    #      canonical 행키·파티션을 못 만들어 blocking — 수집본 전체 반영을 보류한다. ('20260231'=2월 31일은 strptime 파싱 실패라 missing_as_of_date; 범위밖
    #      유효날짜의 bad_as_of_date 는 test_quality_etf 가 커버.)
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR"), [
        _krx_row(COMPST_ISU_CD="005930"),                       # OK
        _krx_row(COMPST_ISU_CD="   "),                          # 구성종목 결측(blocking)
        _krx_row(COMPST_ISU_CD="000660", trd_dd="20260231"),   # 비달력일 → 정규화 실패(blocking)
    ])

    assert normalize_etf.run(storage, "N1") == 2
    rows = _canonical_rows(storage, "KR", "2026-07-14")
    assert rows == []  # 행 탈락은 전체 구성종목의 삭제 증거가 아니다
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_failed"] == 2
    reasons = {r for f in log["failures"] for r in f["reasons"]}
    assert "missing_constituent" in reasons and "missing_as_of_date" in reasons


def test_weight_out_of_range_warns_but_passes(tmp_path):
    # WHY: 비중 이상(>100%)은 파싱/단위 이상 신호라 경고로 표면화하되(coerce-to-passing
    #      방지) 실재 구성종목은 유효해 canonical 로 통과시킨다 — 경고는 quality_log 에 남는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR"), [_krx_row(COMPST_RTO="150.0")])

    assert normalize_etf.run(storage, "N1") == 0
    rows = _canonical_rows(storage, "KR", "2026-07-14")
    assert len(rows) == 1 and rows[0]["weight_pct"] == 150.0  # 통과(보존)
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_warned"] == 1
    assert "weight_out_of_range" in log["warnings"][0]["reasons"]


def test_ref_number_sanitizes_dash_comma_nonfinite_bool():
    # WHY: 참고 수치는 canonical float64 컬럼에 담긴다 — 각도 H(coerce-to-passing 방지):
    #      NaN/Inf(json.loads 가 리터럴을 float 로 파싱)·bool(float(True)=1.0)이 float64 를
    #      오염시키지 않게 정규화가 null 로 원천 차단해야 한다. KRX 대시(-)·콤마문자열도 흡수.
    for bad in ("-", "", "  ", float("nan"), float("inf"), float("-inf"), True, "비수치", None):
        assert normalize_etf._ref_number(bad) is None, f"{bad!r} 는 null 로 정리돼야 한다"
    # 정상값·콤마문자열·음수는 finite float 로 보존(음수는 게이트가 경고로 잡는다).
    assert normalize_etf._ref_number("1,234") == 1234.0
    assert normalize_etf._ref_number(30.5) == 30.5
    assert normalize_etf._ref_number("-5") == -5.0


def test_non_object_row_isolated_not_crash(tmp_path):
    # WHY: 유효 JSON 이지만 객체가 아닌 행(null·배열)은 _normalize 의 record.get 에서 런 전체를
    #      죽여 quality_log 조차 못 남긴다 — 행 단위로 격리해 나머지 검증은 완료돼야 한다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [None, _fmp_row(), []])

    assert normalize_etf.run(storage, "N1") == 2  # 크래시 없이 partial로 완료
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_failed"] == 2
    assert all("non_object_row" in f["reasons"] for f in log["failures"])


def test_unknown_vendor_reported_not_silently_passed(tmp_path):
    # WHY: 알 수 없는 ETF 벤더는 조용히 통과시키지 않고 사유로 드러낸다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("bogus", "KR"), [_krx_row()])

    assert normalize_etf.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and log["records_failed"] == 1
    assert "unsupported_vendor" in log["failures"][0]["reasons"]


def test_input_run_id_scopes_read_and_writes_canonical(tmp_path):
    # WHY: SFN 이 --input-run-id 로 도는 경로다(ALPHA-389) — 그 런의 raw 만 읽어 정제 비용이
    #      여태 쌓인 raw 전체가 아니라 이번 런에 비례한다. 스코프도 canonical 을 쓴다 —
    #      안 쓰면 파이프라인이 아무것도 적재하지 못한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR", run_id="R1"), [_krx_row()])
    _write_raw(storage, _raw_key("krx", "KR", run_id="R2"), [_krx_row(), _krx_row(COMPST_ISU_CD="000660")])

    assert normalize_etf.run(storage, "N1", input_run_id="R2") == 0
    log = _quality_log(storage)
    assert log["records_read"] == 2  # R1(1건) 은 스코프 밖 — 읽지도 않는다
    assert log["canonical_written"] is True and log["canonical_rows_written"] == 2
    pointer = parse_pointer(storage.get_bytes(log["latest_good"]["pointer_key"]))
    assert pointer["source_run_id"] == "N1"
    assert pointer["partition"] == {"as_of_date": "2026-07-14"}
    assert pointer["objects"][0]["rows"] == 2


@pytest.mark.parametrize("status", ["partial", "stopped", "error"])
def test_incomplete_collection_retains_previous_pointer_and_canonical(tmp_path, status):
    """WHY: 부분 수집은 구성과 비중 모두 기존 정상본을 유지해야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    first_key = _raw_key("krx", "KR", run_id="R1")
    _write_raw(storage, first_key, [_krx_row(COMPST_RTO="30.5")])
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 0
    pointer_key = latest_good_pointer_key("etf_holdings", "KR")
    previous = storage.get_bytes(pointer_key)

    second_key = _raw_key("krx", "KR", run_id="R2")
    _write_raw(storage, second_key, [
        _krx_row(COMPST_RTO="31.0", fetched_at="2026-07-15T00:00:00+00:00"),
    ])
    parsed = parse_raw_etf_key(second_key)
    storage.put_bytes(
        collection_log_key("krx", "etf_holdings", parsed["ingest_date"], "R2"),
        json.dumps({"status": status}).encode(),
    )

    assert normalize_etf.run(storage, "N2", input_run_id="R2") == 2
    assert _canonical_rows(storage, "KR", "2026-07-14")[0]["weight_pct"] == 30.5
    assert storage.get_bytes(pointer_key) == previous


def test_row_failure_and_empty_run_retain_previous_pointer(tmp_path):
    """WHY: 행 탈락과 빈 런이 삭제/정정으로 오인되어 기존 정상본을 바꾸면 안 된다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR", run_id="R1"), [_krx_row()])
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 0
    pointer_key = latest_good_pointer_key("etf_holdings", "KR")
    previous = storage.get_bytes(pointer_key)

    _write_raw(storage, _raw_key("krx", "KR", run_id="R2"), [
        _krx_row(COMPST_RTO="31.0", fetched_at="2026-07-15T00:00:00+00:00"),
        _krx_row(COMPST_ISU_CD="   "),
    ])
    assert normalize_etf.run(storage, "N2", input_run_id="R2") == 2
    assert _canonical_rows(storage, "KR", "2026-07-14")[0]["weight_pct"] == 30.5
    assert storage.get_bytes(pointer_key) == previous
    assert (
        _quality_for_run(storage, "N2")["latest_good"]["pointer_intended_action"]
        == "retain_partial"
    )

    assert normalize_etf.run(storage, "N3", input_run_id="HOLIDAY") == 0
    assert storage.get_bytes(pointer_key) == previous
    assert (
        _quality_for_run(storage, "N3")["latest_good"]["pointer_intended_action"]
        == "retain_empty"
    )


@pytest.mark.parametrize("failure", ["artifact", "readback", "quality", "pointer"])
def test_storage_failures_cannot_expose_false_pointer(tmp_path, failure):
    """WHY: artifact/readback/quality/CAS 어느 단계가 깨져도 새 alias가 보여서는 안 된다."""
    class FailingStorage(LocalStorage):
        def put_bytes(self, key, data):
            if failure == "quality" and "data_quality_logs" in key:
                raise OSError("quality write failed")
            return super().put_bytes(key, data)

        def get_bytes(self, key):
            data = super().get_bytes(key)
            if failure == "readback" and "latest_good_partition_artifacts" in key:
                return data + b"corrupt"
            return data

        def put_bytes_if_version(self, key, data, version):
            if failure == "artifact" and "latest_good_partition_artifacts" in key:
                return False
            if failure == "pointer" and key.endswith("/pointer.json"):
                return False
            return super().put_bytes_if_version(key, data, version)

    storage = FailingStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR", run_id="R1"), [_krx_row()])
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 1
    assert latest_good_pointer_key("etf_holdings", "KR") not in storage.list_keys("")


def test_pointer_cas_is_the_last_successful_storage_mutation(tmp_path):
    """WHY: alias 공개 뒤 필수 쓰기가 실패해 성공 포인터만 남는 순서 역전을 막는다."""
    class TrackingStorage(LocalStorage):
        def __init__(self, root):
            super().__init__(root)
            self.events = []

        def put_bytes(self, key, data):
            self.events.append(("put", key))
            return super().put_bytes(key, data)

        def get_bytes(self, key):
            self.events.append(("get", key))
            return super().get_bytes(key)

        def put_bytes_if_version(self, key, data, version):
            self.events.append(("cas", key))
            return super().put_bytes_if_version(key, data, version)

    storage = TrackingStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR", run_id="R1"), [_krx_row()])
    assert normalize_etf.run(storage, "N1", input_run_id="R1") == 0
    assert storage.events[-1] == (
        "cas", latest_good_pointer_key("etf_holdings", "KR"),
    )
    assert not any(
        event == "get" and key.startswith("canonical/")
        for event, key in storage.events
    ), "artifact는 이 런이 직렬화한 merged bytes를 써야지 mutable canonical을 다시 읽으면 안 된다"


def test_krx_kospi_and_kosdaq_resolve_to_distinct_mics(tmp_path):
    # WHY: 파티션의 market=KR 은 **지역**이지 거래소가 아니다(ADR-0027). 실측상 KODEX 반도체
    #      구성종목 35종 중 **28종이 코스닥**이라, 거래소를 지역으로 뭉개면 다운스트림이
    #      코스닥 종목을 유가증권시장으로 적재한다. MKT_ID 를 MIC(ISO 10383)로 흡수해야
    #      canonical 만 보고 instrument.market_code 를 채울 수 있다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR"), [
        _krx_row(COMPST_ISU_CD="005930", COMPST_ISU_NM="삼성전자", MKT_ID="STK"),
        _krx_row(COMPST_ISU_CD="036930", COMPST_ISU_NM="주성엔지니어링", MKT_ID="KSQ"),
    ])
    assert normalize_etf.run(storage, "R1") == 0

    rows = {r["constituent_ticker"]: r for r in _canonical_rows(storage, "KR", "2026-07-14")}
    assert rows["005930"]["constituent_mic"] == "XKRX"  # 유가증권시장
    assert rows["036930"]["constituent_mic"] == "XKOS"  # 코스닥


def test_krx_non_listed_holding_has_no_mic(tmp_path):
    # WHY: 원화현금(KRD010010001)은 MKT_ID·SECUGRP_ID 가 빈 문자열로 온다(실측) — 거래소에
    #      상장된 종목이 아니다. MIC=None 이 곧 그 사실이고, 우리 RDB 는
    #      instrument.market_code NOT NULL 이라 이 행은 애초에 instrument 가 될 수 없다.
    #      canonical 에 CASH로 명시해 하류가 지원 제외와 실제 유실을 구분한다. 구성종목 자체는 보존한다
    #      (ETF 의 실제 보유분이라 비중합이 맞아야 한다).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR"), [
        _krx_row(COMPST_ISU_CD="KRD010010001", COMPST_ISU_CD2="KRD010010001",
                 COMPST_ISU_NM="원화현금", SECUGRP_ID="", MKT_ID="", COMPST_RTO="0.03"),
    ])
    assert normalize_etf.run(storage, "R1") == 0

    rows = _canonical_rows(storage, "KR", "2026-07-14")
    assert len(rows) == 1, "비상장 보유분도 canonical 에는 남아야 한다(비중합 보존)"
    assert rows[0]["constituent_mic"] is None
    assert rows[0]["constituent_asset_type"] == "CASH"
    assert rows[0]["weight_pct"] == 0.03


def test_krx_asset_type_uses_only_observed_code_combinations():
    # WHY: 지원 제외를 넓게 추측하면 새 자산 유형이 유실에서 사라진다. 실측 조합만 분류하고
    #      미지 코드는 UNKNOWN으로 남겨 하류 실패 계측이 계속 울려야 한다.
    assert normalize_etf._constituent_asset_type("krx", _krx_row()) == "EQUITY"
    assert normalize_etf._constituent_asset_type(
        "krx", _krx_row(COMPST_ISU_CD="KRD010010001", SECUGRP_ID="", MKT_ID="")
    ) == "CASH"
    assert normalize_etf._constituent_asset_type(
        "krx", _krx_row(SECUGRP_ID="OP", MKT_ID="DRV")
    ) == "OPTION"
    assert normalize_etf._constituent_asset_type(
        "krx", _krx_row(SECUGRP_ID="BD", MKT_ID="STK")
    ) == "UNKNOWN"
    for malformed in (None, [], True):
        assert normalize_etf._constituent_asset_type(
            "krx", _krx_row(COMPST_ISU_CD="KRD010010001", SECUGRP_ID=malformed, MKT_ID="")
        ) == "UNKNOWN"
    for secugrp_id, mkt_id in ((" ", " "), ("st", "stk"), (" op ", " drv ")):
        assert normalize_etf._constituent_asset_type(
            "krx", _krx_row(
                COMPST_ISU_CD="KRD010010001", SECUGRP_ID=secugrp_id, MKT_ID=mkt_id
            )
        ) == "UNKNOWN"
    missing_codes = _krx_row(COMPST_ISU_CD="KRD010010001")
    missing_codes.pop("SECUGRP_ID")
    missing_codes.pop("MKT_ID")
    assert normalize_etf._constituent_asset_type("krx", missing_codes) == "UNKNOWN"
    assert normalize_etf._constituent_asset_type("fmp", _fmp_row()) == "UNKNOWN"


def test_krx_option_does_not_emit_unknown_market_warning(caplog):
    # WHY: DRV는 OP와 함께 오면 관측된 정상 옵션 조합이다. 지원 제외 행마다 매핑 누락 경고가
    #      뜨면 실제 새 시장 코드 경고가 정상 노이즈에 묻힌다.
    with caplog.at_level("WARNING"):
        assert normalize_etf._krx_mic(_krx_row(SECUGRP_ID="OP", MKT_ID="DRV")) is None
    assert not caplog.records


def test_unknown_krx_market_id_surfaces_instead_of_silent_null(tmp_path, caplog):
    # WHY: KRX 가 새 시장 코드를 늘렸을 때 조용히 None 으로 뭉개면, 그 시장 전 종목이 거래소
    #      없이 적재되고 아무도 모른다(Rule 12). 매핑 누락은 로그로 드러나야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR"), [_krx_row(MKT_ID="NEWMKT")])
    with caplog.at_level("WARNING"):
        assert normalize_etf.run(storage, "R1") == 0

    assert _canonical_rows(storage, "KR", "2026-07-14")[0]["constituent_mic"] is None
    assert any("NEWMKT" in r.getMessage() for r in caplog.records), \
        "미지 MKT_ID 가 로그에 안 드러났다"


def test_fmp_holdings_have_no_mic(tmp_path):
    # WHY: FMP 는 거래소·자산유형 필드를 아예 주지 않는다(실측: symbol·asset·name·isin·
    #      securityCusip·sharesNumber·weightPercentage·marketValue·updatedAt 이 전부).
    #      없는 걸 지어내지 않고 None 으로 둔다 — 한 벤더만 채우는 nullable 은 기존 관례다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row()])
    assert normalize_etf.run(storage, "R1") == 0

    rows = _canonical_rows(storage, "US", "2026-07-11")
    assert rows[0]["constituent_mic"] is None


def test_canonical_rewrite_removes_stale_part_files(tmp_path):
    # WHY: 새 스키마 part-00000만 덮고 구형 part를 남기면 하류가 둘 다 읽어 같은 행을
    # EQUITY와 UNKNOWN으로 이중 계측한다. 파티션은 한 파일로 원자적으로 수렴해야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR"), [_krx_row()])
    assert normalize_etf.run(storage, "R1") == 0
    prefix = canonical_etf_holdings_partition("KR", "2026-07-14")
    part0 = f"{prefix}/part-00000.parquet"
    storage.put_bytes(f"{prefix}/part-00001.parquet", storage.get_bytes(part0))
    nested = f"{prefix}/archive/part-backup.parquet"
    storage.put_bytes(nested, storage.get_bytes(part0))

    assert normalize_etf.run(storage, "R2") == 0
    assert f"{prefix}/part-00001.parquet" not in storage.list_keys(prefix + "/")
    assert part0 in storage.list_keys(prefix + "/")
    assert storage.get_bytes(nested), "직접 자식이 아닌 보관 객체를 삭제했다"


def test_timezone_없는_fetched_at은_canonical_최신행으로_승격하지_않는다(tmp_path):
    # WHY: 절대시각이 아닌 값을 UTC로 가정하면 유효한 기존 행을 덮고, 하류는 그 행을 다시
    # 거부해 보유관계 자체가 사라진다. 생산 단계에서 bad_fetched_at으로 격리해야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("krx", "KR"), [
        _krx_row(fetched_at="2026-07-14T02:00:00")
    ])

    assert normalize_etf.run(storage, "R1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 0
    assert log["failures"][0]["reasons"] == ["bad_fetched_at"]
