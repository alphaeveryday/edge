"""Task instrumentation wrapper — 선택된 MVP 작업의 attempt 를 원장에 기록 (ALPHA-530, 스펙 §6).

3작업(PRICE_COLLECTION_KIS·NORMALIZE_PRICE·LOAD_PRICE_DAILY)의 `run()` 을 감싸 pipeline run·
task key·SFN state/execution·ECS Task ARN·시각·exit code·execution status·data status·failure
reason 를 남긴다. **원장 기록 실패가 본 작업을 실패시키지 않는다**(스펙 §3.4) — 모든 원장 호출은
예외를 삼키고 진행한다.

성공 exit code 를 자동으로 VALID 로 바꾸지 않는다(스펙 §6·§3.3). 신호가 부족하면 UNKNOWN 이다.

미등록 작업(expected_task 없음)·원장 미설정 환경은 **투명하게 통과**한다 — 기존 수집·정제
태스크가 원장 없이도 그대로 돌아야 하므로(회귀 테스트로 보장, 스펙 §6).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from collections.abc import Callable

from . import states
from .ledger import Ledger

logger = logging.getLogger(__name__)


def _detect_ecs_task_arn() -> str | None:
    """ECS Task ARN 을 컨테이너 메타데이터에서 읽는다(Fargate). 실패하면 None.

    ARN 은 attempt 멱등키의 축이자 "가짜 attempt 금지"의 근거다(스펙 §6) — 못 얻으면 attempt 를
    만들지 않고(record_attempt_start 가 None), Reconciler 가 ECS 증거로 사후 복구한다.
    """
    override = os.environ.get("OPS_ECS_TASK_ARN")
    if override:
        return override
    base = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if not base:
        return None
    try:
        with urllib.request.urlopen(f"{base}/task", timeout=2) as resp:  # noqa: S310 (내부 링크로컬)
            return json.loads(resp.read().decode("utf-8")).get("TaskARN")
    except Exception:
        logger.warning("ECS Task ARN 조회 실패 — attempt 없이 진행(Reconciler 복구)")
        return None


def derive_data_status(signals: dict) -> str:
    """산출 데이터 신호 → data_status. **정직하게** — 근거 부족은 UNKNOWN(스펙 §3.3·§6).

    signals(있는 것만):
      exit_code            물리 종료 코드
      request_completed    소스 요청 자체가 정상 완료됐나(bool)
      empty_allowed        데이터셋 계약상 0건이 정상인가(bool)
      records_out          canonical/적재된 유효 건수
      failed_records       탈락/실패 건수
      expected_count       기대 entity 수(스냅샷)
      received_count       수신 unique entity 수
      trading_day          거래일 모순 없음(bool)

    규칙:
      exit≠0                         → UNKNOWN (실행 실패 — 데이터 상태 단정 안 함)
      필수 신호 부재                 → UNKNOWN (exit0 만으로 VALID 금지, 테스트 21)
      완전성 결손(received<expected) → INCOMPLETE
      실패 레코드 있음               → INCOMPLETE
      0건 + (요청완료·계약상 허용·거래일 무모순) → VALID_EMPTY (0건만으로는 불가, 테스트 22)
      0건인데 위 증명 부족           → UNKNOWN
      유효 건수>0·결손/실패 없음     → VALID
    """
    exit_code = signals.get("exit_code")
    if exit_code is not None and exit_code != 0:
        return states.DATA_UNKNOWN

    received = signals.get("received_count")
    expected = signals.get("expected_count")
    if received is not None and expected is not None and received < expected:
        return states.DATA_INCOMPLETE
    if (signals.get("failed_records") or 0) > 0:
        return states.DATA_INCOMPLETE

    records_out = signals.get("records_out")
    if records_out is None:
        return states.DATA_UNKNOWN          # 신호 부족 — exit0 만으로 VALID 금지
    if records_out == 0:
        if signals.get("request_completed") and signals.get("empty_allowed") \
                and signals.get("trading_day", True):
            return states.DATA_VALID_EMPTY  # 정상 0건: 요청완료+계약허용+거래일 무모순 전부 입증
        return states.DATA_UNKNOWN          # 0건만으로 VALID_EMPTY 금지(테스트 22)
    return states.DATA_VALID


def instrument(
    run_fn: Callable[[], int],
    *,
    task_key: str,
    run_id: str,
    ledger: Ledger | None,
    ecs_task_arn: str | None = None,
    sfn_execution_arn: str | None = None,
    sfn_state_name: str | None = None,
    observe_data_fn: Callable[[int], dict] | None = None,
) -> int:
    """run_fn(실제 스텝)을 원장 계측으로 감싼다. run_fn 의 exit code 를 **그대로** 반환한다.

    ledger=None(원장 미설정) 또는 expected_task 부재(미등록)면 계측 없이 run_fn 만 돈다.
    """
    if ledger is None:
        return run_fn()

    expected = _safe(lambda: ledger.find_expected_task(run_id=run_id, task_key=task_key))
    if not expected:
        # 미등록 작업 또는 원장 조회 실패 — 본 작업만 돌린다(투명 통과, 스펙 §6).
        return run_fn()
    expected_task_id = expected["expected_task_id"]

    arn = ecs_task_arn or _detect_ecs_task_arn()
    sfn_exec = sfn_execution_arn or os.environ.get("OPS_SFN_EXECUTION_ARN")
    sfn_state = sfn_state_name or os.environ.get("OPS_SFN_STATE_NAME")
    attempt_id = _safe(lambda: ledger.record_attempt_start(
        expected_task_id=expected_task_id, ecs_task_arn=arn or "",
        sfn_execution_arn=sfn_exec, sfn_state_name=sfn_state,
    ))

    exit_code = run_fn()  # ← 본 작업. 여기서 던지면 계측이 삼키지 않고 그대로 전파한다.

    signals = {"exit_code": exit_code}
    if observe_data_fn is not None:
        try:
            signals.update(observe_data_fn(exit_code) or {})
        except Exception:
            logger.exception("data_status 신호 수집 실패 — UNKNOWN 으로 남긴다")
    data_status = derive_data_status(signals)
    exec_status = states.EXEC_SUCCEEDED if exit_code == 0 else states.EXEC_FAILED

    if attempt_id is not None:
        _safe(lambda: ledger.record_attempt_end(
            attempt_id, execution_status=exec_status, exit_code=exit_code,
            failure_reason=None if exit_code == 0 else "step_nonzero_exit",
            data_status=data_status,
        ))
    # 실행 성공/실패(outcome)와 데이터 상태(data_status)는 별개 축이다 — 데이터가 INCOMPLETE 여도
    # 실행이 성공했으면 outcome=FULFILLED 다(attempt 를 실패로 바꾸지 않는다, 스펙 §3.2·시나리오 D).
    _safe(lambda: ledger.update_task_outcome(
        expected_task_id,
        task_outcome=states.OUTCOME_FULFILLED if exit_code == 0 else states.OUTCOME_FAILED,
        data_status=data_status, current_attempt_id=attempt_id,
        completeness=signals.get("completeness"),
        fulfilled=exit_code == 0,
    ))
    return exit_code


def _safe(fn: Callable):
    """원장 호출을 감싸 예외를 삼킨다 — 원장 장애가 본 작업 흐름을 끊지 않게(스펙 §3.4)."""
    try:
        return fn()
    except Exception:
        logger.exception("원장 기록 실패(무시하고 진행)")
        return None
