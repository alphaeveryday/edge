"""P0–P9 계약 — **단계 사이를 지나는 것은 전부 여기 정의된 값이다.**

설계도: `docs/analysis-engine/architecture/causal-attribution-p0p9.drawio`

이전 구조는 제안 에이전트가 그린 DAG 하나가 파이프라인 전체를 관통했다. 그 그래프는
**그림에 대한 진술**이었다 - 안 그린 간선과 없는 관계가 같은 표현(부재)으로 붙었고,
`identify()` 의 "뒷문 없음"은 세계가 아니라 제안자의 지식 상태를 보고했다.

여기서 닫는 것은 어휘가 아니라 셋이다:

    회계 폐쇄   설명 대상의 합이 맞아야 한다            `Question.budget` -> `Findings`
    교란 폐쇄   그린 변수의 공통원인 전수 선언, 선언된
                U 는 소거되거나 미소거로 기록된다        `WorldGraph.latents` -> `Discriminator`
    처분 폐쇄   검토한 후보에 침묵 없음                  `Disposition`

어휘(`Hypothesis`)는 열려 있다. 노드·간선·메커니즘을 무엇이든 세울 수 있고, 골격도
후보 목록도 주지 않는다. 정직성은 표현을 좁혀서가 아니라 **위 셋을 강제해서** 산다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

# ── 어휘 ────────────────────────────────────────────────────────────────
# 배정 기제. **`chosen` 이면 컴파일러가 U 를 심는다** - 모델이 지울 수 없다.
# 근거: 기업이 고르는 사건(배당·자사주·가이던스·M&A)은 좋은 사적 정보와 함께 온다
# (Bhattacharya 1979 · Miller-Rock 1985). 선택 편의는 예외가 아니라 기본값이다.
Assignment = Literal["mechanical", "scheduled", "natural", "chosen"]
ASSIGNMENT_SAY = {
    "mechanical": "규칙이 시점과 내용을 정한다 (배당락·지수 리밸런스·만기)",
    "scheduled": "시점이 사전 고정, 내용은 아니다 (정기 실적·FOMC)",
    "natural": "기업 밖에서 발생 (규제·재해·피어 사건)",
    "chosen": "기업이 고른다 (배당·자사주·가이던스·M&A)",
}
# U 를 자동 삽입하는 배정 기제와, 삽입되는 U 가 무엇인지.
COMPILED_LATENT = {
    "chosen": "기업이 이 사건을 고르게 만든 미관측 상태 (사적 정보·현금흐름 전망)",
    "scheduled": "발표 내용을 정한 미관측 상태 (시점은 외생이나 내용은 아니다)",
}

IdentStatus = Literal["identified", "identified_under", "not_identified"]
Verdict = Literal["contributing", "not_contributing", "undetermined"]
# 주장 상한. `confirmed` 는 **미소거 U 가 0이고 판별 검정을 통과했을 때만** 나온다.
ClaimCeiling = Literal["confirmed", "mechanism_compatible", "undetermined"]
CEILING_SAY = {
    "confirmed": "원인으로 확인",
    "mechanism_compatible": "메커니즘 양립 - 대안을 배제하지 못함",
    "undetermined": "판단 보류",
}

# ── 인과 역할 (Flash Crash 규율) ─────────────────────────────────────────
# "대규모 매도가 원인" 은 불충분하다 - 그것은 촉발원이고, 유동성 고갈은 증폭이며,
# 거래정지는 종료다. 기여도(share)와 역할은 **다른 축**이다. Kirilenko 의 HFT 판정
# ("원인 아님, 증폭") 은 share 축에서는 표현되지 않는다.
Role = Literal["background", "trigger", "transmission", "amplifier", "terminator"]
ROLE_SAY = {
    "background": "왜 이 사건이 발생하기 쉬운 상태였는가 - 배경조건",
    "trigger": "무엇이 인과연쇄를 시작했는가 - 촉발원",
    "transmission": "충격이 어떤 경로로 결과까지 전달됐는가 - 전달경로",
    "amplifier": "왜 결과가 이 정도로 커졌는가 - 증폭요인",
    "terminator": "무엇이 사건을 멈추거나 되돌렸는가 - 종료·완화요인",
}

# 정규성 등급. Halpern-Hitchcock 2015: **배경조건 ≡ 실제값이 default, 촉발원 ≡ deviant.**
# 참조류 상대적이다 - 진공챔버에서는 산소가 원인이 된다 (HH §7.3).
# `unknown` 은 `default` 로 접으면 안 된다: 못 쟀다는 것과 전형적이라는 것은 다르고,
# 접는 순간 **측정 실패가 자동으로 배경조건 판정을 만들어낸다.**
Deviance = Literal["default", "mild", "deviant", "extreme", "unknown"]
DEVIANCE_RANK = {"default": 0, "mild": 1, "deviant": 2, "extreme": 3}
# 역할이 요구하는 정규성. 어긋나면 위반으로 적는다 (거부가 아니라 기록 - 참조류가
# 우리 축과 다를 수 있고, HH 자신이 정규성 순서의 조작 가능성을 경고한다).
ROLE_NEEDS = {
    "background": ("default", "mild"),
    "trigger": ("deviant", "extreme"),
    "amplifier": ("deviant", "extreme"),
}

# ── 가설 간 관계 (Zaks 2017 RAR) ────────────────────────────────────────
# ★ `share` (= relative causal force) 는 **coincident 관계에서만 정의된다**:
#   "testing the relative causal force is only possible when two explanations can
#    simultaneously but independently bring about the outcome."
# 나머지 유형에서 합산하는 것은 정의되지 않은 양을 더하는 것이다.
# `causal` 은 RAR 에 없다 - RAR 은 증거론적 관계이지 인과 순서가 아니다. 회계는
# 매개분석에서 빌린다 (NDE/NIE).
RelationKind = Literal[
    "mutually_exclusive",   # 하나만 참. 같은 예산 슬롯을 두고 경쟁
    "coincident",           # 공동 산출 + 증거 독립. **합산이 옳은 유일한 경우**
    "congruent",            # 공동 산출 + 증거 연동. 포함-배제
    "inclusive",            # 한쪽이 다른 쪽의 확장. 한 줄로 접는다
    "causal",               # a -> b -> Y 매개. NDE/NIE 로 쪼갠다
    "unknown",              # 판정 못 함. **share 주장을 차단한다**
]
RELATION_SAY = {
    "mutually_exclusive": "하나가 참이면 다른 하나는 거짓",
    "coincident": "둘 다 동시에 설명할 수 있고, 한쪽 증거가 다른 쪽에 영향을 주지 않는다",
    "congruent": "둘 다 설명하고, 한쪽을 지지하는 증거가 다른 쪽도 지지한다",
    "inclusive": "한쪽이 다른 쪽의 확장판이다",
    "causal": "한쪽이 다른 쪽을 유발했다 - 경합이 아니라 직렬",
    "unknown": "관계를 판정하지 못했다",
}
# 대칭 유형은 방향을 갖지 않는다.
DIRECTED_RELATIONS = ("inclusive", "causal")

# ── 메커니즘 영역 (후보 공간의 폐쇄) ────────────────────────────────────
# 어휘는 열려 있다 (P2 에 골격을 주지 않는다). 닫는 것은 **커버리지 보고**다 -
# 열지 않은 영역에 침묵하지 않는다. 뉴스만 뒤지면 D1 으로 편향된다.
Domain = Literal["information", "common_shock", "flow", "microstructure",
                 "feedback", "institution", "measurement", "no_event"]
DOMAIN_SAY = {
    "information": "정보·기대 - 공시·실적·정책·예상과 실제의 차이",
    "common_shock": "공통 외생충격 - 시장·섹터·환율·해외 동종기업",
    "flow": "포지션·수급 - 기관 매도·리밸런싱·강제청산",
    "microstructure": "시장미시구조 - 주문불균형·호가잔량·스프레드·유동성",
    "feedback": "내생적 피드백 - 하락이 추가 매도를 부른다",
    "institution": "제도·시스템 - 거래소 규칙·상하한가·공매도 규제·거래중단",
    "measurement": "측정·데이터 문제 - 잘못된 가격·기준가 조정·시각 불일치",
    "no_event": "무사건 - 정상 잔차 또는 우연한 변동",
}


def deviance(axis: Axis) -> Deviance:
    """축 하나의 실제값이 default 에서 얼마나 벗어났나.

    **참조류 상대적이다.** HH §7.3 의 진공챔버 산소가 그 이유다 - 같은 축이 어느 날은
    배경조건이고 어느 날은 촉발원이다. P1 의 `prior()` 가 이미 참조류(사건 타입·종목)
    상대적이라 이 요구를 만족한다.

    ★ `available=False` 는 언제나 `"unknown"` 이다. `"default"` 로 접으면 측정 실패가
    자동으로 배경조건 판정을 만들어낸다 - 조용한 재앙이다.
    """
    if not axis.available:
        return "unknown"
    v = axis.value
    if axis.name == "type_extremity":
        # 사건 타입의 과거 |초과수익| 분포에서 오늘이 어디인가. band 를 그대로 승격한다.
        bands = {r.get("band") for r in v or [] if isinstance(r, dict)
                 and r.get("need") is not None}
        if not bands:
            return "unknown"
        if "beyond_max" in bands:
            return "extreme"
        if "tail" in bands:
            return "deviant"
        return "default" if bands == {"typical"} else "mild"
    if axis.name == "pre_drift":
        # HH §5 의 "start time 을 default state 로" 를 그대로 구현한 축이다.
        # 사건 전이 평평 = 계의 default 상태. 이미 움직였다 = deviant.
        if not isinstance(v, dict):
            return "unknown"
        ref = v.get("etf") if v.get("etf") is not None else v.get("candidate_median")
        b = v.get("budget")
        if ref is None or not b:
            return "unknown"
        if abs(ref) <= 0.5 * abs(b):
            return "default"
        return "extreme" if abs(ref) >= abs(b) else "deviant"
    if axis.name == "breadth":
        # 산업 피어가 동반 이동 = 공통요인 = default. 고립 = 개별 사건 필요 = deviant.
        # `thin` 은 표본 부족이라 판별력이 없다 - unknown 이지 default 가 아니다.
        if not isinstance(v, dict):
            return "unknown"
        if v.get("n_peers", 0) and v.get("co_moved") is not None and not v.get("thin", False):
            return "default" if v["co_moved"] else "deviant"
        return "unknown"
    if axis.name == "shape":
        # 기여가 소수 종목에 몰렸다 = 개별 사건이 필요하다 = deviant.
        if not isinstance(v, dict):
            return "unknown"
        kind = v.get("kind")
        if kind == "concentrated":
            return "deviant"
        if kind == "broad":
            return "default"
        return "mild" if kind == "mixed" else "unknown"
    if axis.name == "event_timing":
        # 이미 공개돼 반영될 시간이 있었다 = default(놀랄 것 없음). 당일 공개 = deviant.
        if not isinstance(v, dict):
            return "unknown"
        lag = v.get("min_lag_days")
        return "unknown" if lag is None else ("default" if lag >= 1 else "deviant")
    return "unknown"


# ── P0 · 질문 ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Question:
    """설명 대상과 **반사실의 정의**. 그래프보다 먼저 고정된다.

    개입을 문장으로 못 쓰면 그래프를 그릴 자격이 없다. "공시가 없던 세계"는 정의
    가능하고(시점은 조작 가능) "이사회가 다른 결정을 한 세계"는 정의 불가다(그건 다른
    기업이다) - 이 구분이 P3 의 교란 구조를 미리 결정한다.

    `answer_form` 이 점추정이 아닌 이유: 우리 물음은 causes of effects 이고, 그 방향은
    외생성·단조성 없이는 점 답이 없다 (Holland 1986 · Dawid).
    """

    etf_instrument_id: str
    etf_name: str
    trade_date: date
    as_of: str
    observed: float                 # ETF 당일 등락
    residual: float                 # 시장(횡단면 평균) 대비 초과수익 = 설명 예산
    route_code: str
    explanandum: str                # "r⊥[091160, 2026-07-16] = +4.21%"
    intervention: str               # 반사실 세계의 정의
    answer_form: str                # 답의 형태 (구간·상한)
    contributors: list[tuple[str, float]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    # ── 검정력. **E-value 보다 먼저 온다** ──────────────────────────────
    # 실측: 일별 특이잡음 sd ≈ 1.68% 에서 80% 검정력@5% 에 필요한 효과는 4.71%/일.
    # 그리고 sd(u_ETF) = σ·sqrt(1/N_eff + ρ(1-1/N_eff)) 이므로 ρ=0.25 면 종목 수와
    # 무관하게 검출 하한이 2.4%/일에서 정체한다 - 섹터 ETF 는 정의상 ρ 를 극대화한다.
    # |residual| < mde80 이면 "유의하지 않다"가 정보가 아니고 어떤 서사도 반증 불가다.
    resid_sd: float | None = None   # 이 셀의 특이잡음 sd (EWMA 표준화 기준)
    mde80: float | None = None      # 80% 검정력@5% 로 검출 가능한 최소 효과
    p_empirical: float | None = None    # 자기 귀무분포에서의 양측 경험 p. 정규 가정 금지
    p_scan: float | None = None     # 다중검정 보정. 1-(1-p)^(n_cell) — Šidák
    n_history: int = 0              # 귀무분포 표본 크기. p 의 분해능 하한 = 1/(n+1)
    null_note: str = ""             # 귀무분포를 무엇으로 잡았는가

    @property
    def budget(self) -> float:
        """귀속의 합이 넘을 수 없는 총량."""
        return abs(self.residual)

    @property
    def underpowered(self) -> bool:
        """검출 하한 아래인가. **주장 상한 강등 사유다** - 검정력이 없으면 교란
        민감도를 논할 대상 자체가 없다."""
        return self.mde80 is not None and abs(self.residual) < self.mde80

    @property
    def no_explanandum(self) -> bool:
        """설명할 것이 없는가. 잔차가 이 셀 **자신의** 귀무분포 한복판이면 가설을 세우지
        않는다 - 브리핑의 여덟 번째 영역(무사건 가설)을 1급으로 올린 자리다.

        ★ **보정하지 않은 `p_empirical` 을 쓴다.** 다중검정 보정은 "이것은 이례적이다"를
        어렵게 만드는 장치이지 "이것은 평범하다"를 쉽게 만드는 장치가 아니다. 여기에
        `p_scan` 을 걸면 방향이 뒤집힌다 - 250일 이력의 분해능 하한이 p_emp ≥ 1/251 이라
        Šidák 200셀 보정 후에는 **모든 셀**이 0.5 를 넘고, 파이프라인이 통째로 침묵한다
        (실측 확인). `p_scan` 은 반대 방향, 즉 이례성 주장의 상한에 쓴다(`scan_unresolved`).

        정규분포를 안 쓰는 이유는 꼬리다 - 일별 초과수익의 정규 근사는 하필 판정을
        내리는 극단에서 p 를 체계적으로 낮게 준다.
        """
        return self.p_empirical is not None and self.p_empirical > 0.10

    @property
    def at_resolution_floor(self) -> bool:
        """경험 p 가 이 이력이 낼 수 있는 **최솟값**인가.

        n일 이력의 양측 경험 p 는 1/(n+1) 아래로 못 내려간다. 거기 닿았다는 것은 "이
        검정이 보여줄 수 있는 만큼 극단이다"이지 "덜 극단이다"가 아니다.
        """
        if self.p_empirical is None or self.n_history < 1:
            return False
        return self.p_empirical <= 1.5 / (self.n_history + 1)

    @property
    def scan_unresolved(self) -> bool:
        """이례성을 **다중검정 뒤에도** 주장할 수 있나.

        `price_movement_trigger` 통과 셀만 분석하므로 무보정 극단성 판정은 순환논증이고,
        200 ETF 를 매일 훑으면 p=0.005 는 매일 하나씩 나온다.

        ★ 단 **분해능 바닥은 반증이 아니다.** 250일 이력의 p 하한이 1/251 이라 Šidák
        200셀 보정은 원리적으로 0.10 아래로 못 간다 - 여기서 무조건 상한을 깎으면
        `confirmed` 가 구조적으로 도달 불가가 되고, 그건 증거가 세다는 이유로 벌하는 것이다.
        바닥에 닿은 셀은 통과시키고, 그 사실은 `null_note` 가 적는다. 진짜로 이 축을
        열려면 이력이 아니라 **횡단면 풀**(수만 셀)이 필요하다 - 아직 없다.
        """
        if self.p_scan is None:
            return False
        return self.p_scan > 0.10 and not self.at_resolution_floor


# ── P1 · 지문 ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Axis:
    """지문 한 축. **못 쟀으면 `available=False` 로 남는다 - 침묵하지 않는다.**"""

    name: str
    available: bool
    value: Any = None
    says: str = ""
    kills: tuple[str, ...] = ()     # 이 축이 죽이는 가설 부류
    missing_input: str = ""         # 못 쟀을 때 무엇이 없어서인가


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """관측 자신의 지문. **가설 이전에, LLM 이전에 채취한다.**

    애널리스트가 제일 먼저 하는 일이고 후보 공간을 가장 많이 자른다. 후보를 주는 게
    아니라 **후보를 죽일 재료**를 준다 - 공시 전에 이미 움직였으면 그 뒤 논쟁이 무의미하다.
    """

    axes: list[Axis] = field(default_factory=list)

    def get(self, name: str) -> Axis | None:
        return next((a for a in self.axes if a.name == name), None)

    @property
    def kills(self) -> list[str]:
        out: list[str] = []
        for a in self.axes:
            out += [k for k in a.kills if k not in out]
        return out

    @property
    def unavailable(self) -> list[str]:
        return [f"{a.name}: {a.missing_input}" for a in self.axes if not a.available]

    def brief(self) -> str:
        L = ["관측 지문 - 가설을 세우기 전에 읽어라. **이 중 하나라도 어기는 가설은 죽는다.**"]
        for a in self.axes:
            if a.available:
                L.append(f"  [{a.name}] {a.says}")
            else:
                L.append(f"  [{a.name}] 측정 불가 - {a.missing_input}")
        if self.kills:
            L.append("")
            L.append("이 지문이 이미 배제한 것:")
            L += [f"  - {k}" for k in self.kills]
        return "\n".join(L)

    def deviance_map(self) -> dict[str, Deviance]:
        """축마다의 정규성 등급. **선언이 아니라 유도다.**

        Halpern-Hitchcock 2010 의 경고가 이 설계를 강제한다: "the modeler can now
        render any claim false, simply by choosing a normality order." 그래서 P2 의
        LLM 은 정규성을 주장할 수 없고, 여기서 자료로만 나온다.
        """
        return {a.name: deviance(a) for a in self.axes}

    def deviant_axes(self) -> list[str]:
        return [n for n, d in self.deviance_map().items()
                if d in ("deviant", "extreme")]


# ── P2 · 가설 ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Hypothesis:
    """작업가설 하나. **어휘 제한 없음** - 노드도 메커니즘도 자유롭게 세운다.

    Chamberlin(1890) multiple working hypotheses · Platt(1964) strong inference:
    애착을 분산시키려고 처음부터 여럿을 만든다. 형식만 강제한다 - 발명한 것이 **무엇을
    예측하는지** 적어야 P5 에서 갈린다.

    Chamberlin 을 인용하는 이유는 애착 분산만이 아니다. 그가 이 방법을 민 진짜 이유는
    몫의 배분이었다 - "several agencies were conjoined in the production of the
    phenomena. **Honors must often be divided between hypotheses.**" 그리고 그는 그것을
    조작적 요구로 못 박았다: "an estimate of the **measure and mode of each
    participation**" 이며 단일가설법은 "incompetent" 다. **P8 의 예산 회계가 그 요구의
    구현이다.** (Platt 1964 는 이 대목을 인용하지 않는다 - 그에게 다중가설은 배타적
    소거의 심리적 장치였다. 두 목적이 다르고, 그 긴장이 Zaks 2017 의 출발점이다.)
    """

    hid: str
    says: str                       # 이 가설이 주장하는 것 한 문장
    treatment: str                  # 원인 노드 id
    outcome: str                    # 결과 노드 id
    assignment: Assignment
    # ★ 역할. 기여도와 **다른 축**이다 - "원인 아님, 증폭" 은 share 로 표현되지 않는다.
    # **강제하지 않는다** - 어휘 밖 값으로 가설을 거부하면 형식 시비가 생성 예산을
    # 잡아먹는다. P2 가 별칭을 흡수하고, 못 읽으면 구조에서 유추한다.
    role: Role = "trigger"
    # 이 가설이 어느 메커니즘 영역에서 왔나. 커버리지 원장의 입력이다.
    domain: Domain = "information"
    # 신고인가 유추인가. **거부 대신 유추를 택한 대가를 이 두 줄로 갚는다** - 유추로
    # 채워진 커버리지 원장은 신고된 것과 다르게 읽혀야 한다.
    role_source: Literal["declared", "inferred"] = "declared"
    domain_source: Literal["declared", "inferred"] = "declared"
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)
    predicts: list[str] = field(default_factory=list)   # 이 가설이 예측하는 관측
    denies: list[str] = field(default_factory=list)     # 이 가설이면 관측되지 않아야 할 것
    events: list[str] = field(default_factory=list)     # 접지된 source_event_id
    anchor: tuple[float, float] | None = None
    anchor_source: str = ""
    cause_label: str = ""
    author: str = ""                # 어느 세션이 냈나 (독립성 감사)
    queries: list[str] = field(default_factory=list)    # 세우면서 던진 SQL


# ── P3 · 그래프 ─────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Latent:
    """미관측 공통원인 하나. **P5 가 소거하거나 P8 이 미소거로 적는다.**"""

    uid: str
    between: tuple[str, str]
    says: str
    source: Literal["declared", "compiled"]   # compiled = 배정 기제 규칙이 심었다
    blocked_by: list[str] = field(default_factory=list)   # 이걸로 조건화하면 막힌다는 제안


@dataclass(frozen=True, slots=True)
class Relation:
    """가설 두 개 사이의 관계 (Zaks 2017 RAR).

    **이것이 예산 회계를 정한다.** Zaks: "testing the relative causal force is only
    possible … under relationships of coincidence." 즉 `share` 는 coincident 에서만
    정의되고, 나머지 유형에서 합산하는 것은 정의되지 않은 양을 더하는 것이다.

    미판정은 `coincident` 가 **아니라** `unknown` 이다 - 기본값을 coincident 로 두면
    share 합산이 조용히 일어난다.
    """

    a: str
    b: str
    kind: RelationKind
    because: str = ""               # 판정 근거. 비면 unknown 으로 강등된다
    direction: str | None = None    # inclusive: 확장판 쪽. causal: 상류 쪽

    def __post_init__(self) -> None:
        if self.kind in DIRECTED_RELATIONS and self.direction not in (self.a, self.b):
            raise ValueError(
                f"{self.kind} 는 방향이 필수다 - a 와 b 중 하나여야 한다: {self.direction!r}")
        if self.kind not in DIRECTED_RELATIONS and self.direction is not None:
            raise ValueError(f"{self.kind} 는 대칭이다 - direction 을 쓸 수 없다")

    @property
    def pair(self) -> tuple[str, str]:
        return (self.a, self.b) if self.a <= self.b else (self.b, self.a)


def classify(h1: Hypothesis, h2: Hypothesis) -> RelationKind:
    """`predicts`/`denies` 로 관계를 유도한다. **감사 기준선이지 판정 그 자체가 아니다.**

    2017판 정의가 증거론적이라 예측집합 대수로 번역된다 (2011 학회본의 존재론적 정의로는
    이 함수를 쓸 수 없다 - 판본 선택이 구현을 가른다).

    ★ 순서가 의미를 갖는다. SIBI 89: "they need only make different predictions in at
    least one actual or possible instance" - **겹침이 아니라 갈림을 먼저 본다.** 갈림
    하나가 겹침 전부를 이긴다.

    ⚠️ 알려진 편향: `predicts`/`denies` 는 **적어둔** 예측이지 가능한 예측 전부가 아니다.
    적지 않은 갈림이 있으면 배타적 쌍을 congruent 로 오판한다 - 즉 이 함수는 **배타성을
    과소 판정하는 쪽**으로 기운다. 거짓 배제보다 거짓 병합이 안전하지만, 방향을 알고 써야
    한다. (Zaks 2017 p.351 의 판정 절차 원문은 미확보다. 이것은 대체물이 아니라 대용물이다.)
    """
    p1, d1 = {s.strip() for s in h1.predicts}, {s.strip() for s in h1.denies}
    p2, d2 = {s.strip() for s in h2.predicts}, {s.strip() for s in h2.denies}
    if (p1 & d2) or (p2 & d1):
        return "mutually_exclusive"
    if not p1 or not p2:
        return "unknown"
    if p1 == p2:
        return "unknown"            # 관측적으로 구분 불가 - 관계가 아니라 결함이다
    if p1 > p2 or p2 > p1:
        return "inclusive"
    return "congruent" if (p1 & p2) else "coincident"


@dataclass(frozen=True, slots=True)
class WorldGraph:
    """가설 합집합 위의 그래프 + **공통원인 완비 선언.**

    Hernán-Robins 조건: 그래프 위 임의의 두 변수의 공통원인은 모두 그래프에 있어야
    한다. 이걸 만족해야만 그 DAG 가 causal DAG 다. 완비 의무는 **그린 변수에 한해서만**
    걸리므로 표현력을 깎지 않는다 - 무엇을 그리든 자유고, 그린 것에 대한 정직성만 의무다.
    """

    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)
    latents: list[Latent] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    completeness: str = ""          # 완비 선언문 (무엇을 다 봤다고 하는가)
    violations: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    # 가설 쌍 관계. **비어 있거나 unknown 이 남으면 share 배분을 신뢰할 수 없다** -
    # P3 의 Hernán 완비 선언과 같은 성격의 폐쇄 장치다.
    relations: list[Relation] = field(default_factory=list)

    @property
    def directed(self) -> list[tuple[str, str]]:
        return [(e["from"], e["to"]) for e in self.edges]

    @property
    def bidirected(self) -> list[tuple[str, str]]:
        return [u.between for u in self.latents]

    def latent_ids(self) -> list[str]:
        return [u.uid for u in self.latents]

    def relation(self, a: str, b: str) -> Relation | None:
        key = (a, b) if a <= b else (b, a)
        return next((r for r in self.relations if r.pair == key), None)

    def unjudged_pairs(self) -> list[tuple[str, str]]:
        """관계가 선언되지 않았거나 `unknown` 인 쌍. 비어 있어야 예산이 닫힌다."""
        hids = [h.hid for h in self.hypotheses]
        out = []
        for i, a in enumerate(hids):
            for b in hids[i + 1:]:
                r = self.relation(a, b)
                if r is None or r.kind == "unknown":
                    out.append((a, b))
        return out

    def role_violations(self, fp: Fingerprint) -> list[str]:
        """신고된 역할이 지문의 정규성과 어긋나는가 (Halpern-Hitchcock).

        배경조건은 실제값이 default 인 것이고 촉발원은 deviant 인 것이다. 어긋나면
        **거부가 아니라 기록**이다 - 참조류가 우리 축과 다를 수 있고, HH 자신이 정규성
        순서를 고르는 것으로 어떤 주장이든 만들 수 있다고 경고한다.
        """
        devs = [d for d in fp.deviance_map().values() if d != "unknown"]
        if not devs:
            return []
        worst = max(devs, key=lambda d: DEVIANCE_RANK[d])
        out = []
        for h in self.hypotheses:
            need = ROLE_NEEDS.get(h.role)
            if need and worst not in need:
                out.append(
                    f"{h.hid}: role={h.role} 인데 지문의 최대 이탈은 {worst} 다 "
                    f"({h.role} 은 {'·'.join(need)} 를 요구한다)")
        return out


# ── P4 · 식별 ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Identification:
    """간선 하나의 식별 상태. **3값이다 - `not_identified` 가 정상 종료다.**

    점식별 실패가 종료가 아닌 이유: 가정 없는 경계(Manski)는 언제나 존재하고, 그 폭
    자체가 정보다. 지금까지는 `adjust=[]` 가 무조건 성공으로 읽혔다.
    """

    src: str
    dst: str
    status: IdentStatus
    adjust: list[str] = field(default_factory=list)
    alternatives: list[list[str]] = field(default_factory=list)
    iv: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)     # Latent.uid
    assumptions: list[str] = field(default_factory=list)    # identified_under 의 A
    bounds: tuple[float, float] | None = None               # Manski 구간
    bounds_note: str = ""

    @property
    def point_identified(self) -> bool:
        return self.status == "identified"


# ── P5 · 판별 ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Discriminator:
    """두 세계를 가르는 관측 하나.

    `pair`        두 가설이 다르게 예측하는 것 (Platt strong inference)
    `latent`      원인 가설과 **교란 U** 가 다르게 예측하는 것
    `structural`  제도·규칙이 그 가설을 **불가능하게** 만든다. SQL 이 필요 없다
    `capacity`    메커니즘의 물리적 수용력이 요구 규모에 미달한다
    `dose`        **주 가설 자신의** 처치 강도가 결과와 단조인가

    ★ `latent` 가 교란 폐쇄 장치다. P0 의 개입 정의가 요구하는 "통제되어야 할 교란"이
    무엇인지는 선언만으로 알 수 없다 - **U 를 가를 관측을 실제로 적을 수 있는가**가 그
    기준이 된다. 못 적으면 그 U 는 통제되지 않은 것이고, P8 이 미소거로 확정한다.

    ★ 뒤의 셋은 **통계 밖의 기각**을 위해 있다. Flash Crash 보고서의 가장 깨끗한 기각
    여섯 중 셋이 통계가 아니었다 - fat finger 는 CME 가격밴드 ±12pt·최대주문 2,000계약
    으로, 피드 지연 차익은 "통합피드는 별개 시장이 아니다"로 죽었다. `executable: bool`
    만 있으면 이런 기각을 **표현할 수 없다.** `dose` 는 Menkveld-Yueshen 의 결정적
    한 방이다 - 공식 서사의 처치 변수(매도자 공격강도)가 붕괴 구간에서 오히려 66% 줄었다.
    """

    kind: Literal["pair", "latent", "structural", "capacity", "dose"]
    target: str                     # "H1|H2" · Latent.uid · 또는 단일 hid
    observation: str                # 무엇을 보나
    predicts: dict[str, str] = field(default_factory=dict)   # 세계 -> 예측
    sql: str = ""                   # 실행 가능하면 질의
    executable: bool = False
    why_not: str = ""               # 실행 불가면 무엇이 없어서인가
    # 증거의 무게 (Good 1985 · Fairfield-Charman 2017 eq.4), 데시벨:
    #     WOE(Hi:Hj) = 10·log10( P(E|Hi,I) / P(E|Hj,I) )
    # 로그인 이유는 가법성과 Weber-Fechner 다. 해상도 하한은 1 dB - 그보다 잘게 쪼개는
    # 것은 거짓 정밀이다. 부호: 양수면 predicts 의 첫 세계를 지지한다.
    woe_db: int = 0
    woe_because: str = ""           # 각 세계를 inhabit 한 서술. 비면 무효다

    @property
    def common_prediction(self) -> bool:
        """두 세계가 사실상 같은 것을 예측 -> 무용.

        JND(3 dB) 미만이면 좋은 청력의 성인도 지각하지 못하는 차이다 - 그런 관측은
        아무것도 가르지 못하면서 질의는 잘 돌아가므로 `executable` 만 보면 소거로
        통과해 버린다. Bennett(2015) 이 smoking gun 이라 부른 0.2/0.05 도 6 dB 에
        불과하다는 F&C 의 교정이 눈금 감각을 준다.
        """
        return abs(self.woe_db) < 3


@dataclass(frozen=True, slots=True)
class DiscriminationPlan:
    """판별 설계 전체 + **U 소거 대장.**"""

    discriminators: list[Discriminator] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)

    def for_latent(self, uid: str) -> Discriminator | None:
        return next((d for d in self.discriminators
                     if d.kind in ("latent", "structural") and d.target == uid), None)

    def uncleared(self, latents: list[Latent]) -> list[Latent]:
        """소거 검정을 못 받은 U. **이 목록이 비지 않으면 `confirmed` 는 불가능하다.**

        `structural` 은 질의 없이도 소거한다 - 제도가 그 경로를 막았다는 것은 자료로
        반박할 대상이 아니라 규칙이다. 나머지는 실제로 돌아간 질의만 소거로 친다.
        """
        out = []
        for u in latents:
            d = self.for_latent(u.uid)
            if d is None or d.common_prediction:
                out.append(u)
            elif d.kind != "structural" and not d.executable:
                out.append(u)
        return out

    def by_kind(self, kind: str) -> list[Discriminator]:
        return [d for d in self.discriminators if d.kind == kind]

    def dose_failures(self) -> list[Discriminator]:
        """자기 처치가 결과와 **역방향**인 가설. 그 가설은 자기 증거로 죽는다.

        Menkveld-Yueshen 이 공식 서사를 이걸로 흔들었다 - 매도자는 붕괴 구간에서
        공격강도를 66% 줄였고 순매도의 4% 였다. 쌍 판별로는 절대 나오지 않는 기각이다.
        """
        return [d for d in self.discriminators
                if d.kind == "dose" and d.executable and d.woe_db < -3]


# ── P6 · 민감도 ─────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Sensitivity:
    """식별이 안 될 때 주장의 강도를 재는 축.

    E-value (VanderWeele-Ding 2017): 관측 연관을 0 으로 만들려면 미관측 교란이 처치·
    결과 양쪽과 얼마나 강하게 연관돼야 하나. 식별 없이도 수치가 나온다.
    """

    edge: str
    effect: float
    e_value: float
    e_value_lower: float | None = None    # 신뢰한계에 대한 E-value
    gamma: float | None = None            # Rosenbaum Γ
    observed_strongest: float | None = None   # 관측된 최강 교란의 연관 강도
    says: str = ""


# ── P7 · 대조 ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class NegativeControl:
    """영향이 없어야 할 자리가 조용한가 (Lipsitch 2010).

    시끄러우면 공통 교란의 증거다 - 이 검사는 효과를 확인하는 게 아니라 **설계가
    무엇을 잘못 잡고 있는지** 드러낸다.
    """

    kind: Literal["outcome", "exposure"]
    name: str
    n: int
    effect: float | None
    p: float | None
    passed: bool
    says: str = ""


@dataclass(frozen=True, slots=True)
class ConfoundingScreen:
    """사건창 내 타 공시 보유 기업 제외 (Kothari-Warner 표준 절차).

    지금까지 `false_if` 는 문자열로만 남고 아무도 조회하지 않았다.
    """

    n_before: int
    n_dropped: int
    dropped: list[dict[str, Any]] = field(default_factory=list)
    checked: bool = True
    note: str = ""


# ── P8 · 처분 ───────────────────────────────────────────────────────────
# NTSB *Writing Guide* 원문 대조로 정정된 것 두 가지:
#   1. Probable Cause 는 **단수가 아니다** - "can be a series of events or a listing
#      of separate causal factors." 실제 Asiana 214 의 PC 는 4개 병렬이다.
#   2. Findings 의 **첫 항목은 음성 소견 일괄**이다 - "The following were not factors
#      in the accident: …" 부정이 긍정보다 먼저 온다.
# (그리고 CFTC-SEC Flash Crash 보고서에는 Findings 절도 PC 문장도 없다. 두 문서는
#  다른 장르다 - 보고서에서 배울 것은 기각의 기술이고, 처분 형식은 NTSB 에서 온다.)

# Finding 단위 양상 어휘. 처분 전체가 아니라 **소견 하나하나**가 등급을 갖는다.
Modality = Literal["was", "likely", "would_likely_have", "may_have", "not_a_factor"]
MODALITY_SAY = {
    "was": "이었다",
    "likely": "이었을 가능성이 높다",
    "would_likely_have": "그랬다면 결과가 달라졌을 가능성이 높다",
    "may_have": "이었을 수 있다",
    "not_a_factor": "요인이 아니었다",
}


@dataclass(frozen=True, slots=True)
class Disposition:
    """검토한 후보 하나의 판정. **`undetermined` 도 산출물이다 - 침묵만 금지다.**"""

    candidate: str
    verdict: Verdict
    why: str
    evidence: dict[str, Any] = field(default_factory=dict)
    share: float | None = None
    contribution: float | None = None
    ceiling: ClaimCeiling = "undetermined"
    role: Role = "trigger"
    domain: Domain = "information"
    modality: Modality = "may_have"


@dataclass(frozen=True, slots=True)
class DomainCoverage:
    """메커니즘 영역 하나의 처분. **어휘가 아니라 커버리지를 닫는다.**

    P2 에는 여전히 골격도 후보 목록도 주지 않는다 - 골격을 주면 모델이 노드를 만들지
    않고 칸을 채운다. 대신 **열지 않은 영역에 침묵하지 않는다.** `not_considered` 가
    곧 침묵이고, 그것이 뉴스만 뒤져서 정보·기대 영역으로 편향되는 실패의 지문이다.
    """

    domain: Domain
    status: Literal["opened", "unavailable", "not_considered"]
    why: str = ""                   # unavailable 이면 무엇이 없어서인가
    hids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Findings:
    """한 셀의 최종 원장. 서술은 이것만 읽는다."""

    question: Question
    # ★ 복수다. modified HP 의 원인도 연언 집합일 수 있고(L=1 ∧ MD=1), NTSB 규약도
    # 병렬 나열을 명시 허용한다. 단수로 두면 과잉결정을 표현할 자리가 없다.
    probable_cause: list[Disposition] = field(default_factory=list)
    contributing: list[Disposition] = field(default_factory=list)
    not_contributing: list[Disposition] = field(default_factory=list)
    undetermined: list[Disposition] = field(default_factory=list)
    unexplained: float = 0.0
    over_budget: bool = False
    budget_note: str = ""
    uncleared_latents: list[Latent] = field(default_factory=list)
    ceiling: ClaimCeiling = "undetermined"
    ceiling_why: str = ""
    coverage: list[DomainCoverage] = field(default_factory=list)
    role_violations: list[str] = field(default_factory=list)
    unjudged_pairs: list[tuple[str, str]] = field(default_factory=list)

    @property
    def all_dispositions(self) -> list[Disposition]:
        return (self.probable_cause + self.contributing
                + self.not_contributing + self.undetermined)

    def by_role(self, role: Role) -> list[Disposition]:
        """역할별 원장. **Flash Crash 서사가 바로 이 분해다** - 배경조건·촉발원·
        전달경로·증폭·종료를 갈라야 "범인이 누구인가"가 아닌 설명이 된다.

        가설에서 나온 처분만 센다. 미설명분·측정 불가 축·U 같은 **합성 처분은 역할이
        없다** - 원장에는 남아야 하지만(침묵 금지) 인과 패키지의 칸은 아니다. `role`
        기본값이 `trigger` 라서 거르지 않으면 "미설명분"이 촉발원으로 보고된다.
        """
        return [d for d in self.all_dispositions
                if d.role == role and d.verdict != "not_contributing"
                and d.evidence.get("hid")]

    def unopened_domains(self) -> list[Domain]:
        seen = {c.domain for c in self.coverage if c.status != "not_considered"}
        return [d for d in DOMAIN_SAY if d not in seen]


__all__ = [
    "ASSIGNMENT_SAY", "CEILING_SAY", "COMPILED_LATENT", "DEVIANCE_RANK",
    "DIRECTED_RELATIONS", "DOMAIN_SAY", "MODALITY_SAY", "RELATION_SAY", "ROLE_NEEDS",
    "ROLE_SAY",
    "Assignment", "Axis", "ClaimCeiling", "ConfoundingScreen", "Deviance",
    "Discriminator", "DiscriminationPlan", "Disposition", "Domain", "DomainCoverage",
    "Findings", "Fingerprint", "Hypothesis", "IdentStatus", "Identification", "Latent",
    "Modality", "NegativeControl", "Question", "Relation", "RelationKind", "Role",
    "Sensitivity", "Verdict", "WorldGraph", "classify", "deviance",
]
