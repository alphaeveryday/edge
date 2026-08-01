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
    """하루 분할의 한 조각. kind: gap | event | residual"""
    name: str
    start: datetime         # 포함
    end: datetime           # 배타 (가격은 이 시각의 봉 시가 기준으로 자른다)
    kind: str
    event_ids: tuple[str, ...] = ()


def build_windows(session_open: datetime, session_close: datetime,
                  taus: list[tuple[datetime, str]], *,
                  w_minutes: int = W_MINUTES) -> list[Window]:
    """하루를 [갭] + [사건 창들] + [잔여들] 로 서로소 분할한다.

    - 갭: 전일 종가 → 개장. 장전 사건(τ < open)은 전부 갭 창에 귀속된다 —
      갭은 더 잘리지 않으므로(밤새 정보가 뭉친다) 공변량으로만 좁힌다(설계 §9).
    - 장중 τ 는 각자 창을 얻고, 잔여 구간이 사이를 채운다.
    """
    if session_close <= session_open:
        raise ValueError("장마감이 개장보다 빠르다")
    w = timedelta(minutes=w_minutes)

    gap_events = tuple(e for t, e in sorted(taus) if t < session_open)
    # 같은 시각의 사건들은 한 창을 공유한다 — 0폭 창을 만들지 않는 결정론 병합.
    merged: dict[datetime, list[str]] = {}
    for t, e in sorted(taus):
        if session_open <= t < session_close:
            merged.setdefault(t, []).append(e)
    intraday = sorted(merged.items())

    out: list[Window] = [Window("갭", session_open, session_open, "gap", gap_events)]
    cursor = session_open
    for i, (tau, eids) in enumerate(intraday):
        if tau > cursor:
            out.append(Window(f"잔여{len(out)}", cursor, tau, "residual"))
        nxt = intraday[i + 1][0] if i + 1 < len(intraday) else session_close
        end = min(tau + w, nxt, session_close)
        out.append(Window(f"창@{tau:%H:%M}", tau, end, "event", tuple(sorted(eids))))
        cursor = end
    if cursor < session_close:
        out.append(Window(f"잔여{len(out)}", cursor, session_close, "residual"))

    # 서로소·전피복 검사 — 깨지면 합=1 이 거짓말이 되므로 즉사한다.
    intra = [x for x in out if x.kind != "gap"]
    assert intra[0].start == session_open and intra[-1].end == session_close
    for a, b in zip(intra, intra[1:]):
        assert a.end == b.start, (a, b)
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
    assert sum(1 for w in ws if w.kind == "event") == 3


_selfcheck()

__all__ = ["Window", "build_windows"]
