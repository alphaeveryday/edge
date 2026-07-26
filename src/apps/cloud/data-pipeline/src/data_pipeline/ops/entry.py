"""run.py ↔ 원장 이음매 (ALPHA-530). CLI 핸들러(plan-run·reconcile) + 계측 헬퍼.

run.py 의 diff 를 작게 유지하려고 여기 모은다. 원장 커넥션은 **lazy** — settings.db 가 없으면
Ledger 를 만들지 않고(instrument 는 투명 통과), plan-run·reconcile 은 fail-loud(원장 DB 필수).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta, timezone

from ..lake import Storage, collection_log_prefix, quality_log_prefix
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


def task_key_for(step: str, source: str | None) -> str | None:
    """이 실행이 어느 논리 작업인가. 미등록이면 None(계측 없이 통과, ALPHA-181).

    **정본은 실제로 돌린 CLI 다.** 원장에 남길 것은 이 컨테이너가 *한 일*이지 오케스트레이터가
    *의도한 일*이 아니다 — 둘이 어긋날 때 env 를 따르면 하지도 않은 작업을 FULFILLED 로 만든다.
    `OPS_SFN_STATE_NAME`(env)은 그래서 정체성이 아니라 **드리프트 알람**으로 쓴다: 해소 결과와
    다르면 경고를 남기고 CLI 를 따른다. env 를 정본으로 두면 벤더까지 맞춰 검증해야 하는데
    (`ingest-raw` 의 fmp/bigkinds 처럼 한 스텝이 벤더로 갈린다) 그 규칙은 `catalog.by_cli` 에
    이미 있다 — 두 곳에 두면 갈린다.

    CLI 해소라야 수동 회수도 계측된다(`--run-id <원래 run_id>` 로 원장에 붙이는 경로는 env 없이
    돈다, README 레시피).
    """
    entry = catalog.by_cli(step, source)
    state = os.environ.get("OPS_SFN_STATE_NAME")
    if state:
        by_state = catalog.by_sfn_state(state)
        # 두 해소가 다르면 전부 드리프트다 — 서로 다른 작업(override), state 이름만 바뀌고
        # 카탈로그가 안 따라온 경우(by_state=None), 반대로 등록 state 인데 CLI 가 미등록인
        # 경우(entry=None) 모두. 한쪽만 검사하면 알람이 반쪽이라 덮은 척만 한다.
        if (by_state.task_key if by_state else None) != (entry.task_key if entry else None):
            logger.warning(
                "OPS_SFN_STATE_NAME=%s(%s) 과 실행(%s%s→%s)이 불일치 — 실행을 따른다",
                state, by_state.task_key if by_state else "미등록", step,
                f" --source {source}" if source else "",
                entry.task_key if entry else "미등록")
    return None if entry is None else entry.task_key


def instrument(settings, storage: Storage, task_key: str, run_id: str, run_fn):
    """등록 작업 하나를 원장 계측으로 감싼다. 원장 없으면 run_fn 만 돈다(투명 통과)."""
    ledger = ledger_from_settings(settings)
    observe = None if ledger is None else (lambda ec: _observe_from_log(storage, task_key, run_id, ec))
    return wrapper.instrument(
        run_fn, task_key=task_key, run_id=run_id, ledger=ledger, observe_data_fn=observe
    )


def _load_log(storage: Storage, prefix: str, run_id: str) -> dict | None:
    """dataset 프리픽스 아래에서 이 run_id 의 로그(런당 1건)를 찾아 파싱. 없으면 None.

    ponytail: dataset 프리픽스 전체 LIST(O(런 이력)). 등록 작업이 늘면 런당 LIST 도 같이 는다 —
    이력이 커져 느려지면 날짜 후보(오늘·어제)로 정확 키를 만드는 쪽으로 좁혀라.
    """
    import json

    for key in storage.list_keys(prefix):
        if f"/run_id={run_id}/" in key and key.endswith("log.json"):
            try:
                return json.loads(storage.get_bytes(key).decode("utf-8"))
            except Exception:
                logger.warning("로그 파싱 실패: %s", key)
                return None
    return None


def _log_prefix(entry: catalog.CatalogEntry) -> str:
    """이 작업의 로그가 사는 dataset 프리픽스. 벤더가 있으면 collection_log, 없으면 quality_log.

    키가 아니라 프리픽스인 이유: 날짜 세그먼트(started_date·checked_date)가 **런 시작 UTC
    날짜**라 자정을 넘긴 런에서 우리가 추측한 날짜와 어긋난다. 프리픽스 조립은 lake.storage
    빌더(경로 규약 SSOT)가 한다 — 여기서 키를 문자열로 자르면 파티션이 바뀔 때 조용히 어긋난다.
    """
    dataset = entry.log_partition_dataset()
    if entry.source_vendor:
        return collection_log_prefix(entry.source_vendor, dataset)
    return quality_log_prefix(dataset)


def _observe_from_log(storage: Storage, task_key: str, run_id: str, exit_code: int) -> dict:
    """스텝이 남긴 S3 로그에서 data_status 신호를 뽑는다. 로그 없으면 {}(→ UNKNOWN, 스펙 §6).

    새 계측을 심지 않고 **이미 나오는 신호**를 읽는다(ALPHA-182 정신). 성공 exit 를 자동으로
    VALID 로 올리지 않는다 — derive_data_status 가 증명 규칙을 적용한다.

    **task_key 분기가 없다**(ALPHA-181). 스텝이 로그에 남기는 `ops` 봉투(`records_out`·
    `failed_records`)를 읽을 뿐이다. 이 둘은 스텝만 아는 **판정**이라 여기서 재구성할 수 없다 —
    예컨대 적재의 `skipped_unknown_instrument` 는 in-band 유실이지만 `skipped_self`(ETF 자기보유
    제외)는 정상 동작이다. 그래서 봉투는 그 카운터 옆(스텝 안)에 산다.

    ⚠️ 봉투의 스코프는 **이 런이 재판정한 범위**다(활동 기반). 그래서 입력을 매 런 다시 읽고
    다시 거르는 스텝(적재·정제)은 이미 존재하던 행도 산출로 세지만, 이미 처리된 것을 건너뛰는
    스텝(tag-news·assemble-events)은 no-op 재실행에서 0건 → UNKNOWN 이다. 산출과 유실이 같은
    스코프에서 와야 옛 실패가 산출로 뒤집히지 않기 때문이다(비대칭 금지). 상태 기반 완전성
    ("지금 이 데이터셋이 온전한가")은 스냅샷·완전성 축의 몫이다(ALPHA-490).
    """
    entry = catalog.get(task_key)
    if entry is None:
        return {"exit_code": exit_code}  # 미등록 작업 — 관측 대상이 아니다
    log = _load_log(storage, _log_prefix(entry), run_id)
    if not log:
        return {"exit_code": exit_code}
    ops = log.get("ops")
    # 봉투 없음/불완전 = 신호 미제공. 조용히 낙관값(0건·실패없음)으로 메우지 않는다 — 특히
    # failed_records 결측을 '실패 0'으로 읽으면 derive_data_status 의 실패 게이트를 통째로
    # 건너뛰어 유실이 VALID_EMPTY 로 위장된다. UNKNOWN 으로 남기되 드러낸다(Rule 12).
    if (not isinstance(ops, dict)
            or ops.get("records_out") is None or ops.get("failed_records") is None):
        logger.warning("ops 봉투 없음/불완전(task=%s run_id=%s) — data_status UNKNOWN",
                       task_key, run_id)
        return {"exit_code": exit_code}
    # 요청 완료 증명은 로그 종류마다 다른 곳에서 온다. 수집(collection_log)은 status 가 정본이고
    # **결측이면 완료로 치지 않는다**(중단·스키마 드리프트를 성공으로 위장 금지). 정제·적재
    # (quality_log)는 status 필드 자체가 없고 성공 증명이 exit_code 라, exit_code==0 게이트를
    # 통과한 시점에서 요청은 완료된 것이다.
    completed = (log.get("status") in ("success", "skipped")) if entry.source_vendor else True
    return {
        "exit_code": exit_code,
        "records_out": ops.get("records_out"),
        "failed_records": ops.get("failed_records"),
        "request_completed": completed,
        "empty_allowed": entry.empty_allowed,
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

    키는 `planner.slot_run_key` 로 만든다 — Planner 가 쓰는 바로 그 함수다(ALPHA-564). 여기서
    형식을 따로 조립하면 어긋나는 순간 있지도 않은 슬롯을 찾아 PLANNER_MISSING 오탐이 난다.
    ⚠️ 스케줄이 하나(`OPS_DAILY_SCHED_HHMM`)라는 전제는 그대로다 — 하루 여러 스케줄의 결측
    판정은 이 함수가 슬롯 목록을 돌려주도록 바뀌어야 한다(뉴스 레인이 Planner 를 탈 때, 별건).
    """
    hour, minute = _sched_hhmm()
    for back in range(7):
        cand = now_kst.date() - timedelta(days=back)
        if cand.weekday() >= 5:
            continue
        sched = datetime.combine(cand, time(hour, minute), tzinfo=planner.KST)
        if now_kst >= sched:
            return planner.slot_run_key(sched), now_kst >= sched + _PLANNER_GRACE
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
        # ⚠️ 알려진 사각(ALPHA-565): 주기 reconcile 은 **`_due_slot` 이 만드는 스케줄 슬롯 하나만**
        # 본다. ALPHA-564 로 수동·백필 실행이 자기 슬롯을 갖게 됐는데, 그 키는 `_due_slot` 이
        # 절대 만들지 않으므로 `OPS_RUN_KEY` 로 명시 지정하지 않으면 **영영 대조되지 않는다** —
        # 수동 런이 초기에 죽으면 기대작업이 DUE 로 남은 채 이슈 없이 조용히 통과한다(관대한 쪽).
        # 제대로 된 해소는 "종료되지 않은 런"을 원장에서 훑는 것이고, 그건 새 쿼리라 별건이다.
        # 예정+grace 가 지난 **자동 슬롯**만 결측으로 본다. override 는 특정 런을 reconcile 하려는
        # 수동 지정이라(미래 슬롯일 수 있다) 결측 판정 대상이 아니다(edge-review).
        if not override and due and due[1]:
            reconciler.detect_planner_missing(ledger, expected_run_keys=[run_key])
        summary = reconciler.reconcile_run(ledger, run_key=run_key, cluster_arn=cluster_arn, now=now)
        logger.info("reconcile: %s", summary)
    return 0
