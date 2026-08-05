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

    #: 한 응답에 담는 최대 항목 — 실제 S3 는 1,000 에서 자른다. 테스트는 2 로 줄여
    #: 페이지네이션 경로를 항상 밟게 한다(1,000개 픽스처는 불필요하게 비싸다).
    page_size = 2

    def list_objects_v2(self, *, Bucket: str, Prefix: str = "", **kwargs: object) -> dict:
        token = kwargs.get("ContinuationToken")
        if kwargs.get("Delimiter") == "/":
            items = sorted({
                Prefix + key[len(Prefix):].split("/", 1)[0] + "/"
                for key in self.objects
                if key.startswith(Prefix) and "/" in key[len(Prefix):]
            })
            page, nxt = self._page(items, token)
            resp: dict = {"CommonPrefixes": [{"Prefix": p} for p in page]}
        else:
            items = [k for k in sorted(self.objects) if k.startswith(Prefix)]
            page, nxt = self._page(items, token)
            resp = {"Contents": [{"Key": k} for k in page]}
        resp["IsTruncated"] = nxt is not None
        if nxt is not None:
            resp["NextContinuationToken"] = nxt
        return resp

    def _page(self, items: list[str], token: object) -> tuple[list[str], str | None]:
        start = int(token) if token else 0
        page = items[start:start + self.page_size]
        nxt = start + self.page_size
        return page, (str(nxt) if nxt < len(items) else None)


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


def test_prev_closes_survives_truncated_partition_listing():
    """파티션 목록이 잘리면 **잘린 페이지의 마지막**이 최신으로 인증된다 — 오류 없이
    수년 전 종가가 분모가 되고, 그 값으로 만든 기여도가 정상 설명으로 영속된다.
    KR 거래일 ~245/년이라 4년치면 실제 1,000 상한에 닿는다(fake 는 page_size=2)."""
    reader = _reader({
        f"{PRICE_DAILY}trade_date=2026-07-{d:02d}/p.parquet": _parquet(["005930"], [float(d)])
        for d in (10, 11, 12, 13, 14)
    })
    # 페이지 하나만 읽으면 07-11 이 '직전'이 된다 — 실제 직전은 07-14 다
    assert reader.load_prev_closes("KR", date(2026, 7, 15)) == {"005930": 14.0}


def test_prev_closes_ignores_non_date_partition_dirs():
    """문자열 비교로 '직전'을 고르므로 비정상 디렉터리가 정렬상 정상 파티션보다 뒤에
    와서 분모로 뽑힌다 — 오류 없이 틀린 값이 된다.

    길이 검사로는 부족하다. 오염 값이 **정상 파티션과 목표일 사이에** 정렬되는 배치가
    있어야 그 약한 단언이 깨진다 — 목표 `2026-07-20`, 정상 `2026-07-19`, 오염
    `2026-07-1x` 가 그 배치다(열 글자 · `…-19` 보다 뒤 · 목표보다 앞). 오염 값이 목표일
    **뒤로** 정렬되면 `d < target` 이 먼저 걸러버려 날짜 검증이 없어도 테스트가 통과한다.
    """
    reader = _reader({
        f"{PRICE_DAILY}trade_date=2026-07-19/p.parquet": _parquet(["005930"], [70000.0]),
        f"{PRICE_DAILY}trade_date=2026-07-1x/p.parquet": _parquet(["005930"], [2.0]),
        f"{PRICE_DAILY}trade_date=2026-07-19-copy/p.parquet": _parquet(["005930"], [1.0]),
    })
    assert reader.load_prev_closes("KR", date(2026, 7, 20)) == {"005930": 70000.0}


def test_boolean_price_is_not_coerced_to_one():
    """JSON `true` 는 `float(True) == 1.0` 이라 양수·유한성 게이트를 전부 통과한다 —
    분모 70,000 대비 -99.999% 가 '정상 수익률'로 인증된다(coerce-to-passing).
    파이썬에서 bool 은 int 의 하위형이라 `float()` 만으로는 안 걸린다."""
    reader = _reader({
        minute_artifact_key("KR", "2026-07-15", "1030", 1): _bars(
            {"unit_id": "A", "open": "70000", "close": True},
            {"unit_id": "B", "open": "70000", "close": "73500"},
        ),
    })
    returns = reader.load_minute_returns(
        "KR", "2026-07-15", "1030", 1, None, {"A": 70000.0, "B": 70000.0})
    assert returns["A"] is None, "bool 종가가 -99.999% 수익률로 통과했다"
    assert abs(returns["B"] - (73500 / 70000 - 1)) < 1e-9


def test_boolean_prev_close_is_not_coerced_to_one():
    """분모 쪽도 같은 함정이다 — bool 종가가 분모 1.0 이 되면 모든 수익률이 폭발한다."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.table({"ticker": ["A", "B"], "close": [True, None]}), buf)
    reader = _reader({f"{PRICE_DAILY}trade_date=2026-07-14/p.parquet": buf.getvalue()})
    assert reader.load_prev_closes("KR", date(2026, 7, 15)) == {}


def test_prev_closes_sorts_by_parsed_date_not_string():
    """정렬은 **파싱한 날짜**로 한다.

    `date.fromisoformat` 은 기본형(`20260718`)도 받는다(파이썬 3.11+). 파싱만 하고
    문자열로 정렬하면 `'20260718' > '2026-07-19'`(ASCII `'0'` > `'-'`)라 더 **오래된**
    날이 '직전 거래일'로 뽑혀 분모가 조용히 틀린다.
    """
    reader = _reader({
        f"{PRICE_DAILY}trade_date=2026-07-19/p.parquet": _parquet(["005930"], [70000.0]),
        f"{PRICE_DAILY}trade_date=20260718/p.parquet": _parquet(["005930"], [1.0]),
    })
    assert reader.load_prev_closes("KR", date(2026, 7, 20)) == {"005930": 70000.0}


def test_listing_stops_when_token_does_not_advance():
    """절단됐다면서 토큰이 없거나 안 움직이면 같은 페이지를 영원히 다시 받는다 —
    상주 소비자가 메모리를 먹으며 조용히 멈춘다. 부분 목록으로 끊는 편이 낫다.

    **파티션 나열과 객체 나열 양쪽**을 건다: 두 경로가 각자 루프를 갖고 있으면 한쪽만
    고쳐도 다른 쪽에서 그대로 멈춘다(실측 — 이 테스트가 처음엔 그렇게 걸렸다).
    """
    class _StuckS3(_MinuteFakeS3):
        def list_objects_v2(self, **kwargs):
            resp = super().list_objects_v2(**kwargs)
            resp["IsTruncated"] = True               # 항상 절단됐다고 주장하고
            resp.pop("NextContinuationToken", None)  # 토큰은 주지 않는다
            return resp

    reader = LakeReader(_StuckS3({
        f"{PRICE_DAILY}trade_date=2026-07-19/p.parquet": _parquet(["005930"], [70000.0]),
    }), "bkt")
    # 반환된다는 것 자체가 판정 — 무한루프면 여기서 안 돌아온다
    assert reader.load_prev_closes("KR", date(2026, 7, 20)) == {"005930": 70000.0}


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
