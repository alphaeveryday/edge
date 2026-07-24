"""run.py ↔ 원장 이음매 (ALPHA-530). CLI 핸들러(plan-run·reconcile) + 3작업 instrument 헬퍼.

run.py 의 diff 를 작게 유지하려고 여기 모은다. 원장 커넥션은 **lazy** — settings.db 가 없으면
Ledger 를 만들지 않고(instrument 는 투명 통과), plan-run·reconcile 은 fail-loud(원장 DB 필수).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta, timezone

from ..lake import Storage, collection_log_key, quality_log_key
from . import catalog, planner, reconciler, states, wrapper
from .ledger import Ledger

logger = logging.getLogger(__name__)

# Reconciler advisory lock 키(임의 고정 정수 — 이 워크로드 전용 네임스페이스).
_RECONCILE_LOCK = 0x0107_0530
_PLANNER_GRACE = timedelta(minutes=30)  # 예정 직후 Planner 가 뜰 여유 — 이만큼 지나야 결측 판정


def _sched_hhmm() -> tuple[int, int]:
    """daily 스케줄 시각(KST) HH:MM. **env(OPS_DAILY_SCHED_HHMM)로 주입** — statemachine.tf 의
    schedule_expression 과 한 곳에서 오게 해 하드코딩 드리프트를 막는다(edge-review). 기본 15:40."""
    raw = os.environ.get("OPS_DAILY_SCHED_HHMM", "15:40")
    try:
        h, m = raw.split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        logger.warning("OPS_DAILY_SCHED_HHMM 파싱 실패(%s) — 15:40 사용", raw)
        return 15, 40


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
        # 적재 탈락은 전부 in-band 유실이다 — 정체성 결측·미등록 종목·모호 ticker·CHECK 위반 모두
        # 실제 입력 행이 원장에 안 들어간 것이라 failed 로 센다. 안 세면 부분 유실이 VALID 로
        # 위장된다(edge-review G/H). 미등록 종목은 마스터 갭이지만 그 행은 유실이 맞다.
        failed = (len(log.get("failures", []))
                  + log.get("skipped_check_violation", 0)
                  + log.get("skipped_unknown_instrument", 0)
                  + log.get("skipped_ambiguous_ticker", 0)
                  + log.get("skipped_missing_identity", 0))
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


def _due_slot(now_kst: datetime) -> tuple[str, bool] | None:
    """가장 최근에 **예정 시각이 지난** 평일 슬롯의 (run_key, grace_경과). 없으면 None.

    월요일 오전(오늘 미예정)엔 금요일 슬롯을, 주말엔 직전 금요일을 돌려준다 — 아직 예정 전인
    오늘 슬롯을 결측으로 보지 않게(edge-review). grace 경과 여부는 PLANNER_MISSING 판정에 쓴다.
    """
    hour, minute = _sched_hhmm()
    for back in range(7):
        cand = now_kst.date() - timedelta(days=back)
        if cand.weekday() >= 5:
            continue
        sched = datetime.combine(cand, time(hour, minute), tzinfo=planner.KST)
        if now_kst >= sched:
            return f"{catalog.PIPELINE_TYPE}:{cand.isoformat()}", now_kst >= sched + _PLANNER_GRACE
    return None


def reconcile_cli(settings) -> int:
    """주기 Reconciler. advisory lock 으로 중복 실행 방지. 예정 지난 슬롯만 PLANNER_MISSING."""
    ledger = ledger_from_settings(settings)
    if ledger is None:
        raise SystemExit("db 설정 없음 — reconcile 은 원장 DB 필수(DATA_PIPELINE_DB__* 주입)")
    now = _scheduled_time()
    override = os.environ.get("OPS_RUN_KEY")
    due = _due_slot(now.astimezone(planner.KST))
    cluster_arn = os.environ.get("OPS_CLUSTER_ARN")
    with ledger.advisory_lock(_RECONCILE_LOCK) as acquired:
        if not acquired:
            logger.info("reconcile: 다른 인스턴스가 락 보유 — skip")
            return 0
        run_key = override or (due[0] if due else None)
        if run_key is None:
            logger.info("reconcile: 예정 지난 슬롯 없음 — skip")
            return 0
        # 예정+grace 가 지난 슬롯만 결측으로 본다(아직 Planner 가 뜰 시간이면 거짓경보 방지).
        if override or (due and due[1]):
            reconciler.detect_planner_missing(ledger, expected_run_keys=[run_key])
        summary = reconciler.reconcile_run(ledger, run_key=run_key, cluster_arn=cluster_arn, now=now)
        logger.info("reconcile: %s", summary)
    return 0
