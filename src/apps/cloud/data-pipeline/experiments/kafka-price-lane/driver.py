"""실제 Worker commit 경로 구동기 — 대체하는 것은 **수집기 하나**(스크립트 가격)다.

  python /lab/driver.py plan            세션·창 14개 계획, 전일 종가·universe 시드
  python /lab/driver.py commit <seq>    그 창을 실제 PriceWorker 로 수집·커밋
                                        (artifact 저장 → window·job·outbox 한 트랜잭션)

Relay·소비자는 별도 프로세스(compose 서비스)다. 가격 표는 lab.py 의 PRICES 가 정본이다.
"""
import json
import sys
from datetime import date, datetime, timedelta

from data_pipeline.config import DbConfig
from data_pipeline.db import connect
from data_pipeline.lake import LocalStorage
from data_pipeline.minute.commit import MinuteCommitter
from data_pipeline.minute.models import KST, CollectionResult, Universe, content_checksum
from data_pipeline.minute.repository import MinuteLedger
from data_pipeline.minute.worker import PriceWorker, WorkerConfig

PRICES = json.load(open("/lab/prices.json"))
ETF, CONSTITUENT = "500000", "100000"
SESSION_DATE = date(2026, 9, 25)
START = datetime(2026, 9, 25, 9, 0, tzinfo=KST)
UNIVERSE = Universe(universe_version="kafka-lab", etf_ids=(ETF,), constituent_ids=(CONSTITUENT,))
DB = DbConfig(host="postgres", port=5432, name="edge", user="edge", password="edge", sslmode="disable")


class ScriptedCollector:
    def __init__(self, close):
        self.close = close

    def collect(self, request, now):
        records = tuple({"unit_id": u, "ts": request.window_start, "open": "100", "high": "106",
                         "low": "99", "close": self.close if u == ETF else "100", "volume": "10"}
                        for u in request.unit_ids)
        units = {"received": list(request.unit_ids), "missing": [], "no_trade": [], "invalid": []}
        return CollectionResult(
            status="VALID", expected_count=len(records), succeeded_count=len(records),
            failed_count=0, retry_count=0, artifact_uri="pending://artifact",
            manifest_checksum=content_checksum(units), result_checksum=content_checksum(records),
            watermark_before=None, watermark_after=request.window_end, generation=1,
            stage_timestamps={"collection_started_at": now},
        ), records, units


def session_id():
    sid, _ = MinuteLedger(db=DB).plan_session(
        dataset="price_minute", source_group="kafka-lab", session_date=SESSION_DATE,
        universe_version=UNIVERSE.universe_version, universe_hash=UNIVERSE.universe_hash,
        windows=[(START + timedelta(minutes=i), START + timedelta(minutes=i + 1))
                 for i in range(len(PRICES))],
    )
    return sid


def plan():
    with connect(DB) as c:
        c.execute("INSERT INTO entity (entity_id, entity_type, display_name, status)"
                  " VALUES ('lab-500000', 'INSTRUMENT', 'kafka lab ETF', 'ACTIVE') ON CONFLICT DO NOTHING")
        c.execute("INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type, currency_code)"
                  " VALUES ('lab-500000', 'XKRX', %s, 'ETF', 'KRW') ON CONFLICT DO NOTHING", (ETF,))
        c.execute("INSERT INTO price_daily (instrument_id, trade_date, close_price, available_at, data_version)"
                  " VALUES ('lab-500000', '2026-09-24', 100, now(), 'kafka-lab') ON CONFLICT DO NOTHING")
    with open("/run-data/universe.json", "w") as f:
        f.write(UNIVERSE.model_dump_json())
    print(session_id())


def commit(seq):
    sid = session_id()
    now = START + timedelta(minutes=seq + 1, seconds=1)
    config = WorkerConfig(
        worker_id=f"lab-driver-{seq}", dataset="price_minute", source="kis", market="KR",
        session_date=SESSION_DATE.isoformat(), universe=UNIVERSE, run_id="kafka-lab",
        trigger_schema_version="kafka-lab", destination="price-analysis-realtime",
        is_backfill=False, lease_seconds=1, session_lease_seconds=1,
        recovery_budget_per_tick=0, artifact_format="content_v2",
    )
    worker = PriceWorker(session_id=sid, ledger=MinuteLedger(db=DB), committer=MinuteCommitter(db=DB),
                         storage=LocalStorage("/run-data/lake"),
                         collector=ScriptedCollector(str(PRICES[seq])), config=config)
    state = worker.tick(now)
    if state != "PROCESSED":
        raise SystemExit(f"commit {seq}: {state}")
    print(state)


if __name__ == "__main__":
    plan() if sys.argv[1] == "plan" else commit(int(sys.argv[2]))
