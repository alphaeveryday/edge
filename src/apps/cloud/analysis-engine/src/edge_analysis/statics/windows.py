"""창 생성 — 결정론. 창을 고르는 것은 종속변수를 고르는 것이라 p-hacking 이다.

입력은 τ 목록과 전역 상수 W_MINUTES 뿐이고, 사이 구간은 잔여로 자동 채워진다.
선택 지점 0. 같은 입력이면 언제 돌려도 같은 분할이 나온다.

겹침 규칙(결정론): 사건 창은 [τ, min(τ+w, 다음 τ, 장마감)). 두 τ 가 w 안에
겹치면 앞 창이 다음 τ 에서 잘린다 — 창이 서로소여야 합=1 이 성립한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .vocab import W_MINUTES


@dataclass(frozen=True, slots=True)
class Window:
    """하루 분할의 한 조각. kind: gap | event | residual | asked"""
    name: str
    start: datetime         # 포함
    end: datetime           # 배타 (가격은 이 시각의 봉 시가 기준으로 자른다)
    kind: str
    event_ids: tuple[str, ...] = ()


def _intraday(start: datetime, end: datetime,
              items: list[tuple[datetime, list[str]]], w: timedelta,
              seq: list[int]) -> list[Window]:
    """한 구간 안을 [사건 창들] + [잔여들] 로 서로소 분할한다. 잔여 번호는 공유한다.

    `build_windows` 에서 떼어냈다 - 요구 구간(pin)이 들어오면 하루가 여러 구간으로
    갈리고 각 구간에 같은 규칙을 적용해야 한다. 규칙을 두 번 쓰면 두 규칙이 된다.
    """
    out: list[Window] = []
    cursor = start
    inside = [(t, e) for t, e in items if start <= t < end]
    for i, (tau, eids) in enumerate(inside):
        if tau > cursor:
            seq[0] += 1
            out.append(Window(f"잔여{seq[0]}", cursor, tau, "residual"))
        nxt = inside[i + 1][0] if i + 1 < len(inside) else end
        stop = min(tau + w, nxt, end)
        out.append(Window(f"창@{tau:%H:%M}", tau, stop, "event", tuple(sorted(eids))))
        cursor = stop
    if cursor < end:
        seq[0] += 1
        out.append(Window(f"잔여{seq[0]}", cursor, end, "residual"))
    return out


# 가격 측정 격자 (5분봉). **이보다 좁은 창은 만들지 않는다** - 만들면 채울 수 없는
# 칸이 생기고 산출이 "몫 미계측" 으로 뒤덮인다(실측 000660 06-01: τ 46개가 분 단위라
# 15:12~15:12 같은 0폭 창이 났다). τ 를 그 사건이 든 봉의 시작으로 맞추는 것이 옳다:
# 11:03 의 사건은 11:00 봉 안에서 일어났고, 우리가 가진 해상도가 그것이다.
GRID_MINUTES = 5


def _snap(t: datetime, grid: int) -> datetime:
    """τ 를 그 사건이 든 봉의 시작으로 내림. 정보를 버리는 게 아니라 **격자를 인정**한다."""
    return t.replace(minute=t.minute - t.minute % grid, second=0, microsecond=0)


def build_windows(session_open: datetime, session_close: datetime,
                  taus: list[tuple[datetime, str]], *,
                  w_minutes: int = W_MINUTES, grid_minutes: int = GRID_MINUTES,
                  pin: tuple[datetime, datetime] | None = None) -> list[Window]:
    """하루를 [갭] + [사건 창들] + [잔여들] 로 서로소 분할한다.

    - 갭: 전일 종가 → 개장. 장전 사건(τ < open)은 전부 갭 창에 귀속된다 —
      갭은 더 잘리지 않으므로(밤새 정보가 뭉친다) 공변량으로만 좁힌다(설계 §9).
    - 장중 τ 는 각자 창을 얻고, 잔여 구간이 사이를 채운다.
    - `pin=(t0, t1)`: 사람이 **요구한 구간**을 하나의 창으로 못박는다. 그 안의 τ 는
      자기 창을 얻지 못하고 요구창에 흡수된다 - 안 그러면 요구한 구간이 쪼개져
      "이 구간을 설명해달라" 는 질문에 다른 구간으로 답하게 된다. 밖은 요구창을
      경계로 삼아 같은 규칙으로 잘리고, 전체는 여전히 서로소·전피복이다.
    """
    if session_close <= session_open:
        raise ValueError("장마감이 개장보다 빠르다")
    w = timedelta(minutes=w_minutes)

    gap_events = tuple(e for t, e in sorted(taus) if t < session_open)
    # 같은 시각의 사건들은 한 창을 공유한다 — 0폭 창을 만들지 않는 결정론 병합.
    # 같은 **봉**에 든 사건들도 한 창을 공유한다 - '같은 시각' 을 격자로 넓힌 것이다.
    merged: dict[datetime, list[str]] = {}
    for t, e in sorted(taus):
        if session_open <= t < session_close:
            merged.setdefault(max(_snap(t, grid_minutes), session_open), []).append(e)
    intraday = sorted(merged.items())

    out: list[Window] = [Window("갭", session_open, session_open, "gap", gap_events)]
    seq = [0]
    if pin is None:
        out += _intraday(session_open, session_close, intraday, w, seq)
    else:
        a, b = pin
        if not session_open <= a < b <= session_close:
            raise ValueError(f"요구 구간이 장 안에 없다: [{a}, {b})")
        asked = tuple(sorted(e for t, es in intraday if a <= t < b for e in es))
        out += _intraday(session_open, a, intraday, w, seq)
        out.append(Window(f"요구@{a:%H:%M}-{b:%H:%M}", a, b, "asked", asked))
        out += _intraday(b, session_close, intraday, w, seq)

    # 서로소·전피복 검사 — 깨지면 합=1 이 거짓말이 되므로 즉사한다.
    intra = [x for x in out if x.kind != "gap"]
    assert intra[0].start == session_open and intra[-1].end == session_close
    for x, y in zip(intra, intra[1:]):
        assert x.end == y.start, (x, y)
    return out


def _selfcheck() -> None:
    o = datetime(2026, 7, 15, 9, 0)
    c = datetime(2026, 7, 15, 15, 30)
    taus = [(datetime(2026, 7, 15, 6, 3), "e-pre"),        # 장전 → 갭
            (datetime(2026, 7, 15, 10, 0), "e1"),
            (datetime(2026, 7, 15, 10, 10), "e2"),         # e1 창을 10:10 에서 자른다
            (datetime(2026, 7, 15, 14, 50), "e3")]
    ws = build_windows(o, c, taus)
    ws2 = build_windows(o, c, list(reversed(taus)))         # 입력 순서 무관 = 결정론
    assert [(w.name, w.start, w.end) for w in ws] == [(w.name, w.start, w.end) for w in ws2]
    assert ws[0].event_ids == ("e-pre",)
    e1 = next(w for w in ws if w.event_ids == ("e1",))
    assert e1.end == datetime(2026, 7, 15, 10, 10)          # 겹침 절단

    # 격자 스냅: 분 단위 τ 가 봉보다 좁은 창을 만들지 않는다.
    dense = [(datetime(2026, 7, 15, 11, 1), "a"), (datetime(2026, 7, 15, 11, 3), "b"),
             (datetime(2026, 7, 15, 11, 4), "c")]
    dw = [x for x in build_windows(o, c, dense) if x.kind == "event"]
    assert len(dw) == 1, dw                                  # 한 봉 안이면 한 창
    assert dw[0].start == datetime(2026, 7, 15, 11, 0)       # 봉 시작으로 스냅
    assert dw[0].event_ids == ("a", "b", "c")
    assert all(x.end - x.start >= timedelta(minutes=5)
               for x in build_windows(o, c, dense) if x.kind != "gap")
    assert sum(1 for w in ws if w.kind == "event") == 3

    # 요구 구간(pin): 하나의 창으로 못박히고, 그 안의 τ 는 흡수된다.
    pin = (datetime(2026, 7, 15, 9, 50), datetime(2026, 7, 15, 11, 0))
    pw = build_windows(o, c, taus, pin=pin)
    ask = next(x for x in pw if x.kind == "asked")
    assert (ask.start, ask.end) == pin
    assert ask.event_ids == ("e1", "e2"), ask.event_ids      # 안의 둘을 흡수
    assert not any(x.kind == "event" and set(x.event_ids) & {"e1", "e2"} for x in pw)
    assert sum(1 for x in pw if x.kind == "asked") == 1
    # 갭은 여전히 자기 창이고, 장전 사건은 거기 남는다
    assert pw[0].kind == "gap" and pw[0].event_ids == ("e-pre",)
    # 요구 구간이 경계면(개장/마감)에 붙어도 0폭 잔여를 만들지 않는다
    edge = build_windows(o, c, taus, pin=(o, datetime(2026, 7, 15, 10, 0)))
    assert all(x.start < x.end for x in edge if x.kind != "gap")
    # 장 밖 요구는 창 생성기가 아니라 호출자가 자른다 - 여기선 즉사한다
    try:
        build_windows(o, c, taus, pin=(datetime(2026, 7, 15, 8, 0), o))
    except ValueError:
        pass
    else:                                                    # pragma: no cover
        raise AssertionError("장 밖 pin 을 통과시켰다")


_selfcheck()

__all__ = ["Window", "build_windows"]
