"""구간 설명 — 하루를 [갭] + [요구창] + [나머지] 로 갈라 **시간순**으로 말한다.

세 규율이 이 모듈의 전부다.

1. **거부하지 않고 자른다.** 요구가 장 시각 밖으로 삐져나오면 예외를 던지는 게 아니라
   장 안으로 자르고 사유를 적는다. 밤사이는 이미 `갭` 창으로 항상 따로 나오므로
   "밤사이는 못 한다" 는 거짓이었다 — 전일 종가→시가는 일봉으로 관측 가능하다.
2. **창마다 자기 시점까지의 정보만.** 창 k 의 `as_of` 는 창 k 의 **끝**이다. 뒤 창의
   사건을 앞 창 설명에 쓰면 그건 설명이 아니라 사후확신이다.
3. **크기는 회계, 인과는 미검정.** 5분봉 시간 항등식이 창별 몫을 정하고 합이 맞는다.
   검정 패널의 단위는 거래일이라 창에 인과를 못 붙인다 — 그 사실을 창마다 적는다.

    python -m edge_analysis.statics.interval <ticker6> <instrument_id> <YYYY-MM-DD> \
        <HH:MM> <HH:MM>
"""
from __future__ import annotations

import datetime as dt
import math
import sys

from .layers import SESSION_CLOSE, SESSION_OPEN, decompose
from .windows import Window, build_windows

# 자정 τ = **시각 미상**이다. `lake.taus` 는 사이드카가 없으면 `available_at`(자정)로
# 폴백하는데, 그걸 '장 전' 으로 세면 부재가 갭 창의 알리바이를 위장한다.
UNKNOWN_TAU = dt.time(0, 0)

# 이 몫 미달 창은 층까지 가르지 않는다 — `plain.recent_window` 와 같은 규율.
# 요구창은 사람이 물었으므로 몫과 무관하게 항상 가른다.
FLOOR = 0.20

# 상대 크기를 **낱말**로 말한다. 수치 없는 설명이 순위를 잃으면 아무 말도 아니게 된다.
_RANK_WORD = ("가장 크게", "두 번째로 크게", "세 번째로")

# 다른 창을 **간접 지시**하는 낱말. 창 이름·시각을 그대로 부르면("잔여1 09:00~11:00")
# 요구한 구간을 물은 사람에게 다른 구간을 설명하는 셈이 된다. 시간대 낱말은 맥락을
# 주면서도 설명 대상을 옮기지 않는다 - 요구창이 여전히 주어다.
_WHEN = ((9, 30, "장 초반"), (11, 0, "오전 중반"), (13, 0, "점심 무렵"),
         (14, 30, "오후"), (15, 30, "장 막판"))


def _when_word(t: dt.datetime) -> str:
    """그 시각이 속한 시간대 낱말. 경계는 관례이고 셀별로 바꾸지 않는다."""
    for h, m, w in _WHEN:
        if (t.hour, t.minute) < (h, m):
            return w
    return "장 막판"


class IntervalError(ValueError):
    """구간이 시각으로 읽히지 않는다 — 형식 오류만 던진다(범위는 자른다)."""


def clamp(t0: str, t1: str) -> tuple[str, str, list[str]]:
    """`HH:MM[:SS]` 두 개를 장 안으로 자른다. **거부가 아니라 절단 + 사유.**

    장 전을 요구하면 그건 `갭` 창이 답한다(항상 따로 나온다). 장 후를 요구하면
    5분봉이 없으니 마감으로 자른다 — 없는 해상도를 있는 척하지 않되, 질문 자체를
    되돌려보내지도 않는다.
    """
    def norm(t: str) -> str:
        p = t.strip().split(":")
        if not 2 <= len(p) <= 3 or not all(x.isdigit() for x in p):
            raise IntervalError(f"시각 형식이 아니다: {t!r} (HH:MM 또는 HH:MM:SS)")
        h, m, s = (p + ["0"])[:3]
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"

    a, b, why = norm(t0), norm(t1), []
    if a >= b:
        raise IntervalError(f"구간이 비었다: {a} >= {b}")
    if a < SESSION_OPEN:
        why.append(f"{a[:5]} 는 장 전이라 개장으로 잘랐다 — 그 앞은 `갭` 창이 답한다")
        a = SESSION_OPEN
    if b > SESSION_CLOSE:
        why.append(f"{b[:5]} 는 장 후라 마감으로 잘랐다 — 그 뒤는 5분봉이 없다")
        b = SESSION_CLOSE
    if a >= b:
        why.append("자른 뒤 구간이 비었다 — 갭 창만 답한다")
        a, b = SESSION_OPEN, SESSION_CLOSE
    return a, b, why


def _bars(lake, ticker: str, day: str) -> list[tuple[dt.datetime, float, float]]:
    """(ts, 시가, 종가). 하루치 한 번에 읽는다 — 창마다 질의하면 6배 왕복이다."""
    return [(t, float(o), float(c)) for t, o, c in lake.sql(
        f"SELECT ts, open, close FROM bars_5m WHERE trade_date = DATE '{day}' "
        f"AND regexp_replace(symbol, '\\.(KS|KQ)$', '') = '{ticker}' "
        "AND open > 0 AND close > 0 ORDER BY ts")]


def _gap(lake, ticker: str, day: str) -> float | None:
    """ln(당일 첫 봉 시가 / 전 거래일 마지막 봉 종가).

    **5분봉 자체가 답한다.** 일봉(`s3_price_daily`)을 쓰려다 날짜 커버리지가 달라
    갭이 통째로 미계측이 됐다(실측 000660 06-01). 같은 격자·같은 소스에서 뽑으면
    장중 몫과 갭이 같은 척도가 되고, 합이 하루와 맞는다는 주장이 참이 된다.
    """
    rows = lake.sql(
        "SELECT trade_date, first(open ORDER BY ts) AS o, last(close ORDER BY ts) AS c "
        f"FROM bars_5m WHERE trade_date <= DATE '{day}' AND open > 0 AND close > 0 "
        f"AND regexp_replace(symbol, '\\.(KS|KQ)$', '') = '{ticker}' "
        "GROUP BY 1 ORDER BY 1 DESC LIMIT 2")
    if len(rows) < 2 or str(rows[0][0]) != day:
        return None
    o, pc = float(rows[0][1]), float(rows[1][2])
    return math.log(o / pc) if o > 0 and pc > 0 else None


def _window_ret(bars: list[tuple[dt.datetime, float, float]],
                w: Window) -> tuple[float | None, int]:
    """창 안 봉의 시가→종가 로그수익과 봉 수. 봉이 없으면 (None, 0) — 0 이 아니다."""
    seg = [b for b in bars if w.start <= b[0] < w.end]
    if not seg:
        return None, 0
    return math.log(seg[-1][2] / seg[0][1]), len(seg)


def _context(w: Window, ret: float, before: list[tuple[str, float, str]]) -> list[str]:
    """요구창 앞에서 **더 큰** 움직임이 있었으면 그것을 간접적으로 얹는다.

    사용자 규약: 정보 접근은 이전 시간 전부 가능하고 **설명 대상만** 요구 구간이다.
    앞 구간이 더 합리적인 설명이면 쓸 수 있지만 **직접 호명은 금지** - 창 이름이나
    시각을 부르면 물은 구간이 아니라 다른 구간을 설명하는 답이 된다.
    """
    big = [(when, r) for when, r, _k in before if abs(r) > abs(ret) * 1.5]
    if not big:
        return []
    when, r = max(big, key=lambda x: abs(x[1]))
    same = (r > 0) == (ret > 0)
    if same:
        return [f"{when}부터 이어진 흐름이 이 시간대에도 계속됐어요. "
                "앞선 움직임이 더 컸고, 이 구간은 그 연장선에 있어요."]
    return [f"{when}에 반대 방향으로 더 크게 움직인 뒤였어요. "
            "이 시간대의 움직임은 그 되돌림으로 읽는 편이 자연스러워요."]


def _reasons(w: Window, ret: float, rank: int, roll, inside: list[str],
             after: int) -> list[str]:
    """창 하나의 근거들. **수치 없이**, 한 줄보다 길게. 없는 근거는 말하지 않는다."""
    up = "올랐" if ret > 0 else "내렸"
    out: list[str] = []
    if w.kind == "gap":
        out.append(f"밤사이 정보가 한꺼번에 반영되면서 시가가 전일 종가보다 {up}어요. "
                   "이 구간은 장이 열려 있지 않아 더 잘게 나눌 수 없고, "
                   "그래서 안에서 무엇이 먼저였는지는 가릴 수 없어요.")
    else:
        word = _RANK_WORD[rank] if rank < len(_RANK_WORD) else "비교적 작게"
        out.append(f"이 시간대에 {word} {up}어요. "
                   "같은 날의 다른 시간대와 견줘 본 순서이고, "
                   "얼마나 움직였는지는 위의 표가 말해요.")
    if roll is not None and roll.layers:
        big = max(roll.layers, key=lambda x: abs(x.contribution))
        same = (big.contribution > 0) == (ret > 0)
        out.append(
            f"이 시간대의 움직임은 {big.kind} 흐름과 같은 방향이었어요."
            if same else
            f"{big.kind} 흐름은 반대로 갔는데도 이 종목은 {up}어요."
            " 시장을 따라간 게 아니라는 뜻이에요.")
        if abs(roll.idio) > abs(big.contribution):
            out.append("설명이 붙는 공통 흐름보다 이 종목만의 움직임이 더 컸어요. "
                       "무엇이 그 몫을 만들었는지는 아직 가려지지 않았어요.")
    if inside:
        out.append("이 시간대 안에 공시나 보도가 있었어요. "
                   "다만 그것이 이 움직임을 만들었는지는 확인되지 않았어요 — "
                   "같은 시간에 있었다는 것과 원인이라는 것은 다른 말이에요.")
    elif w.kind != "gap":
        out.append("이 시간대에는 새로 알려진 소식이 없었어요. "
                   "그래서 움직임의 이유를 소식에서 찾을 수는 없어요."
                   + (f" 오늘 나온 소식은 이 시간대보다 뒤였어요." if after else ""))
    return out


def explain(lake, ticker: str, instrument_id: str, day: str,
            t0: str, t1: str) -> str:
    """하루를 창으로 갈라 시간순으로 설명한다. 요구 구간은 하나의 창으로 못박힌다."""
    a, b, why = clamp(t0, t1)
    d = dt.date.fromisoformat(day)
    o = dt.datetime.combine(d, dt.time.fromisoformat(SESSION_OPEN))
    c = dt.datetime.combine(d, dt.time.fromisoformat(SESSION_CLOSE))
    pin = (dt.datetime.combine(d, dt.time.fromisoformat(a)),
           dt.datetime.combine(d, dt.time.fromisoformat(b)))

    taus, unknown = [], 0
    for tau, eid in lake.taus(instrument_id, day):
        if tau is None or tau.time() == UNKNOWN_TAU:
            unknown += 1
            continue
        taus.append((tau, eid))
    ws = build_windows(o, c, taus, pin=pin)

    bars = _bars(lake, ticker, day)
    gap = _gap(lake, ticker, day)
    rets: dict[str, float | None] = {}
    nbars: dict[str, int] = {}
    for w in ws:
        if w.kind == "gap":
            rets[w.name], nbars[w.name] = gap, 0
        else:
            rets[w.name], nbars[w.name] = _window_ret(bars, w)

    live = {k: v for k, v in rets.items() if v is not None}
    tot = sum(live.values())
    scale = sum(abs(v) for v in live.values()) or 1.0
    order = sorted(live, key=lambda k: -abs(live[k]))

    whole = rets.get("갭") is not None and all(
        rets[w.name] is not None for w in ws if w.kind != "gap")
    out = [f"[하루] {ticker} {day} · 창 {len(ws)}개"
           f" · 합{'' if whole else '(부분)'} {tot * 100:+.3f}%p"
           f"{' · 시각 미상 사건 ' + str(unknown) + '건' if unknown else ''}"]
    out += [f"  ! {x}" for x in why]
    # 미계측 창이 하나라도 있으면 **합이 하루가 아니다**. 이 줄을 무조건 찍던 것이
    # 코드가 스스로 거짓을 말한 지점이다 - 갭이 없는 날 합을 하루라고 불렀다.
    out.append("  창은 서로소이고 전 구간을 덮는다 — 몫의 합이 하루와 같다(회계)"
               if whole else
               "  **미계측 창이 있어 이 합은 하루가 아니다** — 덮이지 않은 구간이 남았다")
    # **최대 몫을 선두에 세운다**(narrate 7가드). 이걸 빼면 이 날의 가장 중요한 사실이
    # 산문에서 사라진다 - 실측 091160 07-31: 갭이 +17.33%p 로 하루 +25.41%p 의 68%
    # 인데 산문은 장중 창만 얘기했다. 요구창을 물었어도 최대 창은 말해야 한다.
    if order:
        top = order[0]
        tw = next(w for w in ws if w.name == top)
        if tw.kind == "asked":
            out.append("  요구한 구간이 하루에서 가장 큰 몫이다")
        else:
            # **간접 지시**: 어느 창이 컸는지는 말하되 이름·시각으로 호명하지 않는다.
            when = "밤사이" if tw.kind == "gap" else _when_word(tw.start)
            out.append(f"  하루의 가장 큰 움직임은 {when}에 났다 —"
                       f" 요구한 구간은 그 {'뒤' if tw.start < pin[0] else '앞'}이다")

    for w in ws:
        r, nb = rets[w.name], nbars[w.name]
        when = ("전일 종가~시가" if w.kind == "gap"
                else f"{w.start:%H:%M}~{w.end:%H:%M}")
        head = f"\n── {w.name} · {when}"
        if r is None:
            out.append(head + " · **몫 미계측**")
            out.append("  봉이 없다 — 0 이 아니라 못 쟀다는 뜻이다(부재를 0 으로 쓰면 거짓)")
            continue
        share = abs(r) / scale
        out.append(head + f" · {r * 100:+.3f}%p"
                   + (f" · {nb}봉" if nb else "")
                   + (f" · 사건 {len(w.event_ids)}건" if w.event_ids else ""))
        # 층은 몫이 큰 창과 **요구창**만 가른다 — 작은 창까지 가르면 왕복만 늘고
        # 산문은 같아진다. 요구창은 사람이 물었으니 몫과 무관하게 가른다.
        roll = None
        if w.kind != "gap" and (w.kind == "asked" or share >= FLOOR):
            roll = decompose(lake, ticker, day,
                             clock=(f"{w.start:%H:%M:%S}", f"{w.end:%H:%M:%S}"))
            if roll is None:
                out.append("  층 미계측 — 같은 시각 구간의 과거 표본이 β 최소치 미달")
            else:
                for x in roll.layers:
                    out.append(f"  {x.kind} {x.code} {x.contribution * 100:+.3f}%p")
                out.append(f"  고유 {roll.idio * 100:+.3f}%p")
                # **층이 조용히 없으면 고유가 그걸 삼킨다.** 실측 000660 06-01:
                # 시장 5분봉이 그날 시작해 이전 이력이 0 이라 시장 후보가 아예 없었고,
                # 산출은 '고유 +4.09%p' 라고만 말했다 - 시장을 못 쟀다는 사실이 사라졌다.
                if not any(x.kind == "시장" for x in roll.layers):
                    out.append("  시장 미계측 — 이 시각 구간의 시장 5분봉 이력이 없다."
                               " 고유로 적힌 몫에 시장 몫이 섞여 있다")
                if not any(x.kind == "섹터" for x in roll.layers):
                    out.append("  섹터 미계측 — KRX 업종지수는 5분봉이 없다(실측 0건)")
        after = sum(1 for t, _e in taus if t >= w.end)
        for line in _reasons(w, r, order.index(w.name), roll,
                             list(w.event_ids), after):
            out.append("  · " + line)
        # 요구창에만 앞 구간 맥락을 얹는다 - 다른 창까지 얹으면 서사가 서로를 인용한다.
        if w.kind == "asked":
            before = [("밤사이" if x.kind == "gap" else _when_word(x.start),
                       rets[x.name], x.kind)
                      for x in ws if x.end <= w.start and rets[x.name] is not None]
            for line in _context(w, r, before):
                out.append("  · " + line)
        if w.event_ids:
            out.append("  근거 id: " + " ".join(w.event_ids[:6]))
        out.append(f"  [절단] as_of = {w.end:%H:%M} — 이 시각까지의 정보만 쓴다"
                   " (그 앞 구간은 전부 볼 수 있다: 설명 대상만 이 창이다)")
        out.append("  [인과] 미검정 — 검정 패널의 단위가 거래일이다")
    return "\n".join(out)


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit(__doc__)
    from .duck import CausalLake
    print(explain(CausalLake(), *sys.argv[1:]))


if __name__ == "__main__":       # pragma: no cover
    main()
