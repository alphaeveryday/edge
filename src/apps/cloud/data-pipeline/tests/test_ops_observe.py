"""관측 리더(_observe_from_log) 테스트 (ALPHA-181).

WHY: 이 리더가 원장 `data_status` 의 **유일한 입력**이다. 봉투를 못 읽거나 없을 때 낙관값으로
메우면 부분 유실이 VALID 로 위장되고, 원장이 답해야 할 질문("이 작업의 산출이 온전한가")이
거짓이 된다. 리더는 ALPHA-530 때 테스트가 0개였다 — 등록 작업을 넓히기 전에 여기서 고정한다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import pytest

from data_pipeline.lake import LocalStorage, collection_log_key, quality_log_key
from data_pipeline.ops import catalog, entry as ops_entry, states, wrapper
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
    assert "received_count" not in signals  # 아직 완전성 미배선인 작업의 기존 봉투도 그대로 유효


def test_optional_received_count_flows_at_entity_grain(tmp_path):
    """ETF 수집기가 센 unique entity 수가 보존돼야 Planner snapshot과 독립 대조할 수 있다."""
    storage = _storage(tmp_path)
    entry = _entry("ETF_HOLDINGS_COLLECTION_KRX")
    _write_log(
        storage,
        entry,
        {
            "run_id": _RUN,
            "status": "success",
            "ops": {"records_out": 4120, "failed_records": 0, "received_count": 32},
        },
    )

    signals = _observe_from_log(storage, entry.task_key, _RUN, 0)
    assert signals["records_out"] == 4120
    assert signals["received_count"] == 32


def test_optional_entity_resolution_pair_flows_without_reinterpretation(tmp_path):
    """WHY: observer가 rate나 ticker-only resolved를 재구성하면 producer의 해소 정의가 갈린다."""
    storage = _storage(tmp_path)
    entry = _entry("LOAD_ASSERTIONS")
    _write_log(storage, entry, {
        "run_id": _RUN,
        "ops_attempt_id": "attempt-1",
        "ops": {
            "records_out": 100,
            "failed_records": 0,
            "entity_resolution_arguments_total": 4,
            "entity_resolution_arguments_resolved": 3,
        },
    })

    signals = _observe_from_log(storage, entry.task_key, _RUN, 0)
    assert signals["entity_resolution_arguments_total"] == 4
    assert signals["entity_resolution_arguments_resolved"] == 3
    assert signals["entity_resolution_attempt_id"] == "attempt-1"


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


@pytest.mark.parametrize("ops", [
    {"records_out": 0},                          # failed_records 누락
    {"records_out": 0, "failed_records": None},  # null
    {"failed_records": 0},                       # records_out 누락
])
def test_incomplete_envelope_does_not_pass_the_gate(tmp_path, ops):
    # WHY: derive_data_status 는 `failed_records` 가 None 이면 **실패 검사 자체를 건너뛴다** —
    #      결측을 '실패 0'으로 읽으면 0건 + 요청완료 + 계약허용이 모여 VALID_EMPTY 로 위장된다.
    #      결측은 '실패 없음'이 아니라 '모른다'다(edge-review H).
    storage = _storage(tmp_path)
    entry = _entry("PRICE_COLLECTION_KIS")
    _write_log(storage, entry, {"run_id": _RUN, "status": "success", "ops": ops})

    assert _observe_from_log(storage, entry.task_key, _RUN, 0) == {"exit_code": 0}


def test_collection_status_missing_is_not_completed(tmp_path):
    # WHY: 수집 로그의 status 는 요청 완료의 정본이다. 결측(중단·스키마 드리프트)을 success 로
    #      기본 처리하면 절단된 수집이 0건을 '정상 공백'으로 증명하게 된다.
    storage = _storage(tmp_path)
    entry = _entry("PRICE_COLLECTION_KIS")
    _write_log(storage, entry, {"run_id": _RUN, "ops": {"records_out": 0, "failed_records": 0}})

    assert _observe_from_log(storage, entry.task_key, _RUN, 0)["request_completed"] is False


def test_non_dict_envelope_is_rejected(tmp_path):
    # WHY: 봉투 자리에 스칼라·리스트가 오면(직렬화 사고·스키마 드리프트) `.get()` 이 터지거나
    #      더 나쁘게는 통과값으로 강등될 수 있다 — 행 하나가 런을 죽이지도, 위장하지도 않는다.
    storage = _storage(tmp_path)
    entry = _entry("NORMALIZE_PRICE")
    _write_log(storage, entry, {"run_id": _RUN, "ops": "nope"})

    assert _observe_from_log(storage, "NORMALIZE_PRICE", _RUN, 0) == {"exit_code": 0}


def test_non_object_log_is_not_collection_artifact_evidence(tmp_path):
    """WHY: 파일 존재만으로 raw 산출물까지 존재한다고 추정하면 collected_at이 거짓이 된다."""
    storage = _storage(tmp_path)
    entry = _entry("ETF_HOLDINGS_COLLECTION_KRX")
    dataset = entry.log_partition_dataset()
    key = collection_log_key(entry.source_vendor, dataset, "2026-07-25", _RUN)
    storage.put_bytes(key, b"[]")

    assert _observe_from_log(storage, entry.task_key, _RUN, 0) == {"exit_code": 0}


def test_only_current_positive_collection_log_proves_artifact(tmp_path):
    """WHY: 같은 run_id 재시도가 옛 로그를 새 산출물로 오인하면 Monitor 평가가 부당하게 지워진다."""
    storage = _storage(tmp_path)
    entry = _entry("ETF_HOLDINGS_COLLECTION_KRX")
    _write_log(
        storage,
        entry,
        {
            "run_id": _RUN,
            "source_vendor": "krx",
            "started_at": "2026-07-25T06:00:00+00:00",
            "finished_at": "2026-07-25T06:01:00+00:00",
            "records_saved": 10,
            "status": "success",
            "ops": {"records_out": 10, "failed_records": 0},
        },
    )

    old = _observe_from_log(
        storage, entry.task_key, _RUN, 0,
        not_before=datetime.fromisoformat("2026-07-25T07:00:00+00:00"),
    )
    current = _observe_from_log(
        storage, entry.task_key, _RUN, 0,
        not_before=datetime.fromisoformat("2026-07-25T05:00:00+00:00"),
    )

    assert "artifact_observed" not in old
    assert current["artifact_observed"] is True


def test_instrument_uses_actual_run_boundary_for_artifact_log(monkeypatch, tmp_path):
    """WHY: 재시도 경계가 wrapper 진입 시각이면 직전 ECS 시도의 늦은 로그를 현재 산출물로 오인한다."""
    expected_boundary = datetime.fromisoformat("2026-07-25T07:00:00+00:00")
    captured = {}

    class Clock:
        phase = "before-wrapper"

        @classmethod
        def now(cls, tz):
            assert cls.phase == "during-run"
            return expected_boundary

    def fake_wrapper(run_fn, **kwargs):
        Clock.phase = "during-run"
        exit_code = run_fn()
        Clock.phase = "after-run"
        kwargs["observe_data_fn"](exit_code)
        return exit_code

    def fake_observer(storage, task_key, run_id, exit_code, *, not_before):
        captured["not_before"] = not_before
        return {}

    monkeypatch.setattr(ops_entry, "datetime", Clock)
    monkeypatch.setattr(ops_entry, "ledger_from_settings", lambda settings: object())
    monkeypatch.setattr(ops_entry.wrapper, "instrument", fake_wrapper)
    monkeypatch.setattr(ops_entry, "_observe_from_log", fake_observer)

    assert ops_entry.instrument(
        object(), _storage(tmp_path), "ETF_HOLDINGS_COLLECTION_KRX", _RUN, lambda: 0
    ) == 0
    assert captured["not_before"] == expected_boundary


def test_incomplete_collection_log_cannot_prove_artifact(tmp_path):
    """WHY: ops 카운터만 그럴듯한 깨진 로그가 collected_at을 전진시키면 안 된다."""
    storage = _storage(tmp_path)
    entry = _entry("ETF_HOLDINGS_COLLECTION_KRX")
    _write_log(
        storage,
        entry,
        {
            "run_id": _RUN,
            "started_at": datetime.now().astimezone().isoformat(),
            "status": "success",
            "ops": {"records_out": 10, "failed_records": 0},
        },
    )

    signals = _observe_from_log(storage, entry.task_key, _RUN, 0)
    assert "artifact_observed" not in signals


def test_non_contract_quality_log_does_not_emit_collection_warning(tmp_path, caplog):
    """WHY: KRX freshness 검증이 정상 정제 작업마다 경고를 내면 운영 경고가 상시 노이즈가 된다."""
    storage = _storage(tmp_path)
    entry = _entry("NORMALIZE_PRICE")
    _write_log(
        storage,
        entry,
        {"run_id": _RUN, "ops": {"records_out": 10, "failed_records": 0}},
    )

    with caplog.at_level(logging.WARNING):
        _observe_from_log(storage, entry.task_key, _RUN, 0)
    assert "완전한 collection_log" not in caplog.text


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
