"""가격 판정 기록(§33.12) — 실 PostgreSQL 에서만 증명되는 완료 기준.

기록은 '정상 완료한 판정이 실제로 쓴 입력'의 근거다. 그래서 (1) 성공한 판정에는 커밋 당시
유효했던 세대·시도의 기록이 반드시 있고, (2) 무효가 된 시도(소유권 상실·세대 정정·기록 실패)는
기록도 성공도 남기지 않아야 한다. 무발화 경로의 (2)는 이 변경으로 **의도적으로 바뀐** 전이다 —
이전에는 claim·세대 대조 없이 성공했다. 단위 테스트(fake)는 잠금과 롤백을 흉내 내지 못한다.

실제 커널(MinuteConsumer.tick)과 실제 worker 커밋·artifact 읽기를 쓴다. 테스트 전용으로 바꾸는
것은 실패 주입(DB 트리거)과 실행 순서 통제(advisory lock, 서브클래스 훅)뿐이고 판정 규칙은 그대로다.
"""
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")

E, FLAT = "500000", "500001"
N = 5


class ScriptedPrices:
    """window 순번별 (open, close). 대조군 FLAT 은 항상 100."""

    def __init__(self, start, closes):
        self.start, self.closes = start, closes

    def collect(self, request, now):
        from data_pipeline.minute.models import CollectionResult, content_checksum

        i = int((request.window_start - self.start).total_seconds() // 60)
        records = tuple(
            {"unit_id": u, "ts": request.window_start,
             "open": str(self.closes[0] if u == E else 100),
             "high": "200", "low": "1", "close": str(self.closes[i] if u == E else 100),
             "volume": "10"}
            for u in request.unit_ids)
        units = {"received": list(request.unit_ids), "missing": [], "no_trade": [], "invalid": []}
        return CollectionResult(
            status="VALID", expected_count=len(records), succeeded_count=len(records),
            failed_count=0, retry_count=0, artifact_uri="pending://artifact",
            manifest_checksum=content_checksum(units), result_checksum=content_checksum(records),
            watermark_before=None, watermark_after=request.window_end, generation=1,
            stage_timestamps={"collection_started_at": now},
        ), records, units


class Sqs:
    def __init__(self):
        self.queue, self.deleted = [], []

    def receive(self, *, queue_url, max_messages, wait_seconds, visibility_seconds):
        taken, self.queue = self.queue[:max_messages], self.queue[max_messages:]
        return tuple(taken)

    def delete(self, *, queue_url, receipt_handle):
        self.deleted.append(receipt_handle)

    def change_visibility(self, *, queue_url, receipt_handle, seconds):
        pass


@pytest.fixture
def env(tmp_path):
    from data_pipeline.config import DbConfig
    from data_pipeline.db import connect
    from data_pipeline.lake import LocalStorage
    from data_pipeline.minute.commit import MinuteCommitter
    from data_pipeline.minute.jobs import JobLedger
    from data_pipeline.minute.models import KST, WINDOW_SETTLE_SEC, Universe
    from data_pipeline.minute.repository import MinuteLedger
    from data_pipeline.minute.worker import PriceWorker, WorkerConfig

    db = DbConfig(host=os.environ["E2E_PGHOST"], port=int(os.environ["E2E_PGPORT"]),
                  name="edge", user="edge", password="edge", sslmode="disable")
    # 다른 e2e 가 쓰는 거래일(2026-09-07)과 겹치면 롤업의 '세션 둘 이상' 가드가 서로를 막는다
    start = datetime(2026, 10, 5, 9, 0, tzinfo=KST)
    universe = Universe(universe_version="judgment-record", etf_ids=(E, FLAT), constituent_ids=("100000",))
    storage, ledger = LocalStorage(tmp_path), MinuteLedger(db=db)
    sid_holder = {}

    def build(closes):
        sid, _ = ledger.plan_session(
            dataset="price_minute", source_group=f"judgment-{uuid.uuid4().hex[:8]}",
            session_date=start.date(), universe_version=universe.universe_version,
            universe_hash=universe.universe_hash,
            windows=[(start + timedelta(minutes=i), start + timedelta(minutes=i + 1)) for i in range(N)])
        sid_holder["sid"] = sid
        worker = PriceWorker(
            session_id=sid, ledger=ledger, committer=MinuteCommitter(db=db), storage=storage,
            collector=ScriptedPrices(start, closes),
            config=WorkerConfig(worker_id="w1", dataset="price_minute", source="kis", market="KR",
                                session_date="2026-10-05", universe=universe, run_id="t",
                                trigger_schema_version="test", destination="q", is_backfill=False,
                                lease_seconds=60, recovery_budget_per_tick=0,
                                artifact_format="content_v2"))
        # 창은 창 끝 + WINDOW_SETTLE_SEC 에야 due 다(ALPHA-1127) — 마지막 창(끝 start+N)까지
        # 집으려면 그만큼 뒤에서 tick 한다
        for _ in range(N + 2):
            if worker.tick(start + timedelta(minutes=N + 1, seconds=WINDOW_SETTLE_SEC)) != "PROCESSED":
                break
        return sid

    def handler(cls=None, **kw):
        from data_pipeline.minute.price_consumer import PriceTriggerHandler
        return (cls or PriceTriggerHandler)(
            db=db, storage=storage, jobs=JobLedger(db=db), etf_ids=frozenset(universe.etf_ids),
            universe_version=universe.universe_version, universe_hash=universe.universe_hash,
            abs_threshold=Decimal("0.03"), revert_threshold=Decimal("0.01"),
            detection_policy_version="test-policy", destination="q", **kw)

    def deliver(h, i, now, consumer_id="c1"):
        """window i 의 PriceWindowCommitted 를 실제 커널로 처리한다 — 반환은 커널 카운터."""
        from data_pipeline.minute.consumer import ConsumerConfig, ConsumerMessage, MinuteConsumer
        from data_pipeline.minute.relay import build_message_body
        with connect(db) as c:
            row = c.execute(
                """SELECT event_id, event_type, payload FROM dataset_commit_outbox
                   WHERE event_type='PriceWindowCommitted' AND payload->>'session_id'=%s
                     AND (payload->>'window_start')::timestamptz=%s ORDER BY event_id DESC LIMIT 1""",
                (sid_holder["sid"], start + timedelta(minutes=i))).fetchone()
        sqs = Sqs()
        sqs.queue.append(ConsumerMessage(message_id=f"m{i}", receipt_handle=f"r{i}", body=build_message_body(
            {"event_id": row[0], "event_type": row[1], "payload": row[2]})))
        consumer = MinuteConsumer(jobs=JobLedger(db=db), queue=sqs, handler=h, config=ConsumerConfig(
            consumer_id=consumer_id, kind="price", queue_url="q", batch_size=1, wait_seconds=0,
            visibility_seconds=60, heartbeat_seconds=30, max_concurrency=1, lease_seconds=60,
            retry_base_seconds=5, retry_max_seconds=60, max_attempts=5))
        return consumer.tick(now)

    def q(sql, *params):
        with connect(db) as c:
            return c.execute(sql, params).fetchall()

    def records(i):
        return q("""SELECT attempt, generation, summary, anchors_used, tx_anchor, tx_anchor_locked
                    FROM minute_price_judgment WHERE session_id=%s AND window_start=%s ORDER BY attempt""",
                 sid_holder["sid"], start + timedelta(minutes=i))

    def job(i):
        return q("""SELECT status, error_code, attempt_count, generation FROM price_window_job
                    WHERE session_id=%s AND window_start=%s ORDER BY generation DESC LIMIT 1""",
                 sid_holder["sid"], start + timedelta(minutes=i))[0]

    h = type("Env", (), {})()
    h.__dict__.update(db=db, connect=connect, start=start, build=build, handler=handler, deliver=deliver,
                      q=q, records=records, job=job, now=start + timedelta(minutes=N + 2), sid=sid_holder)
    try:
        yield h
    finally:
        sid = sid_holder.get("sid")
        with connect(db) as c:
            c.execute("DROP TRIGGER IF EXISTS judgment_fault ON minute_price_judgment")
            c.execute("DROP FUNCTION IF EXISTS judgment_fault_fn()")
            if sid:
                for sql in (
                        "DELETE FROM minute_price_judgment WHERE session_id=%s",
                        "DELETE FROM minute_price_baseline_set WHERE snapshot_id IN "
                        "(SELECT snapshot_id FROM minute_price_baseline_snapshot WHERE session_id=%s)",
                        "DELETE FROM minute_price_baseline_snapshot WHERE session_id=%s",
                        "DELETE FROM dataset_commit_outbox WHERE payload->>'session_id'=%s",
                        "DELETE FROM minute_price_trigger WHERE session_id=%s",
                        "DELETE FROM minute_trigger_anchor WHERE session_id=%s",
                        "DELETE FROM minute_session_open WHERE session_id=%s",
                        "DELETE FROM price_window_job WHERE session_id=%s",
                        "DELETE FROM minute_window_artifact_commit WHERE session_id=%s",
                        "DELETE FROM minute_ingestion_window WHERE session_id=%s",
                        "DELETE FROM minute_ingestion_session WHERE session_id=%s"):
                    c.execute(sql, (sid,))


def fault(env, body):
    with env.connect(env.db) as c:
        c.execute(f"""CREATE OR REPLACE FUNCTION judgment_fault_fn() RETURNS trigger LANGUAGE plpgsql AS $$
                      BEGIN IF NEW.session_id = '{env.sid["sid"]}' THEN {body} END IF; RETURN NEW; END $$""")
        c.execute("CREATE TRIGGER judgment_fault BEFORE INSERT ON minute_price_judgment "
                  "FOR EACH ROW EXECUTE FUNCTION judgment_fault_fn()")


CLOSES = [100, 101, 104, 100.5, 108]   # W0 시가 폴백 100 · W1 무발화 · W2 발화 · W3 회수 · W4 발화


def test_no_fire_is_recorded_and_succeeds(env):
    env.build(CLOSES)
    assert env.deliver(env.handler(), 1, env.now)["succeeded"] == 1
    [(attempt, gen, summary, used, tx, locked)] = env.records(1)
    assert (attempt, gen) == (1, 1) and summary["fired"] == [] and summary["inserted"] == []
    assert locked is False and used == {}          # 무발화: 비잠금 관측, 앵커 행 없음
    assert env.job(1)[0] == "SUCCEEDED"


def test_record_failure_blocks_no_fire_success_then_recovers(env):
    env.build(CLOSES)
    fault(env, "RAISE EXCEPTION 'injected judgment-record failure';")
    assert env.deliver(env.handler(), 1, env.now)["retried"] == 1     # 기존 커널: UNCLASSIFIED 재시도
    assert env.job(1)[:2] == ("RETRY_WAIT", "UNCLASSIFIED") and env.records(1) == []
    with env.connect(env.db) as c:
        c.execute("DROP TRIGGER judgment_fault ON minute_price_judgment")
    assert env.deliver(env.handler(), 1, env.now + timedelta(minutes=10))["succeeded"] == 1
    assert [r[0] for r in env.records(1)] == [2] and env.job(1)[:3] == ("SUCCEEDED", None, 2)


@pytest.mark.parametrize("change", ["corrected", "in_progress"])
def test_generation_change_blocks_no_fire_success(env, change):
    from data_pipeline.minute.price_consumer import PriceTriggerHandler

    env.build(CLOSES)

    class Interleaved(PriceTriggerHandler):
        def _persist_triggers(self, **kw):   # 판정 계산 뒤·기록 tx 전에 정정이 커밋된다
            sql = ("UPDATE minute_ingestion_window SET generation = generation + 1"
                   if change == "corrected" else "UPDATE minute_ingestion_window SET data_status = 'CLAIMED'")
            env.q(sql + " WHERE session_id=%s AND window_start=%s RETURNING 1",
                  env.sid["sid"], env.start + timedelta(minutes=1))
            return super()._persist_triggers(**kw)

    assert env.deliver(env.handler(Interleaved), 1, env.now)["retried"] == 1
    assert env.job(1)[:2] == ("RETRY_WAIT", "STALE_GENERATION") and env.records(1) == []
    if change == "corrected":   # 재배달은 기존 커널 claim 에서 STALE 격리
        assert env.deliver(env.handler(), 1, env.now + timedelta(minutes=10))["stale"] == 1
        assert env.job(1)[:2] == ("DEAD", "STALE") and env.records(1) == []


def test_superseded_claim_leaves_no_record_and_no_success(env):
    from data_pipeline.minute.jobs import JobLedger
    from data_pipeline.minute.price_consumer import PriceTriggerHandler

    env.build(CLOSES)

    class Superseded(PriceTriggerHandler):
        def _persist_triggers(self, **kw):   # 계산 뒤 lease 가 만료돼 다른 consumer 가 재claim
            [(jid,)] = env.q("SELECT job_id FROM price_window_job WHERE session_id=%s AND window_start=%s",
                             env.sid["sid"], env.start + timedelta(minutes=1))
            assert JobLedger(db=env.db).claim_job(kind="price", job_id=jid, redrive_generation=0,
                                                  worker_id="c2", now=env.now + timedelta(minutes=5),
                                                  lease_seconds=60)["attempt_count"] == 2
            return super()._persist_triggers(**kw)

    assert env.deliver(env.handler(Superseded), 1, env.now)["lost"] == 1
    assert env.records(1) == [] and env.job(1)[:3] == ("CLAIMED", None, 2)


@pytest.mark.parametrize("contender", ["correction", "reclaim"])
def test_contender_waits_until_record_commits(env, contender):
    """검증 뒤·커밋 전에 들어온 세대 정정/재claim 은 잠금에 막혀 **기록 커밋 뒤에** 반영된다.

    그래서 기록은 커밋 당시 유효했던 세대·시도를 가리키고, 그 뒤의 정정은 기록과 구분된다.
    """
    import psycopg

    from data_pipeline.minute.jobs import JobLedger

    env.build(CLOSES)
    fault(env, "PERFORM pg_advisory_xact_lock(7331);")
    holder = psycopg.connect(host=env.db.host, port=env.db.port, dbname="edge", user="edge",
                                          password="edge", autocommit=True)
    holder.execute("SELECT pg_advisory_lock(7331)")
    try:
        _contend(env, holder, contender, JobLedger)
    finally:
        holder.close()   # 실패해도 세션 잠금을 남기지 않는다(다음 사례가 영원히 기다린다)


def _contend(env, holder, contender, JobLedger):
    result = {}
    t = threading.Thread(target=lambda: result.update(env.deliver(env.handler(), 1, env.now)))
    t.start()
    deadline = time.monotonic() + 10
    while not env.q("SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted"):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    [(jid,)] = env.q("SELECT job_id FROM price_window_job WHERE session_id=%s AND window_start=%s",
                     env.sid["sid"], env.start + timedelta(minutes=1))
    done = {}

    def contend():
        if contender == "correction":
            done["r"] = env.q("UPDATE minute_ingestion_window SET generation = 2 WHERE session_id=%s "
                              "AND window_start=%s RETURNING generation", env.sid["sid"],
                              env.start + timedelta(minutes=1))
        else:
            done["r"] = JobLedger(db=env.db).claim_job(kind="price", job_id=jid, redrive_generation=0,
                                                       worker_id="c2", now=env.now + timedelta(minutes=5),
                                                       lease_seconds=60)
    c = threading.Thread(target=contend)
    c.start()
    deadline = time.monotonic() + 10
    while not env.q("SELECT 1 FROM pg_locks WHERE NOT granted AND locktype <> 'advisory'"):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert "r" not in done                          # 판정 tx 의 job·window 잠금에 막혀 있다
    holder.execute("SELECT pg_advisory_unlock(7331)")
    t.join(10), c.join(10)
    [(attempt, gen, *_)] = env.records(1)
    assert (attempt, gen) == (1, 1)                 # 커밋 당시 유효했던 시도·세대
    if contender == "correction":
        assert result["succeeded"] == 1 and env.q(
            "SELECT generation FROM minute_ingestion_window WHERE session_id=%s AND window_start=%s",
            env.sid["sid"], env.start + timedelta(minutes=1)) == [(2,)]   # 정정은 기록 뒤에 반영
    elif result.get("lost"):   # 커밋 직후 대기하던 재claim 이 먼저 잡았다 — 시도 1은 성공하지 않는다
        assert done["r"]["attempt_count"] == 2 and env.job(1)[:3] == ("CLAIMED", None, 2)
    else:                      # 시도 1의 succeed_job 이 먼저 잡았다 — 재claim 은 대상이 없어 빈손
        assert result["succeeded"] == 1 and done["r"] is None and env.job(1)[0] == "SUCCEEDED"


def test_crash_after_commit_before_succeeded_retries_without_duplicate(env):
    env.build(CLOSES)
    assert env.deliver(env.handler(), 0, env.now)["succeeded"] == 1
    assert env.deliver(env.handler(), 1, env.now)["succeeded"] == 1
    real = env.handler()

    def dies_after_commit(**kw):
        real(**kw)                                  # 도메인 + 기록 커밋
        raise SystemExit("process death before succeed_job")   # 커널 except Exception 밖

    with pytest.raises(SystemExit):
        env.deliver(dies_after_commit, 2, env.now)
    assert env.job(2)[0] == "CLAIMED"
    assert env.deliver(env.handler(), 2, env.now + timedelta(minutes=10))["succeeded"] == 1
    recs = env.records(2)
    assert [(r[0], r[2]["inserted"]) for r in recs] == [(1, [E]), (2, [])]   # 시도별 기록
    assert recs[1][3][E][1].startswith("2026-10-05T00:02")   # 시도 2는 자기 window 앵커를 읽었다
    assert env.q("SELECT count(*) FROM minute_price_trigger WHERE session_id=%s", env.sid["sid"]) == [(1,)]
    assert env.job(2)[:3] == ("SUCCEEDED", None, 2)


@pytest.mark.parametrize("order", ["in_order", "later_fire_first"])
def test_revert_path_record_matches_read_anchor_and_result(env, order):
    env.build(CLOSES)
    for i in (0, 1, 2):
        assert env.deliver(env.handler(), i, env.now)["succeeded"] == 1
    if order == "later_fire_first":
        assert env.deliver(env.handler(), 4, env.now)["succeeded"] == 1      # 앵커가 W4 로 전진
    [(price, window)] = env.q("SELECT anchor_price, anchor_window FROM minute_trigger_anchor "
                              "WHERE session_id=%s AND entity_id=%s", env.sid["sid"], E)   # W3 이 읽을 앵커
    assert env.deliver(env.handler(), 3, env.now)["succeeded"] == 1
    [(_, _, summary, used, tx, locked)] = env.records(3)
    assert Decimal(used[E][0]) == price and used[E][1] == window.isoformat()   # 실제 읽은 앵커
    assert locked is True and tx[E] == window.isoformat()                       # 잠금 뒤 관측
    assert summary["revert_candidates"] == [E]
    reverted_events = env.q("""SELECT count(*) FROM dataset_commit_outbox WHERE event_type='ExposureReverted'
                               AND payload->>'session_id'=%s""", env.sid["sid"])[0][0]
    if order == "in_order":
        assert summary["reverted"] == [E] and reverted_events == 1
    else:   # 앵커가 더 늦은 window 에서 왔다 — 조건부 UPDATE 0행(회수 무효)
        assert summary["reverted"] == [] and reverted_events == 0


def _claim_and_payload(env, i):
    from data_pipeline.minute.jobs import JobLedger
    [(payload,)] = env.q("""SELECT payload FROM dataset_commit_outbox WHERE event_type='PriceWindowCommitted'
                            AND payload->>'session_id'=%s AND (payload->>'window_start')::timestamptz=%s""",
                         env.sid["sid"], env.start + timedelta(minutes=i))
    claim = JobLedger(db=env.db).claim_job(kind="price", job_id=payload["job_id"], redrive_generation=0,
                                           worker_id="direct", now=env.now, lease_seconds=60)
    return payload, claim["attempt_count"]


def test_same_attempt_same_judgment_keeps_first_record(env):
    """방어적(직접 호출): 커널은 한 claim 에서 handler 를 두 번 부르지 않는다. 같은 시도 키로 **같은**
    판정이 다시 오면 먼저 커밋된 기록을 유지하고 성공한다(judged_at 같은 관측값은 비교하지 않는다)."""
    env.build(CLOSES)
    assert env.deliver(env.handler(), 0, env.now)["succeeded"] == 1
    payload, attempt = _claim_and_payload(env, 1)
    h = env.handler()
    first = h(job_id=payload["job_id"], payload=payload, attempt=attempt, redrive_generation=0)
    [(judged_at,)] = env.q("SELECT judged_at FROM minute_price_judgment WHERE job_id=%s", payload["job_id"])
    assert h(job_id=payload["job_id"], payload=payload, attempt=attempt, redrive_generation=0) == first
    assert env.q("SELECT judged_at FROM minute_price_judgment WHERE job_id=%s", payload["job_id"]) == [(judged_at,)]


def test_same_attempt_different_judgment_conflicts_and_rolls_back(env):
    """방어적(직접 호출): 같은 시도 키에 **다른** 판정(읽은 앵커·결과가 다름)이 오면 기존 기록을 덮지
    않고 JUDGMENT_RECORD_CONFLICT(재시도 분류)로 실패한다 — 같은 tx 의 트리거·앵커·outbox 도 롤백된다.
    앵커 행 조작은 상태를 바꾸는 테스트 준비일 뿐 판정 규칙은 그대로다."""
    from data_pipeline.minute.consumer import TransientJobError

    env.build(CLOSES)
    for i in (0, 1):
        assert env.deliver(env.handler(), i, env.now)["succeeded"] == 1
    payload, attempt = _claim_and_payload(env, 2)
    env.q("""INSERT INTO minute_trigger_anchor (session_id, entity_id, anchor_price, anchor_window)
             VALUES (%s, %s, 103.5, %s) RETURNING 1""", env.sid["sid"], E, env.start + timedelta(minutes=1))
    env.handler()(job_id=payload["job_id"], payload=payload, attempt=attempt, redrive_generation=0)
    [(before,)] = env.q("SELECT summary FROM minute_price_judgment WHERE job_id=%s", payload["job_id"])
    assert before["fired"] == []                   # 104 vs 앵커 103.5 → 무발화로 기록
    env.q("DELETE FROM minute_trigger_anchor WHERE session_id=%s RETURNING 1", env.sid["sid"])
    with pytest.raises(TransientJobError) as err:  # 앵커가 없으면 같은 입력으로 발화 → 다른 판정
        env.handler()(job_id=payload["job_id"], payload=payload, attempt=attempt, redrive_generation=0)
    assert err.value.code == "JUDGMENT_RECORD_CONFLICT"
    assert env.q("SELECT summary FROM minute_price_judgment WHERE job_id=%s", payload["job_id"]) == [(before,)]
    assert env.q("SELECT count(*) FROM minute_price_trigger WHERE session_id=%s", env.sid["sid"]) == [(0,)]
    assert env.q("SELECT count(*) FROM minute_trigger_anchor WHERE session_id=%s", env.sid["sid"]) == [(0,)]
    assert env.q("""SELECT count(*) FROM dataset_commit_outbox WHERE event_type='PriceTriggerFired'
                    AND payload->>'session_id'=%s""", env.sid["sid"]) == [(0,)]
