"""도구의 모델용 설명과 매개변수 스키마. pi 확장(pi/extension.ts)이 이 목록을 그대로 등록한다(단일 정본)."""
from __future__ import annotations

_STR = {"type": "string"}
_PATH = {"type": "string", "description": "칸 경로. 예: headline[0], body[3].p, contrast.after.text, behaviors[+](끝에 추가)"}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


SPECS = [
    ("view_case", "지금 사건 전체를 '경로 = 값' 줄로 본다. 고치기 전에 반드시 먼저 본다.", _obj({}, []), ("update", "discover")),
    ("view_news", "기사(news-…)나 가져온 웹 원문(web-…) 한 건을 읽는다. 출처로 쓰려면 먼저 읽어야 한다.",
     _obj({"id": {**_STR, "description": "news-… 또는 web-… id"}}, ["id"]), ("update", "discover", "plan", "mover")),
    ("search_news", "찾기 창구 ①: 빅카인즈 뉴스 수집분(제목·요약, 최근 21일)에서 찾는다. 낱말을 모두 포함한 기사를 최신순으로.",
     _obj({"query": {**_STR, "description": "찾을 낱말들 (예: '하이브 송치')"},
           "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "최근 며칠 (기본 30)"}}, ["query"]),
     ("update", "discover", "mover")),
    ("web_search", "찾기 창구 ②: 웹 검색. 결과는 미리보기다. 인용하려면 web_fetch 로 원문을 가져온다. 세션당 웹 호출 6회.",
     _obj({"query": _STR}, ["query"]), ("update", "discover", "mover")),
    ("web_fetch", "웹 원문을 가져와 보관하고 읽는다. 공식 판단·공시·기사 문장을 확인할 때 쓴다.", _obj({"url": _STR}, ["url"]),
     ("update", "discover", "mover")),
    ("add_source", "읽은 기사(news-…)나 원문(web-…)을 sources 에 등록한다. 등록된 출처만 인용할 수 있다.",
     _obj({"ref": {**_STR, "description": "news-… 또는 web-…"},
           "published_at": {**_STR, "description": "웹 원문의 발행일 YYYY-MM-DD (자동으로 모를 때)"},
           "publisher": {**_STR, "description": "웹 원문의 발행 주체 이름 (예: 금융위원회)"}}, ["ref"]), ("update", "discover")),
    ("str_replace", "문자열 칸 안의 일부를 바꾼다. old 는 그 칸에 정확히 한 번 있어야 한다.",
     _obj({"path": _PATH, "old": _STR, "new": _STR}, ["path", "old", "new"]), ("update", "discover")),
    ("set_value", "칸 값을 통째로 넣는다(문자열·숫자·객체·목록). 목록 끝에 더하려면 경로 끝에 [+].",
     _obj({"path": _PATH, "value": {"description": "넣을 값"}}, ["path", "value"]), ("update", "discover")),
    ("remove", "목록 항목(예: body[2])이나 beneficiary 를 지운다.", _obj({"path": _PATH}, ["path"]), ("update", "discover")),
    ("set_impact", "파급력 가치를 0~100억원 사이 점수로 정한다. 조사 우선순위이며 증거가 아니다.",
     _obj({"value": {"type": "number", "minimum": 0, "maximum": 100}, "reason": _STR}, ["value", "reason"]), ("update", "discover")),
    ("propose_verdict", "판정을 밝혀짐(confirmed)·해소(refuted)로 바꾼다. 이 세션에서 읽은 공식 판단·법정공시 출처가 있을 때만 받아들인다. 확인일은 그 출처의 발행일이 된다.",
     _obj({"status": {"type": "string", "enum": ["confirmed", "refuted"]}, "source_id": _STR, "note": _STR},
          ["status", "source_id", "note"]),
     ("update",)),
    ("list_cases", "이미 다루는 사건 목록 (같은 사건을 다시 만들지 않기 위해).", _obj({}, []), ("discover",)),
    ("create_case", "새 사건 뼈대를 만든다. 세션당 하나. 이후 set_value·add_source 로 채운다.",
     _obj({"id": {**_STR, "description": "영문 소문자·숫자·하이픈 (예: kumyang-mongolia-2023)"},
           "subject": _STR, "speaker": _STR, "audience": _STR,
           "instruments": {"type": "array", "minItems": 1, "items": _obj({"code": _STR, "name": _STR}, ["code", "name"])}},
          ["id", "subject", "speaker", "audience", "instruments"]), ("discover",)),
    ("write_notes", "이 사건 조사 노트를 갱신한다. next 는 다음 세션이 찾아볼 것 목록(통째로 바뀜), record 는 이번 조사 기록 한 항목. 끝내기 전에 반드시 한 번.",
     _obj({"next": {"type": "array", "maxItems": 8, "items": {**_STR, "description": "한 줄: 무엇을 어디서 확인할지"}},
           "record": {**_STR, "description": "이번에 확인한 것·바꾼 것·막힌 것 (800자 이내)"}}, ["next", "record"]), ("update",)),
    ("set_plan", "이번 실행에서 조사할 사건 순서(order)와 이번에 볼 필요 없는 후보(skip)를 정한다.",
     _obj({"order": {"type": "array", "items": _obj({"case": {**_STR, "description": "후보 사건 id"},
                                                    "reason": {**_STR, "description": "왜 이 순서인지 한 문장"}}, ["case", "reason"])},
           "skip": {"type": "array", "items": {**_STR, "description": "새 기사가 무관·반복이라 볼 필요 없는 후보 id"}}},
          ["order"]), ("plan",)),
    ("view_disclosure", "이 종목의 최근 공시(첫 메시지 목록) 원문을 읽는다. 읽은 공시는 dart-<접수번호> 로 출처에 쓴다. 세션당 8회.",
     _obj({"rcept_no": {**_STR, "description": "공시 접수번호(14자리)"}}, ["rcept_no"]), ("mover",)),
    ("set_mover_verdict", "급등을 일으킨 사건과 판정을 남긴다. tag: promising(유망주, 원문으로 확인)·false(허위매물, 원문과 어긋나거나 근거 없음)·pending(판단 보류).",
     _obj({"tag": {"type": "string", "enum": ["promising", "false", "pending"]},
           "confidence": {"type": "number", "minimum": 0, "maximum": 1, "description": "판정 확신도"},
           "event_title": {**_STR, "description": "급등 사건 한 줄(60자 이내, 예: '미국 제약사와 3천억 기술수출 계약 공시')"},
           "event_kind": {"type": "string", "enum": ["disclosure", "earnings", "contract", "policy", "news", "rumor", "unknown"]},
           "event_at": {**_STR, "description": "사건 시각 ISO(아는 경우)"},
           "claim": {**_STR, "description": "회사·기관·보도가 내세운 주장(300자 이내)"},
           "finding": {**_STR, "description": "원문으로 확인한 것과 어긋나는 것(400자 이내)"},
           "reasons": {"type": "array", "maxItems": 3, "items": {**_STR, "description": "판정 이유 한 줄"}},
           "source_ids": {"type": "array", "items": {**_STR, "description": "이 세션에서 읽은 news-·web-·dart- id"}}},
          ["tag", "confidence", "event_title", "event_kind", "claim", "finding", "reasons", "source_ids"]), ("mover",)),
    ("check", "계약 검사와 실제 화면 렌더를 한다. '통과' 가 나와야 finish 할 수 있다.", _obj({}, []), ("update", "discover")),
    ("finish", "세션을 끝낸다. 바꿀 것이 없었으면 그렇게 적고 끝내도 된다. 바꾼 내용은 검사를 통과해야 끝난다.",
     _obj({"summary": {**_STR, "description": "무엇을 왜 바꿨는지 한두 문장"}}, ["summary"]), ("update", "discover", "plan", "mover")),
]


def specs(mode: str, *, web: bool) -> list[dict]:
    """세션 종류(plan·update·discover·mover)와 웹 사용 여부에 맞는 도구 목록."""
    return [{"name": name, "description": desc, "parameters": params}
            for name, desc, params, modes in SPECS
            if mode in modes and (web or name not in ("web_search", "web_fetch"))]
