"""분봉 트리거 큐(price-explanation-realtime) 상주 소비자 (ALPHA-719).

사건은 둘이다: PriceTriggerFired(설명 생성) · ExposureReverted(설명 회수 — 가격이
전일 종가 1% 이내로 복귀, super-admin 무효화 API 경유, ALPHA-746).

data-pipeline 의 `MinuteConsumer` 커널을 **쓰지 않는다** — 이 큐는 job 테이블이 없는
사건(`TRIGGER_EVENT_DESTINATIONS`)이라 커널의 DB-authoritative 전제(claim·attempt fence)가
성립하지 않고, DLQ 대사 어휘에서도 명시적으로 빠져 있다. 여기의 권위는 둘뿐이다:

- **멱등의 권위 = explanation_run 존재**(`has_run_for_route`). run_id 재료에 벽시계
  (`explanation_as_of`)가 들어가 재배달마다 run·result 행이 늘고 LLM 이 재과금되므로,
  trigger_id 에서 결정적으로 유도한 route id 로 선판정한다.
- **재시도의 권위 = SQS**(visibility·maxReceiveCount·DLQ). 실패 메시지는 지우지 않고
  가시성 만료로 재배달되다 상한에서 DLQ 로 격리된다 — 근거 보존.

실패 분류는 셋이고 처방이 다르다:

- `ReturnsNotReadyError` — 분봉 window artifact·시가 원장이 아직 없다(ALPHA-710, 초
  단위 커밋 지연). 코드 결함이 아니라 **시간이 낫게 하는** 실패다 → 120초 고정 지연으로
  재확인하고, 진짜 결손이면 반복이 receive 예산(16)을 태워 ≈32분 안에 DLQ 로 간다.
- 봉투 계약 위반(파싱 불가·미지 event_type·trigger_id 결손) — 재시도로 낫지 않는다.
  **지우지도 않는다**(지우면 근거가 사라진다) → 짧은 재배달을 반복하다 DLQ 로 간다.
- 그 외 예외(DB·LLM·일시 장애) — 기본 가시성으로 재배달. 예산 판정은 SQS 상한 몫이다.
"""

from __future__ import annotations

import json
import logging
import signal

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .adapters.eventstore import EventStore, minute_route_id
from .adapters.lake import LakeReader, make_s3_client
from .adapters.llm import DeepSeekClient
from .adapters.superadmin import SuperAdminClient, SuperAdminUnavailableError
from .config import PipelineError, ReturnsNotReadyError, load_settings
from .observability import log
from .pipeline import run

logger = logging.getLogger(__name__)

TRIGGER_EVENT_TYPE = "PriceTriggerFired"  # data-pipeline jobs.py 와 같은 문자열(와이어 계약)
# 노출 회수 사건(ALPHA-746) — 판정기(ALPHA-745)가 같은 destination 으로 발행한다.
# 의미: 가격이 기준선(전일 종가) 1% 이내로 복귀 → 당일 분봉 설명을 비노출로 회수.
# ⚠️ 이 분기가 판정기 발행보다 **먼저 배포**돼야 한다 — 없으면 미지 event_type 이
# 계약 위반으로 분류돼 DLQ 로 간다.
REVERT_EVENT_TYPE = "ExposureReverted"
# 무효화 사유 — super-admin 감사 로그(admin_activity_log)에 그대로 남는 문장.
REVERT_REASON = (
    "가격 복귀 자동 회수 — |close/prev_close-1| ≤ 1% (ExposureReverted, ALPHA-746)"
)
WAIT_SECONDS = 20            # long polling — 빈 응답 폭주 방지
# ReturnsNotReady 지연 — 분해 입력이 분봉 canonical 이 되면서(ALPHA-710) 이 실패는
# "15:40 배치 대기"가 아니라 window artifact 커밋·시가 원장 확정의 **초 단위 지연**이다.
# 짧은 고정 지연으로 재확인하고, 진짜 결손이면 반복이 maxReceiveCount(16) 예산을 태워
# DLQ 로 간다 — 시간이 낫게 하지 않는 실패는 근거 보존 경로가 정답이다. 16회 × 120초
# ≈ 32분이라 일시 지연은 넉넉히 덮고 결손은 당일 안에 드러난다.
RETURNS_RETRY_SECONDS = 120
# 처리 시작 전 이 값으로 가시성을 한 번 연장한다 — 큐 기본(300초)은 LLM 다회 호출
# (호출당 재시도 포함 수분)을 못 덮어, 처리 중 재배달된 메시지가 프리플라이트(아직
# run 없음)를 통과해 **같은 트리거에 LLM 을 이중 과금**한다. 주기 heartbeat 스레드는
# 이 물량(하루 수십 건)에 과잉이라 일괄 연장 하나로 둔다.
PROCESSING_VISIBILITY_SECONDS = 900


KST = ZoneInfo("Asia/Seoul")




def _require_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"payload.{key} 가 없다")
    if value != value.strip():
        # 둘러싼 공백은 정확 일치 DB 질의에서 0건이 돼 no-op 성공으로 위장된다 —
        # 조용히 정규화하지 않고 계약 위반으로 드러낸다(생산자 결함, Rule 12).
        raise ValueError(f"payload.{key} 에 둘러싼 공백이 있다: {value!r}")
    return value


def parse_message(body: str) -> tuple[str, dict]:
    """envelope(JSON: event_id/event_type/payload)를 검증해 (event_type, payload)로.

    이 큐의 사건은 둘이다 — PriceTriggerFired(설명 생성)·ExposureReverted(설명 회수,
    ALPHA-746). 각 사건이 처리에 반드시 필요로 하는 필드만 여기서 강제한다. 위반은
    ValueError — 호출자가 '지우지 않고 남기는' 쪽으로 처리한다(DLQ 가 근거 보존).
    """
    try:
        event = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError(f"envelope 이 JSON 이 아니다: {error}") from error
    if not isinstance(event, dict):
        raise ValueError(f"envelope 이 객체가 아니다: {type(event).__name__}")
    event_type = event.get("event_type")
    if event_type not in (TRIGGER_EVENT_TYPE, REVERT_EVENT_TYPE):
        raise ValueError(f"이 큐의 사건이 아니다: {event_type!r}")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload 가 객체가 아니다")
    if event_type == TRIGGER_EVENT_TYPE:
        _require_str(payload, "trigger_id")
    else:
        # 회수의 대상 좌표 — 종목(entity_id=단축코드)·세션(당일)·상한(window_start:
        # 복귀 window 이전 발화만 회수한다 — 지연·재배달된 회수가 이후 재발화 설명을
        # 잡으면 안 된다). 나머지 payload 필드(prev_close 등)는 판정 근거라 로그로만.
        _require_str(payload, "entity_id")
        _require_str(payload, "session_id")
        try:
            window_start = datetime.fromisoformat(_require_str(payload, "window_start"))
        except ValueError as error:
            raise ValueError(f"payload.window_start 가 ISO 시각이 아니다: {error}") from error
        if window_start.tzinfo is None:
            # naive 시각은 TIMESTAMPTZ 비교에서 DB 세션 시간대로 재해석돼 상한이
            # 조용히 어긋난다(대상 누락=노출 방치 / 과포함=재발화 오회수).
            raise ValueError("payload.window_start 에 시간대(offset)가 없다")
    return event_type, payload


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


def revert_explanations(payload: dict, *, store, client) -> str:
    """ExposureReverted 한 건을 집행한다 — 반환은 판정 라벨(ALPHA-746).

    대상 = 그 종목·세션의 분봉 트리거 기원 PUBLISHED 설명 전부(EOD 는 store 질의가
    구조적으로 제외). 집행 = 대상마다 super-admin 무효화 API — 엔진이 DB 를 직접
    쓰지 않는다(INVALIDATION 발화자 단일화, ALPHA-440).

    - 0건 = no-op — 발화 전 회수(설명이 아직 없다)도 정상이다. 조용히 지나가되 로그.
    - 409(이미 무효)=멱등 정상 / 404(런 없음)=경고 로그.
    - 연결 실패·5xx 는 SuperAdminUnavailableError 로 전파 — 큐 재배달이 재시도한다.
      재배달 재실행은 안전하다: 이미 회수된 행은 PUBLISHED 질의에 다시 걸리지 않는다.
    """
    entity_id = payload["entity_id"]
    session_id = payload["session_id"]
    window_start = datetime.fromisoformat(payload["window_start"])
    run_ids = store.find_published_minute_run_ids(entity_id, session_id, window_start)
    if not run_ids:
        log("revert.noop", entity_id=entity_id, session_id=session_id,
            window_start=payload.get("window_start"))
        return "reverted_noop"
    client.login()
    outcomes = {"invalidated": 0, "already_withdrawn": 0, "not_found": 0}
    for run_id in run_ids:
        outcome = client.invalidate(run_id, REVERT_REASON)
        outcomes[outcome] += 1
        if outcome == "not_found":
            # 질의로 찾은 run 이 API 에선 없다 — 질의·API 의 원장이 갈렸다는 신호라
            # 조용히 넘기지 않는다(단 회수 자체는 남은 대상으로 계속한다).
            logger.warning("무효화 대상 run 이 super-admin 에 없다: %s", run_id)
    if outcomes["not_found"]:
        # 404 = 방금 읽은 run 이 API 원장에 없다(오배선 URL·원장 불일치) — 그 설명은
        # PUBLISHED 로 남았는데 성공으로 접으면 재시도 기회가 사라진다(Rule 12,
        # 반쯤 회수하고 성공 처리 금지). 전 대상 시도 후 재배달로 올린다 — 재질의는
        # 이미 내려간 건을 다시 안 잡으므로(WITHDRAWN) 남은 건만 재시도되고,
        # 반복되면 DLQ 가 드러낸다.
        raise SuperAdminUnavailableError(
            f"무효화 대상 {outcomes['not_found']}/{len(run_ids)}건이 super-admin 에"
            " 없다 — 원장 불일치"
        )
    log("revert.done", entity_id=entity_id, session_id=session_id,
        targets=len(run_ids), **outcomes)
    return "reverted"


def process_revert(payload: dict) -> str:
    """운영 조립 — 설정·EventStore·SuperAdminClient 를 만들어 revert_explanations 로."""
    settings = load_settings()
    if not (settings.super_admin_url and settings.super_admin_email
            and settings.super_admin_password):
        # 미설정을 no-op 으로 접으면 회수가 조용히 유실된다 — 재배달을 반복하다 DLQ 로
        # 드러나는 쪽이 맞다(Rule 12).
        raise PipelineError(
            "SUPER_ADMIN_API_URL/SUPER_ADMIN_EMAIL/SUPER_ADMIN_PASSWORD 가 없다"
        )
    store = EventStore.connect(settings)
    try:
        client = SuperAdminClient(
            settings.super_admin_url, settings.super_admin_email,
            settings.super_admin_password,
        )
        return revert_explanations(payload, store=store, client=client)
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
                     process_fn=process_trigger, revert_fn=process_revert,
                     sqs_client=None) -> int:
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
            event_type, payload = parse_message(message.get("Body", ""))
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
            if event_type == REVERT_EVENT_TYPE:
                outcome = revert_fn(payload)
            else:
                outcome = process_fn(payload["trigger_id"])
        except ReturnsNotReadyError:
            # 시간이 낫게 하는 실패 — 분봉 artifact 커밋·시가 원장 확정의 짧은 지연이라
            # 고정 짧은 간격으로 재확인한다(ALPHA-710 — 배치 착지 대기 산술은 폐기).
            logger.info("returns 미준비(분봉 window·시가 원장) — %d초 뒤 재시도: %s",
                        RETURNS_RETRY_SECONDS, payload.get("trigger_id"))
            _set_visibility(sqs, queue_url, receipt, RETURNS_RETRY_SECONDS)
            _count("deferred")
            continue
        except Exception:
            # 일시 장애(DB·LLM·네트워크) — 가시성을 되돌려 빨리 재배달한다(연장분을
            # 두면 일시 장애 한 번에 15분을 잃는다). 반복되면 DLQ.
            logger.exception("%s 처리 실패 — 재배달된다: %s", event_type,
                             payload.get("trigger_id") or payload.get("entity_id"))
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
