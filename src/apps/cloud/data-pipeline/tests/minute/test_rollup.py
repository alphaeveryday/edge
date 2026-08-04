"""5분봉(intraday_5m) 롤업 테스트 (ALPHA-750).

의도: 이 파생이 틀리면 분석엔진이 틀린 봉으로 설명을 계산한다 — 조용히. 그래서
①각 필드가 **어느 1분봉**에서 와야 하는지(window 축 — record ts 로 정렬하는 회귀를
ts 역순 입력으로 반증), ②원장 확정 세대만 읽는지(S3 orphan 세대 배제), ③결손 분
부분 집계, ④버킷 미완 시 산출 없음 + 안 닫힌 이웃 버킷 배제, ⑤늦은 recovery 커밋
의 재작성과 빈 정정의 재작성(폐기 가격 잔존 금지), ⑥fmp 백필 파티션 보호,
⑦롤업 예외가 1분 커밋(정본)을 깨지 않음, ⑧산출 스키마가 기존 fmp 파일 실측과
동일함을 고정한다.
"""

from __future__ import annotations

import io
import sys
from datetime import date, datetime
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
from data_pipeline.minute.rollup import maybe_rollup
from data_pipeline.minute.worker import PriceWorker, WorkerConfig

# fmp 백필(~2026-07-31) 뒤의 첫 writer 날짜 — WRITER_SINCE 가드 안쪽이다
SESSION_DAY = date(2026, 8, 4)
SESSION_DATE = "2026-08-04"
DAY_KEY = canonical_intraday_5m_key("KR", SESSION_DATE)
UNIVERSE = Universe(
    universe_version="univ-test-v1",
    etf_ids=("500000",),
    constituent_ids=("100000", "100001"),
)


def w(hhmm: str) -> datetime:
    return datetime(2026, 8, 4, int(hhmm[:2]), int(hhmm[2:]), tzinfo=KST)


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
        planned = plan_session_windows(SESSION_DAY, universe=UNIVERSE)
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
        return maybe_rollup(
            self.storage, self.ledger, session_id=self.session_id, market="KR",
            session_date=session_date, universe=UNIVERSE, window_start=w(hhmm),
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
        planned = plan_session_windows(SESSION_DAY, universe=UNIVERSE)
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
                destination="price-analysis-realtime",
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
        self.run_until_idle(worker, datetime(2026, 8, 4, 9, 10, tzinfo=KST))
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
        now = datetime(2026, 8, 4, 9, 10, tzinfo=KST)
        assert worker.tick(now) == "PROCESSED"  # WINDOW_FAILED 가 아니다
        assert {win["data_status"] for win in db.windows.values()} == {"VALID"}
