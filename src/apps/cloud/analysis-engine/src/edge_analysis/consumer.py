"""분봉 트리거 큐(price-explanation-realtime) 상주 소비자 (ALPHA-719).

data-pipeline 의 `MinuteConsumer` 커널을 **쓰지 않는다** — 이 큐는 job 테이블이 없는
사건(`TRIGGER_EVENT_DESTINATIONS`)이라 커널의 DB-authoritative 전제(claim·attempt fence)가
성립하지 않고, DLQ 대사 어휘에서도 명시적으로 빠져 있다. 여기의 권위는 둘뿐이다:

- **멱등의 권위 = explanation_run 존재**(`has_run_for_route`). run_id 재료에 벽시계
  (`explanation_as_of`)가 들어가 재배달마다 run·result 행이 늘고 LLM 이 재과금되므로,
  trigger_id 에서 결정적으로 유도한 route id 로 선판정한다.
- **재시도의 권위 = SQS**(visibility·maxReceiveCount·DLQ). 실패 메시지는 지우지 않고
  가시성 만료로 재배달되다 상한에서 DLQ 로 격리된다 — 근거 보존.

실패 분류는 셋이고 처방이 다르다:

- `ReturnsNotReadyError` — 장중이라 당일 `price_daily` 가 아직 없다. 코드 결함이 아니라
  **시간이 낫게 하는** 실패다 → 가시성을 길게 연장해(기본 30분) 15:40 배치 뒤에 다시 본다.
- 봉투 계약 위반(파싱 불가·미지 event_type·trigger_id 결손) — 재시도로 낫지 않는다.
  **지우지도 않는다**(지우면 근거가 사라진다) → 짧은 재배달을 반복하다 DLQ 로 간다.
- 그 외 예외(DB·LLM·일시 장애) — 기본 가시성으로 재배달. 예산 판정은 SQS 상한 몫이다.
"""

from __future__ import annotations

import json
import logging
import signal

from .adapters.eventstore import EventStore, minute_route_id
from .adapters.lake import LakeReader, make_s3_client
from .adapters.llm import DeepSeekClient
from .config import ReturnsNotReadyError, load_settings
from .observability import log
from .pipeline import run

logger = logging.getLogger(__name__)

TRIGGER_EVENT_TYPE = "PriceTriggerFired"  # data-pipeline jobs.py 와 같은 문자열(와이어 계약)
WAIT_SECONDS = 20            # long polling — 빈 응답 폭주 방지
RETURNS_RETRY_SECONDS = 1800  # ReturnsNotReady — 15:40 배치를 기다리는 지연 재배달
# 처리 시작 전 이 값으로 가시성을 한 번 연장한다 — 큐 기본(300초)은 LLM 다회 호출
# (호출당 재시도 포함 수분)을 못 덮어, 처리 중 재배달된 메시지가 프리플라이트(아직
# run 없음)를 통과해 **같은 트리거에 LLM 을 이중 과금**한다. 주기 heartbeat 스레드는
# 이 물량(하루 수십 건)에 과잉이라 일괄 연장 하나로 둔다.
PROCESSING_VISIBILITY_SECONDS = 900


def parse_trigger_message(body: str) -> str:
    """envelope(JSON: event_id/event_type/payload)에서 trigger_id 를 꺼낸다.

    위반은 ValueError — 호출자가 '지우지 않고 남기는' 쪽으로 처리한다(DLQ 가 근거 보존).
    """
    try:
        event = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError(f"envelope 이 JSON 이 아니다: {error}") from error
    if not isinstance(event, dict):
        raise ValueError(f"envelope 이 객체가 아니다: {type(event).__name__}")
    if event.get("event_type") != TRIGGER_EVENT_TYPE:
        raise ValueError(f"이 큐의 사건이 아니다: {event.get('event_type')!r}")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload 가 객체가 아니다")
    trigger_id = payload.get("trigger_id")
    if not isinstance(trigger_id, str) or not trigger_id.strip():
        raise ValueError("payload.trigger_id 가 없다")
    return trigger_id


def process_trigger(trigger_id: str) -> str:
    """트리거 한 건을 설명 파이프라인에 태운다 — 반환은 판정 라벨.

    "skipped_duplicate" = route 에 run 이 이미 있다(재배달) / "explained" = run() 완료.
    조립을 메시지마다 새로 하는 이유: 이 레인의 물량(하루 수십 건)에서 커넥션 재사용의
    이득이 없고, 오래 쥔 커넥션의 끊김 처리(재접속 상태기계)가 더 큰 코드다.
    """
    settings = load_settings(trigger_id=trigger_id)
    store = EventStore.connect(settings)
    try:
        if store.has_run_for_route(minute_route_id(trigger_id)):
            log("trigger.skipped_duplicate", trigger_id=trigger_id)
            return "skipped_duplicate"
        s3 = make_s3_client(settings)
        lake = LakeReader(s3, settings.lake_bucket)
        client = DeepSeekClient(settings.deepseek_api_key, settings.deepseek_model)
        exit_code = run(settings, lake=lake, store=store, client=client, s3=s3)
        if exit_code != 0:
            # run 의 비0 은 PipelineError 없이도 나올 수 있는 계약이면 실패로 취급한다 —
            # 성공으로 접으면 메시지가 지워져 그 트리거의 설명이 영영 없다.
            raise RuntimeError(f"analyze 가 비0 종료했다: {exit_code}")
        return "explained"
    finally:
        store.close()


class _Stop(Exception):
    pass


def _set_visibility(sqs, queue_url: str, receipt: str, seconds: int) -> None:
    """가시성 제어는 최선 노력이다 — 실패해도 루프를 죽이지 않는다.

    이 호출은 배달 타이밍 최적화지 정확성 장치가 아니다: 연장 실패는 이른 재배달
    (프리플라이트·L1 멱등이 흡수), 되돌림 실패는 늦은 재배달(지연만 늘어남)로 끝난다.
    반면 예외를 전파하면 SQS 스로틀 한 번에 상주 프로세스가 죽어 소비가 통째로 선다.
    """
    try:
        sqs.change_message_visibility(
            QueueUrl=queue_url, ReceiptHandle=receipt, VisibilityTimeout=seconds,
        )
    except Exception:
        logger.warning("가시성 변경 실패(%d초) — 배달 타이밍만 어긋난다: 계속 진행",
                       seconds, exc_info=True)


def consume_triggers(queue_url: str, *, max_polls: int | None = None,
                     process_fn=process_trigger, sqs_client=None) -> int:
    """상주 소비 루프 — `python -m edge_analysis consume-triggers` (ECS Service).

    SIGTERM/SIGINT 는 진행 중 메시지를 끝내고 멈춘다. `max_polls` 는 로컬·검증용 상한 —
    계약 위반·처리 실패가 하나라도 있었으면 1 로 끝난다(검증 실행이 성공으로 위장되면
    소비 경로가 통째로 죽은 환경도 초록으로 보인다).
    """
    import boto3

    if max_polls is not None and max_polls < 1:
        # 0 이하를 통과시키면 한 번도 폴링하지 않고 성공으로 끝난다 — 검증이 무의미해진다
        raise SystemExit(f"--max-polls 는 1 이상이어야 한다: {max_polls}")

    sqs = sqs_client if sqs_client is not None else boto3.client("sqs")
    stopping = {"flag": False}

    def _request_stop(*_):
        stopping["flag"] = True

    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, _request_stop)

    log("consumer.start", queue=queue_url)
    polls = 0
    totals: dict[str, int] = {}

    def _count(key: str) -> None:
        totals[key] = totals.get(key, 0) + 1

    while (max_polls is None or polls < max_polls) and not stopping["flag"]:
        polls += 1
        response = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=WAIT_SECONDS,
        )
        messages = response.get("Messages") or []
        if not messages:
            _count("idle")
            continue
        message = messages[0]
        receipt = message["ReceiptHandle"]
        try:
            trigger_id = parse_trigger_message(message.get("Body", ""))
        except ValueError:
            # 계약 위반 — 재시도로 낫지 않지만 **지우지도 않는다**: 지우면 무엇이 왔는지가
            # 사라진다. 기본 가시성으로 재배달되다 maxReceiveCount 에서 DLQ 로 격리된다.
            logger.exception("봉투 계약 위반 — 지우지 않고 남긴다(DLQ 가 근거 보존)")
            _count("malformed")
            continue
        # 처리 예산만큼 가시성을 먼저 늘린다 — 기본 300초 안에 LLM 처리가 안 끝나면
        # 재배달본이 프리플라이트를 통과해 이중 과금된다(위 상수 주석).
        _set_visibility(sqs, queue_url, receipt, PROCESSING_VISIBILITY_SECONDS)
        try:
            outcome = process_fn(trigger_id)
        except ReturnsNotReadyError:
            # 시간이 낫게 하는 실패 — 15:40 배치 뒤로 미룬다. 짧은 재배달을 반복하면
            # 그 receive 들이 maxReceiveCount 예산을 태워 배치 전에 DLQ 로 가 버린다.
            logger.info("returns 미준비(장중) — %d초 뒤 재시도: %s",
                        RETURNS_RETRY_SECONDS, trigger_id)
            _set_visibility(sqs, queue_url, receipt, RETURNS_RETRY_SECONDS)
            _count("deferred")
            continue
        except Exception:
            # 일시 장애(DB·LLM·네트워크) — 가시성을 되돌려 빨리 재배달한다(연장분을
            # 두면 일시 장애 한 번에 15분을 잃는다). 반복되면 DLQ.
            logger.exception("트리거 처리 실패 — 재배달된다: %s", trigger_id)
            _set_visibility(sqs, queue_url, receipt, 60)
            _count("failed")
            continue
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        _count(outcome)

    log("consumer.stop", polls=polls, **totals)
    # 계약 위반·처리 실패는 성공으로 접지 않는다 — 상주 모드에선 로그·DLQ 로 드러나고,
    # bounded 확인 실행에선 exit 로 드러난다(소비 경로가 죽은 환경의 초록 방지).
    bad = totals.get("malformed", 0) + totals.get("failed", 0)
    return 1 if (max_polls is not None and bad) else 0
