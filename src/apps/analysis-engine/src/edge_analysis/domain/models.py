"""Domain models and vocabulary — plain data plus the Explanation accessors.

No I/O. The structured objects (Member, Decomposition, PriceTrigger,
KodexEvent) replace the ad-hoc ``dict[str, Any]`` payloads the pipeline used to
pass around; Explanation wraps the untyped DeepSeek response with typed
accessors while keeping the raw payload for the run archive.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The target ETF's core constituents (weights prioritise the analysis packet).
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

# Stamped on the observation as its decomposition-output version (ALPHA-411);
# the L0 gate threshold itself lives in the pipeline (single writer).
POLICY_VERSION = "l0-abs-v1"

# DeepSeek verdict/confidence vocabulary -> Cloud Event Store enum values.
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
    """One ETF constituent's weight (fraction), from the holdings snapshot."""

    ticker: str
    name: str | None
    weight: float


@dataclass(frozen=True, slots=True)
class Member:
    """One priced constituent's contribution to the ETF move."""

    ticker: str
    name: str | None
    weight: float
    ret: float
    contribution: float
    rank: int


@dataclass(frozen=True, slots=True)
class Decomposition:
    """Constituent-contribution decomposition over the priced subset."""

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
    """The pipeline-written L0 gate row consumed for the trade day."""

    trigger_id: str
    observed_return: float | None
    reason: str | None
    abs_gate: bool
    rel_gate: bool


@dataclass(frozen=True, slots=True)
class KodexEvent:
    """A KODEX-constituent source event assembled by the pipeline."""

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
    """Typed view over the raw DeepSeek JSON response.

    ``raw`` is kept verbatim so the run archive preserves fields the DB mapping
    drops (verdict wording, key_evidence, unexplained — ALPHA-407).
    """

    raw: dict[str, Any]

    @property
    def summary(self) -> str:
        return str(self.raw.get("explain") or self.raw.get("summary") or "")

    @property
    def headline(self) -> str | None:
        return str(self.raw.get("headline") or "") or None

    @property
    def explanation_type(self) -> str:
        return _VERDICT_TO_TYPE.get(str(self.raw.get("verdict")), "UNCERTAIN")

    @property
    def confidence_level(self) -> str | None:
        return _CONFIDENCE_MAP.get(str(self.raw.get("confidence")))

    @property
    def is_valid(self) -> bool:
        return "verdict" in self.raw and bool(self.raw.get("explain") or self.raw.get("summary"))
