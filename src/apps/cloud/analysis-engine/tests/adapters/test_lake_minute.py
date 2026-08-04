"""분봉 분해 입력 리더 테스트 (ALPHA-710).

의도: 분해는 트리거 판정과 **같은 축**(세션 시가 대비)이어야 한다 — 축이 갈리면
트리거가 설명하는 움직임과 분해가 설명하는 움직임이 다른 값이 된다. artifact 부재는
빈 dict(소비자 재시도 축)로, 가격 계약 위반은 그 unit 만 None(미가격 제외)으로
접어야 한 unit 의 불량이 분해 전체를 죽이거나 통과값으로 위장되지 않는다.
"""
from __future__ import annotations

import json

from botocore.exceptions import ClientError

from edge_analysis.adapters.lake import LakeReader, minute_artifact_key


class _MinuteFakeS3:
    """키 부재를 실제 S3 처럼 NoSuchKey ClientError 로 돌려주는 최소 표면."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        import io

        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


def _bars(*rows: dict) -> bytes:
    return ("\n".join(json.dumps(r) for r in rows) + "\n").encode()


def _reader(objects: dict[str, bytes]) -> LakeReader:
    return LakeReader(_MinuteFakeS3(objects), "bkt")


def test_minute_returns_are_open_vs_trigger_close():
    """세션 시가 window 의 open 대비 트리거 window 의 close — 판정과 같은 축."""
    reader = _reader({
        minute_artifact_key("KR", "2026-07-15", "0900", 1): _bars(
            {"unit_id": "005930", "open": "70000.0", "close": "70100.0"},
            {"unit_id": "000660", "open": "200000.0", "close": "201000.0"},
        ),
        minute_artifact_key("KR", "2026-07-15", "1030", 2): _bars(
            {"unit_id": "005930", "open": "73400.0", "close": "73500.0"},
            {"unit_id": "000660", "open": "190000.0", "close": "190000.0"},
        ),
    })
    returns = reader.load_minute_returns("KR", "2026-07-15", "0900", 1, None, "1030", 2, None)
    assert abs(returns["005930"] - 0.05) < 1e-9      # 73500/70000-1
    assert abs(returns["000660"] - (-0.05)) < 1e-9   # 190000/200000-1 — 하락 방향 보존


def test_trigger_at_open_window_reads_single_artifact():
    """09:00 발화(시가 window == 트리거 window)면 artifact 하나로 close/open."""
    reader = _reader({
        minute_artifact_key("KR", "2026-07-15", "0900", 1): _bars(
            {"unit_id": "005930", "open": "70000.0", "close": "70700.0"},
        ),
    })
    returns = reader.load_minute_returns("KR", "2026-07-15", "0900", 1, None, "0900", 1, None)
    assert abs(returns["005930"] - 0.01) < 1e-9


def test_missing_artifact_is_empty_not_partial():
    """artifact 부재(커밋 지연)는 빈 dict — 부분 결과로 분해하면 결손이 정상 분해로
    위장된다. 빈 dict 는 호출부가 ReturnsNotReady 로 접어 재시도한다."""
    seeded = minute_artifact_key("KR", "2026-07-15", "0900", 1)
    reader = _reader({seeded: _bars({"unit_id": "005930", "open": "1", "close": "1"})})
    assert reader.load_minute_returns("KR", "2026-07-15", "0900", 1, None, "1030", 1, None) == {}
    assert reader.load_minute_returns("KR", "2026-07-15", "1000", 1, None, "1030", 1, None) == {}


def test_contract_violations_fold_to_none_per_unit():
    """0·음수·비수치·시가 결측은 그 unit 만 None — 분해가 미가격으로 제외한다.
    통과값으로 강제(coerce-to-passing)되면 오염된 수익률이 기여 순위에 실린다."""
    reader = _reader({
        minute_artifact_key("KR", "2026-07-15", "0900", 1): _bars(
            {"unit_id": "A", "open": "0", "close": "10"},        # 시가 0
            {"unit_id": "B", "open": "abc", "close": "100"},     # 비수치 시가
            {"unit_id": "D", "open": "100", "close": "110"},     # 정상
        ),
        minute_artifact_key("KR", "2026-07-15", "1030", 1): _bars(
            {"unit_id": "A", "open": "10", "close": "12"},
            {"unit_id": "B", "open": "100", "close": "90"},
            {"unit_id": "C", "open": "50", "close": "55"},       # 시가 window 에 없음
            {"unit_id": "D", "open": "108", "close": "-1"},      # 음수 close
        ),
    })
    returns = reader.load_minute_returns("KR", "2026-07-15", "0900", 1, None, "1030", 1, None)
    assert returns == {"A": None, "B": None, "C": None, "D": None}


def test_infinity_and_nan_fold_to_none():
    """float("Infinity") 는 양수 비교를 통과하고 float("nan") 은 전부 False 다 —
    유한성 게이트가 없으면 손상 레코드가 수익률 inf 로 위장돼 기여 순위를 오염시킨다."""
    reader = _reader({
        minute_artifact_key("KR", "2026-07-15", "0900", 1): _bars(
            {"unit_id": "A", "open": "100", "close": "100"},
            {"unit_id": "B", "open": "Infinity", "close": "100"},
            {"unit_id": "C", "open": "100", "close": "100"},
        ),
        minute_artifact_key("KR", "2026-07-15", "1030", 1): _bars(
            {"unit_id": "A", "open": "100", "close": "Infinity"},
            {"unit_id": "B", "open": "100", "close": "110"},
            {"unit_id": "C", "open": "100", "close": "nan"},
        ),
    })
    returns = reader.load_minute_returns("KR", "2026-07-15", "0900", 1, None, "1030", 1, None)
    assert returns == {"A": None, "B": None, "C": None}


def test_division_overflow_folds_to_none():
    """유한 양수 피연산자끼리도 나눗셈이 오버플로한다(open 1e-300) — 결과 유한성
    검사가 없으면 inf 수익률이 기여 순위·proxy 를 오염시킨다."""
    reader = _reader({
        minute_artifact_key("KR", "2026-07-15", "0900", 1): _bars(
            {"unit_id": "A", "open": "1e-300", "close": "1"},
        ),
        minute_artifact_key("KR", "2026-07-15", "1030", 1): _bars(
            {"unit_id": "A", "open": "1", "close": "9e14"},
        ),
    })
    returns = reader.load_minute_returns("KR", "2026-07-15", "0900", 1, None, "1030", 1, None)
    assert returns == {"A": None}


def test_checksum_mismatch_is_retry_axis_not_silent_consume():
    """원장 checksum 은 커밋된 바이트의 sha256 이다(price_consumer 와 동형 계약) —
    대조 없이 소비하면 트리거 판정과 다른 바이트(동시 PUT 경합·운영 실수)로 분해가
    영속된다. 일치하면 정상 소비, 불일치는 ReturnsNotReady(재시도 축)로 드러난다."""
    import hashlib

    import pytest

    from edge_analysis.config import ReturnsNotReadyError

    bars = _bars({"unit_id": "005930", "open": "70000.0", "close": "70700.0"})
    reader = _reader({minute_artifact_key("KR", "2026-07-15", "0900", 1): bars})
    good = hashlib.sha256(bars).hexdigest()
    returns = reader.load_minute_returns(
        "KR", "2026-07-15", "0900", 1, good, "0900", 1, good)
    assert abs(returns["005930"] - 0.01) < 1e-9
    with pytest.raises(ReturnsNotReadyError, match="checksum"):
        reader.load_minute_returns(
            "KR", "2026-07-15", "0900", 1, "0" * 64, "0900", 1, "0" * 64)
