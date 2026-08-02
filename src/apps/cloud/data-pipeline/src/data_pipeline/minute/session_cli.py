"""1분 세션 계획·drain 진입점 (ALPHA-698, 계획 §13 CLI).

이 트랙의 실행 표면은 가운데가 비어 있었다 — `run relay`·`run qc-minute-session` 은 있는데
**세션을 만드는 자리**가 없어서, EOD QC 조차 손으로 DB 행을 넣지 않으면 돌릴 수 없었다.
여기가 그 앞 두 칸이다.

```text
plan-minute-session   → 하루치 session + window 를 멱등 생성 (Premarket SFN 이 부를 자리)
drain-minute-session  → phase 를 DRAINING 으로 (EOD SFN 이 부를 자리)
→ Worker 가 ack 하면 DRAINED → run qc-minute-session
```

원장(`MinuteLedger.plan_session`·`request_drain`)이 이미 멱등·CAS 를 다 갖고 있으므로 여기는
**얇은 배선**이다. 판정 로직을 여기 두지 않는다 — 재계획 거부(universe 충돌·drain 이후)도
원장이 예외로 말한다.

⚠️ **universe 는 인자로 받는다.** 가격 세션의 window 범위와 `universe_hash` 가 거기서
나오는데, 실 유니버스 파일을 레포에 고정하는 것은 승인 사항이다(계획 §3 "ETF 승인 목록과
holdings snapshot 승인자"). CLI 가 파일을 **찾아 나서지 않는** 이유가 그것이다 — 무엇을
정본으로 볼지는 운영자가 정하고, 이 코드는 그가 준 파일을 읽을 뿐이다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .models import load_universe, plan_session_windows
from .repository import MinuteLedger, SessionFinalizedError, UniverseConflictError
from .states import MINUTE_DATASETS, SOURCE_GROUPS_BY_DATASET, UNIVERSE_DATASETS

logger = logging.getLogger(__name__)


def plan_session_cli(
    settings, *, dataset: str | None, source_group: str | None,
    session_date: str | None, universe: str | None,
) -> int:
    """`run plan-minute-session --dataset … --source-group … --session-date …`.

    exit: 0=계획됨(신규/재실행 no-op 둘 다) / 1=계획할 수 없음(universe 충돌·drain 이후)
    / 2=계획 자체를 못 함(설정·인자 결손·DB 장애).

    재실행이 0 인 이유: `plan_session` 이 멱등이라 **두 번 부르는 게 정상 운영**이다
    (Premarket SFN 재시도·Worker 재기동). 그걸 실패로 만들면 재시도가 곧 장애가 된다.

    ⚠️ 이 계약은 이 함수에 도달한 뒤에만 성립한다. 설정 파일 자체를 못 읽는 경우
    (`--config` 오타 → `ConfigError`)는 `run.py` 공통 진입부에서 나 프로세스가 1 을 낸다 —
    전 스텝 공통 경로라 여기서 바꾸지 않는다(qc-minute-session 과 같은 단서).
    """
    if settings.db is None:
        logger.error("db 설정 없음 — plan-minute-session 은 세션 원장 필수(DATA_PIPELINE_DB__* 주입)")
        return 2
    missing = [name for name, value in
               (("--dataset", dataset), ("--source-group", source_group),
                ("--session-date", session_date)) if not value]
    if missing:
        logger.error("plan-minute-session 에 %s 가 없다", "·".join(missing))
        return 2

    try:
        # ⚠️ `date.fromisoformat` 은 3.11+ 에서 주 날짜(`2026-W01-1`)도 받는다 — 그러면
        # 다른 연도의 세션이 조용히 생긴다. `strptime` 은 그것과 `20260731` 을 거부한다.
        # 비패딩(`2026-7-31`)은 **받는다**: 운영자가 흔히 치는 형태이고 가리키는 날짜가
        # 같아서, 거부하면 잃는 것만 있다. 막아야 할 건 '다른 날짜로 읽히는' 형식이다.
        planned_date = datetime.strptime(session_date, "%Y-%m-%d").date()
    except ValueError:
        logger.error("--session-date 가 YYYY-MM-DD 가 아니다: %s", session_date)
        return 2

    if dataset not in MINUTE_DATASETS:
        # 오타를 조용히 뉴스 세션으로 흘리면(universe 없이 390 window) 그 dataset 을 처리할
        # Worker 가 없어 하루가 통째로 안 도는데 원장은 정상으로 보인다.
        logger.error("--dataset 이 1분 원장 어휘 밖이다: %s (아는 값 %s)",
                     dataset, sorted(MINUTE_DATASETS))
        return 2
    allowed_sources = SOURCE_GROUPS_BY_DATASET[dataset]
    if source_group not in allowed_sources:
        # 원장의 source_group 은 정본이다 — 어휘 밖 값으로 세션이 서면 그 소스를
        # 처리하는 어댑터·Worker 배선이 없어, dataset 오타와 같은 모양으로 조용히 안 돈다.
        logger.error("--source-group 이 dataset %s 의 어휘 밖이다: %s (아는 값 %s)",
                     dataset, source_group, sorted(allowed_sources))
        return 2
    try:
        universe_model = _load_universe(dataset, universe)
    except (ValueError, OSError) as error:
        logger.error("universe 를 읽을 수 없다: %s", error)
        return 2

    windows = plan_session_windows(planned_date, universe=universe_model)
    ledger = MinuteLedger(db=settings.db)
    try:
        session_id, created = ledger.plan_session(
            dataset=dataset, source_group=source_group, session_date=planned_date,
            universe_version="none" if universe_model is None else universe_model.universe_version,
            universe_hash="none" if universe_model is None else universe_model.universe_hash,
            windows=windows,
        )
    except (UniverseConflictError, SessionFinalizedError) as error:
        # 계획을 못 하는 게 아니라 **하면 안 되는** 상태다 — 그 날짜엔 다른 universe 로
        # 고정된 세션이 있거나 이미 drain 경계를 넘었다. 재시도로 풀리지 않는다.
        logger.error("세션 계획 거부: %s", error)
        return 1
    except Exception:
        logger.exception("세션 계획 실패: %s/%s/%s", dataset, source_group, session_date)
        return 2

    print(json.dumps({
        "session_id": session_id, "created": created, "dataset": dataset,
        "source_group": source_group, "session_date": planned_date.isoformat(),
        "window_count": len(windows),
        # 재실행 no-op 도 성공이다 — 무엇이 실제로 새로 생겼는지는 이 값이 말한다
        "windows": {"first": windows[0][0].isoformat(), "last": windows[-1][1].isoformat()},
    }, ensure_ascii=False, sort_keys=True))
    return 0


def drain_session_cli(settings, *, session_id: str | None) -> int:
    """`run drain-minute-session --session-id <id>` (EOD SFN 이 부른다).

    exit: 0=drain 상태에 도달함(방금 걸었든 이미 걸려 있었든) / 2=요청 자체를 못 함
    (설정·인자 결손·**없는 세션**·DB 장애).

    ⚠️ **이미 DRAINING 이후인 것을 실패로 만들지 않는다.** DB 커밋 뒤 출력 전에 죽은 실행을
    SFN 이 재시도하면 두 번째 호출은 반드시 False 를 받는데, 그걸 exit 1 로 내면 **정상
    재시도가 EOD 흐름을 세운다**(ALPHA-693 의 FINALIZED 재실행과 같은 축이다). 방금 걸었는지는
    exit code 가 아니라 출력의 `drain_requested` 가 말한다.

    없는 세션은 다르다 — 그건 지목이 틀린 것이고 재시도로 낫지 않는다.

    ⚠️ 이 계약은 이 함수에 도달한 뒤에만 성립한다. 설정 파일 자체를 못 읽는 경우
    (`--config` 오타)는 `run.py` 공통 진입부에서 나 프로세스가 1 을 낸다(전 스텝 공통 경로).
    """
    if settings.db is None:
        logger.error("db 설정 없음 — drain-minute-session 은 세션 원장 필수(DATA_PIPELINE_DB__* 주입)")
        return 2
    if not session_id:
        logger.error("--session-id 가 없다 — drain 은 세션 하나를 지목해서 건다")
        return 2

    ledger = MinuteLedger(db=settings.db)
    try:
        snapshot = ledger.session_snapshot(session_id=session_id)
        if snapshot is None:
            logger.error("없는 세션이다 — 지목이 틀렸다: %s", session_id)
            return 2
        requested = ledger.request_drain(
            session_id=session_id, now=datetime.now(timezone.utc)
        )
    except Exception:
        logger.exception("drain 요청 실패: %s", session_id)
        return 2

    print(json.dumps({
        "session_id": session_id, "drain_requested": requested,
        "phase_before": snapshot["phase"],
    }, ensure_ascii=False, sort_keys=True))
    if not requested:
        logger.info("이미 drain 이후다(%s) — 재시도로 보고 성공 처리한다: %s",
                    snapshot["phase"], session_id)
    return 0


def _load_universe(dataset: str, universe: str | None):
    """dataset 이 요구하면 universe 파일을 읽는다. 요구하지 않으면 None(뉴스).

    ⚠️ 가격 세션에서 universe 를 빠뜨리면 **거부한다**. 기본값(None)으로 흘리면 정규장
    390 window 만 계획되고, 시간외 종목이 있는 날엔 그 구간이 **아무 실패 신호 없이**
    누락된다(ALPHA-684 가 `plan_session_windows(universe=…)` 를 필수 인자로 만든 이유와
    같은 축이다).
    """
    if dataset not in UNIVERSE_DATASETS:
        if universe:
            raise ValueError(
                f"dataset {dataset!r} 는 universe 를 쓰지 않는데 --universe 가 주어졌다"
            )
        return None
    if not universe:
        raise ValueError(f"dataset {dataset!r} 는 --universe 가 필요하다")
    return load_universe(Path(universe))
