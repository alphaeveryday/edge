"""스모크 — 실제 셀 하나를 정적 층 전체에 통과시킨다.

산출물이 설계 §0 의 "한 표"다. 엣지 게이트는 아직 패널 추정 전이므로
사건 창의 판정은 전부 **판정불가**로 나온다 — 그게 정직한 현재 상태다.
(성립/불성립은 P4 리팩토링에서 패널이 붙어야 나온다. 설계 §20 단계 E.)

사용:  python -m edge_analysis.statics.ops.smoke <ticker> <instrument_id> <YYYY-MM-DD>
       env: EDGE_RDB_DSN (없으면 사건 없이 갭+잔여만), CAUSAL_BACKFILL_DIR
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, time, timedelta, timezone

from ..core.duck import CausalLake
from ..core.gates import edge_gate
from ..core.narrate import narrate
from ..core.render import Row, render
from ..core.tree import decompose
from ..core.windows import build_windows

KST = timezone(timedelta(hours=9))


def _kst_naive(ts) -> datetime:
    """RDB timestamptz → KST naive. 봉 시각(naive KST)과 좌표계를 맞춘다."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(KST).replace(tzinfo=None)
    return ts


def run(ticker: str, instrument_id: str, day: str) -> str:
    lake = CausalLake()
    print(lake.coverage(), file=sys.stderr)

    d = datetime.strptime(day, "%Y-%m-%d")
    session_open = datetime.combine(d.date(), time(9, 0))
    # 창 끝은 배타 경계다 — 15:30 동시호가 인쇄(그날의 종가)를 포함하려면
    # 마감 시각보다 뒤여야 한다. KRX 전역 상수: [09:00, 15:35).
    session_close = datetime.combine(d.date(), time(15, 35))

    taus = []
    labels: dict[str, str] = {}
    after_close: list[str] = []
    if lake.exists.get("rdb") is True:
        for t, e in lake.taus(instrument_id, day):
            t = _kst_naive(t)
            if t >= session_close:
                after_close.append(str(e))   # 마감 후 보도 — 창이 아니라 알리바이로 간다
            else:
                taus.append((t, str(e)))
        ids = [e for _, e in taus] + after_close
        for eid in ids:
            labels[eid] = eid[:16]
        if ids:
            for eid, code in lake.sql(
                    "SELECT source_event_id, event_type_code FROM rdb.public.source_event "
                    f"WHERE source_event_id IN ({','.join(repr(e) for e in ids)})"):
                labels[str(eid)] = str(code)

    bars = [(ts if ts.tzinfo is None else ts.replace(tzinfo=None), float(c))
            for ts, c in lake.bars(ticker, day)]
    prev = lake.prev_close(ticker, day)

    windows = build_windows(session_open, session_close, taus)
    shares = decompose(bars, prev, windows)

    rows = []
    for s in shares:
        if s.window.kind == "event" or (s.window.kind == "gap" and s.window.event_ids):
            # 패널 게이트 미추정 → N=0 → 판정불가. 부재를 기각으로 위장하지 않는다.
            rows.append(Row(s, treatment=",".join(s.window.event_ids)[:20],
                            verdict=edge_gate(0, None)))
        else:
            rows.append(Row(s))
    table = render(rows)
    story = narrate(ticker=ticker, name=instrument_id[:20], day=day, route=None,
                    rows=rows, grounded=labels, after_close=tuple(after_close))
    return table + "\n\n" + story


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    print(run(sys.argv[1], sys.argv[2], sys.argv[3]))
