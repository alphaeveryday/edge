"""사슬 — **간선 유형이 증명 양식을 결정한다. 예산이 그래프를 기각한다.**

이 파일이 답하는 것은 하나다: *오늘 이 ETF 가 이만큼 움직인 것을 어디까지 설명했나.*
일반론적 인과 법칙이 아니라 **한 관측의 귀속**이 대상이므로, 두 성질이 따라온다.

**하나. 예산이 있다.** 설명은 잔차를 나눠 쓴다. 여러 원인이 각자 유의미해도 되는 타입
수준 모형과 달리, 귀속의 합이 잔차를 넘으면 그래프가 틀린 것이다. 그래서 여기서는
적합도 카이제곱이 아니라 **예산 정합**이 기각 경로다 - 훨씬 싸고
훨씬 날카롭다.

**둘. 간선마다 증명 양식이 다르다.** 사건에서 가격까지는 간접이고 몇 단계인지 미리
알 수 없다. 그 사슬 안에는 계산으로 끝나는 자리와 계수가 필요한 자리와 데이터로 재야
하는 자리가 섞여 있다. 이 셋을 같은 방식으로 다루면 계산을 검정하거나 추정을 계산으로
위장한다.

    identity     항등식. 오차가 없다. 검정 대상은 값이 아니라 **입력의 출처**다
    elasticity   계수가 필요한 연역. 계수 불확실성이 사슬을 따라 곱해진다
    statistical  연역이 안 되는 자리. 데이터로 재고 구간이 데이터에서 나온다

세 유형이 한 사슬에 공존하면 **점 예측**이 나온다. "유사 사건에서 중위 2.4% 였다"가
아니라 "이 사건은 3.1% [1.8, 4.4] 여야 한다"다. 점 예측은 훨씬 쉽게 죽고, 그래서 증거로
강하다. 크기를 맞히는 것이 목적이 아니라 **틀리면 경로가 죽기 때문에** 검정력이 생긴다.

구간 폭은 그 자체가 검정력의 자기 진단이다. 예측 폭이 그 종목의 하루 변동성보다 넓으면
어떤 관측도 반증하지 못한다 - `verdict()` 가 그걸 `무력` 으로 표시한다. 넓은 구간을
숨기지 않는 것이 이 설계의 정직성 장치다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import product as _prod

KINDS = ("identity", "elasticity", "statistical")


@dataclass(frozen=True, slots=True)
class Interval:
    """[lo, hi]. **폭이 곧 무지의 크기이므로 접어서 숨기지 않는다.**

    항등식 간선은 lo == hi 로 온다(오차 없음). 그 차이를 타입으로 나누지 않고 같은
    그릇에 담는 이유는, 사슬을 따라 곱할 때 어디서 폭이 벌어졌는지 추적하려면 모든
    간선이 같은 모양이어야 하기 때문이다.
    """

    lo: float
    hi: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.lo) and math.isfinite(self.hi)):
            raise ValueError(f"유한하지 않은 구간: [{self.lo}, {self.hi}]")
        if self.lo > self.hi:
            raise ValueError(f"뒤집힌 구간: [{self.lo}, {self.hi}]")

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2

    @property
    def width(self) -> float:
        return self.hi - self.lo

    @property
    def exact(self) -> bool:
        return self.width == 0.0

    def contains(self, x: float) -> bool:
        return self.lo <= x <= self.hi

    def __str__(self) -> str:
        if self.exact:
            return f"{self.lo:+.2%}"
        return f"{self.mid:+.2%} [{self.lo:+.2%}, {self.hi:+.2%}]"


def multiply(a: Interval, b: Interval) -> Interval:
    """구간 곱. **부호가 섞이면 네 꼭짓점을 다 봐야 한다.**

    `[lo*lo, hi*hi]` 로 줄여 쓰면 음수 계수가 하나 끼는 순간 틀린다. 사슬에는 음의
    탄력성(원가 상승 → 이익 감소)이 흔하므로 이 실수가 조용히 부호를 뒤집는다.
    """
    c = [x * y for x, y in _prod((a.lo, a.hi), (b.lo, b.hi))]
    return Interval(min(c), max(c))


@dataclass(frozen=True, slots=True)
class Edge:
    """사슬의 한 칸. **effect 는 부모 값에 곱하는 배수다.**

    값을 절대 수준으로 들고 다니지 않고 배수로 두는 이유는, 사슬의 어느 지점에서
    끊어도 남은 부분이 그대로 재사용되기 때문이다(같은 경로가 다른 종목·다른 날에
    다시 검정될 때 누적하려면 이 성질이 필요하다).

    `effect` 가 None 이면 아직 재지 않은 것이다 - 제안 단계에서는 statistical 간선의
    배수가 비어 있고, 검정이 채운다. 비었다는 사실이 곧 검정 의제다.
    """

    src: str
    dst: str
    kind: str
    says: str
    because: str = ""
    false_if: str = ""
    effect: Interval | None = None
    formula: str = ""          # identity: 어떤 항등식인가
    source: str = ""           # 값·계수의 출처. **없으면 날조와 구별되지 않는다**
    exposure: str = ""         # statistical: 이 경로에 노출된 집합
    reference: str = ""        # statistical: 비교할 참조집합(비노출). **선택이 결론을 바꾼다**
    invariant_to: tuple[str, ...] = ()   # 의존하지 않는다고 선언한 것 = 곡선의 축
    needs: str = ""            # 없어서 막히는 데이터

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"간선 유형 {self.kind!r} 은 {KINDS} 밖이다")
        if self.kind == "identity" and self.effect and not self.effect.exact:
            raise ValueError(
                f"{self.src}→{self.dst}: 항등식에 폭 있는 구간({self.effect})이 왔다. "
                "오차가 있으면 elasticity 다")

    @property
    def measured(self) -> bool:
        return self.effect is not None


@dataclass(slots=True)
class Path:
    """사건에서 오늘 가격까지의 한 경로. **끝까지 닿아야 예산에 들어간다.**

    `anchor` 는 사슬의 출발 크기다(사건이 실제로 얼마였나 - 수율 몇 %·금액 얼마).
    간선의 `effect` 를 탄력성(자식 변화 / 부모 변화)으로 두면 사슬은 배수의 곱이 되고
    절대 크기는 이 한 곳에서만 들어온다. 그래서 같은 사슬을 다른 종목·다른 사건에 다시
    쓸 때 구조는 그대로 두고 앵커만 바꾼다.

    기본값 1 은 "크기를 따로 대지 않았다"는 뜻이고, 이때 마지막 간선의 `effect` 가
    기여분 자체가 된다 - 연역 사슬이 없는 순수 통계 경로가 이 모양이다.
    """

    cause: str
    edges: list[Edge] = field(default_factory=list)
    anchor: Interval = Interval(1.0, 1.0)

    @property
    def measured(self) -> bool:
        return bool(self.edges) and all(e.measured for e in self.edges)

    @property
    def blocked(self) -> list[Edge]:
        """아직 못 잰 칸. 여기 남은 것이 그대로 데이터 요청이 된다."""
        return [e for e in self.edges if not e.measured]

    @property
    def kinds(self) -> str:
        return "→".join(e.kind[0] for e in self.edges)

    def predict(self) -> Interval | None:
        """경로의 예측 크기. 한 칸이라도 비면 예측이 없다.

        연역 사슬은 **앵커에서 시작해** 배수를 곱한다 - 절대 크기가 사건 노드의 `value` 한
        곳에서만 들어오는 규약이다.

        통계 간선이 있으면 **그 추정치가 스케일을 정한다.** 검정은 그 종류의 실제 사건들로
        코호트를 만들어 처치·대조 차이를 재므로, 사건이 실제로 얼마였는지가 이미 추정치
        안에 있다. 앵커를 다시 곱하면 같은 크기를 두 번 세고(배당 30% × 초과수익 6%),
        관계없는 노드의 `value` 가 경로를 조용히 줄이거나 부풀린다. 통계 간선 **뒤의**
        연역 배수는 그대로 적용한다 - 그건 측정된 양을 다른 단위로 옮기는 변환이다.
        """
        if not self.measured:
            return None
        last = max((i for i, e in enumerate(self.edges) if e.kind == "statistical"),
                   default=-1)
        if last < 0:
            out = self.anchor
            rest = self.edges
        else:
            out = self.edges[last].effect
            rest = self.edges[last + 1:]
        for e in rest:
            out = multiply(out, e.effect)   # type: ignore[arg-type]
        return out

    def widest(self) -> Edge | None:
        """폭을 가장 많이 벌린 칸. **여기가 다음에 좁혀야 할 자리다.**

        실물 효과를 가격까지 전파할 때 최대 불확실성은 대개 크기가 아니라 지속 기간이다
        - 이 함수가 그걸 이름으로 짚어준다.
        """
        got = [e for e in self.edges if e.effect and not e.effect.exact]
        return max(got, key=lambda e: abs(e.effect.width), default=None)  # type: ignore[union-attr]


def paths(edges: list[Edge], target: str,
          anchors: dict[str, Interval] | None = None) -> list[Path]:
    """`target` 으로 닿는 경로 전부. **닿지 않는 간선은 귀속이 아니다.**

    귀속 그래프에서 결론 노드는 하나다. 그리로 가는 길이 없는 간선은 구조상 흥미로울 수
    있어도 이 셀의 설명에 기여하지 않으므로 예산 밖에 둔다.

    `anchors` 는 뿌리 노드의 크기(사건이 실제로 얼마였나)다. 없으면 1 로 두고, 그러면
    마지막 간선의 배수가 기여분 자체로 읽힌다.
    """
    by_dst: dict[str, list[Edge]] = {}
    for e in edges:
        by_dst.setdefault(e.dst, []).append(e)
    out: list[Path] = []

    def walk(node: str, chain: list[Edge], seen: frozenset[str]) -> None:
        ups = by_dst.get(node) or []
        if not ups:
            if chain:
                root = chain[0].src
                out.append(Path(cause=root, edges=list(chain),
                                anchor=(anchors or {}).get(root, Interval(1.0, 1.0))))
            return
        for e in ups:
            if e.src in seen:      # 순환은 상류에서 막지만 여기서도 방어한다
                continue
            walk(e.src, [e, *chain], seen | {e.src})

    walk(target, [], frozenset({target}))
    return out


def budget(ps: list[Path], residual: float, *, tol: float = 0.15,
           weights: dict[int, float | None] | None = None) -> dict:
    """예산 정합. **합이 잔차를 넘으면 그래프가 틀렸다.**

    타입 수준 모형에서는 여러 원인이 각자 유의미해도 모순이 아니다. 귀속에서는 모순이다
    - 같은 한 움직임을 나눠 갖기 때문이다. 이 비대칭이 바텀업 그래프가 가진 가장 값싼
    기각 경로이고, 카이제곱 적합도가 못 하는 일이다.

    `weights[i]` 는 경로 i 의 **셀 스케일 환산 계수**다(처치 단위가 ETF 에서 갖는 비중).
    검정 표본은 종목 단위이고 잔차는 ETF 단위라, 환산하지 않고 비교하면 비중 작은 큰 효과가
    한도를 넘겨 기각되고(6% 종목 효과 vs 4% ETF 잔차) 그 반대도 생긴다. 계수를 못 구한
    경로는 **측정으로 세지 않는다** - 결측을 1 로 대체하면 그 자리가 조용히 틀린다.

    한도 검사는 **잔차와 같은 방향인 몫**으로 한다. 부호를 섞어 더하면 반대 방향 경로가
    한도를 보조해 준다 - +2% 잔차에 +5%·-3% 두 경로가 있으면 합이 +2% 라 통과하고, 뒤에서
    -3% 가 상쇄 요인으로 기각된 뒤 +5% 만 남아 잔차를 혼자 넘긴 채 게시된다.

    `tol` 은 잔차 자체의 측정 오차(요인 분해·체결가 차이)를 감안한 여유다. 이걸 0 으로
    두면 정상 그래프가 반올림으로 기각된다.
    """
    w = weights or {}
    got = [(i, p, _scaled(p.predict(), w.get(i, 1.0) if weights else 1.0))
           for i, p in enumerate(ps)]
    done = [(p, iv) for _i, p, iv in got if iv is not None]
    blocked = [p for _i, p, iv in got if iv is None]
    lo = sum(iv.lo for _, iv in done)
    hi = sum(iv.hi for _, iv in done)
    mid = sum(iv.mid for _, iv in done)
    same = sum(iv.mid for _, iv in done
               if residual == 0.0 or (iv.mid > 0) == (residual > 0))
    cap = abs(residual) * (1 + tol)
    over = abs(same) > cap and cap > 0
    share = (abs(mid) / abs(residual)) if residual else 0.0
    return {"residual": residual,
            "explained": Interval(min(lo, hi), max(lo, hi)),
            "share": share,
            "unexplained": residual - mid,
            "over_budget": over,
            "n_paths": len(ps), "n_measured": len(done), "n_blocked": len(blocked),
            "blocked": [{"cause": p.cause,
                         "needs": [e.needs or f"{e.src}→{e.dst}" for e in p.blocked]}
                        for p in blocked],
            "reason": (f"같은 방향 귀속 합 {same:+.2%} 가 잔차 {residual:+.2%} 를 넘는다 "
                       f"(여유 {tol:.0%} 포함 한도 {cap:.2%})") if over else ""}


def _scaled(iv: Interval | None, weight: float | None) -> Interval | None:
    """경로 예측을 셀 스케일로. 계수가 없으면 **예측이 없다**(결측 != 1)."""
    if iv is None or weight is None:
        return None
    return Interval(iv.lo * weight, iv.hi * weight)


def verdict(iv: Interval | None, observed: float, daily_vol: float | None) -> str:
    """예측과 관측의 대조. **구간이 변동성보다 넓으면 검정이 아니다.**

    맞았다/틀렸다 앞에 `무력` 이 있는 이유는, 가정을 느슨하게 잡아 구간을 벌리면 어떤
    관측도 포함시킬 수 있기 때문이다. 그 경우 통과는 증거가 아니라 침묵이므로, 통과와
    구별해 이름을 붙여야 한다.
    """
    if iv is None:
        return "미측정"
    if daily_vol and iv.width > 2 * daily_vol:
        return "무력"        # 예측 폭이 평시 이틀치 등락보다 넓다
    if iv.contains(observed):
        return "정합"
    return "기각"
