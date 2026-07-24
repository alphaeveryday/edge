"""도메인 모델과 어휘 — 순수 데이터 + Explanation 접근자.

I/O 없음. 구조화 객체(Member·Decomposition·PriceTrigger·KodexEvent)는 파이프라인이
주고받던 임시 ``dict[str, Any]`` payload 를 대체한다. Explanation 은 타입 없는 DeepSeek
응답을 타입 있는 접근자로 감싸되 원본 payload 를 런 아카이브용으로 보존한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 대상 ETF 의 핵심 구성종목(비중은 분석 패킷 우선순위에만 쓴다).
KODEX_CONSTITUENTS: dict[str, tuple[str, float]] = {
    "000660": ("SK하이닉스", 0.40),
    "005930": ("삼성전자", 0.20),
    "042700": ("한미반도체", 0.05),
    "036930": ("주성엔지니어링", 0.04),
    "240810": ("원익IPS", 0.024),
    "058470": ("리노공업", 0.021),
    "319660": ("피에스케이", 0.020),
    "000990": ("DB하이텍", 0.020),
    "039030": ("이오테크닉스", 0.020),
}

# observation 에 분해-산출 버전으로 스탬프된다(ALPHA-411).
# L0 게이트 임계값 자체는 파이프라인(단일 writer)에 있다.
POLICY_VERSION = "l0-abs-v1"

# DeepSeek verdict/confidence 어휘 → Cloud Event Store enum 값.
_VERDICT_TO_TYPE = {
    "공식 이벤트 선행": "EVENT_SUPPORTED",
    "시장·섹터 주도": "MIXED",
    "가격 선행·설명 후행": "MIXED",
    "수급·흐름 추정": "PRICE_ONLY",
    "원인 미확인": "UNCERTAIN",
}
_CONFIDENCE_MAP = {"높음": "HIGH", "중간": "MEDIUM", "보류": "LOW"}


@dataclass(frozen=True, slots=True)
class Holding:
    """holdings 스냅샷의 ETF 구성종목 1개 비중(fraction)."""

    ticker: str
    name: str | None
    weight: float


@dataclass(frozen=True, slots=True)
class Member:
    """가격 보유 구성종목 1개의 ETF 등락 기여."""

    ticker: str
    name: str | None
    weight: float
    ret: float
    contribution: float
    rank: int


@dataclass(frozen=True, slots=True)
class Decomposition:
    """가격 보유 부분집합에 대한 구성종목-기여 분해."""

    members: list[Member]
    proxy_ret: float | None
    covered_weight: float
    total_weight: float
    coverage: float
    top1: float | None
    top3: float | None
    advancing: int
    total_priced: int
    n_constituents: int


@dataclass(frozen=True, slots=True)
class PriceTrigger:
    """당일 소비한, 파이프라인이 쓴 L0 게이트 행."""

    trigger_id: str
    observed_return: float | None
    reason: str | None
    abs_gate: bool
    rel_gate: bool


@dataclass(frozen=True, slots=True)
class KodexEvent:
    """파이프라인이 조립한 KODEX 구성종목 source event 1개."""

    source_event_id: str
    event_type_code: str
    available_at: str
    entity_id: str
    ticker: str
    thread_id: str | None
    novelty_status: str
    title: str


@dataclass(frozen=True, slots=True)
class Explanation:
    """원본 DeepSeek JSON 응답에 대한 타입 있는 뷰.

    ``raw`` 는 그대로 보존해, DB 매핑이 버리는 필드(verdict 원문·key_evidence·
    unexplained — ALPHA-407)를 런 아카이브가 남긴다.
    """

    raw: dict[str, Any]

    @property
    def summary(self) -> str:
        """설명 본문 — explain 우선, 없으면 summary(둘 다 없으면 빈 문자열)."""
        return str(self.raw.get("explain") or self.raw.get("summary") or "")

    @property
    def headline(self) -> str | None:
        """헤드라인 — 빈 문자열이면 None."""
        return str(self.raw.get("headline") or "") or None

    @property
    def explanation_type(self) -> str:
        """verdict 를 Event Store enum 으로 매핑(미지의 verdict 는 UNCERTAIN)."""
        return _VERDICT_TO_TYPE.get(str(self.raw.get("verdict")), "UNCERTAIN")

    @property
    def confidence_level(self) -> str | None:
        """confidence 를 enum 으로 매핑(미지의 값은 None)."""
        return _CONFIDENCE_MAP.get(str(self.raw.get("confidence")))

    @property
    def is_valid(self) -> bool:
        """verdict 와 본문(explain/summary)이 모두 있으면 유효."""
        return "verdict" in self.raw and bool(self.raw.get("explain") or self.raw.get("summary"))
