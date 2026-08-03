"""닫힌 어휘와 가설 튜플 — 구체화 사상 φ 의 정의역.

어휘가 열리면 φ 가 함수가 아니게 되고(같은 가설이 여러 실행으로 갈라진다),
그 분기 하나하나가 숨은 다중비교다. 그래서 여기 없는 값은 **생성 시점에 죽는다** —
검정에 닿기 전에. 확장은 사람이 하는 스키마 변경이지 에이전트 재량이 아니다.

설계 근거: docs/analysis-engine/causal-attribution-design.md §5–§7.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── 어휘 ────────────────────────────────────────────────────────────────
# 채널: 충격이 가치에 닿는 경로. 산업을 가로지르므로(환 노출 ∋ 조선·화학·반도체)
# 자명하지 않은 연결의 원천이고, 노출이 연속이라 용량-반응 반증이 성립한다.
CHANNELS = frozenset({
    "Q수량", "P판가", "C원가", "FX환", "R금리신용", "S주식수", "π확률", "K위험"})

# 계열충격 방아쇠·조건·노출이 딛는 계열의 족.
#   주주·주식수·공매도 는 PIT 스냅샷(pit_snapshot, 248 거래일 · 전종목)이 열었다.
#   주식수는 S주식수 채널의 **관측변수** - 그 전까지 이 채널은 라벨로만 존재했다.
#   레버리지·수익성·성장 은 재무제표(financial_statements, FY2000~ · 1,941종목)가
#   열었다. 회계연도 값이라 **가장 느린 상태** - 방아쇠가 아니라 조건 자리다(SVB).
SERIES_FAMILIES = frozenset({
    "가격잔차", "수급", "거래량", "거시", "지수잔차", "배수", "재무파생", "운영", "신용",
    "주주", "주식수", "공매도", "레버리지", "수익성", "성장", "섹터", "국면"})

# 같은 계열도 변환에 따라 역할이 다르다: 수급 누적=조건, 수급 당일 변화=방아쇠.
#   민감도: 공통 계열(거시·지수)은 하루에 값이 하나라 횡단면 분산이 0 이다 -
#   그 자체로는 어떤 종목의 움직임도 설명하지 못한다. 종목마다 다른 것은
#   **그 계열에 대한 민감도**(롤링 회귀 기울기)이고, 그것이 채널이 뜻하는 바다
#   (FX환 노출 = 환율 민감도). 공통 충격은 노출 이질성을 통해서만 설명이 된다.
TRANSFORMS = frozenset({"수준", "변화", "누적", "변동성", "갭", "민감도"})

MODERATOR_STATES = frozenset({"포지셔닝", "기대수준", "밸류", "국면", "서사단계"})

# DML 역할 태그. 매개를 통제 목록에 넣으면 효과가 흐르는 길을 막는다(나쁜 통제).
FEATURE_ROLES = frozenset({"처치강도", "조절자", "교란", "매개"})

OUTCOME_KINDS = frozenset({"수익률", "전이", "되돌림"})
# 되돌림 = ln(종가/일중고가). "왜 오르다 떨어졌나" 를 **일 단위 스칼라**로 환원한다.
# 경로 질문을 창 단위로 쪼개면 SEM 이 8차에 고친 범주 오류로 되돌아간다.
# 하루 총합의 일부가 아니므로 **몫 배정 금지**(assignable=False) - 전이와 같은 규율.

TRIGGER_KINDS = frozenset({"점", "계열"})
EXPOSURE_SOURCE_KINDS = frozenset({"속성", "관계"})

# 조건 술어의 종류. 처치변수 = 방아쇠(주 술어) ∧ 조건들 - 코드에서 이미 연언이다
# (edge_test 가 방아쇠로 행을 고르고 조건 mask 로 한 번 더 거른다). 종류를 나눈 것은
# **무엇을 재야 하는가**가 다르기 때문이다:
#   상태 → 계열족 시계열의 백분위 (느린 원인, 기존 취약성)
#   사건 → 최근 N 거래일 내 그 사건타입 발생 여부 (사건이 만든 느린 상태)
#   관계 → 타입 있는 1홉 위의 집중도 백분위 (구조가 만든 느린 상태)
CONDITION_KINDS = frozenset({"상태", "사건", "관계"})

# 관계 노출의 닫힌 어휘 (19R). 객체 타입 사이의 **타입 있는 1홉**이고, 각자
# `v_link.link_type` 또는 속성 동일성에 실측으로 대응한다. 자유 문자열을 받던
# 자리다 - 받아 봐야 검정기가 SAME_INDUSTRY 아니면 전부 거부했으므로, 어휘가
# 열려 있다는 인상만 주고 실제로는 침묵하는 거부였다.
#   실측 쌍(2026-07-30 기준): 공급망 102 · 제휴 244 · 공동발행 332 · 지분 24
RELATIONS = frozenset({
    "SAME_INDUSTRY",   # 속성 동일성 - 관계가 아니라 대리(교란에 약하다)
    "SUPPLY_CHAIN",    # CUSTOMER ↔ SUPPLIER
    "PARTNERSHIP",     # PARTNER ↔ PARTNER_2
    "OWNERSHIP",       # INVESTOR ↔ TARGET_COMPANY
    "CO_ISSUER",       # 같은 사건에 공동 발행 - 가장 약한 결합
})
COMPARATORS = frozenset({">=", "<="})

# ── 전역 상수 — 가설별 지정 금지. 바꾸면 전 실험 재실행. ─────────────────
W_MINUTES = 15          # 사건 창 길이
EXPOSURE_CUT = 0.80     # 노출 상위 절단 백분위
MIN_N = 30              # 이보다 얇으면 판정불가
ALPHA = 0.05
FOLDS = 5               # 폴드 A(게이트) / B(계수) 교차적합
FACTOR_MODEL = "market_only"  # 정적 제거는 시장 β 만 — 부호 갈리는 요인은 채널로 남긴다


class VocabError(ValueError):
    """어휘 밖 값. 가설 생성 시점에 죽어야 검정이 오염되지 않는다."""


def _need(value: str, vocab: frozenset[str], slot: str) -> str:
    if value not in vocab:
        raise VocabError(f"{slot}={value!r} 는 닫힌 어휘 밖이다. 허용: {sorted(vocab)}")
    return value


# ── 튜플 부품 ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Condition:
    """처치의 **조건 술어** — 왜 이 회사·얼마나. 수준은 단독 원인이 될 수 없다
    ("어제도 나빴는데 어제는 안 빠졌다") — 방아쇠와 결합해서만 쓴다(INUS).

    처치변수 = 방아쇠(주 술어) ∧ 조건들. 방아쇠는 완화 불가(없으면 처치 자체가
    없다)이고 조건은 완화 가능하다(§14 조절자 모드 - 충족 클래스가 얇으면 엣지
    존재는 전체 패널로 검정하고 조건은 교호 대비로 보고한다).

    종류가 셋인 이유는 **재는 방법**이 다르기 때문이다. 셋 다 "느린 상태"라는
    점은 같다 - 빠른 것은 방아쇠 슬롯으로 간다.
      상태: ident=계열족   → 그 계열 시계열의 백분위 (기존 취약성)
      사건: ident=사건타입 → 최근 lookback 거래일 내 발생 여부 (0/1)
      관계: ident=관계     → 타입 있는 1홉 위 집중도의 백분위
    """
    ident: str              # 상태 → 계열족 · 사건 → 사건타입 id · 관계 → 관계
    transform: str = "수준"  # 상태·관계
    comparator: str = ">="   # 상태·관계
    percentile: float = 0.5  # 상태·관계 임계 백분위 (0,1)
    kind: str = "상태"
    lookback: int = 60      # 사건: 최근 N 거래일 (0 이하 금지)

    def __post_init__(self) -> None:
        _need(self.kind, CONDITION_KINDS, "조건.종류")
        if self.kind == "상태":
            _need(self.ident, SERIES_FAMILIES, "조건.계열족")
            _need(self.transform, TRANSFORMS, "조건.변환")
        elif self.kind == "관계":
            _need(self.ident, RELATIONS, "조건.관계")
        elif not self.ident.strip():
            raise VocabError("사건 조건은 사건타입 id 가 필요하다")
        if self.kind in ("상태", "관계"):
            _need(self.comparator, COMPARATORS, "조건.비교")
            if not 0.0 < self.percentile < 1.0:
                raise VocabError(f"임계 백분위 {self.percentile} 는 (0,1) 밖이다")
        elif self.lookback < 1:
            raise VocabError(f"사건 조건의 회고 창은 1 이상이다: {self.lookback}")

    @property
    def key(self) -> str:
        """패널 피처 키 · 서술용 한 조각. 종류마다 읽히는 모양이 다르다."""
        if self.kind == "사건":
            return f"사건:{self.ident}/최근{self.lookback}일"
        return f"{self.ident}/{self.transform}"


@dataclass(frozen=True, slots=True)
class Trigger:
    """빠른 원인 — 왜 지금. 점(사건타입) 또는 계열충격(계열족 × 전역 임계).
    시간 스케일이 슬롯을 정한다: 수개월 걸친 금리 급등은 방아쇠가 아니라
    조건 생성기다(SVB)."""
    kind: str               # 점 | 계열
    ident: str              # 점 → 사건타입 id · 계열 → 계열족

    def __post_init__(self) -> None:
        _need(self.kind, TRIGGER_KINDS, "방아쇠.종류")
        if self.kind == "계열":
            _need(self.ident, SERIES_FAMILIES, "방아쇠.계열족")
        elif not self.ident.strip():
            raise VocabError("점 방아쇠는 사건타입 id 가 필요하다")


@dataclass(frozen=True, slots=True)
class ExposureSource:
    """대상군·위약군을 유도하는 노출의 출처. 검정자가 비교군을 고르지 못하게
    가설이 여기 못 박는다. 관계 노출은 교란에 약하므로(사외이사 겹침 ≈ 같은
    지역·규모의 대리) 위약 = 같은 속성 ∧ 관계 없음 이 강제된다."""
    kind: str               # 속성 | 관계
    ident: str              # 속성 → 계열족 · 관계 → 온톨로지 경로 (예: "SUPPLIES_TO")
    transform: str = "수준"  # 속성일 때만 의미
    hops: int = 1           # 관계일 때만 의미

    def __post_init__(self) -> None:
        _need(self.kind, EXPOSURE_SOURCE_KINDS, "노출원.종류")
        if self.kind == "속성":
            _need(self.ident, SERIES_FAMILIES, "노출원.계열족")
            _need(self.transform, TRANSFORMS, "노출원.변환")
        elif self.ident not in RELATIONS:
            raise VocabError(f"관계 노출원은 닫힌 관계 어휘여야 한다: {sorted(RELATIONS)}")
        if self.hops < 1:
            raise VocabError("홉수는 1 이상이다")


@dataclass(frozen=True, slots=True)
class HypothesisTuple:
    """가설 하나 = 엣지 하나. 자유 텍스트 없음 — 산문은 검정할 수 없다.

    가설 에이전트의 계약: 슬롯 채우기만. id 생성·수치·새 어휘 금지.
    검사 실패는 폐기+재시도이지 자동 보정이 아니다(보정이 곧 대필이다).
    동시 다중 원인은 튜플 여러 개 — 몫 배분은 시간 분해 트리가 한다.
    """
    conditions: tuple[Condition, ...]
    trigger: Trigger
    channel: str
    exposure: ExposureSource
    # from_role·to_role 은 제거했다 (18R): 닫힌 어휘를 자칭하면서 검증 없는 자유
    # 텍스트 슬롯 2개를 열어뒀고, 라이브 트레이스에서 에이전트가 계열족 이름을
    # ("운영"→"운영") 채워 넣는 게 잡혔다. 어디서도 쓰이지 않았다. 관계 노출이
    # 몫 배정 가능해지면(창 정렬, 백필 #5) 온톨로지 역할 어휘로 닫아서 되살린다.
    outcome: str            # 수익률 | 전이
    sign: int               # +1 | -1
    reduction_note: str = ""  # 환원 근거 (토큰→타입). 자유 텍스트가 아니라 감사 메모.
    intent: str = ""          # **이 튜플로 검정하려는 인과 주장** 한 문장 - 간선에 실려
                              # 검정 에이전트에게 전달된다. 무엇이 사실이면 성립인가.

    def __post_init__(self) -> None:
        _need(self.channel, CHANNELS, "채널")
        _need(self.outcome, OUTCOME_KINDS, "결과종류")
        if self.sign not in (+1, -1):
            raise VocabError(f"부호는 ±1 이다: {self.sign}")



@dataclass(frozen=True, slots=True)
class Feature:
    """조건계수(CATE)의 특징 하나. 역할 태그가 없으면 DML 이 조용히 틀린다:
    처치강도를 공변량으로 빼면 효과가 소거되고, 매개를 통제하면 경로가 막힌다."""
    name: str
    value: float
    role: str

    def __post_init__(self) -> None:
        _need(self.role, FEATURE_ROLES, "특징.역할")


__all__ = [
    "ALPHA", "CHANNELS", "COMPARATORS", "CONDITION_KINDS", "Condition",
    "EXPOSURE_CUT", "EXPOSURE_SOURCE_KINDS", "ExposureSource",
    "FACTOR_MODEL", "FEATURE_ROLES", "FOLDS", "Feature", "HypothesisTuple",
    "MIN_N", "MODERATOR_STATES", "OUTCOME_KINDS", "RELATIONS", "SERIES_FAMILIES",
    "TRANSFORMS", "TRIGGER_KINDS", "Trigger", "VocabError", "W_MINUTES"]
