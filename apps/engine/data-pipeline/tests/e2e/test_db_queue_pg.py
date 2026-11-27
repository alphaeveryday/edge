"""DB 큐(`DbQueue`)의 SQL 을 실 PostgreSQL(Flyway 로 적용된 실제 마이그레이션)에서 고정한다.

단위 테스트(tests/minute/test_db_queue.py)는 SQL 매칭 fake 위에서 돈다 — fake 는 절이
**문자열로 있는지**만 보므로, 조건을 `(claimed_by = %s OR TRUE)` 로 망가뜨리거나 깊이 집계에
`claimed_by IS NULL` 을 넣어도 통과한다. 여기서 지키는 것은 그 SQL 이 실제로 하는 일이다:
- 여러 소비자가 동시에 집어도 한 event 는 한 번만 배달된다 — 겹치면 같은 기사에 LLM 을 두 번 부른다.
- 배달 횟수는 claim 때 오른다 — 실패 기록 때만 오르면 기록 전에 죽는 반복이 상한에 안 잡힌다.
- 남의 claim 은 닫지도 미루지도 못한다 — 닫으면 지금 쥔 쪽의 배달이 사라진다.
- 게이트 깊이는 집힌 행도 센다 — 빼면 처리 중인 일감을 두고 소비자를 내린다.
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")

VISIBILITY = 300


def _db():
    from data_pipeline.config import DbConfig

    return DbConfig(host=os.environ["E2E_PGHOST"], port=int(os.environ["E2E_PGPORT"]),
                    name="edge", user="edge", password="edge", sslmode="disable")


def _conn():
    import psycopg

    db = _db()
    return psycopg.connect(host=db.host, port=db.port, dbname=db.name, user=db.user,
                           password=db.password, autocommit=True)


@pytest.fixture
def lane():
    """테스트마다 새 destination — 다른 테스트·재실행의 행과 섞이지 않는다."""
    from data_pipeline.minute.jobs import JobLedger

    destination = f"t-{uuid.uuid4().hex[:12]}"
    ledger = JobLedger(db=_db())

    def insert(count):
        ids = [f"{destination}:{index:03d}" for index in range(count)]
        for event_id in ids:
            ledger.insert_outbox_event(
                event_id=event_id, event_type="NewsExtractionRequested", destination=destination,
                aggregate_id=event_id, generation=1, payload={"n": event_id},
            )
        return ids

    yield destination, ledger, insert
    with _conn() as c:
        c.execute("DELETE FROM dataset_commit_outbox WHERE destination = %s", (destination,))


def _queue(ledger, consumer_id, *, ahead=0):
    from data_pipeline.minute.consumer import DbQueue

    return DbQueue(jobs=ledger, consumer_id=consumer_id, sleep=lambda _: None,
                   clock=lambda: datetime.now(timezone.utc) + timedelta(seconds=ahead))


def _receive(queue, destination, limit=3):
    return queue.receive(queue_url=destination, max_messages=limit, wait_seconds=0,
                         visibility_seconds=VISIBILITY)


def _row(event_id):
    with _conn() as c:
        return c.execute(
            "SELECT status, attempt_count, claimed_by IS NOT NULL FROM dataset_commit_outbox "
            "WHERE event_id = %s", (event_id,)).fetchone()


def test_concurrent_consumers_never_receive_the_same_event(lane):
    destination, ledger, insert = lane
    ids = insert(40)

    def drain(worker):
        queue, seen = _queue(ledger, f"c{worker}"), []
        while messages := _receive(queue, destination):
            seen += [message.message_id for message in messages]
        return seen

    with ThreadPoolExecutor(8) as pool:
        delivered = [event for seen in pool.map(drain, range(8)) for event in seen]
    assert sorted(delivered) == ids


def test_receive_count_rises_when_claimed(lane):
    destination, ledger, insert = lane
    (event_id,) = insert(1)
    assert len(_receive(_queue(ledger, "c1"), destination)) == 1
    assert _row(event_id) == ("NEW", 1, True)
    # 아무것도 기록하지 않고 죽은 소비자 — visibility 가 지나 다시 집히면 또 센다
    assert len(_receive(_queue(ledger, "c2", ahead=VISIBILITY + 1), destination)) == 1
    assert _row(event_id) == ("NEW", 2, True)


def test_only_the_current_holder_can_close_or_defer(lane):
    destination, ledger, insert = lane
    (event_id,) = insert(1)
    stale_queue = _queue(ledger, "c1")
    stale = _receive(stale_queue, destination)[0]
    current_queue = _queue(ledger, "c2", ahead=VISIBILITY + 1)
    current = _receive(current_queue, destination)[0]

    stale_queue.change_visibility(queue_url=destination, receipt_handle=stale.receipt_handle, seconds=0)
    assert _receive(_queue(ledger, "c3", ahead=VISIBILITY + 2), destination) == (), \
        "옛 배달의 반납이 지금 쥔 쪽의 claim 을 풀었다"
    stale_queue.delete(queue_url=destination, receipt_handle=stale.receipt_handle)
    assert _row(event_id)[0] == "NEW", "옛 배달의 ack 가 지금 쥔 쪽의 event 를 닫았다"

    current_queue.delete(queue_url=destination, receipt_handle=current.receipt_handle)
    assert _row(event_id) == ("PUBLISHED", 2, False)


def test_released_event_is_visible_again_at_once(lane):
    """SIGTERM 때 받아 둔 메시지를 visibility 0 으로 돌려준다 — 안 풀리면 다음 소비자가 5분을 기다린다."""
    destination, ledger, insert = lane
    insert(1)
    queue = _queue(ledger, "c1")
    message = _receive(queue, destination)[0]
    queue.change_visibility(queue_url=destination, receipt_handle=message.receipt_handle, seconds=0)
    assert len(_receive(_queue(ledger, "c2", ahead=1), destination)) == 1


def test_gate_depth_counts_claimed_events_until_they_are_closed(lane):
    destination, ledger, insert = lane
    insert(3)
    queue = _queue(ledger, "c1")
    held = _receive(queue, destination, limit=2)
    assert ledger.count_new_outbox(destinations=[destination]) == {destination: 3}, \
        "집힌 행을 빼고 세면 처리 중인 일감을 두고 소비자를 내린다"
    queue.delete(queue_url=destination, receipt_handle=held[0].receipt_handle)
    assert ledger.count_new_outbox(destinations=[destination, "t-none"]) == {destination: 2}


def test_pending_local_analyses_are_the_unfinished_local_requests():
    """세션 종료 게이트가 세는 것 = 분석 실행기(`cloud.local`)가 아직 안 끝낸 요청. AWS 워크플로
    로 접수된 행(ARN)이나 이미 끝난 행을 세면 게이트가 영영 안 비거나 일찍 빈다."""
    from data_pipeline.minute.jobs import JobLedger

    ledger = JobLedger(db=_db())
    ids = [uuid.uuid4().hex for _ in range(3)]
    before = ledger.count_pending_local_analyses()
    with _conn() as c:
        try:
            for identity, arn, accepted in (
                (ids[0], f"local:{ids[0]}", None),
                (ids[1], f"local:{ids[1]}", datetime.now(timezone.utc)),
                (ids[2], f"arn:aws:states:test:{ids[2]}", None),
            ):
                c.execute(
                    "INSERT INTO analysis_execution_requests (analysis_id, input_json, execution_arn, "
                    "accepted_at) VALUES (%s, '{}', %s, %s)", (identity, arn, accepted))
            assert ledger.count_pending_local_analyses() == before + 1
        finally:
            c.execute("DELETE FROM analysis_execution_requests WHERE analysis_id = ANY(%s)", (ids,))


def test_stop_success_row_is_the_days_1610_slot_and_rerun_updates_it():
    """OCI 감시는 (`minute_session_stop`, 그날 16:10 KST) 행으로 VM-2 를 내린다 — 레인 이름이나
    슬롯 시각이 한 글자라도 다르면 성공해도 "행 없음"으로 알림이 간다. 같은 날 다시 돌리면
    행이 늘지 않고 끝난 시각만 바뀐다."""
    from datetime import date
    from types import SimpleNamespace

    from data_pipeline.minute import session_ops

    with _conn() as c:
        if c.execute("SELECT to_regclass('batch_lane_success')").fetchone()[0] is None:
            # 표는 OCI 배치 cron 변경(V202611270230)이 만든다 — 그 마이그레이션이 dev 에 들어온 뒤 돈다.
            pytest.skip("batch_lane_success 표 없음 — V202611270230 머지 뒤 실행된다")
    settings, day = SimpleNamespace(db=_db()), date(2001, 1, 2)   # 다른 행과 겹치지 않는 날
    where = "lane = 'minute_session_stop' AND slot_at = '2001-01-02T16:10:00+09:00'"
    try:
        session_ops._record_stop_success(settings, day)
        with _conn() as c:
            (first,) = c.execute(f"SELECT finished_at FROM batch_lane_success WHERE {where}").fetchall()
        session_ops._record_stop_success(settings, day)
        with _conn() as c:
            (second,) = c.execute(f"SELECT finished_at FROM batch_lane_success WHERE {where}").fetchall()
        assert second[0] > first[0]
    finally:
        with _conn() as c:
            c.execute(f"DELETE FROM batch_lane_success WHERE {where}")
