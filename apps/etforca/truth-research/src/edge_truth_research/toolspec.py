"""도구의 모델용 설명과 매개변수 스키마. pi 확장(pi/extension.ts)이 이 목록을 그대로 등록한다(단일 정본)."""
from __future__ import annotations

from .ach import KINDS, MARKS, STATUSES

_STR = {"type": "string"}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


SPECS = [
    ("read_disclosure", "첫 메시지 목록의 공시·시장조치 원문을 읽는다(국내 한국거래소 KIND · 미국 SEC EDGAR, 미국 8-K·6-K 는 첨부 보도자료까지). "
     "읽은 것은 kind-<접수번호>(미국 sec-<접수번호>)로 증거에 쓴다. 세션당 8회.",
     _obj({"acptno": {**_STR, "description": "목록 첫 칸의 접수번호(국내 14자리 · 미국 0001234567-26-000001 꼴)"}}, ["acptno"])),
    ("view_news", "기사(news-…)나 가져온 웹 원문(web-…) 한 건을 읽는다. 증거로 쓰려면 먼저 읽어야 한다.",
     _obj({"id": {**_STR, "description": "news-… 또는 web-… id"}}, ["id"])),
    ("search_news", "빅카인즈 뉴스 수집분(제목·요약, 최근 21일)에서 찾는다. 낱말을 모두 포함한 기사를 최신순으로.",
     _obj({"query": {**_STR, "description": "찾을 낱말들 (예: '혜인 공급계약')"},
           "days": {"type": "integer", "minimum": 1, "maximum": 60, "description": "최근 며칠 (기본 30)"}}, ["query"])),
    ("web_search", "웹 검색. 결과는 미리보기다. 증거로 쓰려면 web_fetch 로 원문을 가져온다.", _obj({"query": _STR}, ["query"])),
    ("web_fetch", "웹 원문을 가져와 보관하고 읽는다. 특징주 기사·회사 보도자료·기관 발표 문장을 확인할 때 쓴다.", _obj({"url": _STR}, ["url"])),
    ("set_hypotheses", "급등 이유에 대한 경쟁가설 3~6개를 세운다. 다시 부르면 증거가 비워진다.",
     _obj({"hypotheses": {"type": "array", "minItems": 3, "maxItems": 6, "items": _obj(
         {"text": {**_STR, "description": "가설 한 줄 (예: '데이터센터 463억 공급계약이 실제로 체결됐다')"},
          "kind": {"type": "string", "enum": list(KINDS), "description": " · ".join(f"{k}: {v}" for k, v in KINDS.items())}},
         ["text", "kind"])}}, ["hypotheses"])),
    ("add_evidence", "증거 한 건과 가설마다의 일치(C)·어긋남(I)·무관(N)을 넣는다. 무게: high 공시·공식 원문, mid 구체적 보도, low 단일·불분명 보도.",
     _obj({"source_id": {**_STR, "description": "읽은 news-·web-·kind-(미국 sec-) id, 또는 공시 목록 자체는 kind-list(미국 sec-list)"},
           "note": {**_STR, "description": "이 증거가 말하는 것 한 줄 (예: '10/7~21 회사 공시 없음')"},
           "weight": {"type": "string", "enum": ["high", "mid", "low"]},
           "marks": {"type": "object", "additionalProperties": {"type": "string", "enum": list(MARKS)},
                     "description": "가설 id → C·I·N (예: {\"H1\": \"I\", \"H2\": \"C\"})"}},
          ["source_id", "note", "weight", "marks"])),
    ("set_premises", "전제 검증 단계(첫 메시지가 그렇다고 할 때)에서만. 급등을 이끈 주장을 핵심 전제 2~4개로 쪼개고 전제마다 상태를 하나 고른다. "
     "다시 부르면 전제를 통째로 바꾼다.",
     _obj({"premises": {"type": "array", "minItems": 2, "maxItems": 4, "items": _obj(
         {"text": {**_STR, "description": "전제 한 줄 (예: '아이폰 듀오 힌지 공급사로 확정됐다')"},
          "about": {"type": "string", "enum": ["company", "market"], "description": "company 회사에 관한 사실 · market 업종·수급·시장 반응"},
          "core": {"type": "boolean", "description": "주가가 이 전제에 기대는가(회사 전제만)"},
          "status": {"type": "string", "enum": list(STATUSES), "description": " · ".join(f"{k}: {v}" for k, v in STATUSES.items())},
          "spread_as_fact": {"type": "boolean", "description": "주장·보도가 이 전제를 확정된 사실처럼 말했는가"},
          "sources": {"type": "array", "items": _STR, "description": "상태의 근거가 된 읽은 출처 id"},
          "note": {**_STR, "description": "출처가 이 전제에 대해 말하는 것 한 줄"}},
         ["text", "about", "core", "status", "spread_as_fact", "sources", "note"])}}, ["premises"])),
    ("set_verdict", "표로 태그·확신도를 코드가 정하고, 사건·주장·확인 결과 글과 함께 판정을 남긴다. 결과가 증거와 어긋나면 증거를 보태고 다시 부른다.",
     _obj({"event_title": {**_STR, "description": "왜 올랐는지 한 줄(32자 이내, 시스템 지시의 제목 규칙)"},
           "event_kind": {"type": "string", "enum": ["disclosure", "earnings", "contract", "policy", "news", "rumor", "unknown"]},
           "event_at": {**_STR, "description": "사건 날짜 YYYY-MM-DD(아는 경우)"},
           "claim": {**_STR, "description": "시장·보도가 내세운 급등 이유(300자 이내)"},
           "finding": {**_STR, "description": "어느 설명이 남았고 왜인지 쉬운 말로(400자 이내, 가설 번호 없이)"},
           "reasons": {"type": "array", "maxItems": 3, "items": {**_STR, "description": "가른 증거 한 줄(160자 이내)"}}},
          ["event_title", "event_kind", "claim", "finding", "reasons"])),
    ("finish", "세션을 끝낸다. 판정을 남긴 뒤에만 끝난다.", _obj({"summary": _STR}, ["summary"])),
    # 국내 세션 + 그래프 DB 가 있을 때만(ALPHA-1453). 결과는 실마리다 — 증거는 거기 적힌 기사·공시를 직접 읽어 넣는다.
    ("company_events", "이 회사가 참여한 최근 사건(뉴스·공시에서 뽑힌 사건 그래프): 날짜·사건 타입·이 회사 역할·내용·보도 수·대표 기사 id. "
     "급등 전에 진행 중이던 일(계약 협상·증자 절차 등)이 있었는지 볼 때 쓴다. 증거로 쓰려면 적힌 news- id 를 view_news 로 읽는다.",
     _obj({"days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "최근 며칠 (기본 90)"}}, [])),
    ("find_events", "다른 회사의 같은 타입 사건 중 수치 조건에 맞는 선례를 센다(사건 그래프 스킬 4절의 타입·수치·단위). 이번 일이 흔한 크기인지 볼 때 쓴다. "
     "단위가 다르거나 값이 불확실한 사건은 '모름'으로 따로 센다.",
     _obj({"event_type": {**_STR, "description": "사건 타입 코드 (예: COMPANY.CONTRACT.SIGNING)"},
           "metric": {**_STR, "description": "그 타입의 수치 코드 (예: CONTRACT_VALUE)"},
           "op": {"type": "string", "enum": ["gte", "lte"]},
           "value": {**_STR, "description": "기준값 숫자, 원·주·% 단위 그대로 (예: '45000000000' = 450억 원, '30' = 30%)"},
           "unit": {"type": "string", "enum": ["KRW", "USD", "PCT", "COUNT", "DAY", "YEARS", "MONTHS"], "description": "저장 단위"},
           "basis": {"type": "string", "enum": ["TOTAL", "ANNUAL", "QUARTERLY", "UNKNOWN"],
                     "description": "총액·연간 등 기준. 보통 생략한다 — 저장값 대부분이 UNKNOWN 이라 생략하면 기준 무관으로 찾는다"},
           "days": {"type": "integer", "minimum": 1, "maximum": 730, "description": "최근 며칠 (기본 180)"}},
          ["event_type", "metric", "op", "value", "unit"])),
]
GRAPH_TOOLS = ("company_events", "find_events")


def specs(*, web: bool, graph: bool = False) -> list[dict]:
    """도구 목록. 웹 키가 없으면 웹 도구를, 국내 세션에 그래프 DB 가 없으면 그래프 도구를 뺀다."""
    return [{"name": name, "description": desc, "parameters": params}
            for name, desc, params in SPECS
            if (web or name not in ("web_search", "web_fetch")) and (graph or name not in GRAPH_TOOLS)]
