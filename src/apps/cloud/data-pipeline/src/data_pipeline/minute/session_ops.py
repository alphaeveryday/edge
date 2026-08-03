"""1분 세션 스케일 오케스트레이션 (ALPHA-712).

상주 서비스 3종(price-worker·relay·price-consumer)은 `desired_count = 0` +
`lifecycle ignore_changes` 로 terraform 이 의도적으로 손을 뗀 자리다 — 그 값을 세션
수명에 맞춰 바꾸는 주체가 여기다. 이게 없으면 apply·CD 가 다 끝나도 1분 파이프라인은
아침에 스스로 뜨지 않는다.

```text
start-minute-session (Premarket) : 거래일 판정 → 세션 계획 → desired 0→1
stop-minute-session  (EOD)       : drain 요청 → 원장 게이트 대기 → desired 1→0
```

**오케스트레이터는 EventBridge Scheduler → ECS RunTask 다**(SFN·Lambda 아님). 이유:
① 이 레포의 스케줄은 전부 그 형태다(`aws_scheduler_schedule.daily`·`news`·`reconcile`),
② SFN 을 들여도 내릴 시점 판정은 결국 컨테이너 몫이다 — SFN Choice 는 Postgres 를 못 보고
이 게이트의 근거가 전부 원장에 있다, ③ Lambda 는 레포에 0건이고, 들이면 같은 VPC·DB·이미지를
다시 배선해야 한다. 새 실행체 종류를 하나 늘려서 얻는 게 없다.

⚠️ **내리는 조건은 시각이 아니라 원장 상태다.** 15:30 이 지났다고 내리면 recovery 레인이
잔여 window 를 집고 있는 중에 프로세스가 사라져 그만큼이 조용히 결손된다. 게이트는 셋이고
세 단계가 순서대로 비어야 한다:

- `session.phase` 가 DRAINED 이후 — `ack_drain` 이 CLAIMED 잔존 시 거부하므로 이 값이
  곧 "in-flight window 0" 이다(DUE 잔존은 기다릴 대상이 아니라 EOD QC 가 MISSING 으로
  확정할 몫이다 — Worker 가 이미 recovery 예산을 다 쓴 뒤다).
- 미발행 outbox `NEW` 0 — Relay 가 아직 못 실어보낸 사건이 남아 있으면 그 트리거는
  큐에 나간 적이 없다.
- 게이트 큐 깊이 0 — Consumer 를 먼저 내리면 남은 메시지는 4일 retention 안에서
  아무도 안 집다가 **다음 세션의 Consumer** 가 어제 window 를 처리한다.
"""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import datetime, timezone

from ..db import stable_domain_id
from ..ops.trading_calendar import is_trading_day
from .jobs import JobLedger
from .models import KST
from .repository import MinuteLedger
from .session_cli import plan_session_cli
from .states import MINUTE_DATASETS, SOURCE_GROUPS_BY_DATASET

logger = logging.getLogger(__name__)

# 배선은 env 로 받는다 — 클러스터·서비스명은 terraform 이 아는 값이고, 여기에 이름 규칙을
# 다시 구현하면 서비스 rename 이 조용히 no-op 스케일링이 된다(OPS_CLUSTER_ARN 과 같은 결).
ENV_CLUSTER = "MINUTE_SESSION_CLUSTER"
ENV_SERVICES = "MINUTE_SESSION_SERVICES"
ENV_GATE_QUEUES = "MINUTE_SESSION_GATE_QUEUES"
ENV_DRAIN_TIMEOUT = "MINUTE_SESSION_DRAIN_TIMEOUT_SEC"

DEFAULT_DRAIN_TIMEOUT_SEC = 1800.0
POLL_INTERVAL_SEC = 15.0
# 게이트가 비었다고 **연속으로** 이만큼 관측돼야 내린다. SQS 의 큐 깊이는 approximate 이고
# 생산이 멈춘 뒤에도 최대 1분까지 실제와 어긋날 수 있다(GetQueueAttributes 계약) — 한 번의
# 0 을 완료로 읽으면 마지막 메시지가 남은 채 Consumer 가 내려가 다음 세션까지 안 처리된다.
CLEAR_CONFIRMATIONS = 5  # × POLL_INTERVAL_SEC = 60초 관측

# 이 phase 부터는 원장이 더 이상 움직이지 않는다 — Worker 쪽 claim·기록 경로가 전부
# phase 로 닫힌 뒤라(eod 모듈 주석) 내려도 잃을 in-flight 가 없다.
DRAINED_PHASES = frozenset({"DRAINED", "QC_RUNNING", "FINALIZED", "FAILED"})


def _services() -> list[str]:
    raw = os.environ.get(ENV_SERVICES, "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        # 빈 값으로 통과시키면 "스케일링 성공(0건)" 이 되어 아침에 아무것도 안 뜬 채
        # 스케줄은 초록으로 보인다 — 배선 결손은 여기서 죽는다.
        raise SystemExit(f"{ENV_SERVICES} 가 비었다 — 스케일할 ECS 서비스명을 쉼표로 주입한다")
    return names


def _cluster() -> str:
    cluster = os.environ.get(ENV_CLUSTER, "").strip()
    if not cluster:
        # 생략하면 boto3 가 default 클러스터를 본다 — 없는 서비스라 예외가 나긴 하지만
        # 메시지가 "서비스 없음" 이라 원인(클러스터 미주입)이 안 드러난다.
        raise SystemExit(f"{ENV_CLUSTER} 가 비었다 — 대상 ECS 클러스터를 주입한다")
    return cluster


def _gate_queues() -> list[str]:
    queues = [q.strip() for q in os.environ.get(ENV_GATE_QUEUES, "").split(",") if q.strip()]
    if not queues:
        # 빈 목록은 "볼 큐가 없다" 가 아니라 **큐 게이트가 통째로 사라진 것**이다 — 그러면
        # 원큐에 메시지가 남아 있어도 Consumer 를 내리고 exit 0 이 된다(fail-open, Rule 12).
        raise SystemExit(f"{ENV_GATE_QUEUES} 가 비었다 — 내리기 전에 비어야 할 큐 URL 을 주입한다")
    return queues


def _drain_timeout_sec() -> float:
    raw = os.environ.get(ENV_DRAIN_TIMEOUT, "").strip()
    if not raw:
        return DEFAULT_DRAIN_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(f"{ENV_DRAIN_TIMEOUT} 이 숫자가 아니다: {raw!r}") from None
    # NaN 은 `<= 0` 도 `경과 >= nan` 도 False 라 **상한이 통째로 사라진다**(inf 도 같다).
    # 값이 있으나 마나인 상태가 가장 나쁘다 — 있다고 믿는데 안 걸린다(run.py --deadline-sec 와 같은 축).
    if not math.isfinite(value) or value <= 0:
        raise SystemExit(f"{ENV_DRAIN_TIMEOUT} 은 0 보다 큰 유한한 값이어야 한다: {value}")
    return value


def _scale(*, desired: int, force: bool) -> None:
    """서비스 3종의 desired_count 를 바꾼다. 실패는 전파한다(부분 적용을 숨기지 않는다).

    ⚠️ 스케일업은 `forceNewDeployment` 를 동반해야 한다 — 이미지 태그가 mutable 이고
    ECS 는 **태스크를 띄울 때** 다이제스트를 확정한다. desired 0 인 동안 CD 가 돌린
    재배포는 즉시 COMPLETED 라 아무것도 안 당기므로, 그냥 desired 만 올리면 **직전
    세션의 낡은 다이제스트**로 뜬다(#488 봇 P2).
    """
    from ..ops.aws import ecs_client

    # ⚠️ 팩토리를 거친다 — task-def 는 비표준 `AWS_REGION_NAME` 만 주입하고 botocore 는
    # 그걸 region 으로 안 읽는다. `boto3.client("ecs")` 를 직접 부르면 첫 호출이
    # NoRegionError 로 죽는다(s3 와 달리 폴백이 없다 — ops/aws.py 모듈 주석).
    ecs = ecs_client()
    cluster = _cluster()
    for service in _services():
        ecs.update_service(
            cluster=cluster, service=service,
            desiredCount=desired, forceNewDeployment=force,
        )
        logger.info("desired_count=%d (force_new_deployment=%s): %s", desired, force, service)


def _resolve(dataset: str | None, source_group: str | None) -> tuple[str, str]:
    """어휘 검증 — session_id 가 이 둘에서 결정적으로 유도되므로 오타는 없는 세션을 가리킨다."""
    if dataset not in MINUTE_DATASETS:
        raise SystemExit(f"--dataset 이 1분 원장 어휘 밖이다: {dataset} (아는 값 {sorted(MINUTE_DATASETS)})")
    allowed = SOURCE_GROUPS_BY_DATASET[dataset]
    if source_group not in allowed:
        raise SystemExit(
            f"--source-group 이 dataset {dataset} 의 어휘 밖이다: {source_group} (아는 값 {sorted(allowed)})"
        )
    return dataset, source_group


def start_session_cli(settings, *, dataset: str | None, source_group: str | None,
                      universe: str | None) -> int:
    """`run start-minute-session` — Premarket 스케줄이 부른다.

    exit: 0=올렸음 또는 비거래일 no-op / 그 외=`plan-minute-session` 의 exit 를 그대로 전달.

    세션 날짜는 인자로 받지 않고 **오늘(KST)** 로 고정한다 — Premarket 시각은 KST 한낮이라
    경계 모호성이 없고, 스케줄러가 넘기는 `scheduled-time` 은 UTC 라 08:30 KST 가 전날
    날짜로 읽힌다(그 하루가 통째로 엉뚱한 세션이 된다).

    ⚠️ 계획이 실패하면 **스케일업하지 않는다.** Worker 는 세션 없이 기동을 거부하므로
    (fail-loud), 올려 두면 하루 종일 재기동 루프만 돈다.
    """
    dataset, source_group = _resolve(dataset, source_group)
    today = datetime.now(KST).date()
    if not is_trading_day(today):
        # ⚠️ 공휴일 집합은 `OPS_KR_HOLIDAYS` 수동 주입이다(비면 평일 휴장일이 거래일로
        # 보인다). 그 경우 세션·서비스가 뜨고 window 는 전건 빈 캔들로 남는다.
        logger.info("비거래일이다(%s) — 세션도 만들지 않고 서비스도 올리지 않는다", today)
        return 0

    exit_code = plan_session_cli(
        settings, dataset=dataset, source_group=source_group,
        session_date=today.isoformat(), universe=universe,
    )
    if exit_code != 0:
        logger.error("세션 계획이 실패했다(exit %d) — 스케일업하지 않는다", exit_code)
        return exit_code

    _scale(desired=1, force=True)
    return 0


def stop_session_cli(settings, *, dataset: str | None, source_group: str | None) -> int:
    """`run stop-minute-session` — EOD 스케줄이 부른다.

    exit: 0=게이트가 비어 내렸음(또는 그 날 세션이 없어 내릴 것만 내렸음)
    / 1=상한까지 게이트가 안 비었다(**내리지 않았다**) / 2=요청 자체를 못 함(설정·DB).

    ⚠️ 상한 초과에서 **내리지 않는 쪽을 택한다.** 내리면 처리 중이던 window 가 조용히
    결손되고, 안 내리면 Fargate 태스크 3개가 다음 스케줄까지 떠 있을 뿐이다(다음날
    아침 스케일업이 force-new-deployment 로 교체한다). 잃는 것의 크기가 다르다.

    상한은 **게이트가 안 빈 채 기다린 시간**에 걸린다. 실제 벽시계 상한은 거기에 연속
    확인분(`CLEAR_CONFIRMATIONS × POLL_INTERVAL_SEC`)이 더해진다 — 게이트가 빈 뒤의
    확인은 상한으로 끊지 않는다. 끊으면 확인 횟수가 모자란 채 내려가 이 게이트가 막으려던
    바로 그 결손이 난다.
    """
    if settings.db is None:
        logger.error("db 설정 없음 — stop-minute-session 은 세션 원장 필수(DATA_PIPELINE_DB__* 주입)")
        return 2
    dataset, source_group = _resolve(dataset, source_group)
    day = datetime.now(KST).date()
    session_id = stable_domain_id("msn", dataset, source_group, day.isoformat())

    ledger = MinuteLedger(db=settings.db)
    jobs = JobLedger(db=settings.db)
    try:
        snapshot = ledger.session_snapshot(session_id=session_id)
    except Exception:
        logger.exception("세션 조회 실패: %s", session_id)
        return 2

    if snapshot is None:
        # 비거래일이거나 Premarket 이 안 돈 날이다. **내리지 않는다** — 오늘 세션이 없다는
        # 것은 서비스가 뭘 하고 있는지 모른다는 뜻이지 노는 중이라는 뜻이 아니다. 전 거래일
        # stop 이 상한 초과로 서비스를 살려 둔 채 끝났다면(exit 1), 그 다음 휴장일의 이
        # 경로가 어제의 복구 작업을 지목 없이 죽인다. 정상 운영에선 이미 desired 0 이라
        # 내릴 것도 없다 — 떠 있다면 그건 사람이 볼 상태다.
        logger.warning(
            "오늘 세션이 없다(%s/%s/%s) — 지목할 세션이 없어 스케일을 건드리지 않는다. "
            "서비스가 떠 있다면 전 거래일 stop 이 상한 초과로 끝난 것이다(그 실행의 exit 1 을 본다)",
            dataset, source_group, day,
        )
        return 0

    try:
        requested = ledger.request_drain(session_id=session_id, now=datetime.now(timezone.utc))
    except Exception:
        logger.exception("drain 요청 실패: %s", session_id)
        return 2
    logger.info("drain 요청: session=%s phase_before=%s newly_requested=%s",
                session_id, snapshot["phase"], requested)

    timeout_sec = _drain_timeout_sec()
    queues = _gate_queues()
    deadline = time.monotonic() + timeout_sec
    clear_streak = 0
    while True:
        pending = _gate_pending(ledger, jobs, session_id=session_id, queues=queues)
        if not pending:
            clear_streak += 1
            if clear_streak >= CLEAR_CONFIRMATIONS:
                break
            logger.info("게이트가 비었다(%d/%d 연속) — 큐 깊이가 approximate 라 확인을 더 한다",
                        clear_streak, CLEAR_CONFIRMATIONS)
            time.sleep(POLL_INTERVAL_SEC)
            continue
        clear_streak = 0
        if time.monotonic() >= deadline:
            # 큐가 안 비어 상한에 닿으면 `_gate_pending` 이 outbox 를 읽기 전에 반환하므로
            # DEAD 경고가 한 번도 안 뜬다 — 가장 진단이 필요한 자리라 여기서 한 번 읽는다.
            logger.error(
                "drain 대기 상한(%.0fs) 초과 — 남은 것: %s. 미발행 outbox: %s. "
                "**서비스를 내리지 않는다**(내리면 처리 중인 window 가 결손된다). "
                "원장을 보고 사람이 판단한다: %s",
                timeout_sec, "; ".join(pending), jobs.count_unpublished(), session_id,
            )
            return 1
        logger.info("대기 중 — %s", "; ".join(pending))
        time.sleep(POLL_INTERVAL_SEC)

    _scale(desired=0, force=False)
    logger.info("세션 종료: session=%s — 게이트 전건 비었음", session_id)
    return 0


def _gate_pending(ledger: MinuteLedger, jobs: JobLedger, *,
                  session_id: str, queues: list[str]) -> list[str]:
    """아직 안 끝난 것들의 사람이 읽는 목록. 비면 내려도 된다.

    앞 단계가 안 끝났으면 뒤를 보지 않는다 — Worker 가 아직 window 를 커밋하는 중이면
    그때의 "outbox 0" 은 아직 만들지 않은 것이지 다 나간 것이 아니다.

    ⚠️ **outbox 를 큐보다 나중에 읽는다.** 이 파이프는 순환한다 — Consumer 가 메시지를
    처리하면 그 트랜잭션이 설명 큐용 outbox 행을 새로 만든다. outbox 를 먼저 읽으면 그
    직후 처리된 메시지가 만든 행이 이번 관측에 안 잡히고, 같은 호출의 큐 조회는 이미
    0 이라 "다 끝났음" 으로 인증된다. 순서를 뒤집으면 그 행이 다음 관측이 아니라 **이번**
    관측에 잡힌다(남는 창은 두 조회 사이뿐이고, 연속 확인이 그것도 덮는다).
    """
    phase = (ledger.session_snapshot(session_id=session_id) or {}).get("phase")
    if phase not in DRAINED_PHASES:
        return [f"session phase={phase}"]

    # depth < 0 은 조회 실패다(모름) — `if depth` 가 0 만 통과시키므로 실패도 pending 이다
    queue_pending = [f"queue depth={'unknown' if depth < 0 else depth} {url}"
                     for url, depth in _queue_depths(queues) if depth]
    if queue_pending:
        return queue_pending

    unpublished = jobs.count_unpublished()
    if unpublished["DEAD"]:
        # ⚠️ DEAD 도 미발행이다(`count_unpublished` 계약) — 그런데 **게이트로 세지 않는다.**
        # 기다려서 없어지지 않고(redrive 가 사람 손이다) 게이트로 삼으면 그 세션은 영영
        # 안 내려간다. exit code 로 올리지도 않는다: 이 집계는 세션별이 아니라 **전역**이라
        # 3주 전 DEAD 한 건이 매일의 stop 을 실패로 만든다(거짓 신호가 진짜를 덮는다).
        # 남는 장치는 이 경고다 — 없애려면 세션 범위 집계가 선행이다.
        logger.warning("미발행 DEAD outbox %d 건(전역 집계) — redrive 대상이다. 게이트에는 세지 않는다",
                       unpublished["DEAD"])
    if unpublished["NEW"]:
        return [f"outbox NEW={unpublished['NEW']}"]

    return []


def _queue_depths(queues: list[str]) -> list[tuple[str, int]]:
    """(큐 URL, 미처리 메시지 수). 가시 + 비가시 둘 다 센다 — 비가시는 지금 처리 중이다."""
    if not queues:
        return []
    from ..ops.aws import sqs_client

    sqs = sqs_client()  # region 명시 팩토리 — `_scale` 과 같은 이유
    depths = []
    for url in queues:
        try:
            attrs = sqs.get_queue_attributes(
                QueueUrl=url,
                AttributeNames=["ApproximateNumberOfMessages",
                                "ApproximateNumberOfMessagesNotVisible"],
            )["Attributes"]
        except Exception:
            # ⚠️ **모르는 것은 비지 않은 것으로 친다.** 이 팩토리는 재시도를 1회로 줄여 둬서
            # (`sqs_client`) 일시적 throttling 한 번에도 예외가 온다. 전파시키면 남은 상한을
            # 안 쓰고 프로세스가 죽어 EOD 가 통째로 날아가고, 0 으로 치면 안 본 큐를
            # "비었음" 으로 인증한다(Rule 12). 다음 폴링이 다시 본다.
            logger.warning("큐 깊이 조회 실패 — 비었다고 보지 않고 다음 폴링에서 다시 본다: %s",
                           url, exc_info=True)
            depths.append((url, -1))
            continue
        depths.append((url, int(attrs["ApproximateNumberOfMessages"])
                       + int(attrs["ApproximateNumberOfMessagesNotVisible"])))
    return depths
