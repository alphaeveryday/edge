"""normalize_etf_profile 테스트 — ETF 마스터 재료 정제 (ALPHA-462).

이 canonical 의 소비자는 load_instruments 이고, 통과한 행은 곧 `entity`·`instrument` 가 된다.
그래서 '무엇이 통과하면 안 되는가'를 특히 촘촘히 본다 — 잘못된 행 하나가 가짜 ETF 마스터를
만들면 그 ID 위에 NAV·구성종목·트리거가 쌓여 되돌리기가 매우 비싸다.
"""

import json

import pytest

from data_pipeline.lake import (
    LocalStorage,
    canonical_etf_profile_partition,
    collection_log_key,
    parse_raw_etf_profile_key,
)
from data_pipeline.lake.latest_good import parse_pointer
from data_pipeline.steps import normalize_etf_profile


def _raw_key(run_id="R1", date="2026-07-20", source="kis", market="KR"):
    return (f"raw/source={source}/dataset=etf_profile/market={market}"
            f"/ingest_date={date}/run_id={run_id}/part-00000.ndjson")


def _write_raw(storage, key, rows):
    storage.put_bytes(key, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode())
    parsed = parse_raw_etf_profile_key(key)
    storage.put_bytes(
        collection_log_key(
            parsed["source"], "etf_profile", parsed["ingest_date"], parsed["run_id"],
        ),
        json.dumps({"status": "success"}).encode(),
    )


def _profile_row(**over):
    row = {
        "pdno": "00000A069500", "prdt_abrv_name": "KODEX 200",
        "prdt_name": "삼성 KODEX200 증권상장지수투자신탁[주식]",
        "prdt_eng_abrv_name": "KODEX 200", "prdt_clsf_name": "ETF",
        "std_pdno": "KR7069500007",
        "our_etf_id": "069500", "market": "KR", "kis_symbol": "069500",
        "fetched_at": "2026-07-20T06:00:00+00:00",
    }
    row.update(over)
    return row


def _canonical_rows(storage, market="KR", as_of="2026-07-20"):
    prefix = canonical_etf_profile_partition(market, as_of)
    rows = []
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(normalize_etf_profile._read_parquet_rows(storage.get_bytes(key)))
    return rows


def _log(storage, run_id="R1"):
    keys = [k for k in storage.list_keys("operations_archive/data_quality_logs/")
            if "dataset=etf_profile/" in k and f"run_id={run_id}/" in k]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_약명이_표시명이_되고_법적명칭도_보존한다(tmp_path):
    # WHY: 화면·설명문에 쓰는 건 약명("KODEX 200")이고 법적 명칭은 감사·대조용이다. 약명을
    #      놓치면 마스터 display_name 이 "삼성 KODEX200 증권상장지수투자신탁[주식]" 이 된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_profile_row()])

    assert normalize_etf_profile.run(storage, "R1") == 0

    [row] = _canonical_rows(storage)
    assert row["etf_id"] == "069500"          # pdno(패딩 코드)가 아니라 provenance 티커
    assert row["display_name"] == "KODEX 200"
    assert row["legal_name"] == "삼성 KODEX200 증권상장지수투자신탁[주식]"
    assert row["isin"] == "KR7069500007"
    assert row["currency"] == "KRW"
    assert row["as_of_date"] == "2026-07-20"


@pytest.mark.parametrize("over,reason", [
    ({"prdt_abrv_name": None}, "missing_display_name"),
    ({"prdt_abrv_name": "   "}, "missing_display_name"),
    ({"our_etf_id": None}, "missing_etf_id"),
    ({"market": "US"}, "unsupported_market"),
    ({"prdt_clsf_name": "주식"}, "not_an_etf"),
    ({"prdt_clsf_name": None}, "not_an_etf"),
    ({"fetched_at": None}, "missing_as_of_date"),
])
def test_마스터가_될_수_없는_행은_막힌다(tmp_path, over, reason):
    # WHY: 통과 행이 곧 마스터가 된다. 이름 없으면 entity.display_name(NOT NULL) 위반이고,
    #      ETF 가 아닌 상품이 통과하면 instrument_type='ETF' 인 가짜가 생겨 그 위에 NAV·구성종목이
    #      쌓인다 — 되돌리려면 FK 참조를 전부 걷어내야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_profile_row(**over)])

    assert normalize_etf_profile.run(storage, "R1") == 2
    log = _log(storage)
    assert log["records_passed"] == 0
    assert reason in log["failures"][0]["reasons"]
    assert _canonical_rows(storage) == []


def test_재실행이_수렴하고_최신_fetched_at_이_이긴다(tmp_path):
    # WHY: 개명이 일어나면 더 늦은 수집이 이겨야 마스터가 옛 이름으로 남지 않는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(run_id="R1"),
               [_profile_row(prdt_abrv_name="옛이름", fetched_at="2026-07-20T01:00:00+00:00")])
    _write_raw(storage, _raw_key(run_id="R2"),
               [_profile_row(prdt_abrv_name="새이름", fetched_at="2026-07-20T09:00:00+00:00")])

    assert normalize_etf_profile.run(storage, "R1") == 0
    first = _canonical_rows(storage)
    assert normalize_etf_profile.run(storage, "R2") == 0
    second = _canonical_rows(storage)

    assert len(second) == 1 and second[0]["display_name"] == "새이름"
    assert len(first) == 1
    parts = [k for k in storage.list_keys("canonical/") if k.endswith(".parquet")]
    assert len(parts) == 1, "part 가 누적되면 병합이 아니라 중복이 쌓인다"


def test_깨진_행은_격리되고_남은_행은_통과한다(tmp_path):
    storage = LocalStorage(tmp_path / "lake")
    body = "\n".join([
        json.dumps(_profile_row()),
        "{not json",
        "null",
        json.dumps(_profile_row(our_etf_id="091160", prdt_abrv_name="KODEX 반도체")),
    ]) + "\n"
    storage.put_bytes(_raw_key(), body.encode())

    assert normalize_etf_profile.run(storage, "R1") == 2
    log = _log(storage)
    assert log["records_read"] == 4 and log["records_passed"] == 2
    reasons = [r for f in log["failures"] for r in f["reasons"]]
    assert reasons.count("unparseable_json") == 1 and reasons.count("non_object_row") == 1


def test_input_run_id_는_그_수집런의_raw_만_읽는다(tmp_path):
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(run_id="R1"), [_profile_row(our_etf_id="069500")])
    _write_raw(storage, _raw_key(run_id="R2"), [_profile_row(our_etf_id="091160")])

    assert normalize_etf_profile.run(storage, "N1", "R2") == 0
    assert [r["etf_id"] for r in _canonical_rows(storage)] == ["091160"]
    log = _log(storage, "N1")
    pointer = parse_pointer(storage.get_bytes(log["latest_good"]["pointer_key"]))
    assert pointer["source_run_id"] == "N1"
    assert pointer["partition"] == {"as_of_date": "2026-07-20"}
    assert pointer["objects"][0]["rows"] == 1
