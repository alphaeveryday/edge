"""Task instrumentation wrapper — 선택된 MVP 작업의 attempt 를 원장에 기록 (ALPHA-530, 스펙 §6).

등록 작업(카탈로그 24개)의 `run()` 을 감싸 pipeline run·
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
import math
import os
import urllib.request
from collections.abc import Callable

from . import states
from .contracts import ETF_HOLDINGS_KRX_EOD
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


def _num(value) -> bool:
    """유한한 수치인가(bool 제외). malformed 신호(None·문자열·NaN·inf)를 걸러 crash·오판을 막는다."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        # 거대 int(예: 10**400)는 float 변환 자체가 불가라 isfinite 가 터진다. 봉투는 JSON 이라
        # 임의 크기 정수가 들어올 수 있는데, 여기서 안 막으면 **작업이 성공한 뒤** 판정 단계에서
        # 트레이스백으로 죽는다(exit 0 이 크래시로 뒤집힌다, edge-review H — crash-before-gate).
        return False


def _count(value) -> bool:
    """유효한 건수인가 — 유한 수치이면서 **음수 아님**. 음수 카운트는 malformed 다(게이트 우회 방지)."""
    return _num(value) and value >= 0


# PostgreSQL BIGINT 범위. 넘는 값을 그대로 넘기면 UPDATE 가 통째로 실패하는데, 같은 문장에
# task_outcome·data_status 가 실려 있어 **끝난 작업이 PENDING 으로 남아 MISSED 로 오판**된다.
# 카운터 하나 지키자고 판정 축을 잃지 않는다 — 범위 밖은 저장을 포기하고 NULL 로 둔다.
_BIGINT_MIN, _BIGINT_MAX = -(2**63), 2**63 - 1


def _counter(value) -> int | None:
    """원장에 **저장할** 건수 — 유효한 정수 카운트만, 그 외는 None(컬럼 NULL, ALPHA-182).

    0 으로 메우지 않는 이유는 `derive_data_status` 가 근거 부족을 UNKNOWN 으로 남기는 이유와
    같다 — 0 으로 쓰면 대시보드가 "0건 처리"와 "신호 없음"을 못 가른다(Rule 12).

    소수는 절단하지 않고 버린다(`3.7 → 3` 금지). 건수가 소수로 오면 그건 봉투가 깨진 것이지
    반올림할 값이 아니다 — 조용히 절단하면 깨진 계측이 그럴듯한 숫자로 위장한다.

    **판정 게이트(`derive_data_status`)보다 엄격한 것은 의도다.** 판정은 "이 데이터를 믿어도
    되나"를 정하고 여기는 "사람이 읽을 숫자를 쓸까"를 정한다 — 3.7 건이나 BIGINT 를 넘는 건수는
    판정엔 그냥 양수지만 화면에 적을 수 있는 숫자는 아니다. 둘을 억지로 맞추려고 판정 규칙을
    건드리지 마라(Rule 3 — 이 티켓은 판정 무변경이다).

    값이 **있는데 못 쓰는** 경우엔 경고를 남긴다. 결측(None)은 리더가 이미 경고하지만
    (`entry._observe_from_log`), 봉투는 멀쩡한데 값만 깨진 경우는 여기 말고 드러날 곳이 없다
    (Rule 12 — 불확실을 숨기지 말고 드러낸다).
    """
    if value is None:
        return None                      # 신호 없음 — 리더가 이미 경고한 경로다
    if _count(value):
        truncated = int(value)
        if truncated == value and _BIGINT_MIN <= truncated <= _BIGINT_MAX:
            return truncated
    logger.warning("산출 카운터가 유효한 건수가 아니다(%r) — 원장에 NULL 로 남긴다", value)
    return None


def derive_data_status(signals: dict) -> str:
    """산출 데이터 신호 → data_status. **정직하게** — 근거 부족은 UNKNOWN(스펙 §3.3·§6).

    signals(있는 것만):
      exit_code            물리 종료 코드
      request_completed    소스 요청 자체가 정상 완료됐나(bool)
      empty_allowed        데이터셋 계약상 0건이 정상인가(bool)
      records_out          canonical/적재된 유효 건수
      failed_records       탈락/실패 건수(적재 탈락·미등록·모호 등 in-band 유실 전부)
      expected_count       기대 entity 수(스냅샷)
      received_count       수신 unique entity 수
      trading_day          거래일 모순 없음(bool)

    규칙(malformed 신호는 VALID 로 승격되지 않고 crash 하지도 않는다 — edge-review H각도):
      exit_code 결측/≠0             → UNKNOWN (성공 exit 만으로 VALID 금지, 테스트 21)
      완전성 결손(received<expected) → INCOMPLETE
      실패 레코드>0                  → INCOMPLETE / 실패 레코드 비수치 → UNKNOWN(malformed)
      records_out 결측·음수·NaN·비수치 → UNKNOWN
      0건 + (요청완료·계약허용·거래일무모순) → VALID_EMPTY (0건만으로는 불가, 테스트 22)
      0건인데 증명 부족              → UNKNOWN
      유효 건수>0·알려진 결손/실패 없음 → VALID
    """
    # 성공 exit(정확히 정수 0)이 아니면 데이터 상태를 단정하지 않는다. exit_code 결측(None)·비정수·
    # **bool**(False==0 이라 성공으로 위장) 전부 UNKNOWN — 성공을 확인하지 못한 것이다(H각도).
    ec = signals.get("exit_code")
    if not (isinstance(ec, int) and not isinstance(ec, bool) and ec == 0):
        return states.DATA_UNKNOWN

    # 완전성은 기대집합(스냅샷)과 수신집합이 있어야 판정한다. 둘 다 있을 때만 VALID/INCOMPLETE 를
    # 가르고, 없으면 **완전성 미확인**이라 records 가 있어도 VALID 로 단정하지 않는다(정직한 UNKNOWN,
    # 스펙 §6). ETF 3개 수집 작업은 Planner snapshot과 스텝의 received_count가 이 축을 공급한다
    # (ALPHA-611). 나머지 작업은 배선되기 전까지 false-VALID 대신 UNKNOWN이 맞다.
    expected, received = signals.get("expected_count"), signals.get("received_count")
    completeness_known = _count(expected) and _count(received)
    if completeness_known and received < expected:
        return states.DATA_INCOMPLETE
    failed = signals.get("failed_records")
    if failed is not None:
        if not _count(failed):
            return states.DATA_UNKNOWN       # 비수치·음수 실패 신호 — 게이트 우회 대신 UNKNOWN
        if failed > 0:
            return states.DATA_INCOMPLETE

    records_out = signals.get("records_out")
    if not _num(records_out) or records_out < 0:
        return states.DATA_UNKNOWN            # 결측·음수·NaN·비수치 → VALID 로 위장 금지
    if records_out == 0:
        # 정상 0건: 요청완료+계약허용+거래일 무모순 전부 **명시적 True** 일 때만(truthy 문자열·정수·
        # malformed 값을 증명으로 삼지 않는다 — "false"·0·NaN 이 is not False 를 통과하던 우회 차단,
        # edge-review). trading_day 는 미지정(기본 True)이거나 정확히 True 여야 무모순으로 본다.
        if signals.get("request_completed") is True and signals.get("empty_allowed") is True \
                and signals.get("trading_day", True) is True:
            return states.DATA_VALID_EMPTY
        return states.DATA_UNKNOWN           # 0건만으로 VALID_EMPTY 금지(테스트 22)
    return states.DATA_VALID if completeness_known else states.DATA_UNKNOWN


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
        # ⚠️ 여기엔 세 가지가 섞인다: ① 미등록(의도) ② 조회 실패(_safe 가 logger.exception 남김)
        # ③ **run_id 불일치** — 등록됐고 DB 도 살아 있는데 `find_expected_task` 가 못 찾는 경우다.
        # 수동·백필 런은 run_id 가 `make_run_id()` 타임스탬프라 `pipeline_run_id` 와 안 맞아서
        # 계측이 통째로 사라지는데 exit 0 으로 끝난다. 조용히 넘어가면 "계측된 줄 알았다"가
        # 된다(Rule 12) — 원장에 매칭되는 실행으로 돌리려면 `--run-id <pipeline_run_id>` 를 쓴다.
        logger.info("원장 미매칭 — 계측 없이 진행(task=%s run_id=%s)", task_key, run_id)
        return run_fn()
    if expected.get("plan_status") == states.PLAN_SKIPPED:
        # SKIPPED 작업(비거래일 등)은 attempt 를 만들지 않는다 — SKIPPED→attempt 금지 불변식
        # (스펙 §3.2·마이그레이션 주석). SFN 이 그날 다른 데이터셋 때문에 돌아 이 컨테이너가
        # 실행되더라도, 계획상 SKIP 된 작업에 실행 이력을 붙이면 축이 오염된다(edge-review).
        return run_fn()
    expected_task_id = expected["expected_task_id"]
    # 기대값은 Planner가 실행 전에 고정한 snapshot만 정본이다. observer가 expected_count를
    # 자기신고하도록 두면 수집기가 빠뜨린 대상을 분모에서도 줄여 스스로 만점 처리할 수 있다.
    expected_count = _counter(expected.get("expected_count"))
    unknown_completeness = (
        {"expected": expected_count, "received": None, "missing": None}
        if expected_count is not None else None
    )

    arn = ecs_task_arn or _detect_ecs_task_arn()
    sfn_exec = sfn_execution_arn or os.environ.get("OPS_SFN_EXECUTION_ARN")
    sfn_state = sfn_state_name or os.environ.get("OPS_SFN_STATE_NAME")
    attempt_id = _safe(lambda: ledger.record_attempt_start(
        expected_task_id=expected_task_id, ecs_task_arn=arn or "",
        sfn_execution_arn=sfn_exec, sfn_state_name=sfn_state,
    ))

    try:
        exit_code = run_fn()  # ← 본 작업. 예외는 삼키지 않고 그대로 전파한다.
    except BaseException as exc:
        # 예외로 죽어도 attempt 를 **RUNNING 으로 남기지 않는다**(ALPHA-181). 남기면 Reconciler 가
        # 이미 끝난 실행을 STALLED 로 오판하고, STALLED 는 resolve 경로가 없어 영구 OPEN 이다.
        # SystemExit(인자 검증 fail-loud)도 잡아야 해서 BaseException 이다 — 잡되 되던진다.
        if attempt_id is not None:
            _safe(lambda: ledger.record_attempt_end(
                attempt_id, execution_status=states.EXEC_FAILED, exit_code=None,
                failure_reason=f"{type(exc).__name__}: {exc}"[:500],
                data_status=states.DATA_UNKNOWN,
            ))
        _safe(lambda: ledger.update_task_outcome(
            expected_task_id, task_outcome=states.OUTCOME_FAILED,
            data_status=states.DATA_UNKNOWN, current_attempt_id=attempt_id,
            completeness=unknown_completeness,
            # 이 시도는 산출을 세지 못했다 — 앞 시도의 카운터를 지운다. 안 지우면 실패 판정 옆에
            # 성공했던 건수가 남아 대시보드가 "실패했지만 2736건 처리"로 읽는다.
            counters={},
            # 예외 시도도 raw 를 덮어썼을 수 있다 — 카운터와 같은 이유로 앞 시도의 수집 증거를
            # 리셋한다(관측 없음 = EVIDENCE_MISSING). SIGKILL 처럼 이 코드가 아예 못 도는 경우는
            # 남는다 — 그 잔재는 Reconciler freshness 전이(후속 티켓) 소관이다.
            freshness=_freshness_signal(expected, observed=False),
        ))
        raise

    signals = {"exit_code": exit_code}
    if observe_data_fn is not None:
        try:
            signals.update(observe_data_fn(exit_code) or {})
        except Exception:
            logger.exception("data_status 신호 수집 실패 — UNKNOWN 으로 남긴다")
    # observer의 self-report를 허용하지 않고 원장이 고정한 분모로 덮는다. 수신값도 저장 가능한
    # 정수로 정규화해 malformed 신호가 판정·JSON 어디에서도 그럴듯한 숫자로 남지 않게 한다.
    signals["expected_count"] = expected_count
    received_count = _counter(signals.get("received_count"))
    signals["received_count"] = received_count
    completeness = (
        {
            "expected": expected_count,
            "received": received_count,
            "missing": (
                None if received_count is None
                else max(expected_count - received_count, 0)
            ),
        }
        if expected_count is not None else None
    )
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
        completeness=completeness,
        # 판정에 쓴 그 신호를 그대로 싣는다(ALPHA-182) — 대시보드(ALPHA-514)의 건수 열.
        # 매 시도가 두 값을 함께 덮는다(못 쓰면 NULL) — 이 행의 카운터는 항상 **최신 시도의 것**이다.
        counters={"records_out": _counter(signals.get("records_out")),
                  "failed_records": _counter(signals.get("failed_records"))},
        freshness=_freshness_signal(
            expected, observed=signals.get("artifact_observed") is True),
        fulfilled=exit_code == 0,
    ))
    return exit_code


def _freshness_signal(expected: dict, *, observed: bool) -> dict | None:
    """계약 연결 작업의 freshness 를 **매 시도** 덮기 위한 신호(예외 경로 포함).

    관측 시도만 조건으로 걸면 미관측 재시도(raw 는 덮어썼는데 로그를 못 남기고 죽음)가 앞
    시도의 수집 증거를 물려받는다 — 미관측은 EVIDENCE_MISSING 으로 리셋하는 쪽이 엄격한
    방향이다. KRX 는 응답에 기준일 증거가 없어(ALPHA-653) actual 은 항상 None/UNKNOWN 이다.
    """
    if expected.get("dataset_contract_key") != ETF_HOLDINGS_KRX_EOD:
        return None
    return {
        "actual_as_of_date": None,
        "collected": observed,
        "status": states.FRESHNESS_UNKNOWN,
        "reason": (
            states.FRESHNESS_ACTUAL_AS_OF_UNVERIFIED if observed
            else states.FRESHNESS_EVIDENCE_MISSING
        ),
        "evidence": None,
    }


def _safe(fn: Callable):
    """원장 호출을 감싸 예외를 삼킨다 — 원장 장애가 본 작업 흐름을 끊지 않게(스펙 §3.4)."""
    try:
        return fn()
    except Exception:
        logger.exception("원장 기록 실패(무시하고 진행)")
        return None
