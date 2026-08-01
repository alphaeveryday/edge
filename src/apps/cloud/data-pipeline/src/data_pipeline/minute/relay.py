"""1분 파이프라인 Outbox Relay (ALPHA-670, 계획 §11 / v0.7 9·11절).

commit transaction **밖에서** SQS 로 발행하는 유일한 경로다. Worker 는 job/outbox 를 한
트랜잭션에 남기고 끝내고(그래서 "DB commit 후 Relay 전 종료"가 유실이 아니다), 이
프로세스가 `NEW` event 를 주워 발행한다.

Worker 안에 넣지 않는 이유(v0.7 11.1): 가격 Service scale-in 이 뉴스 발행을 막지 않고,
뉴스 장애가 가격 발행을 막지 않으며, 한 구현이 가격·뉴스·백필 outbox 를 공통 처리한다.

**Relay 는 business logic 을 갖지 않는다** — 무엇을 발행할지는 outbox 행이 이미 정했다.
이 모듈의 판단은 딱 셋이다: 어느 큐로(destination routing), 실패가 일시인가 영구인가,
언제 다시 시도할까(DB 가 기록하는 next_attempt_at).

⚠️ **한 event 가 Relay 를 멈추게 하면 안 된다.** 미정의 destination·크기 초과처럼
재시도해도 절대 발행되지 않는 event 는 예외로 프로세스를 죽이는 대신 **DEAD 로 격리**
한다(조회 가능한 terminal 상태 — v0.7 11.1). 예외로 죽이면 그 행이 outbox 에 남아 있는
한 다음 tick 도 같은 자리에서 죽어 **가격·뉴스·백필 세 큐가 전부 영구히 멈춘다**.

재시도 권위는 PostgreSQL 이다(v0.7 12.4) — SQS 는 wake-up transport 일 뿐이고, 몇 번
시도했고 언제 다시 할지는 outbox 행에만 있다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from .jobs import JobLedger
from .models import canonical_json

logger = logging.getLogger(__name__)

# SQS 상한 — SendMessageBatch 는 요청당 10건, 메시지 하나는 256KiB.
SQS_BATCH_LIMIT = 10
SQS_MAX_MESSAGE_BYTES = 262_144


@dataclass(frozen=True)
class OutboxMessage:
    """발행 단위 — 큐에 실제로 실리는 바이트와 그 출처 event."""

    event_id: str
    body: str


@dataclass(frozen=True)
class PublishFailure:
    """발행 실패 한 건. `terminal` 이면 재시도해도 결과가 같다는 뜻이다."""

    event_id: str
    error: str
    terminal: bool = False


@dataclass
class RelayConfig:
    relay_id: str
    # destination -> queue URL. 큐는 환경마다 다르므로 설정에서 온다(계획 §11 큐 3종).
    queue_urls: Mapping[str, str]
    batch_limit: int = 10
    lease_seconds: int = 60
    # transient 재시도 간격 = base * 2**attempt, cap 까지. DB 가 시각의 권위다.
    retry_base_seconds: int = 2
    retry_max_seconds: int = 300
    # 이 횟수를 채우면 DEAD 로 격리한다 — 조용히 무한 재시도하면 backlog 가 영원히 안 준다
    max_attempts: int = 8

    def __post_init__(self) -> None:
        if self.batch_limit < 1:
            raise ValueError("batch_limit 은 1 이상이어야 한다")
        if self.max_attempts < 1:
            raise ValueError("max_attempts 는 1 이상이어야 한다")
        if self.retry_base_seconds < 1 or self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry_base_seconds <= retry_max_seconds 여야 하고 둘 다 양수다")


def build_message_body(event: dict) -> str:
    """큐에 실리는 결정적 envelope.

    payload 만 보내지 않고 event_id/event_type 을 함께 싣는다 — Consumer(PR 7A)의 멱등
    키가 event_id 이고, 같은 논리 사건의 재전달은 **같은 event_id** 로 오기 때문이다.
    """
    return canonical_json({
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "payload": event["payload"],
    })


class SqsPublisher:
    """실 SQS 발행 — boto3 는 지연 import (`ops/aws.py` 관례, region 명시).

    배치·크기 상한은 SQS 프로토콜 사정이라 여기서 흡수한다. Relay 는 destination 별
    목록만 넘기고 결과(성공 ID 집합, 실패 목록)를 받는다.
    """

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):  # pragma: no cover - 실 AWS 경로
        if self._client is None:
            from ..ops.aws import sqs_client

            self._client = sqs_client()
        return self._client

    def publish_batch(
        self, queue_url: str, messages: tuple[OutboxMessage, ...]
    ) -> tuple[frozenset[str], tuple[PublishFailure, ...]]:
        published: set[str] = set()
        failures: list[PublishFailure] = []
        sendable: list[OutboxMessage] = []
        for message in messages:
            if len(message.body.encode("utf-8")) > SQS_MAX_MESSAGE_BYTES:
                # 재시도해도 영원히 안 들어간다 — 격리해야 backlog 가 막히지 않는다
                failures.append(PublishFailure(
                    message.event_id,
                    f"메시지가 SQS 상한({SQS_MAX_MESSAGE_BYTES}B)을 넘는다",
                    terminal=True,
                ))
            else:
                sendable.append(message)
        for start in range(0, len(sendable), SQS_BATCH_LIMIT):
            chunk = sendable[start : start + SQS_BATCH_LIMIT]
            response = self.client.send_message_batch(
                QueueUrl=queue_url,
                Entries=[
                    # Id 는 요청 안에서만 유일하면 된다 — event_id 를 그대로 쓰면 SQS 의
                    # Id 문자 제약(영숫자·하이픈·언더스코어 80자)에 걸릴 수 있어 순번을 쓴다
                    {"Id": str(index), "MessageBody": message.body}
                    for index, message in enumerate(chunk)
                ],
            )
            for entry in response.get("Successful", ()):
                published.add(chunk[int(entry["Id"])].event_id)
            for entry in response.get("Failed", ()):
                message = chunk[int(entry["Id"])]
                failures.append(PublishFailure(
                    message.event_id,
                    f"{entry.get('Code')}: {entry.get('Message')}",
                    # SenderFault = 요청 자체가 틀렸다(잘못된 큐·형식) — 재시도 무의미
                    terminal=bool(entry.get("SenderFault")),
                ))
        return frozenset(published), tuple(failures)


@dataclass
class OutboxRelay:
    """tick 을 외부(엔트리포인트/테스트)가 돌리는 수동 루프 — sleep 은 호출자 소관."""

    jobs: JobLedger
    publisher: object  # publish_batch(queue_url, messages) -> (published_ids, failures)
    config: RelayConfig
    stopping: bool = False  # SIGTERM — 진행 중 batch 를 끝내고 다음 tick 에 멈춘다

    def request_stop(self) -> None:
        self.stopping = True

    def tick(self, now: datetime) -> str:
        """한 사이클. 반환은 관측용: STOPPED / IDLE / PUBLISHED / PARTIAL."""
        if self.stopping:
            # 진행 중 batch 는 이미 끝났다(tick 단위) — claim 을 잡은 채 죽지 않는다.
            # 잡힌 채 남더라도 claim_expires_at 만료로 다음 Relay 가 회수한다.
            return "STOPPED"
        batch = self.jobs.claim_outbox_batch(
            relay_id=self.config.relay_id, now=now,
            limit=self.config.batch_limit, lease_seconds=self.config.lease_seconds,
        )
        if not batch:
            return "IDLE"
        by_destination: dict[str, list[dict]] = {}
        for event in batch:
            by_destination.setdefault(event["destination"], []).append(event)
        published = failed = 0
        # destination 정렬 — 같은 batch 를 두 번 돌려도 순서가 같아 로그가 비교 가능하다
        for destination in sorted(by_destination):
            events = by_destination[destination]
            queue_url = self.config.queue_urls.get(destination)
            if queue_url is None:
                # ⚠️ 예외를 던지지 않는다 — 그 event 는 outbox 에 남아 있어 다음 tick 도
                # 같은 자리에서 죽고, 멀쩡한 다른 큐까지 영구히 멈춘다. 크게 기록하고
                # 조회 가능한 terminal 로 격리한다(Rule 12 fail loud = 드러내기).
                logger.error(
                    "미정의 destination %r — event %d건 DEAD 격리(설정된 큐: %s)",
                    destination, len(events), sorted(self.config.queue_urls),
                )
                failed += self._record_failures(
                    events, now, error=f"미정의 destination: {destination}", terminal=True
                )
                continue
            messages = tuple(
                OutboxMessage(event["event_id"], build_message_body(event))
                for event in events
            )
            try:
                published_ids, failures = self.publisher.publish_batch(queue_url, messages)
            except Exception as error:
                # 큐·네트워크 장애 — batch 전체를 transient 로 되돌린다. 기록마저 실패하면
                # claim 만료로 회수되므로 유실은 없다.
                logger.exception("발행 실패(%s) — batch %d건 재시도 예약", destination, len(events))
                failed += self._record_failures(
                    events, now, error=f"publish 예외: {error}", terminal=False
                )
                continue
            failure_by_id = {failure.event_id: failure for failure in failures}
            for event in events:
                failure = failure_by_id.get(event["event_id"])
                if failure is not None:
                    failed += self._record_failures([event], now, failure.error, failure.terminal)
                elif event["event_id"] in published_ids:
                    if self.jobs.mark_published(
                        event_id=event["event_id"], relay_id=self.config.relay_id,
                        claim_token=event["claim_token"], now=now,
                    ):
                        published += 1
                    else:
                        # claim 을 잃은 뒤의 성공 보고 — 메시지는 이미 나갔고 행은 NEW 로
                        # 남아 재발행된다. Consumer 가 event_id 로 흡수한다(v0.7 9절).
                        logger.warning("published 기록 거부(claim 상실): %s", event["event_id"])
                else:
                    # 성공에도 실패에도 없는 event — 발행 여부를 모른다. 성공으로 접으면
                    # 유실이 조용히 확정되므로 미보고를 transient 실패로 기록한다.
                    logger.error("발행 결과 미보고: %s", event["event_id"])
                    failed += self._record_failures(
                        [event], now, error="발행 결과 미보고", terminal=False
                    )
        return "PUBLISHED" if failed == 0 else "PARTIAL"

    def _record_failures(
        self, events, now: datetime, error: str, terminal: bool
    ) -> int:
        recorded = 0
        for event in events:
            attempt = event["attempt_count"] + 1
            # 예산을 소진했으면 transient 여도 격리한다 — 안 그러면 backlog 가 영원히
            # 안 줄고 oldest-age 알람만 계속 울린다(운영자가 볼 수 있는 상태로 옮긴다)
            is_terminal = terminal or attempt >= self.config.max_attempts
            next_attempt_at = None if is_terminal else now + timedelta(
                seconds=min(
                    self.config.retry_base_seconds * (2 ** event["attempt_count"]),
                    self.config.retry_max_seconds,
                )
            )
            if self.jobs.record_publish_failure(
                event_id=event["event_id"], relay_id=self.config.relay_id,
                claim_token=event["claim_token"], now=now,
                next_attempt_at=next_attempt_at, error=error, terminal=is_terminal,
            ):
                recorded += 1
            else:
                # claim 상실 — 다른 Relay 가 이 event 를 가져갔다. 기록은 그쪽이 한다.
                logger.warning("실패 기록 거부(claim 상실): %s", event["event_id"])
        return recorded


def relay_cli(settings, *, max_ticks: int | None = None) -> int:
    """상주 Relay 진입점 — `python -m data_pipeline.run relay` (ECS Service 명령).

    `max_ticks` 는 로컬 확인·일회성 배출용 상한이고, 미지정이면 SIGTERM 까지 돈다.
    tick 사이 대기는 **할 일이 없을 때만** 둔다 — backlog 가 쌓여 있으면 쉬지 않고
    비워야 oldest-age 가 줄어든다.

    DB 오류는 여기서 잡지 않는다 — 삼키면 발행이 멈춘 걸 아무도 모른 채 프로세스만
    살아 있다. 전파시켜 task 를 죽이면 ECS 가 재기동하고, 잡힌 채 남은 event 는
    claim_expires_at 만료로 회수된다(유실 0). 벤더/큐 장애는 tick 안에서 이미
    transient 로 흡수되므로 여기까지 오는 건 원장 자체가 안 될 때뿐이다.
    """
    import os
    import signal
    import socket
    import time
    from datetime import timezone

    if settings.db is None:
        raise SystemExit("db 설정 없음 — relay 는 outbox 원장 필수(DATA_PIPELINE_DB__* 주입)")
    if settings.minute_relay is None:
        raise SystemExit(
            "minute_relay 설정 없음 — relay 는 destination→큐 매핑 필수"
            "(DATA_PIPELINE_MINUTE_RELAY__QUEUE_URLS__<destination> 주입)"
        )
    options = settings.minute_relay
    relay = OutboxRelay(
        jobs=JobLedger(db=settings.db),
        publisher=SqsPublisher(),
        config=RelayConfig(
            relay_id=f"relay-{socket.gethostname()}-{os.getpid()}",
            queue_urls=dict(options.queue_urls),
            batch_limit=options.batch_limit, lease_seconds=options.lease_seconds,
            retry_base_seconds=options.retry_base_seconds,
            retry_max_seconds=options.retry_max_seconds,
            max_attempts=options.max_attempts,
        ),
    )
    for received in (signal.SIGTERM, signal.SIGINT):
        # 진행 중 batch 를 끊지 않는다 — tick 경계에서 멈춘다(claim 을 쥔 채 죽지 않게)
        signal.signal(received, lambda *_: relay.request_stop())
    logger.info("relay 시작: id=%s 큐 %s", relay.config.relay_id, sorted(options.queue_urls))
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        state = relay.tick(datetime.now(timezone.utc))
        ticks += 1
        if state == "STOPPED":
            logger.info("relay 종료(SIGTERM) — %d tick", ticks)
            return 0
        if state == "IDLE":
            time.sleep(options.tick_seconds)
    logger.info("relay 종료(max-ticks %d 도달)", ticks)
    return 0
