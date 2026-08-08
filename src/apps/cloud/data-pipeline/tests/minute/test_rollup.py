"""5분봉(intraday_5m) 롤업 테스트 (ALPHA-750).

의도: 이 파생이 틀리면 분석엔진이 틀린 봉으로 설명을 계산한다 — 조용히. 그래서
①각 필드가 **어느 1분봉**에서 와야 하는지(window 축 — record ts 로 정렬하는 회귀를
ts 역순 입력으로 반증), ②원장 확정 세대만 읽는지(S3 orphan 세대 배제), ③결손 분
부분 집계, ④버킷 미완 시 산출 없음 + 안 닫힌 이웃 버킷 배제, ⑤늦은 recovery 커밋
의 재작성과 빈 정정의 재작성(폐기 가격 잔존 금지), ⑥다른 벤더 파티션 보호,
⑦롤업 예외가 1분 커밋(정본)을 깨지 않음, ⑧산출 스키마가 기존 fmp 파일 실측과
동일함을 고정한다.

EOD 배치(ALPHA-839)는 같은 집계를 다른 진입점으로 부른다 — ⑨배치 산출이 후크 산출과
**바이트 동일**(계약: 같은 커밋 세대 집합이면 같은 산출), ⑩재실행 멱등, ⑪커밋 0건은
빈 파일이 아니라 스킵(다른 writer 의 파티션 보호), ⑫원장이 주는 UTC 시각을 KST 로 접음
(fake 가 안 재현하는 축이라 테스트가 직접 밟는다), ⑬확정 세션에 파생이 없는 거래일
판정(파일명이 아니라 파티션 축).
"""

from __future__ import annotations

import io
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import (
    LocalStorage,
    canonical_intraday_5m_key,
    canonical_price_minute_artifact_key,
)
from data_pipeline.minute.artifacts import serialize_records
from data_pipeline.minute.commit import MinuteCommitter
from data_pipeline.minute.fake_collector import FakePriceCollector
from data_pipeline.minute.models import KST, Universe, plan_session_windows
from data_pipeline.minute.repository import MinuteLedger
from data_pipeline.minute.rollup import (
    WRITER_SINCE,
    maybe_rollup,
    rollup_session,
    unfilled_settled_days,
)
from data_pipeline.minute.worker import PriceWorker, WorkerConfig

# **롤업이 소유하는 첫 날**. 날짜를 박지 않고 경계에서 파생시킨다 — 소유권 경계는
# 옮겨진 적이 있고(ALPHA-836) 앞으로도 옮겨진다. 박아 두면 경계가 움직일 때마다
# 이 파일 전체가 "가드 밖"으로 떨어져 무더기로 깨진다.
SESSION_DATE = WRITER_SINCE
SESSION_DAY = date.fromisoformat(SESSION_DATE)
DAY_KEY = canonical_intraday_5m_key("KR", SESSION_DATE)
UNIVERSE = Universe(
    universe_version="univ-test-v1",
    etf_ids=("500000",),
    constituent_ids=("100000", "100001"),
)


def _consecutive(n: int) -> list[str]:
    """`SESSION_DAY` 부터 연속 n일.

    ⚠️ **달력 요일은 안 피한다.** `rollup_session_cli` 는 `trading_day` 가 참일 때만
    rollup 을 부르므로, 경계가 목·금에 놓이면 여기서 나온 뒤쪽 날짜가 주말이 돼 그
    분기를 못 밟는 테스트가 생긴다. 지금 경계(월요일)에서는 안 걸리고, 걸리면 **조용히
    통과가 아니라 크게 깨진다** — 그때 그 테스트만 명시적 평일을 쓰면 된다.
    """
    return [(SESSION_DAY + timedelta(days=i)).isoformat() for i in range(n)]


def w(hhmm: str) -> datetime:
    return datetime.combine(SESSION_DAY, time(int(hhmm[:2]), int(hhmm[2:])), tzinfo=KST)


def naive(hhmm: str) -> datetime:
    return w(hhmm).replace(tzinfo=None)


def bar(unit: str, ts_hhmm: str, o, h, low, c, v) -> dict:
    # 실 record 축(price_collect.record_of): 값은 문자열, source 는 컬럼이다
    return {"unit_id": unit, "ts": w(ts_hhmm), "open": str(o), "high": str(h),
            "low": str(low), "close": str(c), "volume": str(v), "source": "toss"}


class Fixture:
    """원장(FakeMinuteDB) + 레이크(LocalStorage) — 커밋 상태를 직접 심는다."""

    def __init__(self, tmp_path):
        self.db = FakeMinuteDB()
        self.storage = LocalStorage(root=tmp_path)
        self.ledger = MinuteLedger(db=DbConfig(password="x"), connect_fn=self.db.connect)
        planned = plan_session_windows(SESSION_DAY, universe=UNIVERSE, extended_hours=True)
        self.session_id, _ = self.ledger.plan_session(
            dataset="price_minute", source_group="toss", session_date=SESSION_DAY,
            universe_version=UNIVERSE.universe_version,
            universe_hash=UNIVERSE.universe_hash, windows=planned,
        )

    def put_artifact(self, hhmm: str, records: list[dict], generation: int = 1):
        key = canonical_price_minute_artifact_key("KR", SESSION_DATE, hhmm, generation)
        self.storage.put_bytes(key, serialize_records(records))

    def commit(self, hhmm: str, records: list[dict], generation: int = 1):
        """artifact PUT + 원장 커밋 표시 — Worker 커밋 후의 상태를 재현한다."""
        self.put_artifact(hhmm, records, generation)
        window = self.db.windows[(self.session_id, w(hhmm))]
        window["checksum"] = f"c-{hhmm}-{generation}"
        window["generation"] = generation

    def rollup_at(self, hhmm: str, session_date: str = SESSION_DATE) -> str | None:
        # window_start 는 session_date 에서 만든다 — 둘을 따로 두면 계획 밖 window 로
        # 넘어가 버킷 게이트가 먼저 죽고, 정작 보려던 판정에 도달하지 못한다.
        day = date.fromisoformat(session_date)
        return maybe_rollup(
            self.storage, self.ledger, session_id=self.session_id, market="KR",
            session_date=session_date, universe=UNIVERSE,
            window_start=datetime(
                day.year, day.month, day.day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=KST
            ),
        )

    def rollup_session(self, session_date: str = SESSION_DATE) -> str | None:
        return rollup_session(
            self.storage, self.ledger, session_id=self.session_id, market="KR",
            session_date=session_date,
        )

    def read_rows(self) -> list[dict]:
        import pyarrow.parquet as pq

        return pq.read_table(io.BytesIO(self.storage.get_bytes(DAY_KEY))).to_pylist()


class TestAggregation:
    def test_each_field_comes_from_its_window(self, tmp_path):
        # record ts 를 **역순**으로 심는다 — ts(또는 도착 순서)로 정렬하는 구현이면
        # open 이 0904 에서, close 가 0900 에서 나와 여기서 죽는다. 축은 window 다.
        fx = Fixture(tmp_path)
        fx.commit("0900", [bar("500000", "0904", 100, 105, 99, 101, 10)])
        fx.commit("0901", [bar("500000", "0903", 101, 106, 90, 102, 20)])
        fx.commit("0902", [bar("500000", "0902", 102, 120, 100, 103, 30)])
        fx.commit("0903", [bar("500000", "0901", 103, 107, 101, 104, 40)])
        fx.commit("0904", [bar("500000", "0900", 104, 108, 102, 115, 50)])
        assert fx.rollup_at("0904") == DAY_KEY
        [row] = fx.read_rows()
        assert row == {
            "ticker": "500000",
            "source_symbol": "500000",     # 1분 롤업은 bare 그대로(.KS 는 fmp 표기)
            "ts": naive("0900"),           # 구간 시작, naive KST
            "open": 100.0,                 # 버킷 첫 window(0900)의 open
            "high": 120.0,                 # 전 구간 max(0902)
            "low": 90.0,                   # 전 구간 min(0901)
            "close": 115.0,                # 버킷 마지막 window(0904)의 close
            "volume": 150,                 # 합
            # 벤더가 아니라 파생 유래 상수다 — 레인 벤더(kis|toss)가 바뀌어도 소비자
            # 필터 축이 흔들리지 않는다(벤더 정본은 1분 canonical 의 record 컬럼)
            "source_vendor": "1m_rollup",
            "available_at": naive("0905"),  # 구간 끝
        }

    def test_schema_matches_existing_fmp_dataset(self, tmp_path):
        # 기존 파티션(fmp 백필)과 같은 데이터셋에 쓴다 — 컬럼·타입이 갈리면 소비자
        # 스키마 검증이 우리 파티션만 거부한다(여분 컬럼도 두지 않는다).
        import pyarrow as pa
        import pyarrow.parquet as pq

        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902", "0903", "0904"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 101, 99, 100, 1)])
        fx.rollup_at("0904")
        schema = pq.read_table(io.BytesIO(fx.storage.get_bytes(DAY_KEY))).schema
        assert schema.names == ["ticker", "source_symbol", "ts", "open", "high",
                                "low", "close", "volume", "source_vendor",
                                "available_at"]
        assert schema.field("ts").type == pa.timestamp("us")          # naive
        assert schema.field("available_at").type == pa.timestamp("us")
        assert schema.field("volume").type == pa.int64()
        for column in ("open", "high", "low", "close"):
            assert schema.field(column).type == pa.float64()

    def test_reads_ledger_generation_not_s3_orphan(self, tmp_path):
        # PUT 후 DB commit 전에 죽은 orphan(gen2) 이 S3 에 남아도, 원장이 확정한
        # gen1 을 읽어야 한다 — orphan 을 읽으면 미확정 가격이 파생에 실린다.
        fx = Fixture(tmp_path)
        fx.commit("0904", [bar("500000", "0904", 100, 105, 99, 101, 10)], generation=1)
        fx.put_artifact("0904", [bar("500000", "0904", 100, 105, 99, 999, 10)],
                        generation=2)  # orphan — 원장 커밋 없음
        fx.rollup_at("0904")
        [row] = fx.read_rows()
        assert row["close"] == 101.0
        # 정정이 **커밋되면** 그 세대가 정본이다
        fx.commit("0904", [bar("500000", "0904", 100, 105, 99, 999, 10)], generation=2)
        fx.rollup_at("0904")
        [row] = fx.read_rows()
        assert row["close"] == 999.0

    def test_missing_minutes_partial_rollup(self, tmp_path):
        # 0902 는 커밋이 없고, 100000 은 0901·0903 에만 봉이 있다 — 있는 봉만으로
        # 집계한다(결손 분이 버킷 전체를 죽이면 한 분 실패가 5분을 지운다).
        fx = Fixture(tmp_path)
        fx.commit("0900", [bar("500000", "0900", 100, 101, 99, 100, 1)])
        fx.commit("0901", [bar("500000", "0901", 100, 101, 99, 100, 1),
                           bar("100000", "0901", 50, 55, 49, 52, 5)])
        fx.commit("0903", [bar("500000", "0903", 100, 101, 99, 100, 1),
                           bar("100000", "0903", 52, 60, 51, 58, 7)])
        fx.commit("0904", [bar("500000", "0904", 100, 101, 99, 100, 1)])
        fx.rollup_at("0904")
        rows = {row["ticker"]: row for row in fx.read_rows()}
        partial = rows["100000"]
        assert partial["open"] == 50.0    # 있는 봉 중 첫 window(0901)
        assert partial["close"] == 58.0   # 있는 봉 중 마지막 window(0903)
        assert partial["high"] == 60.0 and partial["low"] == 49.0
        assert partial["volume"] == 12
        assert rows["500000"]["volume"] == 4

    def test_incomplete_bucket_produces_nothing(self, tmp_path):
        # 버킷 마지막 분(0904) 전에는 산출이 없어야 한다 — 매분 덮어쓰면 소비자가
        # 미완 버킷을 완성 봉으로 읽는 순간이 생긴다.
        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 101, 99, 100, 1)])
        assert fx.rollup_at("0902") is None
        assert fx.storage.list_keys("canonical/market_data/intraday_5m") == []

    def test_open_sibling_bucket_excluded_from_day_file(self, tmp_path):
        # 09:05 버킷은 0905 만 커밋됐다(안 닫힘) — 0900 버킷 마감 재작성에 실리면
        # 부분 관측이 완성 봉처럼 노출된다(커밋 지평이 버킷 끝을 지나야 싣는다).
        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902", "0903", "0904"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 101, 99, 100, 1)])
        fx.commit("0905", [bar("500000", "0905", 200, 201, 199, 200, 2)])
        fx.rollup_at("0904")
        assert [row["ts"] for row in fx.read_rows()] == [naive("0900")]

    def test_late_recovery_commit_rewrites_day_file(self, tmp_path):
        # recovery lane 은 최고령부터 재청구한다 — 버킷 마지막 분이 먼저 커밋되고 앞
        # 분이 늦게 오는 backlog 경로. 늦은 커밋이 재작성하지 않으면 그 버킷의 5분봉
        # 이 영구 부분본으로 남는다.
        fx = Fixture(tmp_path)
        fx.commit("0904", [bar("500000", "0904", 104, 108, 102, 115, 50)])
        assert fx.rollup_at("0904") == DAY_KEY
        [row] = fx.read_rows()
        assert (row["open"], row["volume"]) == (104.0, 50)
        fx.commit("0900", [bar("500000", "0900", 100, 105, 99, 101, 10)])
        assert fx.rollup_at("0900") == DAY_KEY  # 같은 키를 덮어쓴다(파생물)
        [row] = fx.read_rows()
        assert (row["open"], row["volume"]) == (100.0, 60)

    def test_correction_to_empty_rewrites_file(self, tmp_path):
        # 정정 세대가 그날 봉을 전부 지웠으면 빈 파일로 **재작성**해야 한다 —
        # 산출을 생략하면 직전 파일이 남아 폐기된 가격을 계속 서빙한다.
        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902", "0903", "0904"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 101, 99, 100, 1)])
        fx.rollup_at("0904")
        assert len(fx.read_rows()) == 1
        for hhmm in ("0900", "0901", "0902", "0903", "0904"):
            fx.commit(hhmm, [], generation=2)
        assert fx.rollup_at("0904") == DAY_KEY
        assert fx.read_rows() == []

    def test_whole_day_rewrite_accumulates_buckets(self, tmp_path):
        # 파일은 거래일당 1개다 — 두 번째 버킷 마감이 첫 버킷 행을 지우면 안 된다.
        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902", "0903", "0904"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 101, 99, 100, 1)])
        fx.rollup_at("0904")
        for hhmm in ("0905", "0906", "0907", "0908", "0909"):
            fx.commit(hhmm, [bar("500000", hhmm, 200, 201, 199, 200, 2)])
        fx.rollup_at("0909")
        rows = fx.read_rows()
        assert [(row["ts"], row["close"], row["volume"]) for row in rows] == [
            (naive("0900"), 100.0, 5),
            (naive("0905"), 200.0, 10),
        ]

    def test_refuses_fmp_backfill_partition(self, tmp_path):
        # fmp 백필 정본(~2026-07-31)의 trade_date 파티션은 덮지 않는다 — 과거
        # --session-date 재실행 하나가 벤더 원본 파일을 파생본으로 갈아치운다.
        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902", "0903", "0904"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 101, 99, 100, 1)])
        assert fx.rollup_at("0904", session_date="2026-07-31") is None
        assert fx.storage.list_keys("canonical/market_data/intraday_5m") == []


class TestWorkerHook:
    """워커 배선 — 커밋이 롤업을 부르고, 롤업 실패는 커밋 성공을 못 깬다."""

    def build_worker(self, db, tmp_path, windows=5):
        ledger = MinuteLedger(db=DbConfig(password="x"), connect_fn=db.connect)
        planned = plan_session_windows(SESSION_DAY, universe=UNIVERSE, extended_hours=True)
        session_id, _ = ledger.plan_session(
            dataset="price_minute", source_group="toss", session_date=SESSION_DAY,
            universe_version=UNIVERSE.universe_version,
            universe_hash=UNIVERSE.universe_hash, windows=planned[:windows],
        )
        return PriceWorker(
            session_id=session_id, ledger=ledger,
            committer=MinuteCommitter(db=DbConfig(password="x"), connect_fn=db.connect),
            storage=LocalStorage(root=tmp_path),
            collector=FakePriceCollector({"scenario": "normal"}, seed=42),
            config=WorkerConfig(
                worker_id="w1", dataset="price_minute", source="toss", market="KR",
                session_date=SESSION_DATE, universe=UNIVERSE, run_id="run_t",
                trigger_schema_version="trig-1",
                destination="price-analysis-realtime", is_backfill=False,
            ),
        )

    def run_until_idle(self, worker, now):
        for _ in range(10):
            if worker.tick(now) != "PROCESSED":
                return
        raise AssertionError("IDLE 미도달")

    def test_worker_commits_produce_day_parquet(self, tmp_path):
        import pyarrow.parquet as pq

        db = FakeMinuteDB()
        worker = self.build_worker(db, tmp_path)
        self.run_until_idle(worker, datetime.combine(SESSION_DAY, time(9, 10), tzinfo=KST))
        rows = pq.read_table(
            io.BytesIO(worker.storage.get_bytes(DAY_KEY))
        ).to_pylist()
        assert sorted(row["ticker"] for row in rows) == ["100000", "100001", "500000"]
        # realtime lane 이 0904 를 먼저 커밋해도(재작성 경로) 최종본은 5분 전부의 합이다
        assert all(row["ts"] == naive("0900") for row in rows)
        assert all(row["source_vendor"] == "1m_rollup" for row in rows)

    def test_rollup_failure_does_not_break_commit(self, tmp_path, monkeypatch):
        # 1분 레인이 정본이고 5분은 파생 — 파생 오류가 수집을 죽이면 주객전도다
        import data_pipeline.minute.worker as worker_module

        def boom(*args, **kwargs):
            raise RuntimeError("pyarrow exploded")

        monkeypatch.setattr(worker_module, "maybe_rollup", boom)
        db = FakeMinuteDB()
        worker = self.build_worker(db, tmp_path, windows=3)
        now = datetime.combine(SESSION_DAY, time(9, 10), tzinfo=KST)
        assert worker.tick(now) == "PROCESSED"  # WINDOW_FAILED 가 아니다
        assert {win["data_status"] for win in db.windows.values()} == {"VALID"}


class TestSessionRollup:
    """EOD 배치(ALPHA-839) — 후크와 **같은 바이트**를 내야 하고, 원장이 비면 안 쓴다."""

    def test_matches_hook_output_byte_for_byte(self, tmp_path):
        # 계약이 "같은 커밋 세대 집합이면 같은 산출"이다. 배치가 후크와 다른 바이트를
        # 내면 마감 산출이 장중 산출을 조용히 갈아치우는 것이라, 자기 대조가 이 티켓의
        # 핵심 실증이다(실환경 sha256 대조와 같은 판정을 여기서 고정한다).
        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902", "0903", "0904",
                     "0905", "0906", "0907", "0908", "0909"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 110, 90, 100 + int(hhmm[-1]), 3)])
        assert fx.rollup_at("0909") == DAY_KEY
        hook_bytes = fx.storage.get_bytes(DAY_KEY)

        assert fx.rollup_session() == DAY_KEY
        assert fx.storage.get_bytes(DAY_KEY) == hook_bytes

    def test_rerun_is_byte_identical(self, tmp_path):
        # 백필 재실행이 앞선 산출을 덮어 지운 전례가 있다 — 멱등은 주장이 아니라 단언이다
        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902", "0903", "0904"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 101, 99, 100, 1)])
        assert fx.rollup_session() == DAY_KEY
        first = fx.storage.get_bytes(DAY_KEY)
        assert fx.rollup_session() == DAY_KEY
        assert fx.storage.get_bytes(DAY_KEY) == first

    def test_no_commits_skips_without_touching_file(self, tmp_path):
        # `max()` 가 빈 시퀀스로 ValueError 를 내던 자리다. 더 중요한 건 **파일을 안
        # 건드리는 것** — 원장에 커밋이 없다는 건 그날을 모른다는 뜻이지 그날 봉이
        # 없다는 뜻이 아니라, 빈 파일로 덮으면 다른 writer 의 산출을 지운다.
        fx = Fixture(tmp_path)
        fx.storage.put_bytes(DAY_KEY, b"other-writer")
        assert fx.rollup_session() is None
        assert fx.storage.get_bytes(DAY_KEY) == b"other-writer"

    def test_open_bucket_is_not_exposed_as_a_full_bar(self, tmp_path):
        # 배치엔 버킷 게이트가 없다 — 닫힘 판정을 커밋 지평 하나에 맡긴다. 마지막
        # 버킷의 커밋이 하나라도 없으면 그 버킷은 통째로 빠져야 한다(부분 봉 금지).
        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902", "0903", "0904", "0905", "0906"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 101, 99, 100, 1)])
        assert fx.rollup_session() == DAY_KEY
        assert {row["ts"] for row in fx.read_rows()} == {naive("0900")}

    def test_ledger_timestamps_arrive_in_utc(self, tmp_path):
        # 원장 컬럼은 timestamptz 라 psycopg 는 **UTC-aware** 로 준다. 배치는 그걸 KST
        # 로 접어야 `%H%M` 키가 커밋·버킷 축과 맞는다 — 안 접으면 UTC 의 HHMM(0000)으로
        # 찾아 전건 결손이 되고, 산출이 조용히 빈 파일이 된다.
        # ⚠️ fake 는 계획을 KST-aware 로 심어 이 경로를 안 밟는다(픽스처가 운영과
        # 다르면 결함이 초록으로 통과한다) — 그래서 여기서 명시적으로 UTC 로 바꾼다.
        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902", "0903", "0904"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 101, 99, 100, 1)])
        for row in fx.db.windows.values():
            row["window_start"] = row["window_start"].astimezone(timezone.utc)
            row["window_end"] = row["window_end"].astimezone(timezone.utc)
        assert fx.rollup_session() == DAY_KEY
        assert {row["ts"] for row in fx.read_rows()} == {naive("0900")}

    def test_commits_without_a_closed_bucket_keep_the_file(self, tmp_path):
        # 🔴 커밋이 **1건 이상**인데 닫힌 버킷이 0개인 날(예: 09:00 만 커밋하고 세션이
        # 죽었다). 커밋 0건 가드는 여기를 안 막고, 아래 "빈 파일이라도 쓴다" 경로로
        # 떨어져 다른 writer 가 채운 파티션을 **0행 parquet 로 덮는다**(실측 1,665B).
        # 그 경로는 "정정이 봉을 지웠다"는 후크 전제용이라 여기 오면 안 된다.
        fx = Fixture(tmp_path)
        fx.storage.put_bytes(DAY_KEY, b"other-writer")
        fx.commit("0900", [bar("500000", "0900", 100, 101, 99, 100, 1)])
        assert fx.rollup_session() is None
        assert fx.storage.get_bytes(DAY_KEY) == b"other-writer"

    def test_refuses_when_another_writer_owns_the_partition(self, tmp_path):
        # 같은 파티션에 다른 파일명이 있으면 우리 part-0 을 나란히 놓는 순간 소비자
        # 글롭에서 (ticker, ts) 가 두 번 세어진다(거래량 이중계상). 덮을 수도 지울 수도
        # 없으니 산출하지 않고 사람에게 넘긴다.
        fx = Fixture(tmp_path)
        fx.storage.put_bytes(
            f"canonical/market_data/intraday_5m/market=KR/trade_date={SESSION_DATE}"
            "/part-toss-backfill.parquet", b"x")
        for hhmm in ("0900", "0901", "0902", "0903", "0904"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 101, 99, 100, 1)])
        assert fx.rollup_session() is None
        assert fx.storage.list_keys(
            f"canonical/market_data/intraday_5m/market=KR/trade_date={SESSION_DATE}/"
        ) == [f"canonical/market_data/intraday_5m/market=KR/trade_date={SESSION_DATE}"
              "/part-toss-backfill.parquet"]

    def test_refuses_partition_owned_by_another_vendor(self, tmp_path):
        # 후크에 걸린 WRITER_SINCE 가드가 배치 경로에도 걸려야 한다 — 안 걸리면
        # 과거 --session-date 재실행 하나가 벤더 원본 파티션을 파생본으로 갈아치운다.
        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902", "0903", "0904"):
            fx.commit(hhmm, [bar("500000", hhmm, 100, 101, 99, 100, 1)])
        assert fx.rollup_session(session_date="2026-07-31") is None
        assert fx.storage.list_keys("canonical/market_data/intraday_5m") == []

    def test_writer_owns_a_backfilled_day_before_the_boundary(self, tmp_path):
        """경계 **앞**이라도 롤업이 온전한 계열을 갖게 된 날은 롤업이 쓴다.

        경계의 뜻은 날짜가 아니라 **재료**다(ALPHA-836). 재료가 나중에 생긴 날
        (ALPHA-846 의 KIS 소급 수집)은 그 정의상 롤업 소유인데, 날짜 하나로는 표현할
        수 없다 — 경계를 내리면 재료가 여전히 없는 08-04~08-09 까지 딸려 온다.

        ⚠️ 위 `test_refuses_partition_owned_by_another_vendor`(07-31 거부)만으로는 이
        예외가 실제로 뚫리는지 알 수 없다 — 예외를 통째로 지워도 07-31 은 계속 거부된다.
        """
        from data_pipeline.minute.rollup import WRITER_OWNED_BEFORE_SINCE

        owned = sorted(WRITER_OWNED_BEFORE_SINCE)[0]
        assert owned < WRITER_SINCE, "예외가 경계 앞이어야 의미가 있다"
        fx = Fixture(tmp_path)
        for hhmm in ("0900", "0901", "0902", "0903", "0904"):
            records = [{"unit_id": "500000",
                        "ts": datetime.combine(date.fromisoformat(owned),
                                               time(int(hhmm[:2]), int(hhmm[2:])), tzinfo=KST),
                        "open": "100", "high": "101", "low": "99", "close": "100",
                        "volume": "1", "source": "kis"}]
            fx.storage.put_bytes(
                canonical_price_minute_artifact_key("KR", owned, hhmm, 1),
                serialize_records(records))
            window = fx.db.windows[(fx.session_id, w(hhmm))]
            window["checksum"], window["generation"] = f"c-{hhmm}-1", 1
        assert fx.rollup_session(session_date=owned) == canonical_intraday_5m_key("KR", owned)

class TestUnfilledSettledDays:
    """구멍 판정 — 원장이 멈춘 거래일에 파생이 없는 날."""

    def settle(self, fx, session_date: str, phase: str = "DRAINED"):
        for row in fx.db.sessions.values():
            if row["session_date"] == date.fromisoformat(session_date):
                row["phase"] = phase

    def scan(self, fx, before: date = SESSION_DAY + timedelta(days=1)):
        return unfilled_settled_days(
            fx.storage, fx.ledger, market="KR",
            dataset="price_minute", source_group="toss", before=before,
        )

    def unfilled(self, fx, before: date = SESSION_DAY + timedelta(days=1)) -> list[str]:
        return self.scan(fx, before)[0]

    def contested(self, fx, before: date = SESSION_DAY + timedelta(days=1)) -> list[str]:
        return self.scan(fx, before)[1]

    def candidates(self, fx, before: date = SESSION_DAY + timedelta(days=1)) -> int:
        return self.scan(fx, before)[2]

    def test_owned_day_before_the_boundary_is_watched(self, tmp_path):
        """🔴 소유하는 날은 **감시한다** — 소유권과 판정 창이 갈리면 안 된다.

        예외일(`WRITER_OWNED_BEFORE_SINCE`)은 정의상 경계보다 앞이라, 창을
        `WRITER_SINCE` 로 잡으면 롤업 소유가 되면서 동시에 감시에서 영구 제외된다.
        그러면 그날이 비어 있어도 매일 `unfilled=[] contested=[]` 로 초록이 나고,
        백필은 이미 손을 뗐으니 채울 주체도 없다 — 아무도 못 보는 구멍이 된다.
        """
        from data_pipeline.minute.rollup import WRITER_OWNED_BEFORE_SINCE

        owned = sorted(WRITER_OWNED_BEFORE_SINCE)[0]
        fx = Fixture(tmp_path)
        planned = plan_session_windows(
            date.fromisoformat(owned), universe=UNIVERSE, extended_hours=True
        )
        fx.ledger.plan_session(
            dataset="price_minute", source_group="toss",
            session_date=date.fromisoformat(owned),
            universe_version=UNIVERSE.universe_version,
            universe_hash=UNIVERSE.universe_hash, windows=planned,
        )
        self.settle(fx, owned)
        assert owned in self.unfilled(fx), "롤업이 소유하는 날이 판정 창 밖이다"

    def test_backfill_owned_day_stays_out_of_the_scan(self, tmp_path):
        """반대 방향 — 백필이 소유하는 날은 후보가 아니다.

        ⚠️ 날짜를 **예외일과 경계 사이**에서 고른다. 그 밖(예: 07-31)이면 SQL 하한이
        혼자 걸러 필터를 지워도 초록이다 — 넓힌 창에 섞여 들어오는 구간이 여기뿐이라
        `writer_owns` 재필터가 실제로 하중을 받는 자리도 여기다.
        """
        from data_pipeline.minute.rollup import (
            WRITER_OWNED_BEFORE_SINCE,
            scan_lower,
        )

        older = (date.fromisoformat(scan_lower()) + timedelta(days=1)).isoformat()
        assert scan_lower() < older < WRITER_SINCE, "넓힌 창 **안**이어야 필터가 걸린다"
        assert older not in WRITER_OWNED_BEFORE_SINCE
        fx = Fixture(tmp_path)
        planned = plan_session_windows(
            date.fromisoformat(older), universe=UNIVERSE, extended_hours=True
        )
        fx.ledger.plan_session(
            dataset="price_minute", source_group="toss",
            session_date=date.fromisoformat(older),
            universe_version=UNIVERSE.universe_version,
            universe_hash=UNIVERSE.universe_hash, windows=planned,
        )
        self.settle(fx, older)
        assert older not in self.unfilled(fx)

    def test_drained_day_without_partition_is_reported(self, tmp_path):
        # ⚠️ 축이 DRAINED 다. FINALIZED 로 물으면 dev 원장에서 영영 0건이다 — 실측
        # 2026-08-07 기준 FINALIZED 세션이 **한 건도 없고** 전부 DRAINED 에 멈춰 있다
        # (qc-minute-session 이 안 돈다). 안 본 것을 "구멍 없음"으로 확정하는 자리다.
        fx = Fixture(tmp_path)
        self.settle(fx, SESSION_DATE)
        assert self.unfilled(fx) == [SESSION_DATE]

    def foreign(self, fx):
        fx.storage.put_bytes(
            f"canonical/market_data/intraday_5m/market=KR/trade_date={SESSION_DATE}"
            "/part-toss-backfill.parquet", b"x",
        )

    def test_partition_held_by_another_writer_is_contested_not_unfilled(self, tmp_path):
        # `_rollup_day` 가 타 writer 파일을 보고 산출을 거부한 날은 파티션이 안 빈다 —
        # "비었나"로 물으면 영원히 "채워짐"으로 보인다(조용한 영구 구멍).
        # ⚠️ 결손과 **다른 목록**이어야 한다: 처방이 재수집이 아니라 소유자 결정이다.
        fx = Fixture(tmp_path)
        self.settle(fx, SESSION_DATE)
        self.foreign(fx)
        assert self.contested(fx) == [SESSION_DATE]
        assert self.unfilled(fx) == []

    def test_contested_is_caught_even_when_our_output_already_exists(self, tmp_path):
        # 🔴 운영에서 더 흔한 순서 — 후크가 09:04 에 part-0 를 쓴 **뒤** 백필이 끼어든다.
        # 그때 rollup 은 얼어붙고 남는 건 그 시점의 **부분본**인데, "우리 part-0 있나"로만
        # 물으면 그 부분본이 완성본처럼 보여 영원히 안 잡힌다.
        fx = Fixture(tmp_path)
        self.settle(fx, SESSION_DATE)
        fx.storage.put_bytes(DAY_KEY, b"partial-ours")
        self.foreign(fx)
        assert self.contested(fx) == [SESSION_DATE]

    def test_our_own_output_is_not_a_hole(self, tmp_path):
        # 반대 방향 — 우리 part-0 만 있으면 어느 목록에도 안 든다(위가 항등식이 아님을 못박는다)
        fx = Fixture(tmp_path)
        self.settle(fx, SESSION_DATE)
        fx.storage.put_bytes(DAY_KEY, b"ours")
        assert self.unfilled(fx) == [] and self.contested(fx) == []

    def test_live_session_is_not_a_hole(self, tmp_path):
        # 아직 도는 세션(PLANNED·ACTIVE·DRAINING)은 원장이 움직이는 중이라 "재료가
        # 안 변하는데 파생이 없다"가 성립하지 않는다 — 장중에 오늘이 결손으로 잡히면
        # 목록이 매일 거짓 양성으로 시작한다.
        fx = Fixture(tmp_path)
        assert self.unfilled(fx) == []          # 계획 직후 = PLANNED
        self.settle(fx, SESSION_DATE, phase="ACTIVE")
        assert self.unfilled(fx) == []
        self.settle(fx, SESSION_DATE, phase="FAILED")   # QC 가 모순을 찾은 날은 본다
        assert self.unfilled(fx) == [SESSION_DATE]

    def test_stuck_draining_day_is_reported(self, tmp_path):
        # stop 이 상한 초과로 exit 1 하면 세션이 DRAINING 에 영구 고착한다(ack 할 워커가
        # 없고 다음날 start 는 새 session_id 를 만든다). 그날은 rollup 도 실패할 확률이
        # 가장 높으므로, DRAINED_PHASES 만 보면 **가장 위험한 날이 판정에서 빠진다**.
        fx = Fixture(tmp_path)
        self.settle(fx, SESSION_DATE, phase="DRAINING")
        assert self.unfilled(fx) == [SESSION_DATE]

    def test_today_is_excluded(self, tmp_path):
        # 오늘은 이 판정의 대상이 아니다 — 진행 중인 DRAINING 을 고착으로 오인하면 매일
        # 거짓 양성으로 시작한다. 오늘의 결손은 실행 자신의 exit code 와 key 가 말한다.
        fx = Fixture(tmp_path)
        self.settle(fx, SESSION_DATE, phase="DRAINING")
        assert self.unfilled(fx, before=SESSION_DAY) == []

    def test_reports_denominator(self, tmp_path):
        # 빈 목록은 "구멍 없음"과 "본 게 없음" 둘 다다 — 분모가 그 둘을 가른다.
        fx = Fixture(tmp_path)
        assert self.candidates(fx) == 0                      # 아직 PLANNED
        self.settle(fx, SESSION_DATE)
        assert self.candidates(fx) == 1


class TestRollupSessionCli:
    """CLI 규약 — exit code 와 출력 **형상**. 둘 다 소비자가 오독하기 쉬운 자리다.

    ⚠️ 모든 테스트가 원장을 fake 로 물린다. 안 물리면 실 psycopg 연결 실패가 `except
    Exception → 2` 로 뭉개져 **어떤 가드를 지워도 같은 2 가 나온다**(변이로 확인한 거짓
    초록 — 처음 이 클래스의 3건이 그랬다).
    """

    def settings(self, tmp_path, *, db=True):
        from data_pipeline.config import DbConfig, StorageConfig

        class S:
            pass
        s = S()
        s.db = DbConfig(password="x") if db else None
        s.storage = StorageConfig(backend="local", local_root=str(tmp_path))
        return s

    def with_fake_ledger(self, monkeypatch) -> FakeMinuteDB:
        import data_pipeline.minute.repository as repo
        import data_pipeline.minute.rollup as mod
        from data_pipeline.config import DbConfig

        db = FakeMinuteDB()
        real = repo.MinuteLedger
        monkeypatch.setattr(repo, "MinuteLedger", lambda **kw: real(
            db=DbConfig(password="x"), connect_fn=db.connect))
        # **시계를 잡는다.** 구멍 판정 창은 `[WRITER_SINCE, 오늘)` 이라, 소유권 경계가
        # 미래로 옮겨진 동안(ALPHA-836) 실제 오늘로는 창이 통째로 비어 이 클래스의
        # 판정을 하나도 못 밟는다 — 그러면 "구멍이 없다"와 "볼 창이 없다"가 구분되지 않는다.
        monkeypatch.setattr(mod, "_scan_before", lambda: SESSION_DAY + timedelta(days=7))
        return db

    def plan(self, db, session_date: str, *, phase: str = "DRAINED") -> str:
        """원장에 실제 세션을 만든다 — dict 를 손으로 심으면 필드가 빠져 `session_snapshot`
        이 KeyError 로 죽고, 그게 except 에 잡혀 **테스트가 의도한 경로를 안 밟는다**."""
        from data_pipeline.config import DbConfig
        from data_pipeline.minute.repository import MinuteLedger

        ledger = MinuteLedger(db=DbConfig(password="x"), connect_fn=db.connect)
        day = date.fromisoformat(session_date)
        sid, _ = ledger.plan_session(
            dataset="price_minute", source_group="toss", session_date=day,
            universe_version=UNIVERSE.universe_version,
            universe_hash=UNIVERSE.universe_hash,
            windows=plan_session_windows(day, universe=UNIVERSE, extended_hours=True),
        )
        db.sessions[sid]["phase"] = phase
        return sid

    def run(self, settings, **kw):
        from data_pipeline.minute.rollup import rollup_session_cli

        kw.setdefault("dataset", "price_minute")
        kw.setdefault("source_group", "toss")
        kw.setdefault("session_date", SESSION_DATE)
        return rollup_session_cli(settings, **kw)

    def test_news_dataset_is_refused(self, tmp_path, monkeypatch):
        # 뉴스 세션도 390 window 를 계획하므로 committed 가 안 빈다. 그대로 돌면 뉴스
        # 커밋 지평으로 잘린 5분봉이 **가격 파일을 덮는다**. eod.py 가 orphan 스캔에서
        # 같은 이유로 같은 가드를 둔다.
        # ⚠️ source_group 은 price_minute 어휘 안의 값으로 준다 — bigkinds 면 그 검증에
        # 먼저 걸려 dataset 가드를 지워도 2 가 나온다.
        self.with_fake_ledger(monkeypatch)
        assert self.run(self.settings(tmp_path), dataset="news_minute",
                        source_group="toss") == 2

    def test_unknown_source_group_is_refused(self, tmp_path, monkeypatch):
        self.with_fake_ledger(monkeypatch)
        assert self.run(self.settings(tmp_path), source_group="nope") == 2

    def test_missing_db_config_is_two(self, tmp_path, monkeypatch):
        # 어휘·설정 결손은 전부 2 — qc-minute-session·plan-minute-session 과 같은 규약.
        # 1 로 내면 "판정은 됐는데 결과가 나쁘다"와 구분이 안 된다.
        self.with_fake_ledger(monkeypatch)
        assert self.run(self.settings(tmp_path, db=False)) == 2

    def test_bad_date_format_is_two(self, tmp_path, monkeypatch):
        self.with_fake_ledger(monkeypatch)
        assert self.run(self.settings(tmp_path), session_date="20260804") == 2

    def test_missing_session_is_one_not_two(self, tmp_path, monkeypatch):
        # 계획이 안 돈 거래일은 재시도로 안 낫는다 = 1. 2 로 내면 SFN·운영자가
        # "재시도하면 될 실패"로 읽는다.
        self.with_fake_ledger(monkeypatch)
        assert self.run(self.settings(tmp_path)) == 1

    def test_non_trading_day_keeps_the_full_output_shape(self, tmp_path, capsys,
                                                         monkeypatch):
        # eod.py 규약: "결과 형상은 정상 경로와 같아야 한다 — 키가 빠지면 소비자가 없는
        # 키를 0 으로 읽어 '위반 없음'으로 오독한다." 휴장일 분기가 그걸 어기기 쉽다.
        import json

        self.with_fake_ledger(monkeypatch)
        assert self.run(self.settings(tmp_path), session_date="2026-08-08") == 0  # 토요일
        out = json.loads(capsys.readouterr().out)
        assert set(out) == {"session_id", "session_date", "trading_day", "phase",
                            "key", "unfilled_settled_days", "contested_days",
                            "settled_day_count"}
        assert out["trading_day"] is False and out["key"] is None
        assert out["settled_day_count"] == 0        # 판정은 휴장일에도 돈다

    def test_hole_scan_covers_days_after_the_target(self, tmp_path, capsys, monkeypatch):
        # 🔴 창 상한을 **대상 날짜**로 묶으면 과거 하루를 손으로 되돌리는 실행 — 이 스텝이
        # 존재하는 이유 — 에서 감시가 가장 좁아진다. 상한은 오늘이어야 한다.
        import json

        db = self.with_fake_ledger(monkeypatch)
        for d in _consecutive(3):
            self.plan(db, d)
        self.run(self.settings(tmp_path), session_date=_consecutive(3)[0])
        out = json.loads(capsys.readouterr().out)
        assert out["unfilled_settled_days"] == _consecutive(3)
        assert out["settled_day_count"] == 3

    def test_hole_report_survives_a_rollup_failure(self, tmp_path, capsys, monkeypatch):
        # 🔴 판정을 rollup 과 같은 try 에 두면 PUT 실패(AccessDenied 등)에서 목록이
        # 계산도 출력도 안 된다. 이 레인엔 exit≠0 백스톱이 없어 그 목록이 유일한 신호다
        # — **실패한 날에 꺼지는 감시는 감시가 아니다.**
        import json

        import data_pipeline.minute.rollup as mod

        db = self.with_fake_ledger(monkeypatch)
        self.plan(db, _consecutive(2)[0])
        self.plan(db, _consecutive(2)[1])
        called = []

        def boom(*args, **kwargs):
            called.append(1)
            raise RuntimeError("S3 AccessDenied")

        monkeypatch.setattr(mod, "rollup_session", boom)
        # 예외는 "판정 자체를 못 함"이라 2 다(재시도 가능). 정당한 거부(None)의 1 과 다르다.
        # 대상 날짜는 **계획된 세션이 있는 날**이어야 한다 — 없으면 rollup 이 예외 전에
        # "세션 없음"으로 정당하게 거부해(1) 이 테스트가 의도한 경로를 안 밟는다.
        assert self.run(self.settings(tmp_path), session_date=_consecutive(2)[1]) == 2
        assert called, "rollup_session 이 불리지 않았다 — 이 테스트는 의도한 경로를 안 밟았다"
        out = json.loads(capsys.readouterr().out)
        assert out["key"] is None                                  # rollup 은 죽었다
        assert out["unfilled_settled_days"] == _consecutive(2)   # 판정은 나왔다
        assert out["settled_day_count"] == 2

    def test_scan_failure_does_not_mask_a_legitimate_refusal(self, tmp_path, monkeypatch):
        # 🔴 판정 스캔의 일시 실패(2)가 rollup 의 정당한 거부(1)를 덮으면 "사람이 봐야
        # 한다"가 "재시도하면 된다"로 뒤집힌다. 두 사실은 독립이다.
        import data_pipeline.minute.rollup as mod

        self.with_fake_ledger(monkeypatch)
        monkeypatch.setattr(mod, "unfilled_settled_days", _scan_boom)
        # 세션 없음(정당한 거부) = 1. 스캔 실패의 2 가 이걸 덮으면 안 된다.
        assert self.run(self.settings(tmp_path)) == 1

    def test_scan_failure_is_two_when_rollup_succeeded(self, tmp_path, monkeypatch):
        # 반대 방향 — rollup 이 성공했으면 남는 사실은 "감시가 안 돌았다" 뿐이고 그건
        # 재시도로 나을 수 있다 = 2.
        import data_pipeline.minute.rollup as mod

        db = self.with_fake_ledger(monkeypatch)
        self.plan(db, SESSION_DATE)
        monkeypatch.setattr(mod, "unfilled_settled_days", _scan_boom)
        monkeypatch.setattr(mod, "rollup_session", lambda *a, **k: DAY_KEY)
        assert self.run(self.settings(tmp_path)) == 2


    def test_scan_failure_on_a_holiday_is_still_two(self, tmp_path, monkeypatch):
        # 스케줄이 MON-FRI 라 공휴일마다 뜬다. "휴장일은 조용히 0" 은 맞지만, 그날
        # S3 가 흔들려 감시가 안 돈 것은 휴장과 **무관한 사실**이다 — 0 으로 접으면
        # "휴장일엔 조용하다"가 "감시 실패도 조용하다"가 된다.
        import data_pipeline.minute.rollup as mod

        self.with_fake_ledger(monkeypatch)
        monkeypatch.setattr(mod, "unfilled_settled_days", _scan_boom)
        assert self.run(self.settings(tmp_path), session_date="2026-08-08") == 2  # 토요일


    def test_log_separates_the_two_prescriptions(self, tmp_path, caplog, monkeypatch):
        # 🔴 결손(재수집)과 충돌(소유자 결정)은 처방이 다르다. JSON 이 둘을 갈라 싣지만
        # 로그도 갈라야 운영자가 목록만 보고 잘못된 처방을 고르지 않는다. 로그는 지금까지
        # 변이가 그냥 통과하던 표면이라 명시로 못박는다.
        import logging

        db = self.with_fake_ledger(monkeypatch)
        self.plan(db, _consecutive(2)[0])
        self.plan(db, _consecutive(2)[1])
        unfilled_day, contested_day = _consecutive(2)
        partition = (f"canonical/market_data/intraday_5m/market=KR"
                     f"/trade_date={contested_day}")
        tmp_path.joinpath(partition).mkdir(parents=True)
        tmp_path.joinpath(partition, "part-toss-backfill.parquet").write_bytes(b"x")
        with caplog.at_level(logging.WARNING):
            self.run(self.settings(tmp_path), session_date=_consecutive(3)[2])
        text = caplog.text
        assert "5분 산출이 없는 날 1건" in text and unfilled_day in text
        assert "다른 writer 가 물고 있어" in text and contested_day in text

    def test_zero_denominator_is_warned(self, tmp_path, caplog, monkeypatch):
        # 분모 0 = "판정 축이 원장과 안 맞는다"는 유일한 신호다. 그 경보 자체가 못박혀야
        # 한다 — 없애도 아무 테스트가 안 죽으면 그건 가드가 아니라 주석이다.
        import logging

        self.with_fake_ledger(monkeypatch)
        with caplog.at_level(logging.WARNING):
            self.run(self.settings(tmp_path))
        assert "구멍 판정 후보가 0일이다" in caplog.text

    def test_boundary_in_the_future_says_the_window_is_empty(
            self, tmp_path, caplog, monkeypatch):
        """판정 창의 하한이 아직 안 온 날이면 그 사실을 로그로 말한다.

        WHY: 창 `[하한, 오늘)` 이 공집합이면 스캔은 구조적으로 0건이다. 소유권을 넘긴
        직후엔 정상이지만(그 구간은 벤더 백필이 채운다), 경계를 옮겨 두고 되돌리길
        잊으면 **감시가 조용히 꺼진 채로 남는다**. 0건이 "구멍 없음"인지 "볼 창이 없음"
        인지 갈리지 않으면 아무도 그 차이를 못 본다(Rule 12).

        ⚠️ 하한은 `WRITER_SINCE` 가 아니라 `scan_lower()` 다 — 경계 **앞**의 예외일
        (`WRITER_OWNED_BEFORE_SINCE`)이 창을 그만큼 넓히기 때문이다. 경계로 물으면
        예외일이 창에 들어와 감시가 도는데도 "비어 있다"고 경고한다(도는 감시를 안 도는
        것으로 읽게 만든다).

        ⚠️ 이 테스트는 **경고 경로만** 검증한다. 시계를 하한 앞으로 되돌려 상황을
        합성하므로 경계가 실제로 어디 있는지는 안 본다 — 그건 아래
        `test_boundary_is_not_left_in_the_future` 와 런타임 WARNING 이 본다.
        """
        import logging

        import data_pipeline.minute.rollup as mod

        self.with_fake_ledger(monkeypatch)
        # 시계를 창 **하한**보다 앞으로 되돌린다 — 창이 공집합인 상태 그 자체다.
        lower = date.fromisoformat(mod.scan_lower())
        monkeypatch.setattr(mod, "_scan_before", lambda: lower - timedelta(days=1))
        with caplog.at_level(logging.WARNING):
            self.run(self.settings(tmp_path))
        assert "판정 창이 비어 있다" in caplog.text


    def test_exception_day_inside_the_window_is_not_called_empty(
            self, tmp_path, caplog, monkeypatch):
        """예외일이 창에 들어와 있으면 "비어 있다"고 말하지 않는다.

        ⚠️ 위 테스트는 시계를 하한 **밖**으로 보내므로 경고 조건이 `WRITER_SINCE` 든
        `scan_lower()` 든 똑같이 발화한다 — 둘을 가르는 구간은 **예외일과 경계 사이**뿐이다
        (오늘이 실제로 거기 있다). 경계로 물으면 감시가 도는데도 안 돈다고 말한다.
        """
        import logging

        import data_pipeline.minute.rollup as mod

        self.with_fake_ledger(monkeypatch)
        between = date.fromisoformat(mod.scan_lower()) + timedelta(days=1)
        assert between.isoformat() < mod.WRITER_SINCE, "경계 **앞**이어야 갈린다"
        monkeypatch.setattr(mod, "_scan_before", lambda: between)
        with caplog.at_level(logging.WARNING):
            self.run(self.settings(tmp_path))
        assert "판정 창이 비어 있다" not in caplog.text


def test_boundary_is_not_left_in_the_future():
    """소유권 경계가 **오래도록 미래에 방치되지 않는다**.

    WHY: 경계를 앞당기면 그 구간이 감시에서 빠진다. 넘겨받은 백필이 채우는 동안은
    정상이지만, 되돌리길 잊으면 감시가 꺼진 채 남는다. 위 테스트는 시계를 합성하므로
    경계의 **실제 위치**는 안 본다 — 그 자리가 여기다.

    상한을 넉넉히(2주) 두는 이유: 경계를 다음 거래일로 미리 옮기는 것은 정당한 운영
    행위다(ALPHA-836 이 금요일에 월요일로 옮겼다). 막으려는 것은 그 이동이 아니라
    **잊힌 이동**이다.
    """
    from datetime import date as _date

    boundary = _date.fromisoformat(WRITER_SINCE)
    assert boundary <= _date.today() + timedelta(days=14), (
        f"WRITER_SINCE({WRITER_SINCE}) 가 오늘보다 2주 넘게 미래다 — 그 구간의 구멍 "
        f"감시가 꺼져 있다. 백필이 그 구간을 채웠으면 경계를 되돌려라"
    )


def _scan_boom(*args, **kwargs):
    raise RuntimeError("S3 throttled")
