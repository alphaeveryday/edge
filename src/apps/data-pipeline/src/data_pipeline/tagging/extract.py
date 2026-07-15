"""뉴스 이벤트 태깅 — 기사 1건 → assertion N건 (ALPHA-138).

기사(제목+리드)를 LLM 에 넣어 **문서가 주장하는 사건**을 온톨로지 라벨로 뽑는다. 산출은
`document_assertion`(+`assertion_argument`) 물리 스키마에 대응하는 dict 다 — 적재는 범위
밖(ALPHA-190)이고 이 모듈은 DB 를 모른다.

**assertion 은 event 가 아니다.** 여기서 내는 건 '이 문서가 이렇게 주장했다'는 사실이고,
기사가 틀려도 주장했다는 사실은 남는다. 여러 기사가 같은 사건을 주장하면 assertion 은 N 개,
canonical event 는 1 개다 — 그 조립(source_event)과 스레드 lineage 판정은 후속이며, 결정론이라야
point-in-time 재현이 된다. 그래서 **모델은 추출만 하고 lineage 를 판정하지 않는다**(Rule 5 —
분류·추출은 모델, 라우팅·결정론 변환은 코드).

역할 분담(Rule 5):
  - 모델: doc_class 판정, event_type_code 분류, 술어·역할 추출
  - 코드: 라벨이 온톨로지 허용 집합에 드는지 검증, 결측·불량을 사유와 함께 드러내기

미결(어휘 계약): `document_assertion.modality_code` 는 **허용 어휘가 정의된 적이 없어**(CHECK·
문서·ADR 도, 상류 온톨로지에도 없음) 여기서 값을 내지 않는다. 발명하면 그게 사실상 계약이
되는데, assertion 은 PIT 재현 대상이라 어휘 확정 후 소급 백필이 비싸다. 다만 이게 적재를
막지는 않는다 — ALPHA-361 이 스키마의 NOT NULL 을 풀어 NULL 적재를 허용한다. 어휘가 확정되면
CHECK·NOT NULL 복원과 함께 여기서 값을 내도록 확장한다. predicate 는 온톨로지가 기본값을 주므로
같은 문제가 아니다.

`complete_fn` 은 주입받는다 — 이 모듈은 어느 LLM 벤더인지 모른다. 호출부가 (system, user)
→ 응답 문자열 callable 을 넘긴다. 벤더 배선·모델 선택은 이 모듈의 관심사가 아니고, 단위
테스트는 실제 호출 없이 가짜 complete_fn 으로 돈다.
"""

from __future__ import annotations

import json
import logging

from .ontology import (
    DOC_CLASSES,
    allowed_predicates,
    allowed_roles,
    default_predicate,
    event_type_codes,
    load_profiles,
    ontology_version,
    prompt_catalog,
    required_roles,
)

logger = logging.getLogger(__name__)

TAGGER_VERSION = "tagging-v1"

_SYSTEM = """너는 한국 금융 뉴스에서 '문서가 주장하는 사건'을 구조화하는 추출기다.

규칙:
- 기사에 적힌 것만 뽑는다. 배경지식으로 값을 보충하거나 추론하지 않는다.
- event_type_code 와 predicate_code, role_code 는 아래 카탈로그에 있는 것만 쓴다. 새 라벨을 만들지 않는다.
- 한 기사가 여러 사건을 주장하면 events 를 여러 개 낸다. 사건이 없으면 빈 배열이다.
- 역할 값은 기사에 나온 표현을 그대로 옮긴다(정규화·번역·풀어쓰기 금지).
- 확실하지 않으면 그 역할을 비운다. 지어내는 것보다 비는 게 낫다.

doc_class:
- EVENT: 일어난/결정된 구체 사건을 보도
- OPINION_OR_ANALYSIS: 전망·해설·논평
- NO_EVENT_MARKET_COMMENTARY: 시황·지수 등락 나열
- PROMOTIONAL_OR_SOLICITATION: 홍보·투자권유

출력은 JSON 하나만. 설명·코드펜스 금지.
{"doc_class": "...", "events": [{"event_type_code": "...", "predicate_code": "...",
 "arguments": [{"role_code": "...", "text": "..."}], "confidence": 0.0~1.0}]}"""


def build_prompt(title: str, lead: str | None, published_at: str | None) -> tuple[str, str]:
    """(system, user) 프롬프트. 카탈로그는 ontology 가 파생해 검증과 같은 출처를 쓴다."""
    parts = [f"제목: {title}"]
    if lead:
        parts.append(f"리드: {lead}")
    if published_at:
        parts.append(f"발행: {published_at}")
    user = "\n".join(parts)
    return f"{_SYSTEM}\n\n=== 이벤트 타입 카탈로그 ===\n{prompt_catalog()}", user


def _parse_response(text: str) -> dict:
    """LLM 응답 → dict. 코드펜스만 벗기고, 그 외 형식 위반은 예외로 올린다.

    펜스는 지시를 어겨도 흔해서 관대하게 처리하지만, 그 너머의 관대한 복구(정규식으로 JSON
    긁어내기 등)는 하지 않는다 — 응답이 계약을 벗어난 걸 조용히 메우면 품질 저하가 안 보인다.
    """
    if not isinstance(text, str):
        # complete_fn 이 문자열이 아닌 걸 돌려주는 건 주입 계약 위반이다(벤더 SDK 가 None·
        # 객체를 낼 수 있다). .strip() 에서 AttributeError 로 터지면 배치가 죽는다.
        raise ValueError(f"complete_fn 응답이 문자열이 아님: {type(text).__name__}")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError(f"응답이 객체가 아님: {type(parsed).__name__}")
    return parsed


def _validate_event(raw: object) -> tuple[dict | None, list[str]]:
    """이벤트 1건을 온톨로지로 검증 → (assertion, reasons). 불량이면 (None, reasons).

    검증은 **타입별** 허용 집합으로 한다 — 전역 86개 역할 중 하나라는 건 의미가 없고,
    그 타입이 가질 수 있는 역할인지가 계약이다. 허용 밖 역할은 그 역할만 떨어뜨리고 나머지
    assertion 은 살린다(한 역할의 환각이 사건 전체를 버리게 하지 않는다).
    """
    reasons: list[str] = []
    if not isinstance(raw, dict):
        return None, ["event_not_object"]

    code = raw.get("event_type_code")
    if code not in load_profiles():
        # 온톨로지 밖 타입 = 모델이 라벨을 발명한 것. 사건 자체를 버린다(타입이 없으면
        # 역할 검증의 기준도 없다).
        return None, ["unknown_event_type"]

    # 술어는 모델이 주면 그걸 쓰고, 없거나 허용 밖이면 **온톨로지 기본값**으로 채운다.
    # 비우지 않는 이유: document_assertion.predicate_code 가 NOT NULL 이라 None 을 내면
    # 타입이 맞게 나온 assertion 을 적재할 수 없다. 그렇다고 사건을 버리는 건 손실이 더 크다 —
    # 타입이 이미 사건의 성격을 정하고, 술어는 그 안의 부차 정보다. 기본값은 지어낸 값이
    # 아니라 그 타입 자신의 allowed_predicates[0] 이고, 출처를 predicate_source 로 남겨
    # 다운스트림이 추출값과 구분할 수 있게 한다(alphamale 어셈블러와 같은 규약).
    predicate = raw.get("predicate_code")
    predicate_source = "model"
    if predicate is None:
        predicate_source = "ontology_default"
        predicate = default_predicate(code)
    elif predicate not in allowed_predicates(code):
        reasons.append("predicate_not_allowed")
        predicate_source = "ontology_default"
        predicate = default_predicate(code)
    if predicate is None:
        # 타입에 허용 술어가 하나도 없다 = 온톨로지 스냅샷 이상. 조용히 넘기면 적재에서
        # NOT NULL 위반으로 터진다 — 여기서 사유로 드러낸다.
        reasons.append("no_predicate_available")
        predicate_source = None

    permitted = allowed_roles(code)
    arguments: list[dict] = []
    seen: set[str] = set()
    raw_arguments = raw.get("arguments") or []
    if not isinstance(raw_arguments, list):
        # 스칼라(42)·dict 가 오면 순회가 각각 TypeError·키 순회로 샌다 — 배열이 아니면
        # 역할이 없는 것으로 보고 사유만 남긴다.
        reasons.append("arguments_not_list")
        raw_arguments = []
    for arg in raw_arguments:
        if not isinstance(arg, dict):
            reasons.append("argument_not_object")
            continue
        role = arg.get("role_code")
        text = arg.get("text")
        if role not in permitted:
            reasons.append("role_not_allowed")
            continue
        if not isinstance(text, str) or not text.strip():
            # 역할만 있고 값이 없으면 근거가 없다 — 빈 역할은 안 채운 것과 같다.
            reasons.append("argument_text_missing")
            continue
        if role in seen:
            reasons.append("duplicate_role")
            continue
        seen.add(role)
        # entity_id 는 여기서 안 채운다 — 엔티티 해소는 별도 소관이고, 모델이 사내 엔티티
        # 식별자를 알 수 없다. text 가 해소의 입력이다.
        arguments.append({"role_code": role, "text": text.strip(), "entity_id": None})

    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        # bool 은 int 의 하위형이라 isinstance(True, int) 가 True — 신뢰도로 통과시키면 안 된다.
        confidence = None
    elif not 0.0 <= float(confidence) <= 1.0:
        reasons.append("confidence_out_of_range")
        confidence = None

    missing = required_roles(code) - seen
    assertion = {
        "event_type_code": code,
        "predicate_code": predicate,
        # 술어가 모델 추출인지 온톨로지 기본값인지 — 적재 후에도 구분이 남아야 한다.
        "predicate_source": predicate_source,
        "arguments": arguments,
        "confidence": None if confidence is None else float(confidence),
        # 필수역할 충족 여부 — 후속 조립이 partial 을 확정 사건으로 못 쓰게 하는 표시다.
        "completeness": "complete" if not missing else "partial",
        "missing_required_roles": sorted(missing),
    }
    return assertion, reasons


def extract_assertions(article: dict, *, complete_fn) -> dict:
    """canonical 뉴스행 1건 → 태깅 결과.

    반환: {status, doc_class, assertions[], reasons[], ontology_version, tagger_version}
    status 는 이 기사에 무슨 일이 있었는지 드러낸다(Rule 12 — 조용한 0건 금지):
      ok              — 추출 성공(EVENT 가 아니면 assertions 는 빈 배열)
      no_title        — 제목 결측이라 호출조차 안 함
      llm_error       — complete_fn 이 던짐
      llm_unparseable — 응답이 JSON 계약 위반
      bad_doc_class   — doc_class 가 허용 어휘 밖

    `reasons` 는 통과했더라도 버린 게 있으면 남는다(환각 역할·범위 밖 술어 등) — 태깅
    품질을 나중에 집계할 수 있게 사유를 세어 둔다.
    """
    base = {
        "article_id": article.get("article_id"),
        "ontology_version": ontology_version(),
        "tagger_version": TAGGER_VERSION,
        "doc_class": None,
        "assertions": [],
        "reasons": [],
    }

    title = article.get("title")
    if not isinstance(title, str) or not title.strip():
        # 제목이 없으면 뽑을 근거가 없다 — 호출 낭비 없이 사유로 드러낸다.
        return {**base, "status": "no_title"}

    system, user = build_prompt(title, article.get("lead_text"), article.get("published_at"))
    try:
        response = complete_fn(system, user)
    except Exception as exc:
        # 한 기사의 LLM 실패가 배치를 죽이지 않게 격리하되, 조용히 성공으로 만들지 않는다.
        logger.warning("태깅 LLM 실패(격리): article_id=%s %s", article.get("article_id"), exc)
        return {**base, "status": "llm_error", "reasons": [str(exc)]}

    try:
        parsed = _parse_response(response)
    except (ValueError, IndexError) as exc:
        logger.warning("태깅 응답 파싱 실패: article_id=%s %s", article.get("article_id"), exc)
        return {**base, "status": "llm_unparseable", "reasons": [str(exc)]}

    doc_class = parsed.get("doc_class")
    if doc_class not in DOC_CLASSES:
        return {**base, "status": "bad_doc_class", "reasons": [f"doc_class={doc_class!r}"]}

    raw_events = parsed.get("events") or []
    if not isinstance(raw_events, list):
        # 스칼라(42)면 순회가 TypeError 로 터지고, 문자열이면 글자를 순회한다 — 둘 다
        # 배치를 죽이거나 무의미한 사유를 쏟는다. 배열이 아니면 사건 없음으로 본다.
        reasons_prefix = ["events_not_list"]
        raw_events = []
    else:
        reasons_prefix = []

    reasons: list[str] = list(reasons_prefix)
    assertions: list[dict] = []
    if doc_class == "EVENT":
        # EVENT 만 주장을 낸다 — 논평·시황·홍보는 사건 보도가 아니라 추출 대상이 아니다.
        for raw in raw_events:
            try:
                assertion, event_reasons = _validate_event(raw)
            except Exception as exc:
                # 예기치 못한 응답 모양이 배치를 죽이지 않게 사건 단위로 격리한다
                # (normalize_news 의 row_error 와 동형) — 격리≠은폐라 사유는 남긴다.
                logger.warning("사건 검증 실패(격리): article_id=%s %s",
                               article.get("article_id"), exc)
                reasons.append("event_validation_error")
                continue
            reasons.extend(event_reasons)
            if assertion is not None:
                assertions.append(assertion)
        if not assertions:
            # EVENT 라 해놓고 사건을 못 낸 것도 자기모순이다(아래 반대 방향과 대칭).
            # 골든 150건 eval 에서 이 조합 2건이 **둘 다** 티처 기준 비-사건이었다 — 즉
            # 오탐 EVENT 의 신호다. 라벨을 코드가 뒤집진 않는다(분류는 모델 몫, Rule 5) —
            # 사유로 남겨 다운스트림·집계가 쓰게 한다.
            reasons.append("event_doc_class_without_events")
    elif raw_events:
        # 비-EVENT 인데 사건을 냈다 = 모델 자기모순. 사건은 안 쓰되 사유로 드러낸다.
        reasons.append("events_on_non_event_doc_class")

    return {**base, "status": "ok", "doc_class": doc_class,
            "assertions": assertions, "reasons": reasons}
