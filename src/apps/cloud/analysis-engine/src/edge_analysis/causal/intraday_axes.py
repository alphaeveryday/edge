"""P1 일중 축 — statics 의 시간 분해를 지문으로 승격한다.

에이전트 층 감사(2026-08-01)에서 나온 결함 1의 수술이다: P1 은
intraday_shape·intraday_timing 을 "원장 미보유"로 선언해 왔는데, statics
(5분봉 3.7년 + τ 사이드카)가 생기면서 그 선언은 거짓이 됐다. 결과적으로
**가장 강한 가설 킬러 셋 — 갭 지배 · 사건 없는 최대 몫 · 마감 후 알리바이 —
이 P2 프롬프트에 실리지 않았다.** 지문의 존재 이유가 "후보를 죽일 재료"인데
제일 잘 죽이는 재료가 빠져 있던 것이다.

실패는 부재로 돌아간다(빈 dict + 로그) — P1 의 placeholder 가 남아
"못 쟀다 + 사유"가 산출물에 남는다. 침묵 금지 규율은 로그가 진다.
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone

from ..observability import log
from .contracts import Axis

_KST = timezone(timedelta(hours=9))


def measure(lake, ticker: str, instrument_id: str, day: str) -> dict[str, Axis]:
    """statics 레이크로 일중 두 축을 잰다. 어떤 실패든 {} — placeholder 유지.

    lake: statics.duck.CausalLake 호환 (exists · taus · bars · prev_close).
    """
    try:
        from ..statics.tree import decompose
        from ..statics.windows import build_windows

        d = datetime.strptime(day, "%Y-%m-%d")
        o = datetime.combine(d.date(), time(9, 0))
        c = datetime.combine(d.date(), time(15, 35))   # 마감 동시호가 포함 배타 경계

        taus: list[tuple[datetime, str]] = []
        after_close = 0
        if lake.exists.get("rdb") is True:
            for t, e in lake.taus(instrument_id, day):
                t = t.astimezone(_KST).replace(tzinfo=None) if t.tzinfo else t
                if t >= c:
                    after_close += 1               # 창이 아니라 알리바이로 간다
                else:
                    taus.append((t, str(e)))

        bars = [(ts.replace(tzinfo=None) if ts.tzinfo else ts, float(px))
                for ts, px in lake.bars(ticker, day)]
        shares = decompose(bars, lake.prev_close(ticker, day),
                           build_windows(o, c, taus))
    except Exception as exc:  # noqa: BLE001 — 측정 실패는 부재이지 셀 실패가 아니다
        log("causal.intraday_axes.unavailable", ticker=ticker, day=day,
            reason=f"{type(exc).__name__}: {exc}")
        return {}

    pct = lambda lr: (math.exp(lr) - 1.0) * 100.0  # noqa: E731
    total = sum(s.log_ret for s in shares)
    gap = next(s for s in shares if s.window.kind == "gap")
    intraday_lr = sum(s.log_ret for s in shares if s.window.kind != "gap")
    big = max(shares, key=lambda s: abs(s.log_ret))
    n_event = sum(1 for s in shares if s.window.kind == "event")

    shape_kills: list[str] = []
    if abs(gap.log_ret) > sum(abs(s.log_ret) for s in shares if s.window.kind != "gap"):
        shape_kills.append("장중 국내 사건이 주도했다는 부류 - 갭(밤)이 하루를 지배한다")
    if big.window.kind == "residual" and abs(big.log_ret) > abs(total) * 0.5:
        shape_kills.append(
            f"보도된 사건이 주도했다는 서사 - 최대 몫 {pct(big.log_ret):+.2f}%p 가 "
            f"사건 없는 구간({big.window.name})에서 나왔다")

    timing_kills: list[str] = []
    if after_close:
        timing_kills.append(
            f"마감 후 보도 {after_close}건을 원인으로 세우는 가설 - "
            "오늘 수익률은 장중에 이미 실현됐다")

    return {
        "intraday_shape": Axis(
            name="intraday_shape", available=True,
            value={"gap_pct": round(pct(gap.log_ret), 3),
                   "intraday_pct": round(pct(intraday_lr), 3),
                   "biggest_window": big.window.name,
                   "biggest_is_eventless": big.window.kind == "residual"},
            says=(f"갭 {pct(gap.log_ret):+.2f}%p · 장중 {pct(intraday_lr):+.2f}%p · "
                  f"최대 몫 {big.window.name} {pct(big.log_ret):+.2f}%p"
                  + (" (사건 없는 구간)" if big.window.kind == "residual" else "")),
            kills=tuple(shape_kills)),
        "intraday_timing": Axis(
            name="intraday_timing", available=True,
            value={"event_windows": n_event, "after_close": after_close},
            says=f"장중 사건 창 {n_event}개 · 마감 후 보도 {after_close}건",
            kills=tuple(timing_kills)),
    }


__all__ = ["measure"]
