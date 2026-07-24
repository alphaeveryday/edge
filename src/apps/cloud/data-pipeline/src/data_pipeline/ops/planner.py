"""Run Planner — 실행 전 원장 기록 + SFN 시작 (ALPHA-530, 스펙 §5).

EventBridge → Planner → (DB 트랜잭션: pipeline_run + expected_task + snapshot) → commit →
StartExecution. **실행 계획과 AWS 실행 요청을 분리**한다 — SFN 이 안 떠도 pipeline_run 이 남아
"실행 자체가 안 됐다"를 탐지할 수 있게(ECS 안에서 자기 expected_task 를 만들면 이게 불가능, 스펙 §1).

멱등 축:
  run_key            슬롯 1회 계획(Planner 재기동 무해)
  pipeline_run_id    run_key 에서 결정적 파생 → execution_name 도 결정적
  execution_name     StartExecution 멱등(같은 이름 재호출은 기존 실행)

launch 결과(스펙 §5):
  성공                       → LAUNCHED (+ 확인된 sfn_execution_arn)
  ExecutionAlreadyExists     → DescribeExecution 으로 입력 비교: 동일=LAUNCHED, 상이=LAUNCH_CONFLICT
  명확한 실패(잘못된 입력 등) → LAUNCH_FAILED
  불분명(네트워크 등)         → LAUNCH_UNKNOWN (Reconciler 가 execution name 으로 확정)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..db import stable_domain_id
from . import aws, catalog, states
from .ledger import Ledger
from .trading_calendar import is_trading_day

logger = logging.getLogger(__name__)

# KST 는 DST 가 없어 고정 +9 가 정확하다(zoneinfo·tzdata 의존을 피한다).
KST = timezone(timedelta(hours=9))

_ORCH_MAP = {
    "RUNNING": states.ORCH_RUNNING, "SUCCEEDED": states.ORCH_SUCCEEDED,
    "FAILED": states.ORCH_FAILED, "TIMED_OUT": states.ORCH_TIMED_OUT,
    "ABORTED": states.ORCH_ABORTED,
}


@dataclass
class PlanResult:
    pipeline_run_id: str
    run_key: str
    execution_name: str
    trading_day: bool
    launch_status: str
    created: bool                     # 이 호출이 pipeline_run 을 새로 만들었나
    sfn_execution_arn: str | None
    input_hash: str
    conflict: bool = False


def _canonical_input(mode: str, run_id: str) -> str:
    """SFN 입력의 **결정적** 직렬화 — 같은 슬롯 재계획이 같은 문자열·해시를 내게(스펙 §5)."""
    return json.dumps({"mode": mode, "run_id": run_id}, sort_keys=True, separators=(",", ":"))


def _orch(status: str | None) -> str:
    return _ORCH_MAP.get(status or "", states.ORCH_UNKNOWN)


def _exc_is(exc: Exception, name: str) -> bool:
    """예외 종류를 클래스 이름으로 식별 — 실 botocore 예외와 테스트 가짜를 모두 받는다."""
    return type(exc).__name__ == name


def plan_run(
    ledger: Ledger,
    *,
    state_machine_arn: str,
    scheduled_time: datetime,
    mode: str = "incremental",
    sfn_client=None,
    universe_provider=None,
    holidays=None,
    hard_deadline_seconds: int = 21600,
) -> PlanResult:
    """한 슬롯을 계획하고 SFN 을 시작한다. 멱등 — 같은 scheduled_time 재호출은 run 1개만."""
    sfn = sfn_client if sfn_client is not None else aws.stepfunctions_client()

    day = scheduled_time.astimezone(KST).date()
    trading = is_trading_day(day, holidays)
    run_key = f"{catalog.PIPELINE_TYPE}:{day.isoformat()}"
    pipeline_run_id = stable_domain_id("run", run_key)
    execution_name = run_key.replace(":", "-")
    input_json = _canonical_input(mode, pipeline_run_id)
    input_hash = hashlib.sha256(input_json.encode("utf-8")).hexdigest()
    expected_execution_arn = (
        state_machine_arn.replace(":stateMachine:", ":execution:") + ":" + execution_name
    )
    expected_at = scheduled_time.astimezone(timezone.utc).isoformat()
    hard_deadline_at = (
        scheduled_time.astimezone(timezone.utc) + timedelta(seconds=hard_deadline_seconds)
    ).isoformat()

    # ── 계획: pipeline_run + expected_task + snapshot 를 한 트랜잭션으로(스펙 §1) ──
    with ledger.connect_fn(ledger.db) as conn:
        run_id, created = Ledger._create_pipeline_run_tx(
            conn, pipeline_run_id,
            run_key=run_key, execution_name=execution_name, pipeline_type=catalog.PIPELINE_TYPE,
            schedule_slot=run_key, trading_date=day.isoformat(), hard_deadline_at=hard_deadline_at,
            catalog_version=catalog.version(), catalog_content_hash=catalog.content_hash(),
            image_digest=None, input_hash=input_hash, expected_execution_arn=expected_execution_arn,
        )
        if created:
            _plan_expected_tasks(
                conn, run_id, trading=trading, expected_at=expected_at,
                as_of_date=day.isoformat(), universe_provider=universe_provider,
            )
        # `with conn` 정상 종료 = commit. 여기까지 성공해야 아래 StartExecution 이 돈다.

    launch_status, sfn_arn, conflict = _start_execution(
        ledger, sfn, run_id=run_id, state_machine_arn=state_machine_arn,
        execution_name=execution_name, input_json=input_json, input_hash=input_hash,
        expected_execution_arn=expected_execution_arn, pipeline_run_id=pipeline_run_id,
    )
    return PlanResult(
        pipeline_run_id=run_id, run_key=run_key, execution_name=execution_name,
        trading_day=trading, launch_status=launch_status, created=created,
        sfn_execution_arn=sfn_arn, input_hash=input_hash, conflict=conflict,
    )


def _plan_expected_tasks(conn, run_id, *, trading, expected_at, as_of_date, universe_provider):
    for entry in catalog.entries():
        # 비거래일 가격 수집은 SKIPPED(NON_TRADING_DAY) — attempt 안 생기고 MISSED 안 됨(스펙 §3.3).
        skip = (not trading) and entry.dataset == "price_daily"
        plan_status = states.PLAN_SKIPPED if skip else states.PLAN_DUE
        deadline_at = (
            datetime.fromisoformat(expected_at) + timedelta(seconds=entry.deadline_offset_seconds)
        ).isoformat()
        # 선행 없는 raw 작업은 런 시작과 함께 eligible — deadline 뒤 미실행이면 MISSED 판정 대상.
        # 의존 있는 작업(normalize/load)은 eligible_at 을 비워 Reconciler 가 upstream 완료 시 채운다
        # (그전까지 미실행은 MISSED 가 아니라 BLOCKED, 스펙 §7).
        eligible_at = expected_at if (plan_status == states.PLAN_DUE and not entry.depends_on) else None

        snapshot_id = None
        if not skip and universe_provider is not None:
            u = universe_provider(entry.task_key)
            if u:
                snapshot_id = Ledger._create_snapshot_tx(
                    conn, pipeline_run_id=run_id, task_key=entry.task_key,
                    universe_version=u.get("universe_version"),
                    constituent_as_of_date=u.get("as_of_date"),
                    entity_kind=u.get("entity_kind", "ticker"),
                    entity_ids=u.get("entity_ids"), storage_uri=u.get("storage_uri"),
                    content_hash=u.get("content_hash"),
                )
        Ledger._create_expected_task_tx(
            conn, pipeline_run_id=run_id, task_key=entry.task_key, stage=entry.stage,
            dataset=entry.dataset, required=entry.required, plan_status=plan_status,
            expected_at=expected_at, deadline_at=deadline_at, eligible_at=eligible_at,
            expected_as_of_date=as_of_date, expectation_snapshot_id=snapshot_id,
            skip_reason=states.SKIP_NON_TRADING_DAY if skip else None,
        )


def _start_execution(
    ledger, sfn, *, run_id, state_machine_arn, execution_name, input_json, input_hash,
    expected_execution_arn, pipeline_run_id,
) -> tuple[str, str | None, bool]:
    """StartExecution + 멱등/충돌 처리. (launch_status, sfn_execution_arn, conflict) 반환."""
    try:
        resp = sfn.start_execution(
            stateMachineArn=state_machine_arn, name=execution_name, input=input_json
        )
        arn = resp.get("executionArn")
        ledger.set_launch_result(
            run_id, launch_status=states.LAUNCH_LAUNCHED, sfn_execution_arn=arn
        )
        return states.LAUNCH_LAUNCHED, arn, False
    except Exception as exc:
        if _exc_is(exc, "ExecutionAlreadyExists"):
            return _reconcile_already_exists(
                ledger, sfn, run_id=run_id, execution_name=execution_name,
                expected_execution_arn=expected_execution_arn, input_hash=input_hash,
                pipeline_run_id=pipeline_run_id,
            )
        # 명확한 실패(잘못된 입력·이름·한도 초과)와 불분명(네트워크)을 가른다(스펙 §5).
        if _exc_is(exc, "InvalidExecutionInput") or _exc_is(exc, "InvalidName") \
                or _exc_is(exc, "ExecutionLimitExceeded") or _exc_is(exc, "StateMachineDoesNotExist"):
            logger.error("StartExecution 명확 실패: %s", exc)
            ledger.set_launch_result(run_id, launch_status=states.LAUNCH_FAILED)
            return states.LAUNCH_FAILED, None, False
        logger.exception("StartExecution 불분명 실패 — Reconciler 가 확정한다")
        ledger.set_launch_result(run_id, launch_status=states.LAUNCH_UNKNOWN)
        return states.LAUNCH_UNKNOWN, None, False


def _reconcile_already_exists(
    ledger, sfn, *, run_id, execution_name, expected_execution_arn, input_hash, pipeline_run_id,
) -> tuple[str, str | None, bool]:
    """ExecutionAlreadyExists → 예상 ARN describe 후 입력 비교(스펙 §5). 즉시 LAUNCHED 로 보지 않는다."""
    try:
        desc = sfn.describe_execution(executionArn=expected_execution_arn)
    except Exception:
        logger.exception("ExecutionAlreadyExists 후 describe 실패 — 불분명, Reconciler 확정")
        ledger.set_launch_result(run_id, launch_status=states.LAUNCH_UNKNOWN)
        return states.LAUNCH_UNKNOWN, None, False

    existing_input = desc.get("input") or "{}"
    try:
        existing_run_id = json.loads(existing_input).get("run_id")
    except (ValueError, AttributeError):
        existing_run_id = None
    existing_hash = hashlib.sha256(existing_input.encode("utf-8")).hexdigest()

    same = existing_run_id == pipeline_run_id or existing_hash == input_hash
    if same:
        arn = desc.get("executionArn", expected_execution_arn)
        ledger.set_launch_result(
            run_id, launch_status=states.LAUNCH_LAUNCHED, sfn_execution_arn=arn,
            orchestration_status=_orch(desc.get("status")),
        )
        return states.LAUNCH_LAUNCHED, arn, False

    # 다른 입력/다른 run 이 같은 이름을 차지 — 충돌로 드러낸다(조용히 LAUNCHED 로 삼지 않는다).
    ledger.set_launch_result(run_id, launch_status=states.LAUNCH_CONFLICT)
    ledger.open_or_bump_issue(
        issue_type=states.ISSUE_LAUNCH_CONFLICT,
        dedupe_key=f"launch_conflict:{execution_name}",
        scope="run", scope_key=run_id,
        evidence={"execution_name": execution_name, "existing_run_id": existing_run_id,
                  "expected_run_id": pipeline_run_id},
    )
    return states.LAUNCH_CONFLICT, None, True
