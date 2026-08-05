"""분봉 분해 입력 리더 테스트 (ALPHA-710 · 축 교체 ALPHA-747).

의도: 분해는 트리거 판정과 **같은 축**(전일 종가 대비)이어야 한다 — 축이 갈리면
트리거가 설명하는 움직임과 분해가 설명하는 움직임이 다른 값이 된다. artifact 부재는
빈 dict(소비자 재시도 축)로, 가격 계약 위반은 그 unit 만 None(미가격 제외)으로
접어야 한 unit 의 불량이 분해 전체를 죽이거나 통과값으로 위장되지 않는다.

분모 축이 시가(`minute_session_open`)였을 때 그 원장이 판정기에서 폴백으로 밀려나
장중 설명이 **전건 차단**된 실측이 있다(08-05 dev, start 709건·분해 0건) — 그래서
여기서 고정하는 것은 "전일 종가를 분모로 쓴다"는 사실 자체다.
"""
from __future__ import annotations

import io
import json
from datetime import date

from botocore.exceptions import ClientError

from edge_analysis.adapters.lake import LakeReader, minute_artifact_key

PRICE_DAILY = "canonical/market_data/price_daily/market=KR/"


class _MinuteFakeS3:
    """키 부재를 실제 S3 처럼 NoSuchKey ClientError 로 돌려주는 최소 표면."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, *, Bucket: str, Prefix: str = "", **kwargs: object) -> dict:
        if kwargs.get("Delimiter") == "/":
            firsts = {
                Prefix + key[len(Prefix):].split("/", 1)[0] + "/"
                for key in self.objects
                if key.startswith(Prefix) and "/" in key[len(Prefix):]
            }
            return {"CommonPrefixes": [{"Prefix": p} for p in sorted(firsts)]}
        return {
            "Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)],
            "IsTruncated": False,
        }


def _bars(*rows: dict) -> bytes:
    return ("\n".join(json.dumps(r) for r in rows) + "\n").encode()


def _parquet(tickers: list[str], closes: list[float]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.table({"ticker": tickers, "close": closes}), buf)
    return buf.getvalue()


def _reader(objects: dict[str, bytes]) -> LakeReader:
    return LakeReader(_MinuteFakeS3(objects), "bkt")


def test_minute_returns_are_prev_close_vs_trigger_close():
    """전일 종가 대비 트리거 window 의 close — 판정(intraday-anchor-v2)과 같은 축.

    갭(전일 종가→당일 시가)이 기여에 포함되는 것이 이 축의 정의다 — 시가 대비였다면
    갭이 빠져 트리거가 말하는 등락과 분해 합이 어긋난다.
    """
    reader = _reader({
        minute_artifact_key("KR", "2026-07-15", "1030", 2): _bars(
            {"unit_id": "005930", "open": "73400.0", "close": "73500.0"},
            {"unit_id": "000660", "open": "190000.0", "close": "190000.0"},
        ),
    })
    returns = reader.load_minute_returns(
        "KR", "2026-07-15", "1030", 2, None, {"005930": 70000.0, "000660": 200000.0})
    assert abs(returns["005930"] - 0.05) < 1e-9      # 73500/70000-1
    assert abs(returns["000660"] - (-0.05)) < 1e-9   # 190000/200000-1 — 하락 방향 보존


def test_prev_closes_reads_partition_strictly_before_trade_date():
    """분모는 `trade_date` **미만** 최신 파티션이다.

    당일 파티션 기준으로 직전을 세면(daily `load_returns` 방식) 장중엔 당일 파티션이
    없어 분모가 통째로 비고, 15:40 배치로 당일 파티션이 생긴 뒤에는 **당일 종가**가
    분모가 돼 트리거 축과 갈린다 — 두 방향 다 여기서 막는다.
    """
    reader = _reader({
        f"{PRICE_DAILY}trade_date=2026-07-14/p.parquet": _parquet(["005930"], [70000.0]),
        f"{PRICE_DAILY}trade_date=2026-07-15/p.parquet": _parquet(["005930"], [73500.0]),
    })
    assert reader.load_prev_closes("KR", date(2026, 7, 15)) == {"005930": 70000.0}
    # 당일 파티션이 아직 없는 장중 — 직전 거래일이 그대로 분모다
    reader_intraday = _reader({
        f"{PRICE_DAILY}trade_date=2026-07-14/p.parquet": _parquet(["005930"], [70000.0]),
    })
    assert reader_intraday.load_prev_closes("KR", date(2026, 7, 15)) == {"005930": 70000.0}


def test_prev_closes_empty_when_no_earlier_partition():
    """직전 파티션이 아예 없으면 빈 dict — 호출부가 ReturnsNotReady 로 접는다.
    0 이나 당일 값으로 메우면 결손이 정상 분해로 위장된다."""
    reader = _reader({
        f"{PRICE_DAILY}trade_date=2026-07-15/p.parquet": _parquet(["005930"], [73500.0]),
    })
    assert reader.load_prev_closes("KR", date(2026, 7, 15)) == {}


def test_prev_closes_skip_unconvertible_rows_without_killing_the_rest():
    """비수치 종가는 그 티커만 빠진다 — 빠진 티커는 분해에서 미가격으로 잡혀
    coverage 에 드러난다. 한 행이 분모 전체를 죽이면 그날 설명이 통째로 없다."""
    reader = _reader({
        f"{PRICE_DAILY}trade_date=2026-07-14/p.parquet":
            _parquet(["A", "B"], [float("nan"), 100.0]),
    })
    closes = reader.load_prev_closes("KR", date(2026, 7, 15))
    assert closes["B"] == 100.0
    # nan 은 float() 를 통과하므로 여기서 걸러지지 않는다 — 수익률 계산의 유한성
    # 게이트가 잡는다(test_infinity_and_nan_fold_to_none). 그 분업을 고정한다.
    assert set(closes) == {"A", "B"}


def test_missing_artifact_is_empty_not_partial():
    """트리거 window artifact 부재(커밋 지연)는 빈 dict — 부분 결과로 분해하면 결손이
    정상 분해로 위장된다. 빈 dict 는 호출부가 ReturnsNotReady 로 접어 재시도한다."""
    reader = _reader({
        minute_artifact_key("KR", "2026-07-15", "0900", 1):
            _bars({"unit_id": "005930", "open": "1", "close": "1"}),
    })
    assert reader.load_minute_returns(
        "KR", "2026-07-15", "1030", 1, None, {"005930": 1.0}) == {}


def test_contract_violations_fold_to_none_per_unit():
    """0·음수·비수치·분모 결측은 그 unit 만 None — 분해가 미가격으로 제외한다.
    통과값으로 강제(coerce-to-passing)되면 오염된 수익률이 기여 순위에 실린다."""
    reader = _reader({
        minute_artifact_key("KR", "2026-07-15", "1030", 1): _bars(
            {"unit_id": "A", "open": "10", "close": "12"},        # 분모 0
            {"unit_id": "B", "open": "100", "close": "abc"},      # 비수치 close
            {"unit_id": "C", "open": "50", "close": "55"},        # 분모 결측(전일 미상장)
            {"unit_id": "D", "open": "108", "close": "-1"},       # 음수 close
        ),
    })
    returns = reader.load_minute_returns(
        "KR", "2026-07-15", "1030", 1, None, {"A": 0.0, "B": 100.0, "D": 100.0})
    assert returns == {"A": None, "B": None, "C": None, "D": None}


def test_infinity_and_nan_fold_to_none():
    """float("Infinity") 는 양수 비교를 통과하고 float("nan") 은 전부 False 다 —
    유한성 게이트가 없으면 손상 레코드가 수익률 inf 로 위장돼 기여 순위를 오염시킨다."""
    reader = _reader({
        minute_artifact_key("KR", "2026-07-15", "1030", 1): _bars(
            {"unit_id": "A", "open": "100", "close": "Infinity"},
            {"unit_id": "B", "open": "100", "close": "110"},
            {"unit_id": "C", "open": "100", "close": "nan"},
        ),
    })
    returns = reader.load_minute_returns(
        "KR", "2026-07-15", "1030", 1, None,
        {"A": 100.0, "B": float("inf"), "C": float("nan")})
    assert returns == {"A": None, "B": None, "C": None}


def test_division_overflow_folds_to_none():
    """유한 양수 피연산자끼리도 나눗셈이 오버플로한다(분모 1e-300) — 결과 유한성
    검사가 없으면 inf 수익률이 기여 순위·proxy 를 오염시킨다."""
    reader = _reader({
        minute_artifact_key("KR", "2026-07-15", "1030", 1): _bars(
            {"unit_id": "A", "open": "1", "close": "9e14"},
        ),
    })
    assert reader.load_minute_returns(
        "KR", "2026-07-15", "1030", 1, None, {"A": 1e-300}) == {"A": None}


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
        "KR", "2026-07-15", "0900", 1, good, {"005930": 70000.0})
    assert abs(returns["005930"] - 0.01) < 1e-9
    with pytest.raises(ReturnsNotReadyError, match="checksum"):
        reader.load_minute_returns(
            "KR", "2026-07-15", "0900", 1, "0" * 64, {"005930": 70000.0})
