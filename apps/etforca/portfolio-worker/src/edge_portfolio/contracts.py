from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from datetime import date
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

ROLES = ("question", "portfolio", "outlook", "opinion")
LIMITS = {role: (90, 650) for role in ROLES}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Holding(StrictModel):
    name: str = Field(min_length=1, max_length=250)
    kind: Literal['etf', 'stock', 'cash'] = 'etf'
    instrument_id: str | None = Field(default=None, max_length=32)
    account: Literal["일반 계좌", "ISA", "연금저축", "IRP"] = "일반 계좌"
    amount_krw: Decimal | None = Field(default=None, gt=0, le=10**13, max_digits=16, decimal_places=2)


class Preferences(StrictModel):
    accounts: list[Literal['일반 계좌', 'ISA', '연금저축', 'IRP']] = Field(default_factory=lambda: ['일반 계좌'], max_length=4)
    contribution: str = Field(default='', max_length=100)
    dividend_goal: str = Field(default='', max_length=100)
    interests: list[str] = Field(default_factory=list, max_length=10)
    note: str = Field(default='', max_length=1000)
    tax_profile: 'TaxProfile | None' = None


class TaxAccount(StrictModel):
    account: Literal['일반 계좌', 'ISA', '연금저축', 'IRP']
    subtype: str = Field(default='', max_length=40)
    opened: str = Field(default='', max_length=10)
    remaining_man: str = Field(default='', pattern=r'^\d{0,10}(\.\d{1,2})?$')
    paid_man: str = Field(default='', pattern=r'^\d{0,10}(\.\d{1,2})?$')

    @model_validator(mode='after')
    def valid_date(self):
        if self.opened and date.fromisoformat(self.opened) > date.today():
            raise ValueError('Future account opening date')
        return self


class TaxProfile(StrictModel):
    recommended_accounts: list[Literal['ISA', 'IRP', '연금저축']] = Field(default_factory=list, max_length=3)
    residence: Literal['대한민국 거주자', '비거주자', '확인 필요']
    age: Literal['15세 미만', '15~18세', '19세 미만', '19~54세', '55세 이상', '확인 필요']
    income: str = Field(min_length=1, max_length=60)
    financial_income_tax: Literal['아니요', '예', '확인 필요']
    previous_year_earned_income: Literal['', '예', '아니요', '확인 필요'] = ''
    irp_qualification: Literal['', '근로자·자영업자·직역연금 가입자', '퇴직급여 수령 대상', '해당 없음', '확인 필요'] = ''
    accounts: list[TaxAccount] = Field(max_length=4)

    @model_validator(mode='after')
    def eligible_targets(self, info: ValidationInfo):
        # Saved reports predate eligibility checks and must remain usable for follow-ups.
        if info.context and info.context.get('persisted_input'):
            return self
        if 'ISA' in self.recommended_accounts and (
            self.residence == '비거주자' or self.financial_income_tax == '예' or self.age == '15세 미만'
            or (self.age in ('15~18세', '19세 미만') and self.previous_year_earned_income == '아니요')
        ):
            raise ValueError('ISA 가입 요건에 맞지 않아요. 전략에 포함할 계좌를 다시 선택해 주세요.')
        if 'IRP' in self.recommended_accounts and self.irp_qualification == '해당 없음':
            raise ValueError('IRP 가입 대상이 아니에요. 전략에 포함할 계좌를 다시 선택해 주세요.')
        return self


class ConstraintChip(StrictModel):
    """8차: 고객이 칩으로 고른 조건만 지킬 조건이다. 글(질문·원하는 포트폴리오)은 참고일 뿐 조건이 되지 않는다."""
    kind: Literal['no_leverage', 'some_bonds', 'dividend', 'max_positions', 'no_sell', 'kr_listed_etf_only']
    value: int | None = Field(default=None, ge=1, le=20, strict=True)

    @model_validator(mode='after')
    def value_only_for_count(self):
        if (self.kind == 'max_positions') != (self.value is not None):
            raise ValueError('max_positions needs value 1..20; other chips take no value')
        return self


class V8Fields(StrictModel):
    monthly_krw: int | None = Field(default=None, ge=0, le=10**13, strict=True)
    goal_text: str = Field(default='', max_length=1000)
    constraints: list[ConstraintChip] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def distinct_chips(self):
        if len({chip.kind for chip in self.constraints}) != len(self.constraints):
            raise ValueError('Duplicate constraint chip')
        return self


class DraftInput(V8Fields):
    holdings: list[Holding] = Field(max_length=20)
    question: str = Field(default='', max_length=1000)
    horizon: Literal['', '3년 안', '3~10년', '10년 이상', '정한 때 없음'] = ''
    input_mode: Literal['amount', 'percent', 'names'] = 'amount'
    planning: bool = False
    preferences: Preferences = Field(default_factory=Preferences)

    @model_validator(mode='after')
    def valid_interests(self):
        if any(not i.strip() or len(i) > 50 for i in self.preferences.interests):
            raise ValueError('Invalid interest')
        return self


class AnalysisInput(V8Fields):
    draft_id: UUID | None = None
    holdings: list[Holding] = Field(max_length=20)
    question: str = Field(default="", max_length=1000)
    horizon: Literal["3년 안", "3~10년", "10년 이상", "정한 때 없음"]
    input_mode: Literal['amount', 'percent', 'names'] = 'amount'
    planning: bool = False
    preferences: Preferences = Field(default_factory=Preferences)

    @model_validator(mode='after')
    def valid_amounts(self):
        if not self.holdings and not self.planning:
            raise ValueError('Holdings required for portfolio review')
        if any(len(i) > 50 for i in self.preferences.interests):
            raise ValueError('Interest too long')
        if self.input_mode != 'names' and any(h.amount_krw is None for h in self.holdings):
            raise ValueError('Amounts required')
        if self.input_mode == 'amount' and any(h.amount_krw != h.amount_krw.to_integral_value() for h in self.holdings):
            raise ValueError('KRW must be whole units')
        if self.input_mode == 'percent' and self.holdings and sum(h.amount_krw for h in self.holdings) != 100:
            raise ValueError('Percentages must total 100')
        return self


class Evidence(StrictModel):
    source_id: str
    passage_id: str


def multiple_sentences(text):
    # ETF names contain initialisms such as U.S.; decimals are not sentence ends.
    text = re.sub(r'\b(?:[A-Za-z]\.){2,}', lambda m: m.group().replace('.', ''), text)
    text = re.sub(r'(?<=\d)\.(?=\d)', '', text)
    text = re.sub(r'([.!?。！？])["”’\)\]]+', r'\1', text)
    return bool(re.search(r'[.!?。！？]\s*[^.!?。！？\s]', text))


class Section(StrictModel):
    paragraphs: list[str] = Field(min_length=3, max_length=5)
    evidence: list[Evidence] = Field(max_length=6)
    limitations: list[str] = Field(max_length=3)

    @model_validator(mode="after")
    def nonempty(self):
        if any(not p.strip() for p in self.paragraphs):
            raise ValueError("Empty paragraph")
        for i, paragraph in enumerate(self.paragraphs, 1):
            if len(paragraph) > 180:
                raise ValueError(f'Bullet {i}: at most 180 characters')
        if any(len(p) > 100 for p in self.limitations):
            raise ValueError("Limitation exceeds 100 characters")
        if any(not p.strip() for p in self.limitations):
            raise ValueError('Empty limitation')
        return self


class Scores(StrictModel):
    leadership: float = Field(ge=0, le=5, multiple_of=0.5)
    profitability: float = Field(ge=0, le=5, multiple_of=0.5)
    stability: float = Field(ge=0, le=5, multiple_of=0.5)


class Evaluation(StrictModel):
    scores: Scores | None
    reason: Section


class RecommendationEvaluation(Evaluation):
    # 4~5점은 프롬프트 지침이다. 서버가 점수로 추천을 막으면 고객은 추천 없는 결과를 받는다
    scores: Scores


class Allocation(StrictModel):
    instrument_id: str = Field(min_length=1, max_length=32)
    account: Literal['일반 계좌', 'ISA', '연금저축', 'IRP']
    weight_pct: Decimal = Field(gt=0, le=100, max_digits=11, decimal_places=8)


class Recommendation(StrictModel):
    title: str = Field(min_length=1, max_length=40)
    allocations: list[Allocation] = Field(min_length=1, max_length=20)
    evaluation: RecommendationEvaluation

    @model_validator(mode='after')
    def full_allocation(self):
        if sum(a.weight_pct for a in self.allocations) != 100:
            raise ValueError('Recommended weights must total exactly 100')
        keys = [(a.instrument_id, a.account) for a in self.allocations]
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate recommended instrument/account')
        return self


class Insight(StrictModel):
    title: str = Field(min_length=1, max_length=50)
    content: Section


class ReturnForecast(StrictModel):
    percent: float = Field(ge=-100, le=200, allow_inf_nan=False)
    reason: str = Field(min_length=40, max_length=350)
    evidence: list[Evidence] = Field(min_length=1, max_length=4)


class TaskAnswer(StrictModel):
    question_index: int = Field(ge=1, le=5)
    status: Literal['answered', 'partial', 'needs_input']
    answer: str = Field(min_length=10, max_length=500)
    missing: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode='after')
    def meaningful_answer(self):
        if len(self.answer.strip()) < 10 or any(not value.strip() for value in self.missing):
            raise ValueError('Task answer and missing reasons must contain meaningful text')
        if self.status == 'answered' and self.missing:
            raise ValueError('Answered task cannot have missing requirements')
        return self


class EvidenceCoverage(StrictModel):
    instrument_id: str = Field(min_length=1, max_length=32)
    field: Literal['structure', 'holdings', 'fees', 'distributions']
    status: Literal['supported', 'missing']
    evidence: list[Evidence] = Field(default_factory=list, max_length=3)
    claim_as_of: date | None = None


class FinalReport(StrictModel):
    answers: list[TaskAnswer] = Field(default_factory=list, max_length=5)
    evidence_coverage: list[EvidenceCoverage] = Field(default_factory=list, max_length=240)
    withheld_reason: Literal['quality_failed', 'missing_input', 'missing_data', 'customer_failed', 'search_exhausted'] | None = None
    question: Section
    portfolio: Section
    outlook: Section
    opinion: Section
    current_evaluation: Evaluation
    recommendation: Recommendation | None
    selected_candidate_id: str | None = None
    insights: list[Insight] = Field(min_length=1, max_length=3)
    return_forecast: ReturnForecast | None = None
    # 8차 결과 맨 위·최종 추천·더 물어보기 칸. 옛 보고서에는 없으므로 비어 있어도 된다(화면이 기존 필드로 대신 보인다)
    headline: str = Field(default='', max_length=40)
    answer: str = Field(default='', max_length=300)
    final_note: str = Field(default='', max_length=120)
    suggested_questions: list[Annotated[str, Field(min_length=2, max_length=40)]] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def four_or_no_questions(self):
        if len(self.suggested_questions) not in (0, 4):
            raise ValueError('suggested_questions: write exactly four follow-up questions')
        return self


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
    total = sum(h.amount_krw or 0 for h in request.holdings) if request.input_mode != 'names' else None
    return {"total_krw": str(total) if request.input_mode == 'amount' and total else None, "holdings": [
        {"name": h.name, "kind": h.kind, "instrument_id": h.instrument_id, "account": h.account, "weight_pct": float(round(h.amount_krw / total * 100, 2)) if total else None}
        for h in request.holdings
    ], "expected_return_1y": None, "dividend_yield_1y": None,
        "scores": None, "historical_performance": None,
        "unavailable_reason": "과거 가격·분배금 자료를 아직 조회하지 않았어요. 미래 전망은 별도로 확인해요."}
