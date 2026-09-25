"""Kafka transport — `MinuteConsumer` kernel 과 `OutboxRelay` 의 큐 경계에 끼우는 로컬 개발판.

kernel 은 `receive/delete/change_visibility`, Relay 는 `publish_batch` 만 본다. 이 모듈은
그 두 경계를 Kafka 로 옮긴다 — kernel·handler·Relay 본문은 그대로다. 선택은 URL scheme
(`kafka://<bootstrap>/<topic>?group=<id>`)이고, SQS URL 이면 기존 경로다. 클라우드
이미지에는 `confluent-kafka` 가 없다(선택 설치 그룹 `kafka`, 지연 import).

⚠️ **SQS 와 ack 의미가 다르다.** SQS 는 메시지별로 지우고, 안 지운 메시지는 visibility 가
끝나면 다시 오며 그동안 **다른 메시지는 계속 처리된다.** Kafka offset 은 파티션 단위
누적값이라 뒤 메시지를 commit 하면 앞 메시지도 끝난 것이 된다. 그래서:

  - `delete`            → 그 offset+1 을 동기 commit
  - `change_visibility` → 그 offset 으로 되감고 파티션을 N초 멈춘다(N초 뒤 같은 메시지)
  - 둘 다 안 부름(poison·orphan·contended 등 판정 보류) → SQS 와 같게 수신 시
    visibility 만큼 멈춘 뒤 같은 메시지를 다시 준다

한 번에 한 건만 받는다(`max_messages=1`). 여러 건을 받아 일부만 지우면 누적 offset 이
앞의 미완 메시지를 건너뛴다.

결과: 재시도·판정 보류 중인 창 뒤의 창은 기다린다 — 가격 상태는 순서가 바뀌면 틀리므로
가용성보다 순서를 택했다. ⚠️ **예외는 DEAD 다.** 재시도 예산이 소진되면 kernel 이 DEAD 를
기록하고 delete 하므로 offset 이 넘어가 다음 창을 판정한다. 그 창의 앵커 변화가 빠져 뒤
판정이 달라질 수 있고(회수 누락 → 다음 발화 누락), redrive 로 늦게 온 과거 창은 앵커 역전
가드에 막혀 상태를 되돌리지 못한다 — 복구는 재생 절차다(experiments/kafka-price-lane).

천장: poison 은 DLQ 없이 파티션을 막는다(fail loud). 운영에 쓰려면 DLQ topic 과 대사
경로가 먼저다.

천장: handler 가 도는 동안 poll 이 없다 — 한 창의 처리가 `max.poll.interval.ms`(기본 300초)를
넘으면 소비자가 group 에서 빠지고 다음 receive 가 오류로 CLI 를 죽인다. committed offset 은
미완료 메시지를 넘지 않아 재기동하면 그 자리부터 다시 온다. kernel lease 기본값(600초)보다
짧다는 불일치가 있다.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from .consumer import ConsumerMessage
from .relay import OutboxMessage, PublishFailure

logger = logging.getLogger(__name__)

# Relay lease 검증이 쓰는 호출 예산(15초) 안에 들어와야 한다(relay.SQS_CALL_BUDGET_SECONDS)
_FLUSH_TIMEOUT_SECONDS = 10


def is_kafka_url(url: str) -> bool:
    """`kafka://` scheme 이면 True — 이 값 하나로 SQS 경로와 갈린다."""
    return url.startswith("kafka://")


def parse_kafka_url(url: str) -> tuple[str, str, str | None]:
    """`kafka://host:port[,host:port]/topic?group=id` → (bootstrap, topic, group)."""
    parts = urlsplit(url)
    topic = parts.path.lstrip("/")
    if parts.scheme != "kafka" or not parts.netloc or not topic or "/" in topic:
        raise ValueError(f"kafka URL 형식이 아니다: {url!r}")
    groups = parse_qs(parts.query).get("group", [])
    if len(groups) > 1:
        raise ValueError(f"group 이 여러 개다: {url!r}")
    return parts.netloc, topic, groups[0] if groups else None


@dataclass
class _InFlight:
    topic: str
    partition: int
    offset: int
    hold_until: float     # 판정 보류로 끝나면 이 시각까지 파티션을 멈춘다
    acked: bool = False


class KafkaQueue:
    """kernel 의 `SqsQueue` 자리에 들어가는 Kafka 소비자(모듈 도크스트링의 의미 대응)."""

    def __init__(self, url: str, *, consumer=None, clock=time.monotonic):
        bootstrap, self.topic, group = parse_kafka_url(url)
        if group is None:
            raise ValueError(f"소비자 URL 에 group 이 없다: {url!r}")
        self._clock = clock
        self._inflight: _InFlight | None = None
        self._held: dict[tuple[str, int], float] = {}   # (topic, partition) → 재개 시각
        if consumer is None:  # pragma: no cover - 실 Kafka 경로
            from confluent_kafka import Consumer

            consumer = Consumer({
                "bootstrap.servers": bootstrap, "group.id": group,
                "enable.auto.commit": False,
                # 새 group 은 처음부터 읽는다. 복구 group 의 시작 위치는 소비 전에
                # kafka-consumer-groups --reset-offsets 로 정한다(코드 밖 운영 절차).
                "auto.offset.reset": "earliest",
            })
            consumer.subscribe([self.topic], on_revoke=self._on_revoke)
        self._consumer = consumer

    def _on_revoke(self, _consumer, partitions) -> None:
        # 넘긴 파티션의 멈춤·미완 상태는 새 소유자가 committed offset 부터 다시 판정한다.
        # ⚠️ 멈춘 파티션은 **여기서 resume** 한다(할당 해제 전) — librdkafka 는 앱이 건
        # pause 를 재할당 뒤에도 유지하므로, 예약만 지우면 같은 소비자에 돌아온 파티션이
        # 아무도 풀지 않는 pause 로 영구 정지한다. on_lost 미설정이면 lost 도 여기로 온다.
        held = [tp for tp in partitions if (tp.topic, tp.partition) in self._held]
        if held:
            self._consumer.resume(held)
        for tp in partitions:
            self._held.pop((tp.topic, tp.partition), None)
            if self._inflight and (self._inflight.topic, self._inflight.partition) == (
                tp.topic, tp.partition
            ):
                self._inflight = None

    def receive(self, *, queue_url: str, max_messages: int, wait_seconds: int,
                visibility_seconds: int) -> tuple[ConsumerMessage, ...]:
        """한 건만 받는다. 직전 메시지가 판정 보류였으면 먼저 되감고 멈춘다."""
        if max_messages != 1:
            raise ValueError("Kafka 소비는 batch_size=1 이다 — 누적 offset 이 미완 메시지를 건너뛴다")
        self._settle_previous()
        now = self._clock()
        self._resume_due(now)
        timeout = wait_seconds
        if self._held:
            timeout = max(0.0, min(wait_seconds, min(self._held.values()) - now))
        messages = self._consumer.consume(num_messages=1, timeout=timeout)
        if not messages:
            return ()
        message = messages[0]
        if message.error():
            # 브로커·클라이언트 오류는 여기서 판정하지 않는다 — CLI 를 죽여 재기동한다
            # (DB 오류를 전파하는 relay_cli·price_consumer_cli 와 같은 계약)
            raise RuntimeError(f"Kafka 수신 오류: {message.error()}")
        self._inflight = _InFlight(
            message.topic(), message.partition(), message.offset(),
            hold_until=self._clock() + visibility_seconds,
        )
        handle = f"{message.topic()}:{message.partition()}:{message.offset()}"
        try:
            body = (message.value() or b"").decode("utf-8")
        except UnicodeDecodeError:
            body = ""   # kernel 이 poison 으로 판정하고 지우지 않는다
        return (ConsumerMessage(message_id=handle, receipt_handle=handle, body=body),)

    def delete(self, *, queue_url: str, receipt_handle: str) -> None:
        """그 offset+1 을 동기 commit — 되돌릴 수 없다(SQS delete 와 같은 무게)."""
        inflight = self._current(receipt_handle)
        if inflight is None:
            return
        # commit 이 실패해도 위치는 이미 지났다 — 재기동하면 committed offset 부터 다시
        # 오고, DB 가 terminal 이라 kernel 이 실행 없이 지운다(SQS 삭제 실패와 같은 결과)
        inflight.acked = True
        from confluent_kafka import KafkaException, TopicPartition

        results = self._consumer.commit(
            offsets=[TopicPartition(inflight.topic, inflight.partition, inflight.offset + 1)],
            asynchronous=False,
        )
        # 동기 commit 도 파티션별 오류는 예외가 아니라 반환값에 실린다 — 안 보면 실패한
        # commit 이 성공으로 기록된다. 올리면 kernel(_delete)이 "재배달된다"로 남긴다
        for result in results or ():
            if result.error is not None:
                raise KafkaException(result.error)

    def change_visibility(self, *, queue_url: str, receipt_handle: str,
                          seconds: int) -> None:
        """N초 뒤 같은 메시지 — 실제 되감기는 다음 receive 가 한다(heartbeat 뒤 delete 가 올 수 있다)."""
        inflight = self._current(receipt_handle)
        if inflight is not None and not inflight.acked:
            inflight.hold_until = self._clock() + seconds

    def close(self) -> None:
        """group 을 떠난다 — 안 하면 session timeout 동안 파티션이 재할당되지 않는다."""
        self._consumer.close()

    def _current(self, receipt_handle: str) -> _InFlight | None:
        inflight = self._inflight
        if inflight is None:
            return None
        if receipt_handle != f"{inflight.topic}:{inflight.partition}:{inflight.offset}":
            # 한 건씩만 받으므로 다른 handle 은 rebalance 로 넘어간 뒤의 늦은 호출이다
            logger.warning("현재 메시지가 아닌 handle — 무시한다: %s", receipt_handle)
            return None
        return inflight

    def _settle_previous(self) -> None:
        inflight, self._inflight = self._inflight, None
        if inflight is None or inflight.acked:
            return
        from confluent_kafka import TopicPartition

        tp = TopicPartition(inflight.topic, inflight.partition, inflight.offset)
        self._consumer.pause([tp])
        self._consumer.seek(tp)
        self._held[(inflight.topic, inflight.partition)] = inflight.hold_until

    def _resume_due(self, now: float) -> None:
        due = [key for key, until in self._held.items() if until <= now]
        if not due:
            return
        from confluent_kafka import TopicPartition

        self._consumer.resume([TopicPartition(topic, partition) for topic, partition in due])
        for key in due:
            del self._held[key]


class KafkaPublisher:
    """Relay 의 `SqsPublisher` 자리 — queue_url 을 topic 으로 읽고 발행 확인까지 기다린다."""

    def __init__(self, producer=None):
        self._producer = producer
        self._bootstrap: str | None = None

    def _producer_for(self, bootstrap: str):
        if self._producer is None:  # pragma: no cover - 실 Kafka 경로
            from confluent_kafka import Producer

            self._producer = Producer({
                "bootstrap.servers": bootstrap,
                # 재시도가 순서를 바꾸거나 중복을 만들지 않게(파티션 안에서)
                "enable.idempotence": True,
            })
            self._bootstrap = bootstrap
        elif self._bootstrap not in (None, bootstrap):
            raise ValueError(f"bootstrap 이 둘이다: {self._bootstrap} / {bootstrap}")
        return self._producer

    def publish_batch(
        self, queue_url: str, messages: tuple[OutboxMessage, ...]
    ) -> tuple[frozenset[str], tuple[PublishFailure, ...]]:
        """`(발행 확인된 event_id, 실패)` — 확인 못 한 건은 어느 쪽에도 없다(Relay 가 재시도)."""
        from confluent_kafka import KafkaError, KafkaException

        bootstrap, topic, _ = parse_kafka_url(queue_url)
        producer = self._producer_for(bootstrap)
        published: set[str] = set()
        failures: list[PublishFailure] = []

        def reported(event_id):
            """event_id 를 묶은 발행 확인 콜백 — 브로커 응답이 오면 flush 안에서 불린다."""
            def callback(error, _message):
                """확인되면 성공 집합에, 거부되면 실패 목록에(크기 초과만 terminal)."""
                if error is None:
                    published.add(event_id)
                else:
                    failures.append(PublishFailure(
                        event_id, f"Kafka 발행 실패: {error}",
                        terminal=error.code() == KafkaError.MSG_SIZE_TOO_LARGE,
                    ))
            return callback

        for message in messages:
            try:
                producer.produce(
                    topic, key=_partition_key(message.body),
                    value=message.body.encode("utf-8"), on_delivery=reported(message.event_id),
                )
            except (KafkaException, BufferError) as error:
                code = error.args[0].code() if isinstance(error, KafkaException) else None
                failures.append(PublishFailure(
                    message.event_id, f"Kafka produce 거부: {error}",
                    terminal=code == KafkaError.MSG_SIZE_TOO_LARGE,
                ))
        remaining = producer.flush(_FLUSH_TIMEOUT_SECONDS)
        if remaining:
            logger.error("Kafka 발행 확인 %d건 미도착 — Relay 가 재시도한다", remaining)
        return frozenset(published), tuple(failures)


def _partition_key(body: str) -> bytes | None:
    """세션의 창들이 한 파티션에 모이게 session_id 로 — 없으면(뉴스) 키 없이.

    ⚠️ 형상 밖 payload 에서 예외를 내면 안 된다 — publish_batch 가 통째로 실패해 그
    destination 전체가 재시도에 묶인다(Relay 의 "한 event 가 레인을 멈추지 않는다" 계약).
    """
    payload = json.loads(body).get("payload")
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    return session_id.encode("utf-8") if isinstance(session_id, str) else None
