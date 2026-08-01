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

⚠️ **반대로 일시 실패는 횟수로 포기하지 않는다.** DEAD 는 event 자체가 발행 불가일 때만
이다 — 시도 예산을 두면 몇 분짜리 큐 장애가 멀쩡한 event 를 DEAD 로 만든다. redrive
(ALPHA-672)로 되돌릴 수는 있지만 **스스로 풀리지는 않는다**: 사람이 알아채고 명령을
쳐야 한다. 지연은 알람(backlog·oldest-age, PR 7D)이 잡고, DEAD 는 사람이 본다.

재시도 권위는 PostgreSQL 이다(v0.7 12.4) — SQS 는 wake-up transport 일 뿐이고, 몇 번
시도했고 언제 다시 할지는 outbox 행에만 있다.

**알려진 천장**: outbox `payload` 는 JSONB 라 스키마상 크기 상한이 없다. 충분히 큰 행
하나는 claim 의 fetch 단계(드라이버가 행을 물질화한다)에서 메모리를 소진할 수 있는데,
그건 이 모듈의 격리(행 단위 직렬화 try)보다 앞이라 잡지 못한다 — 재기동마다 같은 행을
다시 읽어 crash-loop 이 된다. 제대로 된 해소는 **쓰는 쪽**(commit)에 크기 계약을 두는
것이고(그 계약이 없으면 Relay 가 방어할 지점이 없다), 그건 writer 변경이라 별건이다.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .jobs import DESTINATION_JOB_KINDS, EVENT_TYPE_JOB_KINDS, JobLedger
from .models import canonical_json

logger = logging.getLogger(__name__)

# 계획 §11·v0.7 12.1 이 고정한 큐 3종. 이 어휘를 두는 이유는 **기동 시 검증**이다:
# 매핑에 빠진 destination 이 있으면 그 큐로 갈 event 가 전부 DEAD 로 몰살되는데,
# DEAD 는 스스로 풀리지 않는다(redrive 는 사람이 친다). 설정 오타 하나가 배달을 통째로
# 파괴하는 경로라, 런타임 격리에 맡기지 않고 기동을 거부해 배포 실패로 드러낸다.
KNOWN_DESTINATIONS = frozenset({
    "price-analysis-realtime",
    "news-extraction-realtime",
    "news-extraction-backfill",
})


# SQS 상한 (docs: SQS Developer Guide "Amazon SQS message quotas", 2026-08 확인) —
# 배치 요청당 10건, 메시지 하나 1 MiB, 배치 합계도 1 MiB(SendMessageBatch API 참조).
# ⚠️ 이 숫자는 **벤더 계약**이라 드리프트한다. 상한을 작게 잡으면 멀쩡한 메시지가
# terminal(DEAD)로 확정되고 상수를 고쳐도 그 행은 재평가되지 않는다 — 바꿀 때 문서로
# 재확인할 것(과거 256 KiB 였다).
# 재시도해도 결과가 같은 실패 — **메시지 자체**의 결함만 넣는다. 큐·권한·스로틀링처럼
# 배포나 시간으로 풀리는 것은 여기 없다(DEAD 는 이 단계에서 되돌릴 수 없다).
TERMINAL_ERROR_CODES = frozenset({
    "InvalidMessageContents",  # 본문에 SQS 가 허용하지 않는 문자
})

# SQS 호출 하나의 시간 예산(connect 5s + read 10s) — lease 하한 계산에 쓴다
SQS_CALL_BUDGET_SECONDS = 15

# backoff 지수 상한 — 2**30 초면 이미 어떤 cap 도 넘는다. 상한 없이 두면 큰
# attempt_count 가 거대 정수 연산을 일으킨다.
_MAX_BACKOFF_EXPONENT = 30

SQS_BATCH_LIMIT = 10
SQS_MAX_MESSAGE_BYTES = 1_048_576
SQS_MAX_BATCH_BYTES = 1_048_576


@dataclass(frozen=True)
class OutboxMessage:
    """발행 단위 — 큐에 실제로 실리는 바이트와 그 출처 event."""

    event_id: str
    body: str


@dataclass(frozen=True)
class PublishFailure:
    """발행 실패 한 건. `terminal` 이면 재시도해도 결과가 같다는 뜻이다.

    ⚠️ `terminal` 은 **메시지 자체가 못 실리는 경우**만이다(TERMINAL_ERROR_CODES·크기
    초과). SenderFault 는 "호출자 측 오류"라는 뜻일 뿐이라 단독으로는 근거가 아니다 —
    잘못된 큐 URL·권한 오류도 SenderFault 이고 그건 배포로 고쳐진다.
    """

    event_id: str
    error: str
    terminal: bool = False


@dataclass
class RelayConfig:
    relay_id: str
    # destination -> queue URL. 큐는 환경마다 다르므로 설정에서 온다(계획 §11 큐 3종).
    queue_urls: Mapping[str, str]
    batch_limit: int = 10
    # 기본 batch_limit(10) × 호출 예산(15초)을 견디는 값. 길수록 crash 후 회수가
    # 늦어지지만(최악 이 값만큼), 짧으면 정상 발행 중에 탈취가 일어난다.
    lease_seconds: int = 150
    # transient 재시도 간격 = base * 2**attempt, cap 까지. DB 가 시각의 권위다.
    retry_base_seconds: int = 2
    retry_max_seconds: int = 300

    def __post_init__(self) -> None:
        missing = KNOWN_DESTINATIONS - set(self.queue_urls)
        if missing:
            # 런타임 DEAD 격리는 **한 건짜리 사고**를 막는 장치지, 설정 누락 같은
            # 전면적 사고까지 감당하지 못한다 — 그 경우 해당 큐의 event 가 전부
            # 되돌릴 수 없는 DEAD 가 된다. 여기서 기동을 막는다.
            raise ValueError(
                f"queue_urls 에 필수 destination 이 없다: {sorted(missing)} — "
                "설정 누락이면 그 큐의 event 가 전부 DEAD 로 격리된다"
            )
        unknown = set(self.queue_urls) - KNOWN_DESTINATIONS
        if unknown:
            # 어휘 밖 키는 오타이거나 미승인 큐다 — 그 destination 의 event 가 엉뚱한
            # 큐로 나가 PUBLISHED 로 확정되면 설정을 고쳐도 되살아나지 않는다
            raise ValueError(f"queue_urls 에 정의되지 않은 destination 이 있다: {sorted(unknown)}")
        shared = [
            url for url in set(self.queue_urls.values())
            if sum(1 for value in self.queue_urls.values() if value == url) > 1
        ]
        if shared:
            # 같은 URL 로 묶이면 다른 큐의 Consumer 가 wake-up 을 못 받는데, event 는
            # PUBLISHED 로 확정돼 나중에 URL 을 고쳐도 재평가되지 않는다(v0.7 12.1 —
            # 가격·뉴스는 큐를 공유하지 않는다)
            raise ValueError(f"두 destination 이 같은 큐를 가리킨다: {sorted(shared)}")
        if self.batch_limit < 1:
            raise ValueError("batch_limit 은 1 이상이어야 한다")
        # 한 batch 는 최악의 경우 **건수만큼의 요청**으로 쪼개진다(큰 메시지는 합계
        # 상한 때문에 하나씩 나간다). 그 전부를 견디지 못하는 lease 는 발행 도중
        # 만료돼 다른 Relay 가 같은 행을 탈취하고, 양쪽 기록이 서로 거부되는 동안
        # 메시지만 중복 발행되며 행은 NEW 로 고착된다.
        required_lease = self.batch_limit * SQS_CALL_BUDGET_SECONDS
        if self.lease_seconds < required_lease:
            raise ValueError(
                f"lease_seconds({self.lease_seconds}) 가 최악의 발행 시간"
                f"({self.batch_limit}건 × {SQS_CALL_BUDGET_SECONDS}초 = {required_lease}초)"
                "보다 짧다 — 발행 도중 만료되면 경쟁 Relay 가 같은 행을 탈취한다"
            )
        if self.retry_base_seconds < 1 or self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry_base_seconds <= retry_max_seconds 여야 하고 둘 다 양수다")


def _non_blank_text(value: object) -> bool:
    """SQS 응답의 필수 문자열 필드가 실제로 채워졌나 — truthiness 만 보면 True·1 도 통과한다."""
    return isinstance(value, str) and bool(value.strip())


def _body_md5(body: str) -> str:
    """SQS 가 돌려주는 MD5OfMessageBody 와 대조할 값 — 보안이 아니라 전송 무결성 확인용."""
    return hashlib.md5(body.encode("utf-8"), usedforsecurity=False).hexdigest()


def build_message_body(event: dict) -> str:
    """큐에 실리는 결정적 envelope.

    payload 만 보내지 않고 event_id/event_type 을 함께 싣는다 — Consumer(ALPHA-672)의 멱등
    키가 event_id 이고, 같은 논리 사건의 재전달은 **같은 event_id** 로 오기 때문이다.
    """
    return canonical_json({
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "payload": event["payload"],
    })


def _chunk_by_size(messages: list[OutboxMessage]) -> list[list[OutboxMessage]]:
    """건수(10)와 **합계 바이트**(1 MiB) 둘 다로 쪼갠다.

    건수만 보면 큰 메시지 몇 건이 한 요청에 몰려 `BatchRequestTooLong` 으로 **요청
    전체**가 거부된다 — 같은 묶음이 영원히 재시도되며 그 destination 의 배달이 막힌다.
    """
    chunks: list[list[OutboxMessage]] = []
    current: list[OutboxMessage] = []
    current_bytes = 0
    for message in messages:
        size = len(message.body.encode("utf-8"))
        if current and (
            len(current) >= SQS_BATCH_LIMIT or current_bytes + size > SQS_MAX_BATCH_BYTES
        ):
            chunks.append(current)
            current, current_bytes = [], 0
        current.append(message)
        current_bytes += size
    if current:
        chunks.append(current)
    return chunks


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
        for chunk in _chunk_by_size(sendable):
            # Id 는 요청 안에서만 유일하면 된다 — event_id 를 그대로 쓰면 SQS 의 Id 제약
            # (영숫자·하이픈·언더스코어 80자)에 걸릴 수 있어 순번을 쓴다
            by_id = {str(index): message for index, message in enumerate(chunk)}
            response = self.client.send_message_batch(
                QueueUrl=queue_url,
                Entries=[
                    {"Id": entry_id, "MessageBody": message.body}
                    for entry_id, message in by_id.items()
                ],
            )
            # 응답을 Id 로 **한 번에** 모은 뒤 판정한다 — 목록을 순서대로 훑으며 확정하면
            # 같은 Id 가 양쪽에 온 모순 응답에서 먼저 본 쪽(성공)이 이겨 버린다.
            verdicts: dict[str, list[tuple[str, dict]]] = {}
            for kind in ("Successful", "Failed"):
                for entry in response.get(kind, ()):
                    # 보낸 Id 와 **정확히** 대조한다. int() 로 강제하면 "-1"·"00" 같은
                    # 응답이 엉뚱한 message 를 가리켜 다른 event 가 PUBLISHED 로 확정되고
                    # (재발행 대상에서 빠져) 조용히 유실된다.
                    entry_id = entry.get("Id")
                    if entry_id not in by_id:
                        logger.error("SQS 응답의 Id 가 요청과 안 맞는다: %r", entry_id)
                        continue
                    verdicts.setdefault(entry_id, []).append((kind, entry))
            for entry_id, reported in verdicts.items():
                message = by_id[entry_id]
                if len(reported) > 1:
                    # 성공이자 실패 — 발행 여부를 알 수 없다. 성공으로 접으면 행이 NEW 에서
                    # 빠져 재평가되지 않는다. 미보고로 두면 Relay 가 재시도한다(안전한 쪽).
                    logger.error("SQS 응답이 모순이다(성공·실패 동시): %s", message.event_id)
                    continue
                kind, entry = reported[0]
                if kind == "Failed":
                    code = entry.get("Code")
                    if not _non_blank_text(code):
                        # Code 는 실패 항목의 필수 필드다. 없으면 무엇이 왜 실패했는지
                        # 모르는 것이라, SenderFault 하나로 비가역 DEAD 를 확정하지
                        # 않는다 — 미보고로 두면 다음 응답에서 제대로 판정된다.
                        logger.error("SQS 실패 항목에 Code 가 없다: %s", message.event_id)
                        continue
                    failures.append(PublishFailure(
                        message.event_id,
                        f"{code}: {entry.get('Message')}",
                        # ⚠️ SenderFault 는 "호출자 측 오류"라는 뜻일 뿐 **영구 오류라는
                        # 계약이 아니다**. 잘못된 큐 URL(NonExistentQueue)·권한 오류도
                        # SenderFault 인데, 그건 배포로 고쳐지는 설정 문제지 event 의
                        # 결함이 아니다 — 그걸 DEAD 로 확정하면 URL 오타 하나가 레인
                        # 전체를 사람 손으로만 되돌릴 수 있게 만든다(job 수만큼의 redrive).
                        # terminal 은 **그 메시지 자체가 못 실리는 경우**로 좁힌다:
                        # 코드가 결함 목록에 있고 SenderFault 도 참일 때만.
                        # 코드가 메시지 결함이면서 **필수 필드 SenderFault 도 참**일
                        # 때만 terminal 이다. 필드가 없거나 False 인 모순 응답을
                        # terminal 로 접으면 malformed 응답 한 번이 영구 누락을 만든다.
                        terminal=(
                            code in TERMINAL_ERROR_CODES
                            and entry.get("SenderFault") is True
                        ),
                    ))
                elif not _non_blank_text(entry.get("MessageId")):
                    # 성공 항목의 필수 필드가 없다 = 큐가 받았다는 근거가 없다.
                    # 근거 없는 성공을 확정하면 그 자리에서 유실이 영구화된다.
                    logger.error("SQS 성공 항목에 MessageId 가 없다: %s", message.event_id)
                elif entry.get("MD5OfMessageBody") != _body_md5(message.body):
                    # 본문 무결성은 **우리가** 검증한다. SDK 가 대신 본다고 가정하지
                    # 않는다 — 확인하지 않은 전제를 근거로 삼으면 그 전제가 틀렸을 때
                    # 손상된 본문이 PUBLISHED 로 확정되고 그 행은 다시 claim 되지 않는다.
                    logger.error(
                        "SQS 응답 MD5 불일치 — 큐가 받은 본문이 보낸 것과 다르다: %s",
                        message.event_id,
                    )
                else:
                    published.add(message.event_id)
        return frozenset(published), tuple(failures)


@dataclass
class OutboxRelay:
    """tick 을 외부(엔트리포인트/테스트)가 돌리는 수동 루프 — sleep 은 호출자 소관."""

    jobs: JobLedger
    publisher: object  # publish_batch(queue_url, messages) -> (published_ids, failures)
    config: RelayConfig
    stopping: bool = False  # SIGTERM — 진행 중 batch 를 끝내고 다음 tick 에 멈춘다
    # tick 안의 **경과 시간**만 재는 기준점(벽시계 아님) — backoff 가 실패 시각 기준이 되게
    _tick_started: float = field(default=0.0, repr=False)

    def request_stop(self) -> None:
        self.stopping = True

    def tick(self, now: datetime) -> str:
        """한 사이클. 반환은 관측용: STOPPED / IDLE / PUBLISHED / PARTIAL."""
        self._tick_started = time.monotonic()
        if self.stopping:
            # 진행 중 batch 는 이미 끝났다(tick 단위) — claim 을 잡은 채 죽지 않는다.
            # 잡힌 채 남더라도 claim_expires_at 만료로 다음 Relay 가 회수한다.
            return "STOPPED"
        # **destination 별로 집고 바로 발행한다.** 나눠 집는 이유는 레인 격리다 — 한 번에
        # 집으면 장애 난 레인의 오래된 재시도 행이 batch 를 채워 멀쩡한 레인이 계속
        # 밀린다(가격 장애가 뉴스 발행을 막지 않는다는 게 공용 Relay 의 근거 — v0.7 11.1).
        # 그리고 **집은 뒤 바로 발행해야** 한다: 전부 집어놓고 순차 발행하면 뒤 레인의
        # lease 가 자기 차례 전에 흘러가 다른 Relay 가 탈취하고, lease 검증이 무의미해진다.
        published = failed = 0
        claimed_any = False
        for destination, events in self._claim_lanes(now):
            claimed_any = True
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
            # destination 과 event_type 이 어긋나면 **발행하지 않는다** — 큐는
            # 가격·뉴스를 공유하지 않는다(v0.7 12.1). 그대로 내보내면 그 큐의
            # Consumer 가 자기 테이블이 아니라며 처리도 삭제도 못 해 DLQ 로 가고,
            # 대사도 남의 레인이라 건드리지 않아 아무도 해소하지 못한다.
            # **terminal 이다.** 배포로 바뀌는 건 앞으로 쓰일 행이고, 이미 쓰인 이
            # 행의 destination·event_type 은 컬럼에 박혀 있어 영영 그대로다 — transient
            # 로 두면 매 tick 이 같은 자리에서 거부하며 그 행이 영구 고착된다
            # (이 모듈의 terminal 기준 그대로: 재시도해도 결과가 같다).
            mismatched = [
                event for event in events
                if DESTINATION_JOB_KINDS.get(destination)
                != EVENT_TYPE_JOB_KINDS.get(event["event_type"])
            ]
            if mismatched:
                logger.error(
                    "destination %r 에 %s 사건이 실려 있다 — 쓰는 쪽 배선 오류",
                    destination, sorted({e["event_type"] for e in mismatched}),
                )
                failed += self._record_failures(
                    mismatched, now,
                    error=f"destination {destination} 과 event_type 이 어긋난다",
                    terminal=True,
                )
                mismatched_ids = {event["event_id"] for event in mismatched}
                events = [e for e in events if e["event_id"] not in mismatched_ids]
                if not events:
                    continue
            # 직렬화는 **event 단위로** 격리한다 — payload 는 JSONB 라 크기 상한이 없어
            # 거대 행 하나가 OOM·직렬화 예외를 낼 수 있는데, 묶어서 만들면 그 하나가
            # 같은 destination 의 정상 행까지 끌고 가 그 큐가 통째로 고착된다.
            messages = []
            for event in events:
                try:
                    messages.append(
                        OutboxMessage(event["event_id"], build_message_body(event))
                    )
                except Exception as error:  # noqa: BLE001 — 행 단위 격리가 목적
                    logger.exception("event %s 직렬화 실패 — 격리", event["event_id"])
                    failed += self._record_failures(
                        [event], now, error=f"직렬화 실패: {error}", terminal=False
                    )
            if not messages:
                continue
            sendable_events = {event["event_id"]: event for event in events}
            events = [sendable_events[m.event_id] for m in messages]
            try:
                published_ids, failures = self.publisher.publish_batch(
                    queue_url, tuple(messages)
                )
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
                        # 깨끗한 성공이 아니므로 tick 상태에 드러낸다(PARTIAL).
                        failed += 1
                        logger.warning("published 기록 거부(claim 상실): %s", event["event_id"])
                else:
                    # 성공에도 실패에도 없는 event — 발행 여부를 모른다. 성공으로 접으면
                    # 유실이 조용히 확정되므로 미보고를 transient 실패로 기록한다.
                    logger.error("발행 결과 미보고: %s", event["event_id"])
                    failed += self._record_failures(
                        [event], now, error="발행 결과 미보고", terminal=False
                    )
        if not claimed_any:
            return "IDLE"
        return "PUBLISHED" if failed == 0 else "PARTIAL"

    def _claim_time(self, now: datetime) -> datetime:
        """지금 이 claim 의 시각 = tick 시작 + 그동안 흐른 시간.

        lease 만료는 claim 시각 기준으로 잡힌다 — tick 시작 시각을 그대로 쓰면 앞 레인
        발행에 쓴 시간만큼 뒤 레인의 실질 lease 가 깎여, 발행 도중 만료·탈취를 막으려고
        둔 lease 가드가 무의미해진다. 벽시계 권위는 주입된 now 하나이고(가상 시계 원칙)
        경과만 monotonic 으로 잰다 — backoff 와 같은 축.
        """
        return now + timedelta(seconds=time.monotonic() - self._tick_started)

    def _claim_lanes(self, now: datetime):
        """destination 하나씩 claim 해 (destination, events) 로 넘긴다 — 발행 직전에 집는다.

        마지막에 설정 밖 destination 을 쓸어 담는다. 그 행들은 위 루프가 건드리지 않아
        남겨두면 영원히 NEW 로 남는다 — **그것만** 집는다(전역으로 집은 뒤 골라내면
        처리하지 않을 정상 행까지 lease 로 묶여 그만큼 발행이 밀린다).
        """
        for destination in sorted(self.config.queue_urls):
            events = self.jobs.claim_outbox_batch(
                relay_id=self.config.relay_id, now=self._claim_time(now),
                limit=self.config.batch_limit, lease_seconds=self.config.lease_seconds,
                destination=destination,
            )
            if events:
                yield destination, events
        orphans: dict[str, list[dict]] = {}
        for event in self.jobs.claim_outbox_batch(
            relay_id=self.config.relay_id, now=self._claim_time(now),
            limit=self.config.batch_limit, lease_seconds=self.config.lease_seconds,
            exclude_destinations=tuple(self.config.queue_urls),
        ):
            orphans.setdefault(event["destination"], []).append(event)
        yield from sorted(orphans.items())

    def _record_failures(
        self, events, now: datetime, error: str, terminal: bool
    ) -> int:
        """실패를 기록하고 **시도한 건수**를 돌려준다.

        기록 성공 수가 아니라 시도 수인 이유: CAS 가 거부돼도(다른 Relay 가 lease 를
        가져감) 이 tick 에서 발행에 실패한 사실은 그대로다. 성공 수로 세면 발행이
        하나도 안 된 tick 이 PUBLISHED 로 보고된다(Rule 12).
        """
        attempted = 0
        # 반올림하지 않는다 — 내림하면 실제 실패 시각보다 이른 시각이 나와(경과 10.9→10,
        # base 1) backoff 가 사실상 사라지고, 올림하면 설정한 간격에 매번 1초가 얹힌다.
        # 계약은 "**실패한 시점**부터 최소 base*2**n"이다.
        elapsed = timedelta(seconds=time.monotonic() - self._tick_started)
        for event in events:
            # ⚠️ **일시 실패는 횟수로 포기하지 않는다.** 시도 예산을 두면 몇 분짜리 SQS
            # 장애가 멀쩡한 event 를 DEAD 로 만드는데, 큐가 복구돼도 그 행은 스스로
            # 돌아오지 않는다 — 사람이 redrive 를 쳐야 한다(ALPHA-672). 지연은 알람이
            # 잡지만(backlog·oldest-age, PR 7D) 격리는 사람이 봐야 풀린다.
            # DEAD 는 **event 자체가 발행 불가**일 때만이다(미정의 destination·크기 초과·
            # SenderFault) — 그건 재시도해도 결과가 같다.
            # backoff 는 **실패한 시각** 기준이어야 한다 — tick 시작 시각으로 재면 발행이
            # 오래 걸린 경우(예: 10초 timeout, 2초 backoff) next_attempt_at 이 기록 시점에
            # 이미 과거다. 그러면 다음 tick 이 같은 행을 즉시 재claim 하고, claim 이
            # created_at 순이라 batch_limit 이 작을 때 뒤의 정상 event 가 장애 해소까지
            # 한 번도 못 나간다(FIFO 독점).
            # 벽시계 권위는 주입된 now 하나로 두고(가상 시계 원칙), 경과는 monotonic 으로
            # **기간만** 잰다. 초 단위로 절삭해 테스트 결정성을 유지한다.
            # 지수를 **먼저** 제한한다 — 복원·마이그레이션으로 큰 attempt_count 가 들어온
            # 행에서 2**n 이 거대 정수가 되면 기록 전에 MemoryError 가 나고, attempt_count
            # 는 그대로라 lease 만료 때마다 같은 행이 프로세스를 죽인다.
            capped_exponent = min(event["attempt_count"], _MAX_BACKOFF_EXPONENT)
            next_attempt_at = None if terminal else now + elapsed + timedelta(
                seconds=min(
                    self.config.retry_base_seconds * (2 ** capped_exponent),
                    self.config.retry_max_seconds,
                )
            )
            attempted += 1
            if not self.jobs.record_publish_failure(
                event_id=event["event_id"], relay_id=self.config.relay_id,
                claim_token=event["claim_token"], now=now,
                next_attempt_at=next_attempt_at, error=error, terminal=terminal,
            ):
                # claim 상실 — 다른 Relay 가 이 event 를 가져갔다. 기록은 그쪽이 한다.
                logger.warning("실패 기록 거부(claim 상실): %s", event["event_id"])
        return attempted


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
            "(DATA_PIPELINE_MINUTE_RELAY__QUEUE_URLS='{\"<destination>\":\"<url>\"}' 주입)"
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
        ),
    )
    for received in (signal.SIGTERM, signal.SIGINT):
        # 진행 중 batch 를 끊지 않는다 — tick 경계에서 멈춘다(claim 을 쥔 채 죽지 않게)
        signal.signal(received, lambda *_: relay.request_stop())
    logger.info("relay 시작: id=%s 큐 %s", relay.config.relay_id, sorted(options.queue_urls))
    ticks = 0
    partial = 0
    state = "IDLE"
    while max_ticks is None or ticks < max_ticks:
        state = relay.tick(datetime.now(timezone.utc))
        ticks += 1
        partial += state == "PARTIAL"
        if state == "STOPPED":
            logger.info("relay 종료(SIGTERM) — %d tick, PARTIAL %d", ticks, partial)
            # 상주 모드의 SIGTERM 은 정상 종료다. bounded 모드는 배출 게이트라
            # "비운 걸 확인"하지 못한 채 끝난 것이므로 성공으로 보고하지 않는다.
            return 0 if max_ticks is None else 1

        if state == "IDLE":
            time.sleep(options.tick_seconds)
    # bounded 모드(--max-ticks)는 배출 게이트로 쓰인다. IDLE 은 "지금 집을 게 없다"는
    # 뜻일 뿐이라(재시도 대기·타 Relay 점유 행은 claim 에 안 잡힌다) 완료 판정에 쓸 수
    # 없다 — **미발행 행을 실제로 세서** 판정한다.
    remaining = relay.jobs.count_unpublished()
    logger.info(
        "relay 종료(max-ticks %d 도달) — PARTIAL %d, 미발행 NEW %d건·DEAD %d건",
        ticks, partial, remaining["NEW"], remaining["DEAD"],
    )
    if remaining["DEAD"]:
        # DEAD 도 큐에 나간 적이 없다 — 기다려서 풀리지 않으므로 사람이 봐야 한다
        logger.error(
            "격리된 미발행 event %d건 — `run redrive --kind … --job-id … --reason …` 필요",
            remaining["DEAD"],
        )
    return 0 if partial == 0 and not any(remaining.values()) else 1
