"""Kafka transport 의 ack 의미 대응 — 가격 창은 순서가 바뀌면 상태가 틀린다.

SQS 는 안 지운 메시지만 나중에 다시 오고 뒤 메시지는 계속 처리된다. Kafka 는 뒤 offset 을
commit 하면 앞 메시지도 끝난 것이 된다. 그래서 판정 보류·재시도는 **같은 offset 에서
파티션을 멈춰야** 한다 — 이게 깨지면 실패한 창을 건너뛰고 뒤 창이 앵커를 움직인다.
"""

import pytest

# importorskip 을 쓰지 않는다 — 의존성이 빠진 환경에서 조용히 skip 되면 이 계약이 CI 에서
# 한 번도 검증되지 않는다. 없으면 수집 단계에서 실패해야 한다(CI 는 kafka 그룹을 설치한다).
from data_pipeline.minute.kafka_transport import (
    KafkaPublisher,
    KafkaQueue,
    _partition_key,
    parse_kafka_url,
)
from confluent_kafka import KafkaException
from data_pipeline.minute.relay import OutboxMessage

URL = "kafka://broker:9092/price-analysis-realtime?group=price-live"


class _Message:
    def __init__(self, offset):
        self._offset = offset

    def topic(self):
        return "price-analysis-realtime"

    def partition(self):
        return 0

    def offset(self):
        return self._offset

    def value(self):
        return b'{"x": 1}'

    def error(self):
        return None


class _FakeConsumer:
    """단일 파티션 로그 — position·pause 만 흉내 낸다."""

    def __init__(self, size):
        self.size, self.position, self.paused = size, 0, False
        self.commits, self.timeouts = [], []
        self.fail_commit = None

    def consume(self, num_messages, timeout):
        self.timeouts.append(timeout)
        if self.paused or self.position >= self.size:
            return []
        message = _Message(self.position)
        self.position += 1
        return [message]

    def commit(self, offsets, asynchronous):
        assert asynchronous is False   # commit 전에 다음 메시지로 가면 안 된다
        if self.fail_commit == "raise":
            raise RuntimeError("REBALANCE_IN_PROGRESS")
        if self.fail_commit == "partition":
            # 동기 commit 의 파티션별 실패는 예외가 아니라 반환값의 error 로 온다
            return [_Committed(error="UNKNOWN_MEMBER_ID")]
        self.commits.append(offsets[0].offset)
        return [_Committed(error=None)]

    def rebalance(self):
        """재할당 — committed offset 부터 다시 읽는다. **앱이 건 pause 는 유지된다**
        (librdkafka 는 할당 해제·재할당에서 라이브러리 pause 만 푼다)."""
        self.position = self.commits[-1] if self.commits else 0

    def pause(self, _tps):
        self.paused = True

    def resume(self, _tps):
        self.paused = False

    def seek(self, tp):
        self.position = tp.offset


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _queue(size=3):
    clock, fake = _Clock(), _FakeConsumer(size)
    return KafkaQueue(URL, consumer=fake, clock=clock), fake, clock


def _receive(queue, visibility=30):
    return queue.receive(queue_url=URL, max_messages=1, wait_seconds=20,
                         visibility_seconds=visibility)


def test_delete_commits_next_offset_so_restart_resumes_after_it():
    queue, fake, _ = _queue()
    (message,) = _receive(queue)
    queue.delete(queue_url=URL, receipt_handle=message.receipt_handle)
    assert fake.commits == [1]
    assert _receive(queue)[0].message_id.endswith(":1")


def test_undecided_message_blocks_later_windows_until_visibility_expires():
    # poison·orphan·contended 는 kernel 이 delete 도 visibility 도 안 부른다
    queue, fake, clock = _queue()
    (first,) = _receive(queue, visibility=5)
    assert _receive(queue) == ()               # offset 1 로 넘어가지 않는다
    assert fake.timeouts[-1] == 5              # long poll(20)보다 남은 멈춤이 짧으면 그만큼만
    clock.now = 5
    (again,) = _receive(queue)
    assert again.message_id == first.message_id
    assert fake.commits == []


def test_retry_visibility_redelivers_same_offset_after_db_backoff():
    queue, _, clock = _queue()
    (message,) = _receive(queue)
    queue.change_visibility(queue_url=URL, receipt_handle=message.receipt_handle, seconds=5)
    clock.now = 4
    assert _receive(queue) == ()
    clock.now = 5
    assert _receive(queue)[0].message_id == message.message_id


def test_heartbeat_then_success_does_not_rewind():
    # heartbeat 가 visibility 를 민 뒤 성공하면 delete 가 이긴다 — 되감으면 중복 판정
    queue, fake, _ = _queue()
    (message,) = _receive(queue)
    queue.change_visibility(queue_url=URL, receipt_handle=message.receipt_handle, seconds=300)
    queue.delete(queue_url=URL, receipt_handle=message.receipt_handle)
    assert _receive(queue)[0].message_id.endswith(":1")
    assert fake.commits == [1]


def test_batch_receive_is_refused():
    queue, _, _ = _queue()
    with pytest.raises(ValueError, match="batch_size=1"):
        queue.receive(queue_url=URL, max_messages=10, wait_seconds=20, visibility_seconds=30)


def test_url_and_partition_key():
    assert parse_kafka_url(URL) == ("broker:9092", "price-analysis-realtime", "price-live")
    with pytest.raises(ValueError):
        parse_kafka_url("https://sqs.ap-northeast-2.amazonaws.com/1/q")
    assert _partition_key('{"payload": {"session_id": "s1"}}') == b"s1"
    assert _partition_key('{"payload": {"article_id": "a"}}') is None
    # 형상 밖 payload 한 건이 publish_batch 를 통째로 죽이면 destination 전체가 멈춘다
    assert _partition_key('{"payload": ["x"]}') is None


def test_revoke_while_held_redelivers_from_committed_offset():
    # 재시도 대기 중 파티션을 잃었다가 되찾아도 미완료 창을 건너뛰지 않는다
    queue, fake, _ = _queue()
    (first,) = _receive(queue)
    queue.delete(queue_url=URL, receipt_handle=first.receipt_handle)
    (pending,) = _receive(queue)                  # offset 1 — 판정 보류로 끝난다
    assert _receive(queue) == ()                  # 멈춤
    queue._on_revoke(None, [_TopicPartition()])
    fake.rebalance()
    assert _receive(queue)[0].message_id == pending.message_id
    assert fake.commits == [1]


def test_commit_failure_never_moves_committed_offset_past_unfinished():
    # commit 실패는 kernel 이 로그만 남긴다(_delete). 그 뒤 판정 보류 메시지가 있어도
    # committed offset 은 그 메시지를 넘지 않는다 — 재기동하면 실패한 commit 분부터 다시 온다
    queue, fake, _ = _queue()
    (first,) = _receive(queue)
    fake.fail_commit = "raise"
    with pytest.raises(RuntimeError):
        queue.delete(queue_url=URL, receipt_handle=first.receipt_handle)
    fake.fail_commit = None
    (second,) = _receive(queue)                   # 위치는 이미 지났다(DB 는 terminal)
    assert second.message_id.endswith(":1")
    assert _receive(queue) == ()                  # 두 번째는 판정 보류 → 멈춤
    assert fake.commits == []
    queue._on_revoke(None, [_TopicPartition()])   # 재할당(revoke 콜백 → 재할당 순서)
    fake.rebalance()
    assert _receive(queue)[0].message_id == first.message_id


class _TopicPartition:
    topic, partition = "price-analysis-realtime", 0


class _Committed:
    def __init__(self, error):
        self.error = error


def test_partition_commit_error_is_raised_not_recorded_as_success():
    # 실패한 commit 을 성공으로 넘기면 관측(committed)과 실제 위치가 어긋난다(Rule 12)
    queue, fake, _ = _queue()
    (message,) = _receive(queue)
    fake.fail_commit = "partition"
    with pytest.raises(KafkaException):
        queue.delete(queue_url=URL, receipt_handle=message.receipt_handle)


class _Producer:
    """발행 확인이 늦게 오는 producer — flush 시간 안에 못 받은 콜백을 다음 flush 로 미룬다."""

    def __init__(self):
        self.pending, self.late = [], True

    def produce(self, topic, key, value, on_delivery):
        self.pending.append(on_delivery)

    def flush(self, timeout):
        if self.late:
            self.late = False
            return len(self.pending)
        for callback in self.pending:
            callback(None, None)
        self.pending = []
        return 0


def test_late_confirmation_is_not_counted_for_a_later_batch():
    # 확인이 늦은 사건은 성공으로도 실패로도 보고되지 않아 Relay 가 재발행한다(중복은 소비자가
    # job 상태로 흡수 — terminal). 늦게 도착한 확인이 **다음 호출의 성공으로 잘못 집계되면**
    # 그 호출에서 실제로 안 나간 사건이 PUBLISHED 로 확정돼 유실된다
    publisher = KafkaPublisher(producer=_Producer())
    url = "kafka://broker:9092/price-analysis-realtime"
    body = '{"event_id":"e","event_type":"PriceWindowCommitted","payload":{"session_id":"s"}}'
    assert publisher.publish_batch(url, (OutboxMessage("e1", body),)) == (frozenset(), ())
    published, failures = publisher.publish_batch(url, (OutboxMessage("e2", body),))
    assert published == {"e2"} and failures == ()
