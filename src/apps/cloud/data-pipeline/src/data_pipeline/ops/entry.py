"""run.py ↔ 원장 이음매 (ALPHA-530). CLI 핸들러(plan-run·reconcile) + 3작업 instrument 헬퍼.

run.py 의 diff 를 작게 유지하려고 여기 모은다. 원장 커넥션은 **lazy** — settings.db 가 없으면
Ledger 를 만들지 않고(instrument 는 투명 통과), plan-run·reconcile 은 fail-loud(원장 DB 필수).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from ..lake import Storage, collection_log_key, quality_log_key
from . import catalog, planner, reconciler, states, wrapper
from .ledger import Ledger

logger = logging.getLogger(__name__)

# Reconciler advisory lock 키(임의 고정 정수 — 이 워크로드 전용 네임스페이스).
_RECONCILE_LOCK = 0x0107_0530


def ledger_from_settings(settings) -> Ledger | None:
    """settings.db 가 있으면 Ledger, 없으면 None(원장 미설정 — instrument 는 통과)."""
    db = getattr(settings, "db", None)
    return None if db is None else Ledger(db)


def _scheduled_time() -> datetime:
    """스케줄 시각 — EventBridge 가 넣는 env(OPS_SCHEDULED_TIME, ISO) 또는 지금(UTC)."""
    raw = os.environ.get("OPS_SCHEDULED_TIME")
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning("OPS_SCHEDULED_TIME 파싱 실패(%s) — 현재시각 사용", raw)
    return datetime.now(timezone.utc)


def instrument(settings, storage: Storage, task_key: str, run_id: str, run_fn):
    """3작업 중 하나를 원장 계측으로 감싼다. 원장 없으면 run_fn 만 돈다(투명 통과)."""
    ledger = ledger_from_settings(settings)
    observe = None if ledger is None else (lambda ec: _observe_from_log(storage, task_key, run_id, ec))
    return wrapper.instrument(
        run_fn, task_key=task_key, run_id=run_id, ledger=ledger, observe_data_fn=observe
    )


def _load_log(storage: Storage, prefix: str, run_id: str) -> dict | None:
    """dataset 프리픽스 아래에서 이 run_id 의 로그(런당 1건)를 찾아 파싱. 없으면 None."""
    import json

    for key in storage.list_keys(prefix):
        if f"/run_id={run_id}/" in key and key.endswith("log.json"):
            try:
                return json.loads(storage.get_bytes(key).decode("utf-8"))
            except Exception:
                logger.warning("로그 파싱 실패: %s", key)
                return None
    return None


def _observe_from_log(storage: Storage, task_key: str, run_id: str, exit_code: int) -> dict:
    """스텝이 남긴 S3 로그에서 data_status 신호를 뽑는다. 로그 없으면 {}(→ UNKNOWN, 스펙 §6).

    새 계측을 심지 않고 **이미 나오는 신호**를 읽는다(ALPHA-182 정신). 성공 exit 를 자동으로
    VALID 로 올리지 않는다 — derive_data_status 가 증명 규칙을 적용한다.
    """
    if task_key == "PRICE_COLLECTION_KIS":
        log = _load_log(
            storage, collection_log_key("kis", "price_daily", "", run_id).split("/started_date=")[0] + "/",
            run_id)
        if not log:
            return {"exit_code": exit_code}
        status = log.get("status")
        return {
            "exit_code": exit_code,
            "records_out": log.get("records_saved"),
            "failed_records": log.get("records_failed_symbols", 0),
            "request_completed": status in ("success", "skipped"),
            # 거래일에 가격 0건은 정상이 아니다 — VALID_EMPTY 를 막아 UNKNOWN 으로 남긴다.
            "empty_allowed": False,
        }
    dataset = "price_daily_load" if task_key == "LOAD_PRICE_DAILY" else "price_daily"
    log = _load_log(
        storage, f"operations_archive/data_quality_logs/dataset={dataset}/", run_id)
    if not log:
        return {"exit_code": exit_code}
    if task_key == "LOAD_PRICE_DAILY":
        records_out = (log.get("created", 0) + log.get("updated", 0) + log.get("already_present", 0))
        failed = len(log.get("failures", [])) + log.get("skipped_check_violation", 0)
    else:  # NORMALIZE_PRICE
        records_out = log.get("records_passed")
        failed = log.get("records_failed", 0)
    return {
        "exit_code": exit_code, "records_out": records_out, "failed_records": failed,
        "request_completed": True, "empty_allowed": False,
    }


# ── CLI 핸들러 ────────────────────────────────────────────
def plan_run_cli(settings) -> int:
    """EventBridge → Planner. 상태머신 ARN·원장 DB 필수(없으면 fail-loud). launch 성공만 exit 0."""
    arn = os.environ.get("OPS_STATE_MACHINE_ARN")
    if not arn:
        raise SystemExit("OPS_STATE_MACHINE_ARN 없음 — plan-run 은 상태머신 ARN 필수")
    ledger = ledger_from_settings(settings)
    if ledger is None:
        raise SystemExit("db 설정 없음 — plan-run 은 원장 DB 필수(DATA_PIPELINE_DB__* 주입)")
    result = planner.plan_run(ledger, state_machine_arn=arn, scheduled_time=_scheduled_time())
    logger.info(
        "plan-run: run=%s launch=%s created=%s trading=%s",
        result.pipeline_run_id, result.launch_status, result.created, result.trading_day,
    )
    # LAUNCHED 만 성공. FAILED/CONFLICT/UNKNOWN 은 비0 으로 드러낸다(fail-loud, Rule 12).
    return 0 if result.launch_status == states.LAUNCH_LAUNCHED else 1


def reconcile_cli(settings) -> int:
    """주기 Reconciler. advisory lock 으로 중복 실행 방지. PLANNER_MISSING 도 확인."""
    ledger = ledger_from_settings(settings)
    if ledger is None:
        raise SystemExit("db 설정 없음 — reconcile 은 원장 DB 필수(DATA_PIPELINE_DB__* 주입)")
    day = _scheduled_time().astimezone(planner.KST).date()
    run_key = os.environ.get("OPS_RUN_KEY") or f"{catalog.PIPELINE_TYPE}:{day.isoformat()}"
    with ledger.advisory_lock(_RECONCILE_LOCK) as acquired:
        if not acquired:
            logger.info("reconcile: 다른 인스턴스가 락 보유 — skip")
            return 0
        # schedule 상 있어야 할 슬롯이 원장에 없으면 PLANNER_MISSING(스펙 §7).
        reconciler.detect_planner_missing(ledger, expected_run_keys=[run_key])
        summary = reconciler.reconcile_run(ledger, run_key=run_key)
        logger.info("reconcile: %s", summary)
    return 0
