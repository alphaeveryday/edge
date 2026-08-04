"""ETF 라우팅 — 층 분해가 **어디로 인과 워크플로우를 보낼지** 정한다.

## 왜 라우팅인가

ETF 하루 변동을 시장·섹터·고유로 가른 다음, 어느 층이 지배하느냐에 따라 물어야 할
질문이 완전히 다르다. 지금까지는 그 분기가 없어서 시장이 끌고 간 날에도 종목 사건을
찾으러 갔다 - 실측(042700 07-31): 하루의 77% 가 시장인데 9간선 전부 종목 가설이었다.

    시장 지배    -> 시장 워크플로우: 밤사이 해외 팩터 · 장중 시장 사건 · 국면
                    (종목 가설을 세우지 않는다. 세우면 시장 충격을 종목 이야기로 위장)
    섹터 지배    -> 섹터 워크플로우: 그 업종의 공통 처치 (정책·수요·가격)
    고유 지배    -> 종목 워크플로우: |기여| 상위 3종목에 개별 인과 시행
    괴리 단독    -> ETF 고유: 수급/유동성. 구성종목으로 내려가지 않는다

## 지배 판정은 회계가 한다 (에이전트가 아니라)

층 분해는 항등식이고 `|기여|/Σ|기여|` 는 계산이다. 판정자가 개입할 자리가 없다.
동률(어느 층도 임계 미달)이면 **혼합**으로 라우팅하고 그 사실을 말한다 - 억지로
하나를 고르면 나머지가 조용히 사라진다.
"""
from __future__ import annotations

from dataclasses import dataclass

DOMINANT = 0.55        # 이 비중 이상이면 그 층이 지배 (전역 상수)
TOP_NAMES = 3          # 고유 지배 시 개별 시행을 돌릴 종목 수
# 종목 기여는 **고유 총량 대비**로 본다. 절대값으로 두면 조용한 날에 대상이 0 이
# 되고 격한 날에 전 종목이 들어온다 - 라우팅이 변동성에 끌려다닌다.
MIN_NAME_SHARE = 0.10  # |종목 기여| / |고유| 이 이보다 작으면 설명할 것이 없다


@dataclass(frozen=True, slots=True)
class Route:
    """라우팅 결정. 판정이 아니라 **회계 결과**다 - 층 비중이 곧 이 값이다."""

    kind: str                    # 시장 | 섹터 | 고유 | 혼합 | 괴리단독
    share: float                 # 지배 층의 |기여| 비중
    layer_name: str              # 그 층의 이름 (섹터면 업종명)
    targets: tuple[str, ...]     # 고유 라우팅일 때 종목 티커 상위 N
    why: str                     # 사람이 읽는 한 문장
    breakdown: tuple[tuple[str, float], ...]   # (층, 기여) 전량 - 접지


def route_etf(roll, premium=None) -> Route:
    """`layers.Rollup` (+ 괴리 판정) → 라우팅.

    괴리 단독이 **먼저**다: 바스켓이 안 움직였으면 구성종목 이야기가 아니라 ETF
    고유(수급·유동성) 이야기다. 그걸 무시하고 층으로 내려가면 없는 원인을 찾는다.
    """
    # **시장 프록시 자신은 섹터로 내려갈 수 없다** (범주 오류). KODEX 200 의 층
    # 분해에는 시장 층이 없다(자기 제외) - 그러면 섹터만 남고 라우팅이 "섹터 화학
    # 60%" 라고 말한다. 형식은 맞지만 직관은 틀렸다: 그것이 시장이다.
    # 실측 069500 07-29 에서 그렇게 나왔다.
    from .layers import MARKET_CODE
    etf_code = getattr(roll, "etf", "") or ""
    if etf_code and (etf_code == MARKET_CODE or etf_code.endswith(MARKET_CODE)):
        return Route("시장", 1.0, getattr(roll, "etf_name", etf_code), (),
                     "이 ETF 가 **시장 프록시 자신**이다 - 섹터로 내려가지 않는다. "
                     "밤사이 해외 팩터·장중 시장 사건·국면으로만 환원한다",
                     # **자기 잔차는 '고유' 가 아니다** - 그것이 시장이다. 실측
                     # 069500 07-31: 라우팅 '시장 100%' 인데 회계는 '고유 +20.29%p'
                     # 라 모순처럼 읽혔다. 시장 프록시의 잔차는 시장 자신의 움직임이다.
                     tuple((x.kind, x.contribution) for x in roll.layers)
                     + (("시장 자신", roll.idio),))
    if premium is not None and not getattr(premium, "basket_moved", True):
        return Route("괴리단독", 1.0, "ETF", (),
                     "바스켓이 안 움직였다 - 구성종목이 아니라 ETF 수급/유동성 이야기다",
                     ())
    # 층 이름을 같이 싣는다 - "섹터 -2.34 · 섹터 -5.74" 는 둘이 뭔지 못 알려준다.
    parts: list[tuple[str, float]] = [
        (f"{x.kind}({x.name})", x.contribution) for x in roll.layers]
    parts.append(("고유", roll.idio))
    tot = sum(abs(v) for _k, v in parts) or 1.0
    lab, val = max(parts, key=lambda kv: abs(kv[1]))
    kind = lab.split("(")[0]
    share = abs(val) / tot
    if share < DOMINANT:
        bits = " · ".join(f"{k} {v / tot * 100:.0f}%" for k, v in parts)
        return Route("혼합", share, "", (),
                     f"어느 층도 {DOMINANT:.0%} 를 못 넘는다 ({bits}) - 하나를 고르면 "
                     "나머지가 조용히 사라진다. 층별로 나눠 묻는다",
                     tuple(parts))
    if kind == "고유":
        floor = MIN_NAME_SHARE * abs(roll.idio)
        names = tuple(n.ticker for n in roll.names if abs(n.pct) >= floor)[:TOP_NAMES]
        return Route("고유", share, "고유", names,
                     f"고유가 {share:.0%} - 종목 이야기다. |기여| 상위 "
                     f"{len(names)}종목에 개별 시행을 돌린다",
                     tuple(parts))
    nm = lab[lab.find("(") + 1:-1] if "(" in lab else kind
    say = ("밤사이 해외 팩터 · 장중 시장 사건 · 국면으로 환원한다. "
           "**종목 가설을 세우지 않는다** - 세우면 시장 충격을 종목 이야기로 위장한다"
           if kind == "시장" else
           "그 업종의 공통 처치(정책·수요·가격)를 묻는다. 종목 개별성은 조절자로만")
    return Route(kind, share, nm, (), f"{kind}({nm})이 {share:.0%} - {say}", tuple(parts))


def say_route(r: Route) -> str:
    out = [f"[라우팅] **{r.kind}**"
           + (f" · {r.layer_name}" if r.layer_name and r.kind != "고유" else "")
           + f" · 비중 {r.share:.0%} — {r.why}"]
    if r.breakdown:
        out.append("  층 회계: " + " · ".join(
            f"{k} {v * 100:+.2f}%p" for k, v in r.breakdown))
    if r.targets:
        out.append("  개별 시행 대상: " + " · ".join(r.targets))
    return "\n".join(out)


def _selfcheck() -> None:
    class L:
        def __init__(self, kind, name, c):
            self.kind, self.name, self.contribution = kind, name, c

    class N:
        def __init__(self, t, p):
            self.ticker, self.pct = t, p

    class R:
        def __init__(self, layers, idio, names=(), etf="TEST", etf_name="T"):
            self.layers, self.idio, self.names = layers, idio, names
            self.etf, self.etf_name = etf, etf_name

    mkt = route_etf(R((L("시장", "KODEX200", 0.20), L("섹터", "반도체", -0.01)), 0.01))
    assert mkt.kind == "시장" and mkt.share > 0.9
    assert "종목 가설을 세우지 않는다" in mkt.why

    idio = route_etf(R((L("시장", "K", 0.001),), 0.05,
                       (N("000660", 0.03), N("005930", 0.02), N("x", 0.001))))
    assert idio.kind == "고유" and idio.targets == ("000660", "005930")

    mix = route_etf(R((L("시장", "K", 0.03), L("섹터", "S", 0.03)), 0.03))
    assert mix.kind == "혼합" and "하나를 고르면" in mix.why

    # 시장 프록시 자신은 섹터로 안 내려간다
    self_mkt = route_etf(R((L("섹터", "화학", -0.05),), 0.01, etf="069500"))
    assert self_mkt.kind == "시장" and "시장 프록시 자신" in self_mkt.why
    # 자기 잔차를 '고유' 로 부르면 '시장 100% 인데 고유가 최대' 라는 모순이 읽힌다
    assert dict(self_mkt.breakdown).get("시장 자신") == 0.01
    assert "고유" not in dict(self_mkt.breakdown)

    class P:
        basket_moved = False
    assert route_etf(R((L("시장", "K", 0.9),), 0.0), premium=P()).kind == "괴리단독"
    print("ok")


if __name__ == "__main__":
    _selfcheck()
