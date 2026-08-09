"""1분 세션 스케일 오케스트레이션 (ALPHA-712).

상주 서비스(price-worker·relay·price-consumer·news-consumer 2종)는 `desired_count = 0` +
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
- 게이트 큐 깊이 0 — Consumer 를 먼저 내리면 남은 메시지는 7일 retention 안에서
  아무도 안 집다가 **다음 세션의 Consumer** 가 어제 window 를 처리한다.
"""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import NamedTuple

from ..db import stable_domain_id
from ..ops.trading_calendar import is_trading_day
from .jobs import JobLedger
from .models import KST
from .repository import MinuteLedger
from .session_cli import plan_session_cli
from .states import (
    DATASET_DISCLOSURE_MINUTE,
    DATASET_ETF_INAV_MINUTE,
    DATASET_NEWS_MINUTE,
    MINUTE_DATASETS,
    SCALED_DATASETS,
    SOURCE_GROUPS_BY_DATASET,
    UNIVERSE_DATASETS,
)

logger = logging.getLogger(__name__)

# 배선은 env 로 받는다 — 클러스터·서비스명은 terraform 이 아는 값이고, 여기에 이름 규칙을
# 다시 구현하면 서비스 rename 이 조용히 no-op 스케일링이 된다(OPS_CLUSTER_ARN 과 같은 결).
ENV_CLUSTER = "MINUTE_SESSION_CLUSTER"
ENV_SERVICES = "MINUTE_SESSION_SERVICES"
ENV_GATE_QUEUES = "MINUTE_SESSION_GATE_QUEUES"
ENV_DRAIN_TIMEOUT = "MINUTE_SESSION_DRAIN_TIMEOUT_SEC"
# 뉴스 세션 편입 토글(ALPHA-717) — source_group 을 주면 start 가 news_minute 세션도
# 계획하고 stop 이 함께 드레인한다. 비우면 가격 레인만(뉴스 미편입 환경).
ENV_NEWS_SOURCE_GROUP = "MINUTE_SESSION_NEWS_SOURCE_GROUP"
# 뉴스 생산자(news-worker) 서비스명 — 공용 목록(ENV_SERVICES)과 분리한다. 뉴스 세션
# 계획이 실패한 날 news-worker 까지 올리면 세션 부재 기동 거부로 하루 종일 재기동
# 루프(비용·알람 소음)를 돌기 때문에, 이 목록은 **뉴스 세션이 선 날만** 올린다.
# 소비자 2종은 공용 목록에 남는다 — 빈 큐 폴링은 무해하고 backfill 소비는 세션 무관.
ENV_NEWS_WORKER_SERVICES = "MINUTE_SESSION_NEWS_WORKER_SERVICES"
# 공시(ALPHA-875) — 뉴스와 **같은 성질**이다: 유니버스 없는 소스 단위 레인이고, 생산자가
# 세션 결속이라 세션이 선 날만 올려야 한다. 그래서 같은 축 두 개를 그대로 갖는다.
ENV_DISCLOSURE_SOURCE_GROUP = "MINUTE_SESSION_DISCLOSURE_SOURCE_GROUP"
ENV_DISCLOSURE_WORKER_SERVICES = "MINUTE_SESSION_DISCLOSURE_WORKER_SERVICES"
# iNAV(ALPHA-882) — 위 둘과 같은 축이되 **universe 를 쓴다**(`UNIVERSE_DATASETS`).
# 하위 소비자는 없어(`commit_inav_window` 가 job·outbox 를 안 만든다) 공용 목록에 넣을
# 소비자도 없고, 생산자 하나만 세션 수명에 묶인다.
ENV_INAV_SOURCE_GROUP = "MINUTE_SESSION_INAV_SOURCE_GROUP"
ENV_INAV_WORKER_SERVICES = "MINUTE_SESSION_INAV_WORKER_SERVICES"
# 설명 소비자(analysis-consumer, ALPHA-719) — 공용 목록에서 떼어 자기 목록으로 둔다
# (ALPHA-910). 수명은 그대로 세션에 묶이지만(start 에서 1, stop 에서 0), 소유 축이
# 분리돼야 그 desired 를 오토스케일링에 넘길 수 있다: 공용에 있으면 스케일러가 큐 깊이로
# 올린 값을 매일 밤 stop 이 0 으로 덮어써 둘 다 틀린다.
# ⚠️ 선택 레인 목록과 달리 **토글이 없다** — 이 서비스는 늘 세션과 함께 뜬다. 그래서 빈
# 값은 미편입이 아니라 배선 결손이고, `_services` 와 같은 축으로 죽는다.
ENV_ANALYSIS_SERVICES = "MINUTE_SESSION_ANALYSIS_SERVICES"


class _OptionalLane(NamedTuple):
    """가격 레인에 **얹히는** 선택 레인 — 토글(env)이 켜진 날만 계획·스케일된다.

    뉴스와 공시가 글자 그대로 같은 규칙을 따르므로 표로 둔다(두 벌로 두면 한쪽만 고쳐진다 —
    `states.SOURCE_GROUPS_BY_DATASET` 를 한 표로 둔 것과 같은 이유). 늘어나는 자리는 여기다.

    ⚠️ 레인이 **모든 축에서 같지는 않다** — universe 를 쓰는지는 여기 있는지가 아니라
    `UNIVERSE_DATASETS` 가 정한다(iNAV 는 쓰고 뉴스·공시는 안 쓴다). 표에 넣었다고 그
    축까지 같다고 접으면 그 레인이 매 거래일 통째로 안 선다(`start_session_cli` 주석).
    """

    dataset: str
    source_env: str
    services_env: str


_OPTIONAL_LANES = (
    _OptionalLane(DATASET_NEWS_MINUTE, ENV_NEWS_SOURCE_GROUP, ENV_NEWS_WORKER_SERVICES),
    _OptionalLane(DATASET_DISCLOSURE_MINUTE, ENV_DISCLOSURE_SOURCE_GROUP,
                  ENV_DISCLOSURE_WORKER_SERVICES),
    _OptionalLane(DATASET_ETF_INAV_MINUTE, ENV_INAV_SOURCE_GROUP, ENV_INAV_WORKER_SERVICES),
)

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
    # 설명 소비자는 자기 목록이 소유한다(ALPHA-910) — terraform 이 아직 공용에도 싣고 있어도
    # **여기서 뺀다**. 소유 축을 코드가 정해야 배포 순서와 무관하게 축이 하나로 유지된다:
    # 남겨두면 같은 서비스에 desired 를 두 번 쓰고(force 라 배포도 두 번), 그 상태로
    # 오토스케일링을 붙이면 공용 경로가 스케일러의 desired 를 계속 되돌린다.
    owned = set(_analysis_services())
    return [name for name in names if name not in owned]


def _analysis_services() -> list[str]:
    """설명 소비자의 서비스 목록. **비어 있을 수 있다** — 그 상태가 컷오버 중이다.

    ⚠️ 다른 목록들과 달리 빈 값에 죽지 않는다. terraform 과 이미지 CD 는 각자 `push: dev`
    로 발화하는 독립 워크플로라 어느 쪽이 먼저 착지할지 모르는데, 여기서 죽으면 이 이미지가
    apply 보다 먼저 뜬 날 **거래일 판정 전에** 오케스트레이터가 끝나 1분 파이프라인이 통째로
    안 뜬다. 빈 값은 배선 결손이 아니라 **구 task-def** 이고, 그때는 공용 목록이 아직
    소비자를 싣고 있어 스케일은 그대로 된다(terraform 이 그래서 공용에서 안 뺐다).
    공용 목록에서 실제로 빼는 시점(오토스케일링 부착)에 이 관대함을 회수한다 — 그때는
    빈 값이 곧 "아무도 안 올린다"가 되므로 `_services` 와 같은 fail-loud 로 바꾼다.
    """
    raw = os.environ.get(ENV_ANALYSIS_SERVICES, "")
    return [n.strip() for n in raw.split(",") if n.strip()]


def _lane_worker_services(lane: _OptionalLane) -> list[str]:
    """그 레인 생산자의 서비스 목록 — 비어 있을 수 있다(미편입 환경). 결손 검증은
    토글과 **짝으로** 본다: 토글이 켜졌는데 목록이 비면 배선 결손이다."""
    raw = os.environ.get(lane.services_env, "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if _lane_source_group(lane) is not None and not names:
        raise SystemExit(
            f"{lane.source_env} 이 켜졌는데 {lane.services_env} 가 비었다 — "
            f"{lane.dataset} 세션은 계획되는데 그걸 처리할 서비스가 스케일 목록에 없다"
        )
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


def _scale(*, desired: int, force: bool, services: list[str] | None = None) -> None:
    """상주 서비스의 desired_count 를 바꾼다. 실패는 전파한다(부분 적용을 숨기지 않는다).
    `services` 미지정 = 공용 목록(ENV_SERVICES).

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
    for service in (_services() if services is None else services):
        ecs.update_service(
            cluster=cluster, service=service,
            desiredCount=desired, forceNewDeployment=force,
        )
        logger.info("desired_count=%d (force_new_deployment=%s): %s", desired, force, service)


def _lane_source_group(lane: _OptionalLane) -> str | None:
    """그 레인의 토글 — 빈 값이면 꺼진 것(그 레인을 계획도 스케일도 하지 않는다)."""
    value = os.environ.get(lane.source_env, "").strip()
    if not value:
        return None
    allowed = SOURCE_GROUPS_BY_DATASET[lane.dataset]
    if value not in allowed:
        # 오타를 통과시키면 없는 세션 id 가 유도돼 그 Worker 가 기동 거부 루프를 돈다 —
        # 배선 결손은 오케스트레이터 기동에서 죽는다(_services 와 같은 축).
        raise SystemExit(
            f"{lane.source_env} 이 {lane.dataset} 어휘 밖이다: {value} (아는 값 {sorted(allowed)})"
        )
    return value


def _resolve(dataset: str | None, source_group: str | None) -> tuple[str, str]:
    """어휘 검증 — session_id 가 이 둘에서 결정적으로 유도되므로 오타는 없는 세션을 가리킨다."""
    if dataset not in MINUTE_DATASETS:
        raise SystemExit(f"--dataset 이 1분 원장 어휘 밖이다: {dataset} (아는 값 {sorted(MINUTE_DATASETS)})")
    if dataset not in SCALED_DATASETS:
        # ⚠️ 어휘에 있다고 **구동 레인**은 아니다. start/stop 이 올리고 내리는 서비스
        # 목록은 **공용**이고 `_scale` 은 dataset 을 아예 안 본다 — 그래서 이 세션이
        # 소유하지 않은 서비스를 건드리게 된다. 특히 stop 은 phase 게이트가 이 세션만
        # 보고(claim 0 → 즉시 통과) 큐·outbox 게이트는 전역이라 **살아 있는 price-worker
        # 를 내린다**.
        # ⚠️ **자기 상주 서비스를 갖는 것으로는 이 조건이 안 풀린다** — 선택 레인
        # (news_minute·disclosure_minute·etf_inav_minute)은 자기 Worker 를 소유하지만
        # 여전히 여기 못 든다. `_scale` 이 dataset 별 목록을 받게 되기 전까지는
        # 그대로다(`_OPTIONAL_LANES` 주석).
        raise SystemExit(
            f"--dataset {dataset!r} 는 start/stop-minute-session 의 구동 레인이 아니다"
            f"(아는 값 {sorted(SCALED_DATASETS)}). 선택 레인은 인자가 아니라 env 토글로 "
            f"켠다({', '.join(lane.source_env for lane in _OPTIONAL_LANES)}). "
            f"계획·수집만 하려면 plan-minute-session 과 그 dataset 의 worker 로 한다"
        )
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
    # 배선 오타는 계획 전에 죽는다(_services 와 같은 축). 켜진 레인만 남긴다.
    lane_groups = [(lane, _lane_source_group(lane)) for lane in _OPTIONAL_LANES]
    for lane, group in lane_groups:
        # 목록 결손도 계획 전에 — 계획만 세우고 올릴 서비스가 없으면 그 레인은 하루 종일
        # PLANNED 로 남고, 그 사실이 드러나는 자리가 없다.
        if group is not None:
            _lane_worker_services(lane)
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

    # 선택 레인(뉴스 ALPHA-717 · 공시 ALPHA-875 · iNAV ALPHA-882) — 가격 계획 성공 뒤에 계획한다.
    # ⚠️ 선택 레인 계획이 실패해도 **가격 스케일업은 한다**: 가격 레인까지 세우면 그 레인의
    # 결손이 하루치 가격 결손으로 번진다. 실패한 레인의 Worker 는 세션 부재로 기동 거부
    # 루프를 돌아 실패가 드러나고, exit 는 그 실패를 그대로 실어 스케줄 기록에 남긴다.
    # ⚠️ **가격 레인을 선택 레인 계획보다 먼저 올린다**(ALPHA-882 리뷰 라운드1). 위 문단이
    # 내건 "선택 레인이 실패해도 가격은 진행한다"를 exit code 경로에서만이 아니라 **예외
    # 경로에서도** 성립시킨다 — `plan_session_cli` 의 except 는 (ValueError, OSError) 뿐이라
    # universe 객체를 읽는 S3 의 botocore ClientError 는 그대로 뚫고 나온다. 뒤에 두면 그
    # 한 번에 가격 레인이 통째로 안 뜬다. universe 를 읽는 레인이 생긴 지금 실재하는 경로다.
    _scale(desired=1, force=True)
    # 설명 소비자는 가격 레인과 **같은 조건**으로 바로 뒤에 올린다(ALPHA-910) — 공용 목록에
    # 얹혀 있던 때와 순서·시각·force 가 같다. 선택 레인처럼 자기 계획 성공을 기다리지 않는다:
    # 이 서비스는 세션 원장을 안 보고 큐만 폴링해서, 세션이 없어도 기동을 거부하지 않는다.
    # 목록이 비면 구 task-def 다 — 위 공용 스케일이 이미 이 서비스를 덮었으니 건너뛴다.
    if analysis_services := _analysis_services():
        _scale(desired=1, force=True, services=analysis_services)

    lane_exits = {}
    for lane, group in lane_groups:
        if group is None:
            continue
        lane_exits[lane] = plan_session_cli(
            settings, dataset=lane.dataset, source_group=group,
            session_date=today.isoformat(),
            # ⚠️ **선택 레인이라고 universe 가 다 None 인 게 아니다.** 뉴스·공시는 소스
            # 단위라 안 쓰지만 iNAV 는 `UNIVERSE_DATASETS` 라 planner 가 없으면 **거부한다**
            # (exit 2 → 그 레인이 매 거래일 통째로 안 선다). 통과시키더라도 원장에
            # universe_version="none" 이 박혀 Worker 의 `_session_ready` 가 영영 False 다 —
            # 두 축 모두에서 틀린다. worker 의 `--universe` 와 **같은 객체**여야 한다
            # (terraform 이 둘에 같은 URI 를 준다).
            universe=universe if lane.dataset in UNIVERSE_DATASETS else None,
        )
        if lane_exits[lane] != 0:
            logger.error("%s 세션 계획 실패(exit %d) — 가격 레인은 진행하고 그 Worker 는 "
                         "올리지 않는다(세션 부재 재기동 루프 방지)",
                         lane.dataset, lane_exits[lane])
            continue
        # ⚠️ **그 레인의 스케일업을 다음 레인 계획보다 먼저 한다**(#642 봇 P2). 가격 레인에
        # 쓴 것과 **같은 논증을 한 층 아래에 적용**한 것이다: 스케일업을 루프 뒤로 모으면
        # 뒤 레인의 계획이 예외로 죽을 때(iNAV universe 의 S3 ClientError — `plan_session_cli`
        # 가 안 잡는다) 이미 계획에 성공한 앞 레인이 desired 0 인 채 남는다. 세션은 섰는데
        # Worker 가 없으니 그 레인은 그날 조용히 아무것도 수집하지 않는다.
        # 레인별로 판정한다: 뉴스가 실패해도 iNAV 는 올라가야 한다(서로 독립이다).
        _scale(desired=1, force=True, services=_lane_worker_services(lane))
    # ⚠️ 여러 레인이 실패하면 **첫 실패 코드**를 낸다(0 이 아니면 스케줄 기록에 남는 건
    # 같고, 어느 레인인지는 위 로그가 말한다). 합치거나 최댓값을 쓰면 없는 코드가 나온다.
    return next((code for code in lane_exits.values() if code != 0), 0)


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
    lanes = [(dataset, source_group)]
    for lane, group in ((lane, _lane_source_group(lane)) for lane in _OPTIONAL_LANES):
        if group is not None:
            lanes.append((lane.dataset, group))
    # ⚠️ 레인 Worker 목록을 **게이트 전에 전부 해석한다**(ALPHA-882 리뷰 라운드1). 이 조회는
    # 배선 결손에서 SystemExit 이라(토글은 켜졌는데 목록이 빔), 스케일다운 루프 안에서
    # 처음 부르면 공용 서비스를 0 으로 내린 **뒤** 죽어서 나머지 레인 Worker 가 밤새
    # desired 1 로 남는다 — 게다가 exit 는 "stop 전체 실패" 로 읽힌다. start 와 같은 순서다.
    lane_workers = {lane: _lane_worker_services(lane) for lane in _OPTIONAL_LANES}
    ids = {lane: stable_domain_id("msn", lane[0], lane[1], day.isoformat()) for lane in lanes}

    ledger = MinuteLedger(db=settings.db)
    jobs = JobLedger(db=settings.db)
    try:
        snapshots = {lane: ledger.session_snapshot(session_id=ids[lane]) for lane in lanes}
    except Exception:
        logger.exception("세션 조회 실패: %s", sorted(ids.values()))
        return 2
    # 존재하는 세션만 드레인·게이트 대상이다 — 뉴스 계획이 실패한 날의 뉴스 부재가
    # 가격 레인 종료까지 막으면 안 된다(그날 뉴스 결손은 start 의 exit 가 이미 말했다).
    live = [lane for lane in lanes if snapshots[lane] is not None]
    session_ids = [ids[lane] for lane in live]

    if not live:
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

    # 전 레인을 시도한 뒤 실패를 판정한다 — 첫 실패에서 끊으면 부분 드레인 상태로
    # 끝나고(가격만 DRAINING), 스케줄러는 exit 를 재시도하지 않아 나머지 레인이
    # ACTIVE 로 고립된다. drain 은 멱등이라 재실행이 안전하다.
    drain_failures = []
    for lane in live:
        try:
            requested = ledger.request_drain(
                session_id=ids[lane], now=datetime.now(timezone.utc)
            )
            logger.info("drain 요청: session=%s(%s/%s) phase_before=%s newly_requested=%s",
                        ids[lane], lane[0], lane[1], snapshots[lane]["phase"], requested)
        except Exception:
            logger.exception("drain 요청 실패: %s(%s/%s)", ids[lane], lane[0], lane[1])
            drain_failures.append(ids[lane])
    if drain_failures:
        logger.error("drain 부분 실패 — 내리지 않는다. 재실행이 안전하다(멱등): %s",
                     drain_failures)
        return 2

    timeout_sec = _drain_timeout_sec()
    queues = _gate_queues()
    deadline = time.monotonic() + timeout_sec
    clear_streak = 0
    while True:
        pending = _gate_pending(ledger, jobs, session_ids=list(ids.values()), queues=queues)
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
                timeout_sec, "; ".join(pending), jobs.count_unpublished(), session_ids,
            )
            return 1
        logger.info("대기 중 — %s", "; ".join(pending))
        time.sleep(POLL_INTERVAL_SEC)

    _scale(desired=0, force=False)
    # 설명 소비자 — 공용 목록에 얹혀 있던 때와 **같은 자리에서** 내린다(ALPHA-910). 게이트가
    # 이 서비스의 큐를 안 보는 것도 그대로다(설명 큐는 게이트 밖 — `_gate_pending` 주석).
    if analysis_services := _analysis_services():
        _scale(desired=0, force=False, services=analysis_services)
    # ⚠️ 토글과 무관하게 **목록이 있으면 내린다** — 토글을 끈 날 그 서비스가 떠 있으면
    # 아무도 안 내려 계속 돈다(어제 켜고 오늘 끈 경우가 정확히 그 모양이다). 내리는 방향은
    # 과하게 잡아도 안전하다(이미 0 이면 no-op). 목록은 위에서 이미 해석해 뒀다.
    for workers in lane_workers.values():
        if workers:
            _scale(desired=0, force=False, services=workers)
    logger.info("세션 종료: sessions=%s — 게이트 전건 비었음", session_ids)
    return 0


def _gate_pending(ledger: MinuteLedger, jobs: JobLedger, *,
                  session_ids: list[str], queues: list[str]) -> list[str]:
    """아직 안 끝난 것들의 사람이 읽는 목록. 비면 내려도 된다.

    앞 단계가 안 끝났으면 뒤를 보지 않는다 — Worker 가 아직 window 를 커밋하는 중이면
    그때의 "outbox 0" 은 아직 만들지 않은 것이지 다 나간 것이 아니다.

    ⚠️ **outbox 를 큐보다 나중에 읽는다.** 이 파이프는 순환한다 — Consumer 가 메시지를
    처리하면 그 트랜잭션이 설명 큐용 outbox 행을 새로 만든다. outbox 를 먼저 읽으면 그
    직후 처리된 메시지가 만든 행이 이번 관측에 안 잡히고, 같은 호출의 큐 조회는 이미
    0 이라 "다 끝났음" 으로 인증된다. 순서를 뒤집으면 그 행이 다음 관측이 아니라 **이번**
    관측에 잡힌다(남는 창은 두 조회 사이뿐이고, 연속 확인이 그것도 덮는다).
    """
    phase_pending = []
    for session_id in session_ids:
        snapshot = ledger.session_snapshot(session_id=session_id)
        if snapshot is None:
            # 그 시점에 없는 세션은 게이트 대상이 아니다(뉴스 계획 실패 날). 매 폴링
            # 재확인하므로 **늦게 생긴 세션**(stop 중 start 재실행)은 다음 관측부터
            # 게이트에 들어온다 — 드레인은 안 걸렸으니 상한까지 pending 으로 남아
            # exit 1(안 내림)로 끝난다. 조용히 내리는 것보다 시끄럽게 남는 쪽이다.
            continue
        if snapshot.get("phase") not in DRAINED_PHASES:
            phase_pending.append(f"session {session_id[:12]}… phase={snapshot.get('phase')}")
    if phase_pending:
        return phase_pending

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
