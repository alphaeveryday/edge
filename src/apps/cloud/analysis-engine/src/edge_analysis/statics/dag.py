"""가설 DAG - 대상 하나(시장·섹터·고유 중 하나)의 경쟁가설들을 한 그래프로.

구조는 의도적으로 얕다: 모든 간선은 방아쇠 노드 → 대상(결과) 노드로 향하고,
간선 하나가 튜플 하나 = 인과 주장 하나다. 경쟁가설 = 같은 결과로 들어오는
서로 다른 채널의 간선들. **간선마다 검정 의도(intent)가 실려** 검정 에이전트에게
"무엇이 사실이면 성립인가"를 전달한다.

공통요인은 두 종류를 명시한다 - 조용히 두지 않는다:
  · 통제됨: 시장·산업 (패널 결과가 산업층 이중차감 ar 이라 이미 빠져 있다)
  · 공유 노드: 두 간선 이상이 같은 방아쇠·같은 노출 계열족을 딛으면 그 노드가
    공통요인 후보다 - 검정 에이전트가 교란으로 고려해야 한다.

사이클은 구조상 불가능하다(모든 간선이 결과로만 향한다). 검증은 그래서
사이클 검사가 아니라 **의도 존재·채널 중복·접지**를 본다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .vocab import HypothesisTuple

CONTROLLED = ("시장 (시장차감 ar)", "산업 (이중차감 ar_ind)")


@dataclass
class Finding:
    """검정 에이전트가 간선 하나에 남기는 것. SEM 이 이것만 먹는다."""

    causal: bool | None          # True 성립 · False 기각(간선 절단) · None 판정불가(희소)
    conclusion: str              # 결론 한 단락 - 근거 수치 포함
    confidence: str = ""         # 높음|중간|낮음
    se_kind: str = ""            # 0/1 | 시계열 | 수준  - 구조방정식 변수의 형
    se_name: str = ""            # 변수 이름 (예: usdkrw_z, 실적발표_당일)
    se_value: str = ""           # 오늘 값 (예: 1, z=+2.7, -0.83%p)
    se_meaning: str = ""         # **그 데이터의 의미** - 방정식에서 이 항이 뜻하는 것
    cut_reason: str = ""         # 기각 시 가설 에이전트에게 보고되는 사유

    @property
    def status(self) -> str:
        return "성립" if self.causal else "판정불가" if self.causal is None else "기각"


@dataclass
class CEdge:
    """간선 = 인과 주장 하나. 튜플(닫힌 어휘) + 의도(무엇이 사실이면 성립인가)."""

    eid: str
    tup: HypothesisTuple
    intent: str
    round: int = 1
    finding: Finding | None = None

    @property
    def status(self) -> str:
        return self.finding.status if self.finding else "미검정"

    def head(self) -> str:
        t = self.tup
        vul = " ".join(f"{v.ident}/{v.transform}{v.comparator}p{v.percentile:.0%}"
                       for v in t.conditions) or "-"
        return (f"[{self.eid}] {t.channel} · {t.trigger.kind}:{t.trigger.ident} "
                f"→ {t.outcome} (부호{t.sign:+d}) · 노출 {t.exposure.ident}/"
                f"{t.exposure.transform} · 조건 {vul}")


@dataclass
class TargetDAG:
    """대상 하나의 경쟁가설 묶음. merge 가 공통요인을 드러내고 validate 가 반려한다."""

    target_kind: str             # 시장 | 섹터 | 고유
    target_label: str            # 예: "고유 -1.21%p"
    budget_pct: float            # 이 대상이 설명해야 할 몫 (%p)
    edges: list[CEdge] = field(default_factory=list)

    def add(self, tuples: list[HypothesisTuple], round: int = 1) -> list[str]:
        """튜플 → 간선. 반려 사유 목록을 돌려준다 (조용히 버리지 않는다)."""
        rejected = []
        for t in tuples:
            if not t.intent.strip():
                rejected.append(f"{t.channel}: 검정 의도가 비었다 - 간선은 주장 없이 못 선다")
                continue
            if any(e.tup.channel == t.channel and e.round == round for e in self.edges):
                rejected.append(f"{t.channel}: 같은 라운드 채널 중복 - 경쟁가설은 채널이 갈려야 한다")
                continue
            self.edges.append(CEdge(
                eid=f"{self.target_kind}{len(self.edges) + 1}", tup=t,
                intent=t.intent, round=round))
        return rejected

    def common_factors(self) -> list[str]:
        """두 간선 이상이 딛는 노드 = 공통요인 후보. 검정자가 교란으로 고려한다."""
        trig = Counter(f"{e.tup.trigger.kind}:{e.tup.trigger.ident}" for e in self.edges)
        expo = Counter(e.tup.exposure.ident for e in self.edges)
        out = [f"방아쇠 공유 {k}" for k, n in trig.items() if n >= 2]
        out += [f"노출 계열족 공유 {k}" for k, n in expo.items() if n >= 2]
        return out

    def validate(self) -> list[str]:
        """병합 후 최종 점검 - 넘기기 전에 한 번 더. 문제 목록(비면 통과)."""
        out = []
        if not self.edges:
            out.append(f"{self.target_kind}: 간선이 없다")
        for e in self.edges:
            if not e.intent.strip():
                out.append(f"{e.eid}: 의도 없음")
        return out

    def render(self, *, verbose: bool = True) -> str:
        mark = {"성립": "✓", "기각": "✂", "판정불가": "?", "미검정": "·"}
        L = [f"◇ 대상 {self.target_label}  (간선 {len(self.edges)})"]
        for e in self.edges:
            L.append(f"  {mark[e.status]} {e.head()}")
            L.append(f"      의도: {e.intent}")
            if verbose and e.finding:
                f = e.finding
                L.append(f"      판정: {f.status}({f.confidence}) - {f.conclusion}")
                if f.causal:
                    L.append(f"      SEM 재료: {f.se_kind} {f.se_name}={f.se_value} · {f.se_meaning}")
                elif f.causal is False:
                    L.append(f"      절단 사유: {f.cut_reason}")
        cf = self.common_factors()
        L.append(f"  공통요인: 통제됨 {' · '.join(CONTROLLED)}"
                 + (f" | 공유 {' · '.join(cf)}" if cf else ""))
        return "\n".join(L)

    def connected(self) -> list[CEdge]:
        return [e for e in self.edges if e.finding and e.finding.causal]

    def cut(self) -> list[CEdge]:
        return [e for e in self.edges if e.finding and e.finding.causal is False]

    def pending(self) -> list[CEdge]:
        return [e for e in self.edges if e.finding is None]


__all__ = ["CONTROLLED", "CEdge", "Finding", "TargetDAG"]
