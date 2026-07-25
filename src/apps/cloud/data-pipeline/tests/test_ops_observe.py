"""관측 리더(_observe_from_log) 테스트 (ALPHA-181).

WHY: 이 리더가 원장 `data_status` 의 **유일한 입력**이다. 봉투를 못 읽거나 없을 때 낙관값으로
메우면 부분 유실이 VALID 로 위장되고, 원장이 답해야 할 질문("이 작업의 산출이 온전한가")이
거짓이 된다. 리더는 ALPHA-530 때 테스트가 0개였다 — 등록 작업을 넓히기 전에 여기서 고정한다.
"""

from __future__ import annotations

import json
import logging

import pytest

from data_pipeline.lake import LocalStorage, collection_log_key, quality_log_key
from data_pipeline.ops import catalog, states, wrapper
from data_pipeline.ops.entry import _observe_from_log

_RUN = "20260725T060000Z"


def _storage(tmp_path):
    return LocalStorage(tmp_path / "lake")


def _write_log(storage, entry: catalog.CatalogEntry, payload: dict, run_id: str = _RUN) -> None:
    """그 엔트리의 로그를 실제 경로 빌더로 심는다(리더의 경로 해소까지 함께 검증)."""
    dataset = entry.log_partition_dataset()
    key = (collection_log_key(entry.source_vendor, dataset, "2026-07-25", run_id)
           if entry.source_vendor else quality_log_key(dataset, "2026-07-25", run_id))
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _entry(task_key: str) -> catalog.CatalogEntry:
    e = catalog.get(task_key)
    assert e is not None
    return e


def test_envelope_flows_into_data_status(tmp_path):
    # WHY: 봉투의 두 값이 그대로 data_status 판정의 입력이 돼야 한다 — 리더가 스텝의 판정을
    #      재해석하면(예: 자기가 카운터를 합산하면) 스텝만 아는 유실 의미론이 어긋난다.
    storage = _storage(tmp_path)
    entry = _entry("LOAD_PRICE_DAILY")
    _write_log(storage, entry, {"run_id": _RUN, "ops": {"records_out": 120, "failed_records": 0}})

    signals = _observe_from_log(storage, "LOAD_PRICE_DAILY", _RUN, 0)
    assert signals["records_out"] == 120 and signals["failed_records"] == 0


def test_failed_records_make_it_incomplete(tmp_path):
    # WHY: 이 티켓 전체의 유일한 데이터-정합성 위험 — 유실 건수가 흘러가지 않으면 부분 유실이
    #      VALID 로 위장된다(edge-review G/H 가 원래 잡은 결함의 재발).
    storage = _storage(tmp_path)
    entry = _entry("LOAD_PRICE_DAILY")
    _write_log(storage, entry, {"run_id": _RUN, "ops": {"records_out": 100, "failed_records": 7}})

    signals = _observe_from_log(storage, "LOAD_PRICE_DAILY", _RUN, 0)
    assert wrapper.derive_data_status(signals) == states.DATA_INCOMPLETE


def test_missing_envelope_is_unknown_and_loud(tmp_path, caplog):
    # WHY: 증거 없음은 성공이 아니다. 봉투가 없을 때 0건·허용 같은 낙관 기본값으로 메우면
    #      계측 안 된 스텝이 조용히 초록으로 보인다(Rule 12).
    storage = _storage(tmp_path)
    entry = _entry("NORMALIZE_PRICE")
    _write_log(storage, entry, {"run_id": _RUN, "records_passed": 10, "records_failed": 0})

    with caplog.at_level(logging.WARNING):
        signals = _observe_from_log(storage, "NORMALIZE_PRICE", _RUN, 0)
    assert signals == {"exit_code": 0}
    assert wrapper.derive_data_status(signals) == states.DATA_UNKNOWN
    assert "ops 봉투 없음" in caplog.text  # 조용히 넘어가지 않는다


def test_non_dict_envelope_is_rejected(tmp_path):
    # WHY: 봉투 자리에 스칼라·리스트가 오면(직렬화 사고·스키마 드리프트) `.get()` 이 터지거나
    #      더 나쁘게는 통과값으로 강등될 수 있다 — 행 하나가 런을 죽이지도, 위장하지도 않는다.
    storage = _storage(tmp_path)
    entry = _entry("NORMALIZE_PRICE")
    _write_log(storage, entry, {"run_id": _RUN, "ops": "nope"})

    assert _observe_from_log(storage, "NORMALIZE_PRICE", _RUN, 0) == {"exit_code": 0}


@pytest.mark.parametrize("status,completed", [
    ("success", True), ("skipped", True), ("partial", False), ("error", False), ("stopped", False),
])
def test_collection_status_gates_request_completed(tmp_path, status, completed):
    # WHY: 소스 요청 자체가 끝나지 않았으면 0건을 '정상 공백'으로 증명할 수 없다. 이 게이트가
    #      없으면 절단·중단된 수집이 VALID_EMPTY 로 올라간다.
    storage = _storage(tmp_path)
    entry = _entry("PRICE_COLLECTION_KIS")
    _write_log(storage, entry,
               {"run_id": _RUN, "status": status, "ops": {"records_out": 0, "failed_records": 0}})

    signals = _observe_from_log(storage, "PRICE_COLLECTION_KIS", _RUN, 0)
    assert signals["request_completed"] is completed


def test_empty_allowed_comes_from_catalog(tmp_path):
    # WHY: '0건이 정상인가'는 데이터셋의 정적 계약이지 런타임 관측이 아니다. 거래일 가격 0건은
    #      비정상이라 UNKNOWN 이어야 하고(entry.empty_allowed=False), 리더가 이걸 상수로 밀면
    #      '할 일이 없었다'와 '증거가 없다'가 섞인다.
    storage = _storage(tmp_path)
    entry = _entry("PRICE_COLLECTION_KIS")
    assert entry.empty_allowed is False
    _write_log(storage, entry,
               {"run_id": _RUN, "status": "success", "ops": {"records_out": 0, "failed_records": 0}})

    signals = _observe_from_log(storage, "PRICE_COLLECTION_KIS", _RUN, 0)
    assert signals["empty_allowed"] is False
    assert wrapper.derive_data_status(signals) == states.DATA_UNKNOWN


def test_unregistered_task_is_not_observed(tmp_path):
    # WHY: 카탈로그에 없는 작업은 관측 대상이 아니다 — 없는 엔트리로 경로를 지어내 남의 로그를
    #      읽으면 다른 작업의 결과로 상태를 판정한다.
    assert _observe_from_log(_storage(tmp_path), "NOT_REGISTERED", _RUN, 0) == {"exit_code": 0}


def test_missing_log_is_unknown(tmp_path):
    # WHY: 로그가 아예 없으면(스토리지 장애·미실행) 증거가 없는 것이다 — UNKNOWN 으로 남는다.
    signals = _observe_from_log(_storage(tmp_path), "NORMALIZE_PRICE", _RUN, 0)
    assert signals == {"exit_code": 0}


@pytest.mark.parametrize("entry", catalog.entries(), ids=lambda e: e.task_key)
def test_reader_is_task_key_agnostic(tmp_path, entry):
    # WHY: 이게 "task_key 무관"의 정의다. 새 엔트리를 등록할 때 리더를 함께 고쳐야 한다면
    #      if-체인이 부활한 것이고, 이 티켓이 푼 문제가 되돌아온다.
    storage = _storage(tmp_path)
    _write_log(storage, entry,
               {"run_id": _RUN, "status": "success", "ops": {"records_out": 5, "failed_records": 0}})

    signals = _observe_from_log(storage, entry.task_key, _RUN, 0)
    assert signals["records_out"] == 5
    assert signals["failed_records"] == 0
    assert signals["empty_allowed"] is entry.empty_allowed
