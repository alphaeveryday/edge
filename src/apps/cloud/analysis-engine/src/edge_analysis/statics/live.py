"""실시간 축 — 분봉 트리거를 소비해 셀을 돌리고 계보에 기록한다.

## 왜 폴링인가 (그리고 왜 그것이 정직한가)

이 레포의 실행 축은 **EventBridge Scheduler → ECS RunTask(Planner) → Step Functions**
하나다. Lambda·SQS 소비자·API Gateway 는 선례가 없다(infra 전수 grep 0건). 그리고
원장 규약이 명시한다: `aws stepfunctions start-execution` 직접 호출은 **원장에 안 남아
대조 대상이 아니다**. 그래서 이벤트 푸시 축을 새로 세우는 대신, 이미 있는
Reconciler 와 같은 형태(`rate(N minutes)`)로 **미분석 트리거를 소비**한다.

'미분석' 의 정의는 상태 컬럼이 아니라 **계보**다:
`etf_contribution_observation.minute_price_trigger_id` 가 비어 있으면 아직 안 한 것이다.
상태 컬럼을 두면 그것과 계보가 갈라진다 - 원장이 답을 알아야 한다.

## 한 트리거가 한 셀이다

분봉 트리거는 (entity, window_start) 이고 우리 셀은 (ETF, 거래일) 이다. 같은 날 같은
ETF 에 트리거가 여러 번 뜨면 **셀은 하나**다 - 계보 id 가 트리거에서 파생하므로 행은
트리거마다 생기지만, 분석 내용은 그 거래일 전체를 본다. 분봉 단위 인과는 우리 창
분해가 이미 하고 있고(5분봉 τ 창), 트리거는 '언제 봐야 하는가' 만 정한다.

사용:  python -m edge_analysis.statics.live [최대건수]
       (배포에서는 `python -m edge_analysis analyze-minute`)
"""
from __future__ import annotations

import sys

from .record import Cell, Verdicts, open_minute_triggers, record

MAX_PER_RUN = 10        # 한 런에서 소비할 트리거 상한 (ECS task 하나의 벽시계 보호)
HITS = ("**유의**", "verdict=성립", "→ **오늘 적용**", "[함의]")


def _kst_date(ts) -> str:
    """트리거 시각 → 거래일. 분봉 트리거는 UTC 로 실리므로 KST 로 옮긴 뒤 날짜를 뗀다."""
    from datetime import timedelta, timezone
    if isinstance(ts, str):
        from datetime import datetime
        ts = datetime.fromisoformat(ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone(timedelta(hours=9))).date().isoformat()


def run_one(lake, trig: dict, *, ask=None) -> tuple[dict[str, str], str, str]:
    """트리거 하나 → (적재 id 들, 사유, 산출 전문).

    분석 자체는 `etfcell.run` 이 한다 - 실시간 축은 **무엇을 언제** 만 정하고
    분석 로직을 복제하지 않는다.
    """
    from .duck import CausalLake
    from .etfcell import run as run_cell
    from .layers import decompose
    from .route import route_etf

    etf = str(trig["entity_id"])
    day = _kst_date(trig["window_start"])
    text = run_cell(lake or CausalLake(), etf, day, ask)
    honest, _, plain = text.partition("[쉬운 설명] 수치 없이 - 방금 왜 움직였나")
    headline = plain.strip().lstrip("=").strip()

    roll = rt = None
    try:
        roll = decompose(lake or CausalLake(), etf, day)
        rt = route_etf(roll)
    except Exception:                       # noqa: BLE001 - 회계 실패는 사유로만
        pass
    v = Verdicts(
        applied_edges=text.count("→ **오늘 적용**"),
        credible=text.count("[함의]") - text.count("[함의] 없음"),
        significant_market=text.count("**유의**"),
        undecided=text.count("판정불가"),
        route_kind=(rt.kind if rt else ""),
        idio_qualified=bool(roll is None or roll.rho is None or abs(roll.rho) < 0.20),
        bundles=tuple(_bundle_ids(text)))
    cell = Cell(
        etf_instrument_id=_instrument(lake, etf) or etf,
        trade_date=day, honest=honest.strip(), headline=headline,
        verdicts=v, minute_trigger_id=str(trig["trigger_id"]),
        etf_return=(roll.total if roll else None),
        constituents=(len(roll.names) if roll else None),
        reconciliation_error=(roll.rollup_gap if roll else None),
        route_code=(rt.kind if rt else "UNKNOWN"),
        route_reason=(rt.why if rt else "층 분해 실패"),
        event_search=bool(rt and rt.kind in ("섹터", "고유", "혼합")),
        stage={"trigger": {"kind": "minute", "change_rate": trig.get("change_rate"),
                           "threshold": trig.get("threshold"),
                           "policy": trig.get("policy"),
                           "window_start": str(trig.get("window_start"))},
               "layers": [{"kind": x.kind, "name": x.name,
                           "contribution": x.contribution} for x in (roll.layers if roll else ())],
               "idio": (roll.idio if roll else None),
               "rho": (roll.rho if roll else None)})
    ids, why = record(cell)
    return ids, why, text


def _bundle_ids(text: str) -> list[str]:
    import re
    return sorted(set(re.findall(r"\bev_[0-9a-f]{16}\b", text)))


def _instrument(lake, etf: str) -> str:
    """ETF 코드 → instrument_id. 없으면 빈 문자열 - 코드로 폴백하면 FK 가 막는다."""
    try:
        rows = lake.sql("SELECT instrument_id FROM rdb.public.instrument "
                        f"WHERE ticker = '{etf}' LIMIT 1")
    except Exception:                       # noqa: BLE001
        return ""
    return str(rows[0][0]) if rows else ""


def run(*, limit: int = MAX_PER_RUN, ask=None) -> int:
    """미분석 트리거를 소비한다. 반환: 처리 건수.

    **실패한 트리거는 계보가 안 붙으므로 다음 런이 다시 집는다** - 재시도 상태를
    따로 들고 있지 않다. 무한 재시도가 걱정되면 정책은 원장에 두는 것이 맞고,
    지금은 실패 사유를 로그로 드러내는 것이 먼저다(조용한 재시도 금지).
    """
    from ..observability import log
    from .duck import CausalLake
    trigs = open_minute_triggers(limit=limit)
    log("live.triggers", open=len(trigs))
    if not trigs:
        return 0
    lake = CausalLake()
    done = 0
    for t in trigs:
        try:
            ids, why, text = run_one(lake, t, ask=ask)
        except Exception as e:              # noqa: BLE001 - 셀 실패가 런을 죽이지 않는다
            log("live.cell.failed", trigger=t["trigger_id"], error=f"{type(e).__name__}: {e}")
            continue
        if why:
            log("live.record.failed", trigger=t["trigger_id"], reason=why)
            continue
        log("live.cell.recorded", trigger=t["trigger_id"], entity=t["entity_id"],
            hits=sum(text.count(h) for h in HITS), **ids)
        done += 1
    return done


def main() -> int:
    import os
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else MAX_PER_RUN
    ask = None
    if key := os.environ.get("DEEPSEEK_API_KEY"):
        from ..adapters.llm import DeepSeekClient, TracingClient
        ask = TracingClient(DeepSeekClient(
            key, os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))).complete_json
    n = run(limit=limit, ask=ask)
    print(f"[live] 처리 {n}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
