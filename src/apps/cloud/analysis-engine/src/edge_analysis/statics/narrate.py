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
class Edge:
    """채널판 문장의 재료 — 전부 paneltest 가 계산한 값이다.

    가드 (자격 없는 문장은 생성 시점에 죽는다):
      - applied 인데 verdict != 성립 → 게이트 없는 적용 주장 (NarrationError)
      - 성립인데 미적용이면 why_not 필수 — 반증을 침묵으로 삼키면 기각 위장이다
      - 과대식별 모순이면 크기(구간) 인용 금지 — '크기 보류' 어법만 허용
    """
    channel: str                     # 닫힌 어휘의 채널
    event_type: str                  # 사건 타입 (점) 또는 계열 ident
    verdict: str                     # 성립 | 불성립 | 판정불가
    applied: bool                    # 오늘 적용 (INUS 충족 + 환원 미불일치)
    why_not: str = ""                # 성립-미적용의 사유 (필수)
    iset_lo: float | None = None     # 일 단위 식별집합 (CI ∩ (0, 하루 총합])
    iset_hi: float | None = None
    contradiction: bool = False      # 과대식별 모순 — 크기 주장 금지


@dataclass(frozen=True, slots=True)
class GapCovariate:
    """§9 갭 공변량의 재료 — 부분식별. β CI × 직전 미국 세션 → 갭 설명 구간.

    가드: 부재는 reason 필수 (침묵 금지 - 부재의 사유가 곧 백필 요청이다).
    구간 없이 점 β 주장은 아예 표현할 수 없다 (필드가 없다).
    """
    factor: str = "미국지수"
    factor_ret: float | None = None      # 직전 미국 세션 수익률
    n: int = 0                           # β 표본
    beta_lo: float | None = None
    beta_hi: float | None = None
    explained: tuple[float, float] | None = None   # 갭 몫과 교차된 설명 구간
    contradiction: bool = False          # 방향 모순 - 공통충격 설명 0
    reason: str = ""                     # 부재 선언 (factor_ret 이 None 이면 필수)

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
            after_close: tuple[str, ...] = (),
            edges: tuple[Edge, ...] = (),
            gap_cov: GapCovariate | None = None,
            idio: tuple[float, float] | None = None,
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
    refuted_windows: Counter[str] = Counter()   # 같은 라벨 반복은 개수로 접는다
    for r in rows:
        for eid in r.share.window.event_ids:
            if eid not in grounded:
                raise NarrationError(f"접지 안 된 사건 인용: {eid} — 근거를 지어낼 수 없다")
        if r.share.window.kind == "event" and r.verdict == "불성립":
            refuted_windows[" · ".join(sorted({grounded[e] for e in r.share.window.event_ids}))] += 1
    negatives += [f"{lab}{f' (창 ×{n})' if n > 1 else ''}: 그 타입 엣지가 패널에서 "
                  "서지 않는다 — 원인이 아니다" for lab, n in refuted_windows.most_common()]
    if after_close:
        for eid in after_close:
            if eid not in grounded:
                raise NarrationError(f"접지 안 된 사건 인용: {eid} — 근거를 지어낼 수 없다")
        counts = Counter(grounded[e] for e in after_close)
        folded = " · ".join(f"{lab} ×{n}" if n > 1 else lab for lab, n in counts.most_common(4))
        negatives.append(
            f"마감 후 보도 {len(after_close)}건({folded}{' 외' if len(counts) > 4 else ''}): "
            "오늘 수익률은 장중에 이미 실현됐다 — 오늘의 원인이 될 수 없다 (시간 알리바이)")
    gap = next((r for r in rows if r.share.window.kind == "gap"), None)
    intraday_events = [r for r in rows if r.share.window.kind == "event"]
    if gap is not None and abs(gap.share.log_ret) > sum(
            abs(r.share.log_ret) for r in intraday_events) and not gap.share.window.event_ids:
        negatives.append(
            f"하루의 최대 몫이 갭({_pct(gap.share.log_ret)})인데 장전 사건이 없다 — "
            "장중 국내 사건 전체가 주범 후보에서 밀려난다 (시간 알리바이)")
    biggest = max(rows, key=lambda r: abs(r.share.log_ret), default=None)
    if (biggest is not None and biggest.share.window.kind == "residual"
            and abs(biggest.share.log_ret) > abs(total) * 0.5):
        negatives.append(
            f"최대 몫 {_pct(biggest.share.log_ret)} 이 사건 없는 구간"
            f"({biggest.share.window.name})에서 나왔다 — 보도된 사건으로 하루를 "
            "설명하려는 서사는 데이터가 반박한다")
    for e in edges:
        if e.applied and e.verdict != "성립":
            raise NarrationError(f"{e.channel}·{e.event_type}: 게이트 없는 적용 주장 — "
                                 "패널이 서지 않은 엣지는 오늘을 설명할 수 없다")
        if e.verdict == "성립" and not e.applied:
            if not e.why_not:
                raise NarrationError(f"{e.channel}·{e.event_type}: 성립-미적용의 사유가 없다 — "
                                     "반증을 침묵으로 삼키면 기각 위장이다")
            negatives.append(f"{e.channel}·{e.event_type}: 패널은 서지만 오늘 {e.why_not} — "
                             "오늘의 원인 자격 없음")
        elif e.verdict == "불성립":
            negatives.append(f"{e.channel}·{e.event_type}: 그 채널 엣지가 패널에서 서지 않는다 "
                             "— 원인이 아니다")
    out.append("[아닌 것 먼저] " + ("; ".join(negatives) if negatives else "배제된 후보 없음"))

    # ── 3. 몫 — 미설명이 최대면 선두. 서술은 목록이 아니라 요약이다 ─────
    top = sorted(rows, key=lambda r: -abs(r.share.log_ret))[:4]
    rest = [r for r in rows if r not in top]
    share_bits = [f"{r.share.window.name} {_pct(r.share.log_ret)}" for r in top]
    if rest:
        share_bits.append(f"나머지 {len(rest)}창 합 {_pct(sum(r.share.log_ret for r in rest))}")
    # 적용 엣지가 있으면 미설명도 구간이 된다: 하루 총합 − Σ식별집합.
    # 점을 지어내지 않고 크기 층(항등식)과 인과 층(iset)을 화해시키는 유일한 형태.
    # 모순(iset 없는 적용 엣지)이 하나라도 있거나 셀 점귀속이 거절이면 뺄 수 없다.
    applied = [e for e in edges if e.applied]
    if applied and route != "거절" and all(e.iset_lo is not None for e in applied):
        # iset 은 ar(산술) 단위, 몫은 로그 - 이 크기(±3%)에서 격차 <1bp 라 선형으로
        # 통일한다 ([채널] 문장과 같은 렌더). ponytail: 큰 수익률 셀이 오면 로그 정합.
        lin = lambda x: f"{x * 100:+.2f}%p"  # noqa: E731
        exp_lo = sum(e.iset_lo for e in applied)
        exp_hi = sum(e.iset_hi for e in applied)
        unexp_line = (f"미설명 [{lin(unexplained - exp_hi)}, {lin(unexplained - exp_lo)}] "
                      f"— 적용 채널의 식별집합 [{lin(exp_lo)}, {lin(exp_hi)}]을 뺀 구간. "
                      "점이 아니라 구간이 정직하다")
    else:
        unexp_line = (f"미설명 {_pct(unexplained)} — 우리가 설명하지 못하는 몫이고, "
                      "이것을 줄이는 것은 서사가 아니라 데이터다")
    if abs(unexplained) >= max((abs(r.est or 0.0) for r in rows), default=0.0):
        out.append(f"[몫] **{unexp_line}**. 상위: " + " · ".join(share_bits))
    if idio is not None:
        # 20R: 인과가 청구할 수 있는 대상은 원수익이 아니라 **고유요인**이다.
        # 둘을 안 나누면 시장이 끌고 간 날에 종목 사건으로 설명하려 들게 된다.
        i, m = idio
        out.append(f"[대상] 원수익 {_pct(total)} = 시장 {_pct(m)} + 고유 {_pct(i)}. "
                   f"**인과 엣지가 청구할 수 있는 것은 고유 {_pct(i)} 뿐이다** — "
                   f"나머지는 이 종목 사건으로 만들어질 수 없는 몫이다"
                   + (" (부호가 원수익과 반대다 - 시장 대비로는 초과수익)"
                      if i * total < 0 else ""))
    else:
        out.append("[몫] 상위: " + " · ".join(share_bits) + f". {unexp_line}")

    # ── 3¾. 갭 공변량 (§9) — 부분식별. 부재도 문장이다 ──────────────────
    if gap_cov is not None and gap is not None:
        gshare = gap.share.log_ret
        if gap_cov.factor_ret is None:
            if not gap_cov.reason:
                raise NarrationError("갭 공변량 부재에 사유가 없다 - 침묵은 백필 좌표를 지운다")
            out.append(f"[갭] 공변량 미계측 — {gap_cov.reason}. "
                       f"갭 {_pct(gshare)} 는 통째로 미설명에 남는다.")
        elif gap_cov.contradiction:
            out.append(f"[갭] 밤새 {gap_cov.factor} {gap_cov.factor_ret * 100:+.2f}% 는 "
                       f"갭 {_pct(gshare)} 와 방향이 어긋난다 — 공통충격 설명 0, "
                       "갭 전체가 종목 고유·기타 후보다.")
        else:
            e_lo, e_hi = gap_cov.explained
            r_lo, r_hi = gshare - e_hi, gshare - e_lo
            out.append(f"[갭] 밤새 {gap_cov.factor} {gap_cov.factor_ret * 100:+.2f}% × "
                       f"β [{gap_cov.beta_lo:.2f}, {gap_cov.beta_hi:.2f}] (n={gap_cov.n}) → "
                       f"갭 {_pct(gshare)} 중 [{e_lo * 100:+.2f}, {e_hi * 100:+.2f}]%p 는 공통충격 — "
                       f"종목 고유 후보는 [{r_lo * 100:+.2f}, {r_hi * 100:+.2f}]%p 만. "
                       "점이 아니라 구간이 정직하다.")

    # ── 4. 채널판 — 오늘 적용된 엣지만. 크기는 일 단위 식별집합 어법으로 ─
    for e in edges:
        if not e.applied:
            continue
        if route == "거절":
            # 게이트가 이 셀의 점귀속을 거절했다 - 두 줄 위에서 거절한 주장을
            # 여기서 인용하면 산문이 자기모순이다. 존재 판정만 남긴다.
            size = ("크기는 **보류** — 셀 점귀속 거절(요인 오염). 타입 엣지의 존재는 "
                    "패널(타 종목) 소관이라 살아남지만, 오늘 이 종목의 크기는 "
                    "요인 재구성 전에 인용 금지")
        elif e.contradiction:
            size = "크기는 **보류** — SEM 구간이 하루 총합과 모순 (과대식별 검산 실패)"
        elif e.iset_lo is not None and e.iset_hi is not None:
            size = (f"기여는 많아야 {e.iset_hi * 100:+.2f}%p "
                    f"(식별집합 [{e.iset_lo * 100:+.2f}, {e.iset_hi * 100:+.2f}]%p) — "
                    "상한 밖 주장은 금지")
        else:
            size = "크기 미상 (τ̂ 추정 불가) — 존재 판정만 말한다"
        out.append(f"[채널] {e.channel}·{e.event_type}: 패널 성립 · 오늘 취약성 충족 · "
                   f"환원 일치 → **오늘 적용**. {size}.")
    # ── 4½. 성립한 창 — 접지 id 와 구간을 달고서만 ──────────────────────
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
        out.append(f"[모른다] {evs}: 성립·적용된 엣지가 닿지 않은 창 — 기각이 아니라 미지다. "
                   "(사유는 채널판에 - 여기서 지어내지 않는다)")

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

__all__ = ["BaseRate", "Conditional", "Edge", "GapCovariate", "MIN_OPPOSITE",
           "NarrationError", "narrate"]
