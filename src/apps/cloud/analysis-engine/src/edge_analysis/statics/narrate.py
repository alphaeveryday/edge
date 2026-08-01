"""서술기 — 정성 품질을 계약으로 강제한다. 문장마다 계산된 사실이 물려 있다.

STORM 실측이 보여준 정성 실패 두 양식이 설계 근거다:
  base  접지 없는 근거를 지어냈다 (EVT_KR_… 전량 허구)  → 접지 가드
  dyn2  참이지만 정보 없는 이야기 (시장→β→종목)        → 기저율·배제 우선

그래서 이 모듈은 LLM 산문이 아니다. **검증된 값만 문장이 될 수 있고, 자격 없는
문장은 생성 시점에 죽는다**:

  1. 사건 언급 = 접지된 id 만 (NarrationError)
  2. 아닌 것 먼저 — NTSB Findings 규율: 음성 소견이 긍정 주장보다 앞선다
  3. 미설명이 최대 몫이면 **문단 선두** — 전부 설명하려는 충동이 날조의 원인
  4. 판정불가는 "모른다"로 말한다 — 기각으로 위장 금지
  5. 조건 문장 = 반사실 쌍 + positivity(반대 사례 수) + 교호항 유의 셋 다 갖출 때만
  6. 수치는 표(render.Row)와 같은 객체에서 나온다 — 산문과 표가 어긋날 수 없다

설계: causal-attribution-design.md §12(거절이 기능) · §14(반사실 쌍) · §2(괴리 문장).
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .frame import PathVerdict
from .gates import Route
from .render import Row

MIN_OPPOSITE = 5    # positivity: 반대 상태 사례가 이보다 적으면 반사실 문장 금지


class NarrationError(ValueError):
    """자격 없는 문장. 서술 단계에서 죽어야 날조가 산출물에 닿지 않는다."""


@dataclass(frozen=True, slots=True)
class Conditional:
    """반사실 쌍의 재료 — 전부 CATE 층이 계산한 값이다."""
    state_name: str          # 사전 고정 목록의 조건 이름 (예: 포지셔닝)
    observed_pct: float      # 이 상태에서의 기여 (%p)
    counterfactual_pct: float  # 반대 상태였다면 (%p)
    n_opposite: int          # 반대 상태의 역사 사례 수 (positivity)
    interaction_significant: bool


@dataclass(frozen=True, slots=True)
class BaseRate:
    """조건부 기저율 — '이상이 아니라 규칙'을 말할 자격."""
    bucket: str              # 예: "실적 상회 × 외국인 누적 p90+"
    n: int
    mean_pct: float
    today_inside: bool       # 오늘이 조건부 분포 안인가


def _pct(logret: float) -> str:
    return f"{(math.exp(logret) - 1) * 100:+.2f}%p"


def narrate(*, ticker: str, name: str, day: str, route: Route | None, rows: list[Row],
            grounded: dict[str, str], premium: PathVerdict | None = None,
            conditional: Conditional | None = None,
            baserate: BaseRate | None = None) -> str:
    """셀 하나의 최종 서술. 표(render)와 같은 Row 에서 조립된다."""
    total = sum(r.share.log_ret for r in rows)
    unexplained = sum(r.unexplained for r in rows)
    out: list[str] = [f"[셀] {ticker} {name} · {day} · 하루 {_pct(total)}"]

    # ── 0. 괴리 판정 (ETF 셀) — 프레임의 심장 분기가 첫 문장이다 ─────────
    if premium is not None:
        if premium.basket_moved:
            out.append(f"[경로] 바스켓이 움직였다 (NAV {_pct(premium.nav_return)}, "
                       f"괴리 {_pct(premium.premium_return)}) — ETF 이야기가 아니라 "
                       "종목 이야기다. 귀속은 구성종목으로 내려간다.")
        else:
            out.append(f"[경로] **수급 단독** (괴리 {_pct(premium.premium_return)} > "
                       f"NAV {_pct(premium.nav_return)}) — 펀더멘털 무관, 되돌림 후보. "
                       "유일하게 ETF 고유의 발견이다.")

    # ── 1. 귀속 형태 — 거절이 기능이다 ──────────────────────────────────
    route_say = {
        "점추정": "게이트 전부 통과 — 점추정 자격.",
        "구간만": "노출 분산 또는 시각 분리 결손 — 구간만 말한다.",
        "배제만": "예고 드리프트 또는 노출 내생 — 배제만 정직하다.",
        "거절": "요인 오염(지수 비중) — 이 셀의 점귀속은 거절된다. 구조적 한계이지 데이터 부족이 아니다.",
        # None = 게이트 입력이 아직 안 계산됐다. 미계산을 판정으로 위장하지 않는다.
        None: "게이트 입력(노출 분산·사전 드리프트·지수 비중) 미계산 — 귀속 형태 판정 전. 아래는 정적 분해만이다.",
    }
    out.append(f"[귀속 형태] {route_say[route]}")

    # ── 2. 아닌 것 먼저 (NTSB) ──────────────────────────────────────────
    negatives: list[str] = []
    for r in rows:
        for eid in r.share.window.event_ids:
            if eid not in grounded:
                raise NarrationError(f"접지 안 된 사건 인용: {eid} — 근거를 지어낼 수 없다")
        if r.share.window.kind == "event" and r.verdict == "불성립":
            evs = " · ".join(grounded[e] for e in r.share.window.event_ids)
            negatives.append(f"{evs}: 그 타입 엣지가 패널에서 서지 않는다 — 원인이 아니다")
    gap = next((r for r in rows if r.share.window.kind == "gap"), None)
    intraday_events = [r for r in rows if r.share.window.kind == "event"]
    if gap is not None and abs(gap.share.log_ret) > sum(
            abs(r.share.log_ret) for r in intraday_events) and not gap.share.window.event_ids:
        negatives.append(
            f"하루의 최대 몫이 갭({_pct(gap.share.log_ret)})인데 장전 사건이 없다 — "
            "장중 국내 사건 전체가 주범 후보에서 밀려난다 (시간 알리바이)")
    out.append("[아닌 것 먼저] " + ("; ".join(negatives) if negatives else "배제된 후보 없음"))

    # ── 3. 몫 — 미설명이 최대면 선두 ────────────────────────────────────
    share_bits = [f"{r.share.window.name} {_pct(r.share.log_ret)}" for r in rows]
    unexp_line = (f"미설명 {_pct(unexplained)} — 우리가 설명하지 못하는 몫이고, "
                  "이것을 줄이는 것은 서사가 아니라 데이터다")
    if abs(unexplained) >= max((abs(r.est or 0.0) for r in rows), default=0.0):
        out.append(f"[몫] **{unexp_line}**. 분해: " + " · ".join(share_bits))
    else:
        out.append("[몫] " + " · ".join(share_bits) + f". {unexp_line}")

    # ── 4. 성립한 것 — 접지 id 와 구간을 달고서만 ───────────────────────
    positives: list[str] = []
    for r in rows:
        if r.verdict == "성립" and r.est is not None:
            evs = " · ".join(f"{grounded[e]}({e})" for e in r.share.window.event_ids)
            iv = (f" 구간 [{_pct(r.lo)}, {_pct(r.hi)}]"
                  if r.lo is not None and r.hi is not None else "")
            positives.append(f"{r.share.window.name}: {evs} → 기여 {_pct(r.est)}{iv} "
                             f"(창의 몫 {_pct(r.share.log_ret)} 이 상한)")
    if positives:
        out.append("[성립] " + "; ".join(positives))

    # ── 5. 모르는 것 — 부재를 기각으로 위장하지 않는다 ──────────────────
    unknown = [r for r in rows if r.verdict == "판정불가"]
    if unknown:
        # 같은 라벨의 반복은 개수로 접는다 — 서술은 목록이 아니라 요약이다.
        counts = Counter(grounded[e] for r in unknown for e in r.share.window.event_ids)
        evs = " · ".join(f"{lab} ×{n}" if n > 1 else lab
                         for lab, n in counts.most_common())
        out.append(f"[모른다] {evs}: 패널 표본이 없어 판정불가 — 기각이 아니라 미지다.")

    # ── 6. 조건 — 자격 셋을 전부 갖출 때만 ──────────────────────────────
    if conditional is not None:
        if not conditional.interaction_significant:
            raise NarrationError(f"{conditional.state_name}: 교호항이 유의하지 않다 — "
                                 "조건을 말하면 서사이지 조건부 인과가 아니다")
        if conditional.n_opposite < MIN_OPPOSITE:
            raise NarrationError(f"{conditional.state_name}: 반대 사례 {conditional.n_opposite}건 "
                                 f"< {MIN_OPPOSITE} — positivity 없이 반사실은 외삽이다")
        out.append(f"[조건] {conditional.state_name} 상태라 {conditional.observed_pct:+.2f}%p. "
                   f"정상이었다면 {conditional.counterfactual_pct:+.2f}%p 였을 것 "
                   f"(반대 사례 {conditional.n_opposite}건 확인).")

    # ── 7. 기저율 — 이상이 아니라 규칙인가 ──────────────────────────────
    if baserate is not None:
        verdictt = ("조건부 분포 **안** — 이상이 아니라 규칙이다. 새 원인이 필요 없다"
                    if baserate.today_inside else
                    "조건부 분포 **밖** — 초과분만 새 원인으로 설명한다")
        out.append(f"[기저율] {baserate.bucket}: 과거 {baserate.n}건 평균 "
                   f"{baserate.mean_pct:+.2f}%p. 오늘은 {verdictt}.")

    return "\n".join(out)


def _selfcheck() -> None:
    from datetime import datetime
    from .tree import Share
    from .windows import Window
    o = datetime(2026, 6, 1, 9, 0)
    g = Share(Window("갭", o, o, "gap", ()), 0.02)
    ev = Share(Window("창@10:00", datetime(2026, 6, 1, 10, 0),
                      datetime(2026, 6, 1, 10, 15), "event", ("e1",)), -0.006)
    rest = Share(Window("잔여", datetime(2026, 6, 1, 10, 15),
                        datetime(2026, 6, 1, 15, 35), "residual"), -0.001)
    rows = [Row(g), Row(ev, treatment="e1", verdict="판정불가"), Row(rest)]
    txt = narrate(ticker="000660", name="SK하이닉스", day="2026-06-01",
                  route="구간만", rows=rows, grounded={"e1": "국방 AI 진출"})
    assert "[아닌 것 먼저]" in txt and "시간 알리바이" in txt         # 갭 지배 → 알리바이
    assert txt.index("[아닌 것 먼저]") < txt.index("[몫]")            # 음성이 먼저
    assert "기각이 아니라 미지" in txt                                # 판정불가 어법
    assert "미설명" in txt
    # 가드: 접지 없는 인용은 죽는다.
    try:
        narrate(ticker="x", name="y", day="d", route="구간만",
                rows=[Row(ev, verdict="성립", est=-0.005)], grounded={})
        raise AssertionError("접지 가드가 안 걸렸다")
    except NarrationError:
        pass
    # 가드: positivity 없는 반사실은 죽는다.
    try:
        narrate(ticker="x", name="y", day="d", route="점추정", rows=rows,
                grounded={"e1": "t"},
                conditional=Conditional("포지셔닝", -1.8, -0.4, 2, True))
        raise AssertionError("positivity 가드가 안 걸렸다")
    except NarrationError:
        pass


_selfcheck()

__all__ = ["BaseRate", "Conditional", "MIN_OPPOSITE", "NarrationError", "narrate"]
