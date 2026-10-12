from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROLES = ("question", "portfolio", "outlook", "opinion")
LIMITS = {"question": (300, 500), "portfolio": (500, 800), "outlook": (500, 800), "opinion": (400, 700)}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Holding(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    account: Literal["일반 계좌", "ISA", "연금저축", "IRP"] = "일반 계좌"
    amount_krw: Decimal = Field(gt=0, le=10**13, max_digits=14, decimal_places=0)


class AnalysisInput(StrictModel):
    holdings: list[Holding] = Field(min_length=1, max_length=20)
    question: str = Field(default="", max_length=1000)
    horizon: Literal["3년 안", "3~10년", "10년 이상", "정한 때 없음"]


class Evidence(StrictModel):
    source_id: str
    passage_id: str


class Section(StrictModel):
    paragraphs: list[str] = Field(min_length=2, max_length=5)
    evidence: list[Evidence] = Field(max_length=6)
    limitations: list[str] = Field(max_length=3)

    @model_validator(mode="after")
    def nonempty(self):
        if any(not p.strip() for p in self.paragraphs):
            raise ValueError("Empty paragraph")
        if any(len(p) > 100 for p in self.limitations):
            raise ValueError("Limitation exceeds 100 characters")
        return self


class FinalReport(StrictModel):
    question: Section
    portfolio: Section
    outlook: Section
    opinion: Section


def validate_section(role, raw, sources):
    section = Section.model_validate(raw)
    length = sum(len(p) for p in section.paragraphs)
    low, high = LIMITS[role]
    if not low <= length <= high:
        raise ValueError(f"{role}: paragraphs must total {low}..{high} characters; got {length}")
    by_id = {s["id"]: s for s in sources}
    citations = []
    for e in section.evidence:
        passages = {p['id']: p['text'] for p in by_id.get(e.source_id, {}).get('passages', [])}
        if e.passage_id not in passages:
            raise ValueError(f"Evidence must select a registered passage: {e.source_id}/{e.passage_id}")
        citations.append({**e.model_dump(), 'quote': passages[e.passage_id]})
    if role in ("portfolio", "outlook") and not section.evidence:
        raise ValueError("Portfolio and outlook require source evidence")
    return {**section.model_dump(), 'evidence': citations}


def calculate(request: AnalysisInput):
    total = sum(h.amount_krw for h in request.holdings)
    return {"total_krw": str(total), "holdings": [
        {"name": h.name, "account": h.account, "weight_pct": float(round(h.amount_krw / total * 100, 2))}
        for h in request.holdings
    ], "expected_return_1y": None, "dividend_yield_1y": None,
        "scores": None, "historical_performance": None,
        "unavailable_reason": "검증된 가격·분배금 시계열과 전망 모형이 아직 연결되지 않았어요."}
