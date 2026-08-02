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
from datetime import date, datetime, timezone
from pathlib import Path

from .models import load_universe, plan_session_windows
from .repository import MinuteLedger, SessionFinalizedError, UniverseConflictError

logger = logging.getLogger(__name__)

# universe 가 필요한 dataset — 가격은 종목별 거래시간이 window 범위를 정한다(ALPHA-684).
# 뉴스는 소스 단위라 유니버스 개념이 없다(정규장 390 고정).
_UNIVERSE_DATASETS = frozenset({"price_minute"})


def plan_session_cli(
    settings, *, dataset: str | None, source_group: str | None,
    session_date: str | None, universe: str | None,
) -> int:
    """`run plan-minute-session --dataset … --source-group … --session-date …`.

    exit: 0=계획됨(신규/재실행 no-op 둘 다) / 1=계획할 수 없음(universe 충돌·drain 이후)
    / 2=계획 자체를 못 함(설정·인자 결손·DB 장애).

    재실행이 0 인 이유: `plan_session` 이 멱등이라 **두 번 부르는 게 정상 운영**이다
    (Premarket SFN 재시도·Worker 재기동). 그걸 실패로 만들면 재시도가 곧 장애가 된다.
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
        planned_date = date.fromisoformat(session_date)
    except ValueError:
        logger.error("--session-date 가 YYYY-MM-DD 가 아니다: %s", session_date)
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

    exit: 0=요청됨 / 1=요청할 상태가 아님(이미 DRAINING 이후이거나 없는 세션)
    / 2=요청 자체를 못 함(설정·인자 결손·DB 장애).

    1 과 0 을 가르는 이유: `request_drain` 은 `PLANNED|ACTIVE` 에서만 참이다. 이미 넘어간
    세션에 또 요청하는 건 **무해하지만 무의미**하고, SFN 이 그걸 "내가 방금 걸었다"로
    읽으면 뒤따르는 대기·QC 타이밍을 잘못 잡는다.
    """
    if settings.db is None:
        logger.error("db 설정 없음 — drain-minute-session 은 세션 원장 필수(DATA_PIPELINE_DB__* 주입)")
        return 2
    if not session_id:
        logger.error("--session-id 가 없다 — drain 은 세션 하나를 지목해서 건다")
        return 2

    ledger = MinuteLedger(db=settings.db)
    try:
        requested = ledger.request_drain(
            session_id=session_id, now=datetime.now(timezone.utc)
        )
    except Exception:
        logger.exception("drain 요청 실패: %s", session_id)
        return 2

    print(json.dumps({"session_id": session_id, "drain_requested": requested},
                     ensure_ascii=False, sort_keys=True))
    if not requested:
        logger.warning(
            "drain 요청이 적용되지 않았다 — 이미 DRAINING 이후이거나 없는 세션이다: %s",
            session_id,
        )
    return 0 if requested else 1


def _load_universe(dataset: str, universe: str | None):
    """dataset 이 요구하면 universe 파일을 읽는다. 요구하지 않으면 None(뉴스).

    ⚠️ 가격 세션에서 universe 를 빠뜨리면 **거부한다**. 기본값(None)으로 흘리면 정규장
    390 window 만 계획되고, 시간외 종목이 있는 날엔 그 구간이 **아무 실패 신호 없이**
    누락된다(ALPHA-684 가 `plan_session_windows(universe=…)` 를 필수 인자로 만든 이유와
    같은 축이다).
    """
    if dataset not in _UNIVERSE_DATASETS:
        if universe:
            raise ValueError(
                f"dataset {dataset!r} 는 universe 를 쓰지 않는데 --universe 가 주어졌다"
            )
        return None
    if not universe:
        raise ValueError(f"dataset {dataset!r} 는 --universe 가 필요하다")
    return load_universe(Path(universe))
