"""1분 파이프라인 SQS Consumer 공통 kernel (ALPHA-672, 계획 §12 PR 7A / v0.7 12.4).

뉴스(7B)·가격(7C) Consumer 가 공유하는 골격이다. 무엇을 처리할지(handler)만 구현체가
채우고, **언제 실행해도 되는지·언제 다시 할지·언제 포기할지는 전부 여기서** 정한다.

핵심 계약 하나: **재시도 권위는 PostgreSQL 뿐이다.** SQS delivery 와 receive count 는
wake-up·transport 안전장치·관측값이지 논리 attempt 정책이 아니다(v0.7 12.4 — 재비교
금지). 그래서 이 모듈은 SQS 가 몇 번 줬는지 세지 않는다. 메시지는 "이 job 을 지금 봐라"
는 신호일 뿐이고, 실행 자격은 DB CAS 가 판정한다.

```text
receive
→ DB status/next_attempt_at 확인
  ├─ SUCCEEDED/DEAD: 실행 없이 delete
  ├─ RETRY_WAIT 이고 아직 이른 시각: DB 기준으로 visibility 변경(실행·삭제 없음)
  └─ PENDING 또는 retry eligible:
       DB CAS CLAIMED + attempt_count 증가
       → handler 실행 (진행 중 visibility + DB lease 를 함께 heartbeat)
         ├─ 성공: SUCCEEDED commit → delete
         ├─ transient: RETRY_WAIT + next_attempt_at commit → 그 시각에 맞춰 visibility
         └─ permanent/예산 소진: DEAD commit → delete
```

⚠️ **삭제(ack)는 되돌릴 수 없다.** 그래서 이 모듈은 "판정할 수 있을 때만" 지운다 —
파싱 실패·job 행 없음·큐 배선 오류처럼 **우리가 상황을 모르는** 메시지는 지우지 않고
남긴다. 남기면 maxReceiveCount 가 DLQ 로 보내고(reconciler 가 받는다) 근거가 보존되지만,
지우면 그 자리에서 조용히 사라진다. "관측되지 않음"은 "존재하지 않음"이 아니다.

⚠️ **DEAD 도 근거가 그 job 자체의 성질일 때만이다.** 분류되지 않은 예외는 재시도로
보내고 예산(max_attempts)이 판정한다 — 설정·시간·외부 장애로 나는 예외를 첫 회에
terminal 로 확정하면 그 job 은 원인이 사라진 뒤에도 안 돌아온다. PR 6 과 달리 예산을
두는 근거는 **이 PR 이 redrive 를 함께 만들기 때문**이다(DEAD 가 되돌릴 수 있게 됐다).
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .jobs import EVENT_TYPE_JOB_KINDS, JOB_EVENT_TYPES, JobLedger, build_event_id
from .states import JOB_DEAD, JOB_RETRY_WAIT, JOB_SUCCEEDED

logger = logging.getLogger(__name__)

# SQS 상한 (docs: SQS Developer Guide "Amazon SQS quotas", 2026-08 확인).
# ⚠️ 벤더 계약이라 드리프트한다 — 늘려 잡으면 API 가 요청을 거부하고, 줄여 잡으면
# 멀쩡한 대기 시간이 잘려 DB 가 정한 시각보다 일찍 깨어난다(무해하지만 헛돈다).
SQS_RECEIVE_LIMIT = 10
SQS_MAX_LONG_POLL_SECONDS = 20
SQS_MAX_VISIBILITY_SECONDS = 43_200

# backoff 지수 상한 — 복원·마이그레이션으로 큰 attempt_count 가 들어와도 2**n 이
# 거대 정수가 되지 않게(relay 와 같은 이유·같은 값).
_MAX_BACKOFF_EXPONENT = 30


class JobExecutionError(Exception):
    """handler 가 실패를 **분류해서** 올리는 통로. `code` 가 job.error_code 가 된다."""

    code = "JOB_ERROR"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class TransientJobError(JobExecutionError):
    """일시 실패 — 같은 입력으로 다시 하면 될 수 있다(vendor 429/5xx/timeout)."""

    code = "TRANSIENT"


class PermanentJobError(JobExecutionError):
    """그 job 자체의 결함 — 재시도해도 결과가 같다(입력 불량·계약 위반).

    ⚠️ 여기 해당하는 근거는 **job 의 성질**이어야 한다. 설정 오류·권한·큐 장애처럼
    배포나 시간으로 바뀌는 것은 transient 다 — terminal 로 접으면 원인이 사라진 뒤에도
    그 job 은 스스로 돌아오지 않는다(운영자 redrive 가 유일한 복구 경로가 된다).
    """

    code = "PERMANENT"


@dataclass(frozen=True)
class ConsumerMessage:
    """큐에서 받은 메시지 한 건 — SQS 응답 중 우리가 쓰는 부분만."""

    message_id: str
    receipt_handle: str
    body: str


@dataclass(frozen=True)
class JobDelivery:
    """메시지가 지목한 job. `event_id` 가 그대로 멱등 키다."""

    event_id: str
    event_type: str
    kind: str
    job_id: str
    redrive_generation: int
    payload: dict


def _reject_duplicate_keys(pairs):
    """같은 키가 두 번 나오면 raise — 기본 json.loads 는 **마지막 값으로 조용히 접는다**.

    접히면 `{"event_id": <가짜>, "event_id": <진짜>}` 같은 본문이 계약 검사를 통과해
    엉뚱한 job 을 claim 한다. 전송 경계의 입력이라 관대할 이유가 없다(Rule 12).
    """
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"중복 키: {key!r}")
        seen[key] = value
    return seen


def _reject_json_constant(token: str):
    """NaN/Infinity 거부 — 표준 JSON 이 아니고, NaN 은 어떤 비교도 False 라 하류
    수치 게이트를 조용히 통과한다(canonical_json 의 allow_nan=False 와 같은 규약)."""
    raise ValueError(f"표준 JSON 이 아닌 상수: {token}")


# job_id 는 결정적 sha256 hex 다(v0.7 10.6) — 형상을 강제해야 서로게이트·제어문자 같은
# 이상 문자열이 DB 파라미터 인코딩 단계까지 내려가 tick 을 반복해서 죽이지 않는다.
_JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def parse_delivery(body: str) -> JobDelivery:
    """Relay 가 실은 envelope(`relay.build_message_body`)을 되읽는다.

    미지 키·틀린 타입·형식 밖 event_id 는 즉시 raise 한다 — 조용히 기본값을 넣으면
    엉뚱한 job 을 실행하거나(같은 큐의 다른 사건) 없는 job 을 만들어낸다. 호출자는 이
    실패를 poison 으로 세고 메시지를 **남긴다**(DLQ 가 근거를 보존한다).

    ⚠️ 여기서 나가는 예외는 **ValueError 하나로 모은다** — 호출자가 그것만 잡아
    격리하므로, 다른 예외 종류가 새면 메시지 한 건이 tick 을 통째로 죽이고 뒤의
    멀쩡한 메시지까지 막는다.
    """
    try:
        event = json.loads(
            body, object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except ValueError as error:
        raise ValueError(f"메시지 본문이 유효한 JSON 이 아니다: {error}") from error
    if not isinstance(event, dict):
        raise ValueError(f"메시지 본문이 객체가 아니다: {type(event).__name__}")
    expected = {"event_id", "event_type", "payload"}
    if event.keys() != expected:
        raise ValueError(
            f"envelope 필드 계약 위반 — 누락: {sorted(expected - event.keys())}, "
            f"미지: {sorted(event.keys() - expected)}"
        )
    event_id, event_type, payload = (
        event["event_id"], event["event_type"], event["payload"]
    )
    if not isinstance(payload, dict):
        raise ValueError(f"payload 가 객체가 아니다: {type(payload).__name__}")
    # 타입 검사가 **조회보다 먼저**다 — dict.get 에 리스트를 넣으면 TypeError 가 나고
    # 그건 ValueError 가 아니라 호출자의 격리를 빠져나간다.
    if not isinstance(event_type, str):
        raise ValueError(f"event_type 이 문자열이 아니다: {type(event_type).__name__}")
    if not isinstance(event_id, str):
        raise ValueError(f"event_id 가 문자열이 아니다: {type(event_id).__name__}")
    kind = EVENT_TYPE_JOB_KINDS.get(event_type)
    if kind is None:
        raise ValueError(f"정의되지 않은 event_type: {event_type!r}")
    parts = event_id.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        raise ValueError(f"event_id 형식이 아니다: {event_id!r}")
    job_id, generation = parts[1], int(parts[2])
    # 재조립해 **정확히** 대조한다 — 접두 event_type 불일치나 "007" 같은 비정규 표기를
    # 통과시키면 같은 job 이 서로 다른 event_id 로 두 번 살아난다(멱등 키가 깨진다).
    if not _JOB_ID_PATTERN.match(job_id):
        raise ValueError(f"job_id 가 sha256 hex 형상이 아니다: {job_id!r}")
    if build_event_id(event_type, job_id, generation) != event_id:
        raise ValueError(f"event_id 가 유도식과 다르다: {event_id!r}")
    return JobDelivery(
        event_id=event_id, event_type=event_type, kind=kind, job_id=job_id,
        redrive_generation=generation, payload=payload,
    )


class SqsQueue:
    """SQS 접근 — boto3 는 지연 import (`ops/aws.py` 관례, SDK 자체 재시도 off).

    Relay 의 `SqsPublisher` 와 같은 이음매다: 실 AWS 호출은 여기 한 곳이고 kernel 은
    이 인터페이스만 본다(테스트는 fake 를 끼운다).
    """

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):  # pragma: no cover - 실 AWS 경로
        if self._client is None:
            from ..ops.aws import sqs_client

            self._client = sqs_client()
        return self._client

    def receive(
        self, *, queue_url: str, max_messages: int, wait_seconds: int,
        visibility_seconds: int,
    ) -> tuple[ConsumerMessage, ...]:
        response = self.client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,          # long polling
            VisibilityTimeout=visibility_seconds,
        )
        messages = []
        for entry in response.get("Messages", ()):
            handle, body = entry.get("ReceiptHandle"), entry.get("Body")
            if not isinstance(handle, str) or not handle or not isinstance(body, str):
                # ack 할 수 없는 메시지 = 우리가 다룰 수 없다. 크게 남기고 넘긴다 —
                # 조용히 빈 본문으로 만들면 그 자리에서 job 이 사라진다.
                logger.error("SQS 응답에 ReceiptHandle/Body 가 없다: %r", entry.get("MessageId"))
                continue
            messages.append(ConsumerMessage(
                message_id=str(entry.get("MessageId") or ""),
                receipt_handle=handle, body=body,
            ))
        return tuple(messages)

    def delete(self, *, queue_url: str, receipt_handle: str) -> None:
        self.client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)

    def change_visibility(
        self, *, queue_url: str, receipt_handle: str, seconds: int
    ) -> None:
        self.client.change_message_visibility(
            QueueUrl=queue_url, ReceiptHandle=receipt_handle, VisibilityTimeout=seconds
        )


@dataclass
class ConsumerConfig:
    consumer_id: str
    kind: str          # news | price — 이 Consumer 가 보는 job 테이블
    queue_url: str
    batch_size: int = 1
    wait_seconds: int = 20                 # long polling — 빈 응답 폭주를 막는다
    visibility_seconds: int = 300
    heartbeat_seconds: int = 60
    max_concurrency: int = 1
    lease_seconds: int = 600
    retry_base_seconds: int = 5
    retry_max_seconds: int = 900
    # 논리 retry 예산. SQS maxReceiveCount 는 이보다 넉넉한 transport 상한으로 둔다
    # (v0.7 12.4) — 반대로 두면 transport 가 먼저 포기해 DB 예산이 의미를 잃는다.
    max_attempts: int = 5

    def __post_init__(self) -> None:
        if self.kind not in JOB_EVENT_TYPES:
            raise ValueError(f"kind 는 {sorted(JOB_EVENT_TYPES)} 중 하나다: {self.kind!r}")
        for name in ("consumer_id", "queue_url"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} 은 비어 있지 않은 문자열이어야 한다: {value!r}")
        # **타입부터 강제한다.** 아래 범위 비교는 float 을 통과시키는데, NaN 은 모든
        # 비교가 False 라 `max_attempts=nan` 이면 예산 검사(`attempt >= max_attempts`)가
        # 영원히 거짓이 돼 **재시도 예산이 통째로 사라지고**, 소수 batch_size 는 botocore
        # 가 첫 receive 에서 거부해 Consumer 가 기동 즉시 죽는다(설정이 그대로면 재기동
        # 해도 같은 자리다). bool 은 int 의 하위형이라 명시적으로 뺀다.
        for name in ("batch_size", "wait_seconds", "visibility_seconds",
                     "heartbeat_seconds", "max_concurrency", "lease_seconds",
                     "retry_base_seconds", "retry_max_seconds", "max_attempts"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} 은 정수여야 한다: {value!r}")
        if not 1 <= self.batch_size <= SQS_RECEIVE_LIMIT:
            raise ValueError(f"batch_size 는 1..{SQS_RECEIVE_LIMIT} 이다")
        if not 0 <= self.wait_seconds <= SQS_MAX_LONG_POLL_SECONDS:
            raise ValueError(f"wait_seconds 는 0..{SQS_MAX_LONG_POLL_SECONDS} 이다")
        if not 1 <= self.visibility_seconds <= SQS_MAX_VISIBILITY_SECONDS:
            raise ValueError(f"visibility_seconds 는 1..{SQS_MAX_VISIBILITY_SECONDS} 이다")
        if self.heartbeat_seconds < 1 or self.heartbeat_seconds * 2 > self.visibility_seconds:
            # 만료 전에 최소 한 번은 연장이 돌아야 한다 — 아니면 heartbeat 가 있으나
            # 마나가 돼 긴 job 의 메시지가 재배달되고 같은 LLM 호출이 중복된다.
            raise ValueError(
                f"heartbeat_seconds({self.heartbeat_seconds}) × 2 가 "
                f"visibility_seconds({self.visibility_seconds}) 를 넘는다 — "
                "만료 전에 연장이 한 번도 못 돈다"
            )
        if self.lease_seconds < self.visibility_seconds:
            # DB lease 가 delivery 창보다 짧으면, 메시지가 아직 안 보이는 동안 job 만
            # eligible 이 돼 중복 실행 여지가 열린다(lease 가 "누가 하고 있다"의 근거).
            raise ValueError(
                f"lease_seconds({self.lease_seconds}) 가 "
                f"visibility_seconds({self.visibility_seconds}) 보다 짧다"
            )
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency 는 1 이상이다")
        if self.lease_seconds > SQS_MAX_VISIBILITY_SECONDS:
            # 상한을 둔다 — 거대한 값은 timedelta 연산에서 터지고(OverflowError) 설정이
            # 그대로면 재기동해도 같은 자리에서 죽는다(relay lease 와 같은 이유).
            raise ValueError(f"lease_seconds 는 {SQS_MAX_VISIBILITY_SECONDS} 이하다")
        if self.retry_base_seconds < 1 or self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry_base_seconds <= retry_max_seconds 여야 하고 둘 다 양수다")
        if self.retry_max_seconds > SQS_MAX_VISIBILITY_SECONDS:
            raise ValueError(f"retry_max_seconds 는 {SQS_MAX_VISIBILITY_SECONDS} 이하다")
        if self.max_attempts < 1:
            raise ValueError("max_attempts 는 1 이상이다")


@dataclass(frozen=True)
class _Claimed:
    """claim 까지 끝난 메시지 — 실행 스레드에 넘어가는 단위."""

    message: ConsumerMessage
    delivery: JobDelivery
    attempt: int


@dataclass
class MinuteConsumer:
    """tick 을 외부(엔트리포인트/테스트)가 돌리는 수동 루프 — sleep 은 호출자 소관.

    `handler(job_id=..., payload=..., attempt=...) -> result_checksum` 만 구현체가
    준다. handler 는 성공하면 결과 checksum 문자열을 돌려주고, 실패는
    `TransientJobError`/`PermanentJobError` 로 **분류해서** 올린다.
    """

    jobs: JobLedger
    queue: object   # SqsQueue 인터페이스(receive/delete/change_visibility)
    handler: Callable[..., str]
    config: ConsumerConfig
    stopping: bool = False   # SIGTERM — 새 receive 를 멈추고 in-flight 는 끝낸다
    # tick 안의 **경과 시간**만 재는 기준점(벽시계 아님) — retry 시각·lease 연장이
    # "지금"을 기준으로 잡히게. 벽시계 권위는 주입된 now 하나다(가상 시계 원칙).
    _tick_started: float = field(default=0.0, repr=False)
    _pool: ThreadPoolExecutor = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=self.config.max_concurrency, thread_name_prefix="minute-consumer"
        )

    def request_stop(self) -> None:
        self.stopping = True

    def close(self) -> None:
        """in-flight 를 끝까지 기다린다 — 중간에 끊으면 실행은 했는데 기록이 없다."""
        self._pool.shutdown(wait=True)

    # ── tick ─────────────────────────────────────────────────
    def tick(self, now: datetime) -> Counter:
        """한 사이클. 반환은 관측용 카운터(키가 곧 판정 어휘)."""
        self._tick_started = time.monotonic()
        if self.stopping:
            # 새 receive 를 하지 않는다. 직전 tick 의 in-flight 는 그 tick 안에서 끝났다.
            return Counter(stopped=1)
        messages = self.queue.receive(
            queue_url=self.config.queue_url, max_messages=self.config.batch_size,
            wait_seconds=self.config.wait_seconds,
            visibility_seconds=self.config.visibility_seconds,
        )
        if self.stopping:
            # long polling(최대 20초) 중에 SIGTERM 이 왔다 — 받아 둔 메시지로 **새 작업을
            # 시작하지 않는다**(종료 유예 직전에 LLM 호출을 걸면 강제 종료로 CLAIMED
            # lease 만 남는다). 즉시 다시 보이게 돌려주고 다음 Consumer 가 집게 한다.
            for message in messages:
                self._change_visibility(message, 0)
            return Counter(stopped=1, released=len(messages))
        if not messages:
            return Counter(idle=1)
        return self._run(messages, now)

    def _run(self, messages, now: datetime) -> Counter:
        counts = Counter(received=len(messages))
        # claim 은 **본 스레드에서 먼저** 끝낸다 — 실행 스레드가 각자 claim 하면
        # heartbeat 가 "무엇을 연장해야 하는지"를 경합 없이 알 수 없다.
        running: dict[object, _Claimed] = {}
        last_heartbeat = time.monotonic()
        for message in messages:
            prepared = self._prepare(message, now)
            if isinstance(prepared, str):
                counts[prepared] += 1
            else:
                running[self._pool.submit(self._execute, prepared, now)] = prepared
            # 배치가 크고 원장 왕복이 느리면 이 루프만으로 앞서 제출한 job 의 lease·
            # visibility 가 만료된다 — 아래 heartbeat 루프에 들어가기 전에도 민다.
            if running and time.monotonic() - last_heartbeat >= self.config.heartbeat_seconds:
                self._heartbeat_running(running, now)
                last_heartbeat = time.monotonic()
        pending = set(running)
        try:
            while pending:
                done, pending = wait(pending, timeout=self.config.heartbeat_seconds)
                for future in pending:
                    # 아직 안 끝난 것 = 실행 중이거나 pool 대기열에 있는 것. 둘 다 claim 은
                    # 이미 잡혀 있으므로 visibility 와 lease 를 함께 민다.
                    self._heartbeat(running[future], now)
                for future in done:
                    counts[future.result()] += 1
        finally:
            if pending:
                # 한 future 의 예외로 여기 왔다. 남은 실행은 claim 을 쥔 채 pool 에서
                # 계속 도는데, 그냥 빠져나가면 아무도 lease 를 연장하지 않고 다음 tick 이
                # _tick_started 를 덮어 기록 시각까지 어긋난다. 끝날 때까지 기다린다 —
                # 결과 집계는 잃지만 원장은 자기 손으로 마감한다.
                logger.error("tick 이 예외로 중단됐다 — 실행 중 job %d건을 마저 기다린다",
                             len(pending))
                wait(pending)
        return counts

    def _heartbeat_running(self, running: dict, now: datetime) -> None:
        for future, claimed in running.items():
            if not future.done():
                self._heartbeat(claimed, now)

    # ── 메시지 한 건 ──────────────────────────────────────────
    def _prepare(self, message: ConsumerMessage, now: datetime) -> _Claimed | str:
        """실행 자격 판정 + claim. 실행에 못 가면 판정 어휘(문자열)를 돌려준다."""
        try:
            delivery = parse_delivery(message.body)
        except ValueError:
            # 지우지 않는다 — 우리가 못 읽었을 뿐이라 근거를 남긴다. maxReceiveCount 가
            # DLQ 로 보내고 거기서 사람이 본다(reconciler 도 같은 이유로 못 지운다).
            logger.exception("메시지 파싱 실패 — 삭제하지 않는다: %s", message.message_id)
            return "poison"
        if delivery.kind != self.config.kind:
            # 이 큐에 올 수 없는 사건이 왔다 = 배선(destination↔큐) 오류다. 실행하면
            # 엉뚱한 테이블을 보고, 지우면 멀쩡한 job 이 사라진다.
            logger.error(
                "kind 불일치 — 이 Consumer 는 %s 인데 %s 메시지가 왔다: %s",
                self.config.kind, delivery.kind, delivery.event_id,
            )
            return "misrouted"
        job = self.jobs.fetch_job(kind=delivery.kind, job_id=delivery.job_id)
        if job is None:
            # job 과 outbox event 는 같은 트랜잭션에서 쓰인다(PR 2C) — 없다는 건 그
            # 계약이 깨졌거나 다른 DB 를 보고 있다는 뜻이다. 삭제는 근거 인멸이다.
            logger.error("메시지가 가리키는 job 행이 없다: %s", delivery.event_id)
            return "orphan"
        if delivery.redrive_generation < job["redrive_generation"]:
            # 운영자가 redrive 했다 — 더 높은 세대의 event 가 같은 트랜잭션에 이미
            # 들어가 있으므로(durable) 이 메시지는 낡았다. 남기면 매 배달마다 같은
            # 판정을 반복하고 결국 DLQ 를 오염시킨다.
            self._delete(message)
            return "superseded"
        if delivery.redrive_generation > job["redrive_generation"]:
            # 메시지가 job 보다 앞선다 = 원자 동기화가 깨졌다는 신호. 실행도 삭제도
            # 하지 않고 드러낸다(Rule 12).
            logger.error(
                "메시지 세대(%d)가 job(%d)보다 높다: %s",
                delivery.redrive_generation, job["redrive_generation"], delivery.event_id,
            )
            return "ahead"
        if job["status"] in (JOB_SUCCEEDED, JOB_DEAD):
            # 이미 끝난 일 — 중복 배달이다. 실행하지 않고 ack (v0.7 12.4).
            self._delete(message)
            return "terminal"
        # 시각은 **한 번만** 읽는다 — tick 시작 시각으로 비교하면 long polling(최대
        # 20초)과 앞선 메시지 처리에 쓴 시간이 통째로 무시돼, 이미 재시도 시각에 닿은
        # job 을 deferred 로 분류하고 그만큼 또 미룬다(DB 가 정한 시각보다 늦어진다).
        moment = self._now(now)
        if (
            job["status"] == JOB_RETRY_WAIT
            and job["next_attempt_at"] is not None
            and job["next_attempt_at"] > moment
        ):
            # **DB 가 정한 시각**까지 안 깨운다 — 여기서 실행하면 재시도 간격이
            # SQS 배달 주기로 바뀌어 DB 의 retry 권위가 사라진다(LLM 호출 0).
            self._change_visibility(
                message, (job["next_attempt_at"] - moment).total_seconds()
            )
            return "deferred"
        claim = self.jobs.claim_job(
            kind=delivery.kind, job_id=delivery.job_id,
            redrive_generation=delivery.redrive_generation,
            worker_id=self.config.consumer_id, now=moment,
            lease_seconds=self.config.lease_seconds,
        )
        if claim == "STALE":
            # window 가 이미 다음 세대로 정정됐다 — 이 job 은 DEAD('STALE') 로 격리됐고
            # correction commit 이 만든 새 job/event 가 대신 돈다(v0.7 10.5).
            self._delete(message)
            return "stale"
        if claim is None:
            # 다른 Consumer 가 쥐고 있거나 그새 상태가 바뀌었다. 지우지 않고 두면
            # visibility 만료 뒤 다시 판정한다(그때 terminal 이면 그때 지운다).
            return "contended"
        claimed = _Claimed(
            message=message, delivery=delivery, attempt=claim["attempt_count"]
        )
        if claim["attempt_count"] > self.config.max_attempts:
            # 예산을 **실행 전에** 본다. 실패 뒤에만 보면, 외부 호출 후 기록 전에
            # 죽는 패턴이 반복될 때 attempt 는 오르는데 handler 는 계속 불려 DB 예산이
            # 아무것도 막지 못한다(실질 상한이 SQS maxReceiveCount 로 넘어간다).
            logger.error(
                "job %s 가 예산(%d회)을 넘긴 attempt %d 로 claim 됐다 — 실행 없이 DEAD",
                delivery.job_id, self.config.max_attempts, claim["attempt_count"],
            )
            return self._finish_dead(claimed, "RETRY_BUDGET_EXHAUSTED", now)
        return claimed

    def _execute(self, claimed: _Claimed, now: datetime) -> str:
        delivery = claimed.delivery
        try:
            checksum = self.handler(
                job_id=delivery.job_id, payload=delivery.payload, attempt=claimed.attempt,
            )
        except PermanentJobError as error:
            logger.error("job %s permanent 실패: %s", delivery.job_id, error)
            return self._finish_dead(claimed, error.code, now)
        except TransientJobError as error:
            logger.warning("job %s transient 실패: %s", delivery.job_id, error)
            return self._finish_retry(claimed, error.code, now)
        except Exception:
            # 분류되지 않은 예외 — 되돌릴 수 없는 DEAD 로 확정할 근거가 없다(설정·
            # 네트워크·일시 장애가 여기로 온다). 재시도로 보내고 예산이 판정한다.
            logger.exception("job %s 처리 중 미분류 예외", delivery.job_id)
            return self._finish_retry(claimed, "UNCLASSIFIED", now)
        if not isinstance(checksum, str) or not checksum.strip():
            # handler 계약 위반 — 같은 코드가 다시 같은 값을 돌려준다(재시도 무의미).
            logger.error(
                "handler 가 result_checksum 문자열을 돌려주지 않았다: %r", checksum
            )
            return self._finish_dead(claimed, "RESULT_CONTRACT", now)
        if not self.jobs.succeed_job(
            kind=delivery.kind, job_id=delivery.job_id,
            worker_id=self.config.consumer_id, attempt=claimed.attempt,
            redrive_generation=delivery.redrive_generation,
            now=self._now(now), result_checksum=checksum,
        ):
            # claim 상실 뒤의 늦은 보고 — 지금 이 job 은 남의 것이다. 메시지를 지우면
            # 새 소유자의 wake-up 까지 함께 지운다.
            logger.warning("성공 기록 거부(claim 상실) — 삭제하지 않는다: %s", delivery.job_id)
            return "lost"
        self._delete(claimed.message)
        return "succeeded"

    def _finish_retry(self, claimed: _Claimed, error_code: str, now: datetime) -> str:
        if claimed.attempt >= self.config.max_attempts:
            # 예산 소진 — 계속 두면 같은 실패를 무한히 반복한다. redrive 가 있으므로
            # 이 DEAD 는 되돌릴 수 있다(PR 6 의 outbox DEAD 와 다른 점).
            logger.error(
                "job %s 재시도 예산 소진(%d회) — DEAD",
                claimed.delivery.job_id, claimed.attempt,
            )
            return self._finish_dead(claimed, "RETRY_BUDGET_EXHAUSTED", now)
        # 실패한 **시각** 기준 지수 backoff — tick 시작 시각으로 재면 오래 걸린 실패의
        # next_attempt_at 이 기록 시점에 이미 과거가 되고, 그러면 즉시 재배달이 돈다.
        delay = min(
            self.config.retry_base_seconds
            * 2 ** min(claimed.attempt - 1, _MAX_BACKOFF_EXPONENT),
            self.config.retry_max_seconds,
        )
        moment = self._now(now)
        if not self.jobs.retry_job(
            kind=claimed.delivery.kind, job_id=claimed.delivery.job_id,
            worker_id=self.config.consumer_id, attempt=claimed.attempt,
            redrive_generation=claimed.delivery.redrive_generation, now=moment,
            next_attempt_at=moment + timedelta(seconds=delay), error_code=error_code,
        ):
            logger.warning(
                "재시도 기록 거부(claim 상실): %s", claimed.delivery.job_id
            )
            return "lost"
        # DB 가 정한 시각과 visibility 를 **맞춘다** — 짧으면 이른 배달이 헛돌고,
        # 길면 재시도가 그만큼 늦어진다(권위는 어디까지나 DB 쪽 값이다).
        self._change_visibility(claimed.message, delay)
        return "retried"

    def _finish_dead(self, claimed: _Claimed, error_code: str, now: datetime) -> str:
        if not self.jobs.dead_job(
            kind=claimed.delivery.kind, job_id=claimed.delivery.job_id,
            worker_id=self.config.consumer_id, attempt=claimed.attempt,
            redrive_generation=claimed.delivery.redrive_generation,
            now=self._now(now), error_code=error_code,
        ):
            logger.warning("DEAD 기록 거부(claim 상실): %s", claimed.delivery.job_id)
            return "lost"
        self._delete(claimed.message)
        return "dead"

    # ── SQS 부수효과 (실패해도 DB 판정이 권위) ─────────────────
    def _heartbeat(self, claimed: _Claimed, now: datetime) -> None:
        if not self.jobs.heartbeat_job(
            kind=claimed.delivery.kind, job_id=claimed.delivery.job_id,
            worker_id=self.config.consumer_id, attempt=claimed.attempt,
            redrive_generation=claimed.delivery.redrive_generation,
            now=self._now(now), lease_seconds=self.config.lease_seconds,
        ):
            # lease 를 뺏겼거나 **그새 끝났다**. 끊지는 않는다(중간에 죽이면 외부
            # 부수효과가 미완으로 남는다). visibility 도 **밀지 않는다** — 방금 끝난
            # 실행이 DB 가 정한 재시도 시각에 맞춰 놓은 visibility 를 여기서 덮으면
            # 그 재시도가 몇 분 밀린다(DB 가 권위라는 계약이 무너진다).
            logger.warning("lease 연장 거부 — 이 실행의 기록은 거부된다: %s",
                           claimed.delivery.job_id)
            return
        self._change_visibility(claimed.message, self.config.visibility_seconds)

    def _delete(self, message: ConsumerMessage) -> None:
        try:
            self.queue.delete(
                queue_url=self.config.queue_url, receipt_handle=message.receipt_handle
            )
        except Exception:
            # ack 실패는 재배달을 뜻할 뿐이다(DB 가 이미 terminal 이라 다음 배달은
            # 실행 없이 지운다). 조용히 넘기지 않되 판정을 뒤집지도 않는다.
            logger.exception("메시지 삭제 실패 — 재배달된다: %s", message.message_id)

    def _change_visibility(self, message: ConsumerMessage, seconds: float) -> None:
        # SQS 상한을 넘기면 요청 자체가 거부돼 **연장이 통째로 안 걸린다** — 자른다.
        bounded = max(0, min(math.ceil(seconds), SQS_MAX_VISIBILITY_SECONDS))
        try:
            self.queue.change_visibility(
                queue_url=self.config.queue_url,
                receipt_handle=message.receipt_handle, seconds=bounded,
            )
        except Exception:
            logger.exception("visibility 변경 실패: %s", message.message_id)

    def _now(self, now: datetime) -> datetime:
        """tick 시작 시각 + 그동안 흐른 시간 — retry 시각·lease 가 실제 '지금' 기준이 되게."""
        return now + timedelta(seconds=time.monotonic() - self._tick_started)


@dataclass
class DlqReconciler:
    """DLQ 에 도착한 메시지와 DB job 상태를 대사한다 (v0.7 12.4).

    메시지가 DLQ 에 있다 = transport 예산(maxReceiveCount)이 먼저 소진됐다는 뜻이다.
    그런데 DB job 이 아직 non-terminal 이면 원장과 실제가 갈린 것이라, `SQS_MAX_RECEIVE`
    사유의 DEAD 로 수렴시킨다 — 그래야 운영자가 조회하고 redrive 할 수 있다.

    ⚠️ **메시지를 지우지 않는다.** 지우면 근거가 사라지고, 남기면 보존기간까지 남아
    사람이 볼 수 있다. 판정은 멱등이라(이미 DEAD 면 CAS no-op) 반복 실행이 안전하고,
    이번에 못 죽인 것(살아 있는 lease)은 **다음 실행**(주기 잡)이 수렴시킨다 — 한 실행
    안에서는 이미 판정한 메시지를 다시 세지 않는다(안 그러면 재수신이 종료를 막는다).
    """

    jobs: JobLedger
    queue: object
    queue_urls: Mapping[str, str]   # destination -> DLQ URL
    batch_size: int = 10
    wait_seconds: int = 20
    visibility_seconds: int = 60
    stopping: bool = False
    # 이번 실행에서 이미 판정한 메시지 — 지우지 않으므로 visibility 가 풀리면 같은
    # 메시지가 다시 온다. 그걸 "새 일감"으로 세면 종료 조건(받은 게 없다)이 영원히
    # 성립하지 않아 일회성 명령이 안 끝난다. MessageId 는 배달이 반복돼도 같다.
    seen: set = field(default_factory=set)

    def request_stop(self) -> None:
        self.stopping = True

    def tick(self, now: datetime) -> Counter:
        counts: Counter = Counter()
        if self.stopping:
            return Counter(stopped=1)
        for _destination, queue_url in sorted(self.queue_urls.items()):
            messages = self.queue.receive(
                queue_url=queue_url, max_messages=self.batch_size,
                wait_seconds=self.wait_seconds,
                visibility_seconds=self.visibility_seconds,
            )
            for message in messages:
                counts["received"] += 1
                if message.message_id in self.seen:
                    counts["repeat"] += 1
                    continue
                self.seen.add(message.message_id)
                counts[self._reconcile(message, now)] += 1
        return counts

    def _reconcile(self, message: ConsumerMessage, now: datetime) -> str:
        try:
            delivery = parse_delivery(message.body)
        except ValueError:
            # DLQ 의 poison 은 여기서 판정할 수 없다 — 어느 job 인지 모른다.
            logger.exception("DLQ 메시지 파싱 실패: %s", message.message_id)
            return "poison"
        job = self.jobs.fetch_job(kind=delivery.kind, job_id=delivery.job_id)
        if job is None:
            logger.error("DLQ 메시지가 가리키는 job 행이 없다: %s", delivery.event_id)
            return "orphan"
        if delivery.redrive_generation != job["redrive_generation"]:
            # 세대가 다르면 이 메시지는 그 job 의 **현재** 배달이 아니다. 낡은 배달로
            # 살아 있는 세대를 죽이면 운영자의 redrive 가 통째로 무효가 된다.
            return (
                "superseded" if delivery.redrive_generation < job["redrive_generation"]
                else "ahead"
            )
        if job["status"] in (JOB_SUCCEEDED, JOB_DEAD):
            return "terminal"
        if self.jobs.dead_on_dlq(
            kind=delivery.kind, job_id=delivery.job_id,
            redrive_generation=delivery.redrive_generation, now=now,
        ):
            logger.error(
                "DLQ 도착 + DB non-terminal(%s) → DEAD(SQS_MAX_RECEIVE): %s",
                job["status"], delivery.event_id,
            )
            return "dead"
        # 살아 있는 lease(실행 중)거나 그새 상태가 바뀌었다 — 다음 회차가 본다.
        return "deferred"


def _resolve_queue_urls(settings) -> Mapping[str, str]:
    """DLQ 매핑을 읽고 **원 큐와 겹치지 않는지** 확인한다.

    DLQ 자리에 원 큐 URL 이 들어가면 reconciler 가 정상 배달 중인 메시지를 전부
    "DLQ 도착"으로 읽어 **살아 있는 job 을 몰살**한다. 되돌리는 데 job 수만큼의 redrive
    가 필요한 사고다.

    그래서 relay 큐 매핑을 **함께 요구한다.** "있으면 검사"로 두면 그 설정이 없는
    프로세스에서 검사가 통째로 꺼지는데, 그건 사고가 나는 바로 그 상황(원 큐 URL 을
    DLQ 자리에 붙여넣은 일회성 실행)이다. 같은 환경의 같은 형태 env 라 요구 비용은 낮다.
    """
    if settings.minute_consumer is None:
        raise SystemExit(
            "minute_consumer 설정 없음 — dlq-reconcile 은 destination→DLQ 매핑 필수"
            '(DATA_PIPELINE_MINUTE_CONSUMER__DLQ_URLS=\'{"<destination>":"<url>"}\')'
        )
    if settings.minute_relay is None:
        raise SystemExit(
            "minute_relay 설정 없음 — dlq-reconcile 은 원 큐 매핑도 필요하다"
            "(DLQ 자리에 원 큐가 들어갔는지 대조해야 살아 있는 job 몰살을 막는다)"
        )
    dlq_urls = dict(settings.minute_consumer.dlq_urls)
    shared = sorted(set(dlq_urls.values()) & set(settings.minute_relay.queue_urls.values()))
    if shared:
        raise SystemExit(
            f"DLQ 매핑이 원 큐와 같은 URL 을 가리킨다: {shared} — "
            "정상 배달 중인 job 이 전부 DEAD 가 된다"
        )
    return dlq_urls


def dlq_reconcile_cli(settings, *, max_ticks: int | None = None) -> int:
    """DLQ 대사 진입점 — `python -m data_pipeline.run dlq-reconcile`.

    주기 실행용이다(상주 아님): 보이는 메시지를 다 훑으면 끝난다. 지우지 않으므로
    같은 메시지를 다음 실행이 다시 보지만 판정은 멱등이다.
    """
    import signal
    from datetime import timezone

    if settings.db is None:
        raise SystemExit("db 설정 없음 — dlq-reconcile 은 job 원장 필수(DATA_PIPELINE_DB__* 주입)")
    # 설정 검증을 **먼저** 끝낸다 — 인자 평가 순서에 기대면(뒤 인자에서 None 참조)
    # 리팩터링 한 번에 AttributeError 로 바뀐다.
    queue_urls = _resolve_queue_urls(settings)
    options = settings.minute_consumer
    reconciler = DlqReconciler(
        jobs=JobLedger(db=settings.db), queue=SqsQueue(), queue_urls=queue_urls,
        batch_size=options.batch_size, wait_seconds=options.wait_seconds,
        visibility_seconds=options.visibility_seconds,
    )
    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, lambda *_: reconciler.request_stop())
    totals: Counter = Counter()
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        counts = reconciler.tick(datetime.now(timezone.utc))
        ticks += 1
        totals += counts
        # **새로 본 메시지**가 없으면 끝난다. received 로 판정하면, 지우지 않은 메시지가
        # visibility 만료로 다시 보이는 순간 종료 조건이 영원히 성립하지 않는다.
        if counts["stopped"] or counts["received"] == counts["repeat"]:
            break
    logger.info("dlq-reconcile 종료 — %d tick, %s", ticks, dict(sorted(totals.items())))
    # 판정 불가(poison·orphan·ahead)는 사람이 봐야 한다 — 0 으로 끝내면 아무도 모른다.
    unresolved = totals["poison"] + totals["orphan"] + totals["ahead"]
    if unresolved:
        logger.error("판정하지 못한 DLQ 메시지 %d건 — 사람이 봐야 한다", unresolved)
    return 1 if unresolved else 0


def redrive_cli(settings, *, kind: str, job_id: str) -> int:
    """DB-first redrive 진입점 — `python -m data_pipeline.run redrive --kind news --job-id X`.

    콘솔에서 DLQ 메시지만 blind redrive 하지 않는다(v0.7 12.4): 여기서 DB 를 먼저
    되돌리고 새 delivery event 를 남기면 Relay 가 그걸 발행한다. 기존 DLQ 메시지는
    옮기지 않는다 — 세대가 낡아 Consumer 가 superseded 로 버린다.
    """
    if settings.db is None:
        raise SystemExit("db 설정 없음 — redrive 는 job 원장 필수(DATA_PIPELINE_DB__* 주입)")
    if kind not in JOB_EVENT_TYPES:
        raise SystemExit(f"--kind 는 {sorted(JOB_EVENT_TYPES)} 중 하나다: {kind!r}")
    from datetime import timezone

    try:
        event_id = JobLedger(db=settings.db).redrive_job(
            kind=kind, job_id=job_id, now=datetime.now(timezone.utc)
        )
    except (LookupError, ValueError) as error:
        raise SystemExit(f"redrive 중단: {error}") from error
    logger.info("redrive 완료 — 새 delivery event: %s", event_id)
    print(event_id)
    return 0
