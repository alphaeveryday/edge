"""normalize_investor 스텝 테스트 — 숫자화·정체성 게이트·필수(headline)/선택(기관세부)·멱등 병합.

가격(normalize_price)과 달리 OHLCV 물리 불변식이 없다(순매수 음수 정상) — 게이트는 정체성
(market·ticker·trade_date)과 수치 캐스팅이다. headline 3종은 필수, 기관 세부는 선택(null 허용).
"""

import hashlib
import json

from data_pipeline.lake import (
    LocalStorage,
    canonical_investor_flow_partition,
    canonical_run_manifest_key,
)
from data_pipeline.steps import normalize_investor


def _raw_key(source: str = "kis", market: str = "KR", run_id: str = "R1", date: str = "2026-07-01") -> str:
    return (
        f"raw/source={source}/dataset=investor_flow_daily/market={market}"
        f"/ingest_date={date}/run_id={run_id}/part-00000.ndjson"
    )


def _write_raw(storage, key: str, rows: list[dict]) -> None:
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    storage.put_bytes(key, body.encode("utf-8"))


def _kis_row(**over) -> dict:
    # KIS 원본은 전부 zero-pad 문자열, 날짜 YYYYMMDD. headline + 기관세부(연기금=fund) 실측 필드.
    row = {
        "stck_bsop_date": "20260701",
        "prsn_ntby_qty": "-00000000000070203", "prsn_ntby_tr_pbmn": "-00000000000003190",
        "frgn_ntby_qty": "000000000000039367", "frgn_ntby_tr_pbmn": "000000000000001713",
        "orgn_ntby_qty": "000000000000011941", "orgn_ntby_tr_pbmn": "000000000000000660",
        "fund_ntby_qty": "000000000000005000", "fund_ntby_tr_pbmn": "000000000000000250",
        "our_ticker": "005930", "market": "KR", "kis_symbol": "005930",
        "fetched_at": "2026-07-01T00:00:00+00:00",
    }
    row.update(over)
    return row


def _quality_log(storage) -> dict:
    keys = storage.list_keys("operations_archive/data_quality_logs/")
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def _canonical_rows(storage, market: str, trade_date: str) -> list[dict]:
    prefix = canonical_investor_flow_partition(market, trade_date)
    rows: list[dict] = []
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(normalize_investor._read_parquet_rows(storage.get_bytes(key)))
    return rows


def _manifest(storage, run_id: str = "N1") -> dict:
    return json.loads(storage.get_bytes(
        canonical_run_manifest_key("investor_flow_daily", run_id)
    ))


def test_normalizes_and_coerces_zero_padded_strings(tmp_path):
    # WHY: 정제의 존재 이유는 KIS zero-pad 문자열(음수 포함)을 표준 정수 순매수 행으로 수렴시키는
    #      것 — 개인/외국인/기관계 + 연기금이 정확한 부호·값으로 canonical 에 적재돼야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row()])

    assert normalize_investor.run(storage, "N1") == 0
    [row] = _canonical_rows(storage, "KR", "2026-07-01")
    assert row["ticker"] == "005930" and row["currency"] == "KRW"
    assert row["net_qty_individual"] == -70203  # 수량(주식수)은 미환산·음수 정상
    assert row["net_val_individual"] == -3190 * 1_000_000  # 대금 백만원→원 환산(currency=KRW 정합)
    assert row["net_qty_foreign"] == 39367
    assert row["net_val_foreign"] == 1713 * 1_000_000
    assert row["net_qty_institution_total"] == 11941
    assert row["net_qty_pension"] == 5000  # 연기금(기금) 세부도 적재
    log = _quality_log(storage)
    assert (log["records_read"], log["records_passed"], log["records_failed"]) == (1, 1, 0)


def test_negative_net_buy_is_valid(tmp_path):
    # WHY: 순매수는 순매도(음수)가 정상이다 — 가격 OHLCV 같은 '음수=이상' 게이트를 잘못 이식하면
    #      정상 매도일이 통째로 탈락한다. 전 필드 음수여도 통과해야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(
        frgn_ntby_qty="-000000000000039367", orgn_ntby_qty="-000000000000011941")])

    assert normalize_investor.run(storage, "N1") == 0
    assert _quality_log(storage)["records_passed"] == 1


def test_missing_headline_field_fails_row(tmp_path):
    # WHY: headline(개인·외국인·기관계) 순매수는 필수다 — 하나라도 결측이면 그 행은 정체성만
    #      있고 핵심 수급이 빠져 canonical 을 오염시킨다. 탈락시키고 사유를 quality_log 로 남긴다.
    storage = LocalStorage(tmp_path / "lake")
    bad = _kis_row()
    del bad["frgn_ntby_qty"]  # 외국인 순매수 수량 결측
    _write_raw(storage, _raw_key(), [bad])

    assert normalize_investor.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and log["records_failed"] == 1
    assert "missing_field" in log["failures"][0]["reasons"]
    assert _canonical_rows(storage, "KR", "2026-07-01") == []


def test_optional_sub_field_missing_nulls_but_row_survives(tmp_path):
    # WHY: 기관 세부(연기금 등)는 선택이다 — KIS 가 특정 종목에 세부를 안 주더라도 headline 이
    #      온전하면 그 행은 살아야 한다(raw 에 원본이 남아 유실 아님). 빠진 세부는 canonical 에서 null.
    storage = LocalStorage(tmp_path / "lake")
    bad = _kis_row()
    del bad["fund_ntby_qty"]  # 연기금 수량 결측(선택 필드)
    _write_raw(storage, _raw_key(), [bad])

    assert normalize_investor.run(storage, "N1") == 0
    [row] = _canonical_rows(storage, "KR", "2026-07-01")
    assert row["net_qty_pension"] is None  # 세부만 null
    assert row["net_qty_foreign"] == 39367  # headline 은 온전
    assert _quality_log(storage)["records_passed"] == 1


def test_large_integer_preserved_without_float_rounding(tmp_path):
    # WHY: canonical 이 int64 라 float 왕복(float("9007199254740993")→…992)으로 2^53 초과
    #      순매수를 조용히 반올림하면 원본과 다른 값이 passed 로 적재된다(coerce-to-passing).
    #      정수 문자열은 int() 로 정확히 파싱해야 한다(edge-review F5). 수량(net_qty)은 환산이
    #      없어 파싱 정밀도만 순수 검증한다(대금은 백만원→원 ×1e6 이라 이 극단값이 int64 초과).
    storage = LocalStorage(tmp_path / "lake")
    big = "9007199254740993"  # 2^53+1 — float 은 이 값을 표현 못 해 반올림된다
    _write_raw(storage, _raw_key(), [_kis_row(frgn_ntby_qty=big)])

    assert normalize_investor.run(storage, "N1") == 0
    [row] = _canonical_rows(storage, "KR", "2026-07-01")
    assert row["net_qty_foreign"] == 9007199254740993  # 반올림 없이 정확히 보존


def test_optional_sub_field_garbage_fails_loud(tmp_path):
    # WHY: 기관 세부는 결측을 관용하지만(null), 존재하는데 비수치(garbage)는 스키마 드리프트라
    #      조용히 null 로 삼키면 안 된다 — non_numeric 으로 드러내 행을 탈락시킨다(edge-review F6).
    #      결측(관용)과 present-garbage(surface)를 구분한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(fund_ntby_qty="garbage")])  # 세부 필드 존재+비수치

    assert normalize_investor.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and log["records_failed"] == 1
    assert "non_numeric" in log["failures"][0]["reasons"]


def test_non_numeric_headline_fails_loud(tmp_path):
    # WHY: NaN/문자 등 비수치 순매수를 조용히 통과시키면 canonical 이 오염된다 — 소수 순매수도
    #      드리프트다(순매수는 정수 카운트). 비수치 headline 은 탈락·사유 기록(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(prsn_ntby_qty="1.5")])  # 소수 순매수

    assert normalize_investor.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and "non_numeric" in log["failures"][0]["reasons"]


def test_bad_trade_date_fails_loud(tmp_path):
    # WHY: 문자열 슬라이싱이 아니라 strptime 왕복으로 실재 달력일까지 검증한다 — '20260231'
    #      (2월 31일) 같은 비달력일을 정상 거래일로 인증하면 안 된다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(stck_bsop_date="20260231")])

    assert normalize_investor.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and "bad_trade_date" in log["failures"][0]["reasons"]


def test_missing_ticker_fails_row(tmp_path):
    # WHY: ticker 는 canonical 정체성 키(market,ticker,trade_date)의 일부다 — 없으면 키를 만들
    #      수 없어 canonical 로 못 간다. 결측을 missing_field 로 드러낸다(passed 위장 금지).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(our_ticker="")])

    assert normalize_investor.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and "missing_field" in log["failures"][0]["reasons"]


def test_canonical_idempotent_and_latest_fetched_at_wins(tmp_path):
    # WHY: canonical 은 run_id 없이 멱등이라 같은 raw 를 몇 번 정제해도 결과가 같고, 같은
    #      (market,ticker,trade_date)를 재적재하면 최신 fetched_at 이 이겨야 한다(정정 반영).
    storage = LocalStorage(tmp_path / "lake")
    old = _kis_row(frgn_ntby_qty="000000000000039367", fetched_at="2026-07-01T00:00:00+00:00")
    new = _kis_row(frgn_ntby_qty="000000000000050000", fetched_at="2026-07-02T00:00:00+00:00")
    _write_raw(storage, _raw_key(run_id="R1"), [old])
    _write_raw(storage, _raw_key(run_id="R2"), [new])

    assert normalize_investor.run(storage, "N1") == 0
    [row] = _canonical_rows(storage, "KR", "2026-07-01")
    assert row["net_qty_foreign"] == 50000  # 최신 fetched_at 승리
    # 재실행해도 part 누적 없이 되쓰기(멱등)
    assert normalize_investor.run(storage, "N2") == 0
    parts = [k for k in storage.list_keys("canonical/") if k.endswith(".parquet")]
    assert len(parts) == 1


def test_non_object_row_isolated(tmp_path):
    # WHY: 유효 JSON 이지만 객체가 아닌 행(배열·스칼라)은 raw.get 에서 런을 죽인다 — 한 행이
    #      검증 잡을 무너뜨리지 않게 격리한다(정상 행은 계속 처리).
    storage = LocalStorage(tmp_path / "lake")
    body = json.dumps(_kis_row()) + "\n" + json.dumps(["not", "obj"]) + "\n"
    storage.put_bytes(_raw_key(), body.encode("utf-8"))

    assert normalize_investor.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_failed"] == 1
    assert "non_object_row" in log["failures"][0]["reasons"]


def test_unsupported_vendor_isolated(tmp_path):
    # WHY: 현재 투자자 수급은 KIS 단독이다 — 알 수 없는 벤더의 raw 를 조용히 통과시키지 않고
    #      사유로 드러낸다(벤더 판별은 raw 키의 source= 가 SSOT).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(source="mystery"), [_kis_row()])

    assert normalize_investor.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and "unsupported_vendor" in log["failures"][0]["reasons"]


class _TrackingStorage:
    def __init__(self, inner, *, fail_put: str | None = None, corrupt_completed: bool = False):
        self.inner = inner
        self.fail_put = fail_put
        self.corrupt_completed = corrupt_completed
        self.events: list[tuple[str, str, bool | None]] = []

    def list_keys(self, prefix):
        return self.inner.list_keys(prefix)

    def get_bytes(self, key):
        data = self.inner.get_bytes(key)
        completed = None
        if "canonical_run_manifests" in key:
            completed = json.loads(data).get("canonical_written")
            if self.corrupt_completed and completed is True:
                data = b"{}"
        self.events.append(("get", key, completed))
        return data

    def put_bytes(self, key, data):
        completed = None
        if "canonical_run_manifests" in key:
            completed = json.loads(data).get("canonical_written")
        self.events.append(("put", key, completed))
        if self.fail_put and self.fail_put in key:
            raise OSError("의도된 저장 실패")
        return self.inner.put_bytes(key, data)


def test_manifest는_동일값_재확정과_최신_fetched_at_winner를_기록한다(tmp_path):
    # WHY(ALPHA-1040): consumer 범위는 값 변경분이 아니라 이번 실행에서 gate를 통과해
    # 재확정된 winner다. 같은 키 중에는 최신 관측 하나만 논리 범위에 남아야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(run_id="R0"), [_kis_row()])
    assert normalize_investor.run(storage, "N0", "R0") == 0
    _write_raw(storage, _raw_key(run_id="R1"), [
        _kis_row(),
        _kis_row(our_ticker="000660", fetched_at="2026-07-01T00:00:00+00:00"),
        _kis_row(our_ticker="000660", fetched_at="2026-07-01T01:00:00+00:00",
                 frgn_ntby_qty="50000"),
    ])

    assert normalize_investor.run(storage, "N1", "R1") == 0
    [part] = _manifest(storage)["canonical_partitions"]
    assert part["winner_ids"] == [{"ticker": "000660"}, {"ticker": "005930"}]
    assert part["key"] == (
        f"{canonical_investor_flow_partition('KR', '2026-07-01')}/part-00000.parquet"
    )
    assert part["sha256"] == hashlib.sha256(storage.get_bytes(part["key"])).hexdigest()
    rows = {row["ticker"]: row for row in _canonical_rows(storage, "KR", "2026-07-01")}
    assert rows["000660"]["net_qty_foreign"] == 50000


def test_교차_vendor_충돌은_winner에서_제외하고_성공범위를_보존한다(tmp_path):
    # WHY(ALPHA-1040): 기존 canonical과 다른 vendor가 같은 논리 키를 주장하면 임의 선택은
    # 오염이다. 충돌 키만 제외하고 같은 파티션의 다른 성공 winner는 manifest에 남긴다.
    storage = LocalStorage(tmp_path / "lake")
    legacy = normalize_investor._normalize("kis", _kis_row())[0]
    legacy["source_vendor"] = "legacy"
    key = f"{canonical_investor_flow_partition('KR', '2026-07-01')}/part-00000.parquet"
    storage.put_bytes(key, normalize_investor._write_parquet_rows([legacy]))
    _write_raw(storage, _raw_key(), [_kis_row(), _kis_row(our_ticker="000660")])

    assert normalize_investor.run(storage, "N1", "R1") == 2
    [part] = _manifest(storage)["canonical_partitions"]
    assert part["winner_ids"] == [{"ticker": "000660"}]
    log = _quality_log(storage)
    assert log["vendor_collisions"][0]["ticker"] == "005930"
    assert log["ops"] == {"records_out": 1, "failed_records": 1}


def test_행실패는_성공_manifest를_보존하고_exit2다(tmp_path):
    # WHY(ALPHA-1040): 한 불량 행 때문에 성공 winner 계보를 폐기하면 consumer가 처리할
    # 범위를 잃는다. 성공을 commit하되 전체 상태는 부분 실패로 남긴다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(), _kis_row(our_ticker="BAD", frgn_ntby_qty="x")])

    assert normalize_investor.run(storage, "N1", "R1") == 2
    assert _manifest(storage)["canonical_written"] is True
    assert _manifest(storage)["canonical_partitions"][0]["winner_ids"] == [{"ticker": "005930"}]


def test_빈_입력은_유효한_빈_완료_manifest다(tmp_path):
    # WHY(ALPHA-1040): 정상 0건과 manifest 결손을 구분해야 consumer가 과거 전체로 넓히지 않는다.
    storage = LocalStorage(tmp_path / "lake")
    assert normalize_investor.run(storage, "N1", "EMPTY") == 0
    assert _manifest(storage) == {
        "run_id": "N1", "producer": "normalize_investor", "canonical_written": True,
        "canonical_partitions": [],
    }


def test_manifest_commit순서와_동일run_멱등성을_고정한다(tmp_path):
    # WHY(ALPHA-1040): 완료 marker가 canonical/quality보다 먼저 보이거나 같은 run 재시도에서
    # 달라지면 consumer가 미완성 범위를 승인하거나 lineage가 비결정적으로 변한다.
    inner = LocalStorage(tmp_path / "lake")
    _write_raw(inner, _raw_key(), [_kis_row()])
    storage = _TrackingStorage(inner)

    assert normalize_investor.run(storage, "N1", "R1") == 0
    first = inner.get_bytes(canonical_run_manifest_key("investor_flow_daily", "N1"))
    assert normalize_investor.run(storage, "N1", "R1") == 0
    assert inner.get_bytes(canonical_run_manifest_key("investor_flow_daily", "N1")) == first
    first_run_events = storage.events[:8]
    assert first_run_events[0][0::2] == ("put", False)
    canonical_put = next(i for i, event in enumerate(first_run_events)
                         if event[0] == "put" and event[1].startswith("canonical/"))
    quality_put = next(i for i, event in enumerate(first_run_events)
                       if event[0] == "put" and "data_quality_logs" in event[1])
    completed_put = next(i for i, event in enumerate(first_run_events)
                         if event == ("put", canonical_run_manifest_key(
                             "investor_flow_daily", "N1"), True))
    assert canonical_put < quality_put < completed_put


def test_storage와_manifest_무결성_실패는_exit1이고_완료를_남기지_않는다(tmp_path):
    # WHY(ALPHA-1040): canonical/quality/manifest 중 하나라도 신뢰할 수 없으면 부분 성공이
    # 아니라 fatal이다. 이전 완료 marker를 그대로 두면 consumer가 stale 범위를 승인한다.
    for failing_prefix in ("canonical/market_data/investor_flow_daily", "data_quality_logs"):
        inner = LocalStorage(tmp_path / failing_prefix.replace("/", "_"))
        _write_raw(inner, _raw_key(), [_kis_row()])
        storage = _TrackingStorage(inner, fail_put=failing_prefix)
        assert normalize_investor.run(storage, "N1", "R1") == 1
        assert _manifest(inner)["canonical_written"] is False

    inner = LocalStorage(tmp_path / "manifest_corrupt")
    _write_raw(inner, _raw_key(), [_kis_row()])
    assert normalize_investor.run(
        _TrackingStorage(inner, corrupt_completed=True), "N1", "R1"
    ) == 1
    assert _manifest(inner)["canonical_written"] is False
