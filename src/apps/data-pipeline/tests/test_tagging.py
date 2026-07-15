"""뉴스 이벤트 태깅 테스트 — 온톨로지 구속 + 추출 검증 (ALPHA-138).

LLM 은 호출하지 않는다 — complete_fn 을 주입해 응답을 고정한다. 여기서 검증하는 건 모델의
정확도가 아니라 **모델이 계약을 어겼을 때 우리 코드가 그걸 드러내는지**다. 정확도 측정은
실제 호출이 필요해 별도 eval(scripts/eval_tagging.py) 소관이다.
"""

import json

import pytest

from data_pipeline.tagging import extract, ontology


def _fn(payload) -> object:
    """고정 응답 complete_fn — dict 면 JSON 직렬화, str 이면 그대로 낸다."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return lambda system, user: text


def _article(**over) -> dict:
    row = {"article_id": "a1", "title": "에코프로비엠, 북미 완성차와 양극재 공급계약 체결",
           "lead_text": "2조원 규모다.", "published_at": "2026-07-01T09:00:00+00:00"}
    row.update(over)
    return row


def _event(**over) -> dict:
    ev = {"event_type_code": "COMPANY.CONTRACT.SIGNING", "predicate_code": "SIGN",
          "arguments": [{"role_code": "SUPPLIER", "text": "에코프로비엠"},
                        {"role_code": "CONTRACT_OBJECT", "text": "양극재"}],
          "confidence": 0.9}
    ev.update(over)
    return ev


# ── 온톨로지 스냅샷 ────────────────────────────

def test_snapshot_is_the_agreed_contract():
    # WHY: 온톨로지는 분석 담당(alphamale)과의 계약이다. 타입 수·버전이 말없이 바뀌면 우리
    # 산출물의 라벨 의미가 바뀌는데, ontology_version 만 보고는 못 알아챈다 — 스냅샷 교체를
    # 의식적 행위로 만들려고 계약 값을 고정한다.
    assert ontology.ontology_version() == "0.1.0"
    assert len(ontology.load_profiles()) == 53


def test_roles_are_scoped_per_type_not_global():
    # WHY: 검증을 '전역 86개 역할 중 하나'로 하면 CONTRACT.SIGNING 에 EARNINGS 의 역할을
    # 붙여도 통과한다. 타입별 집합이라야 라벨 구속이 의미를 가진다.
    assert "SUPPLIER" in ontology.allowed_roles("COMPANY.CONTRACT.SIGNING")
    assert "SUPPLIER" not in ontology.allowed_roles("COMPANY.EARNINGS.RESULT_RELEASE")


def test_prompt_catalog_and_validation_share_one_source():
    # WHY: 프롬프트가 부르는 목록과 검증이 쓰는 목록이 갈라지면, 모델이 프롬프트를 지켜도
    # 검증에서 떨어지는 모순이 생긴다. 카탈로그는 허용 타입 전량을 담아야 한다.
    catalog = ontology.prompt_catalog()
    for code in ontology.event_type_codes():
        assert code in catalog


# ── 추출: 정상 ────────────────────────────

def test_extracts_assertion_with_roles():
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "EVENT", "events": [_event()]}))
    assert result["status"] == "ok"
    assert result["doc_class"] == "EVENT"
    [a] = result["assertions"]
    assert a["event_type_code"] == "COMPANY.CONTRACT.SIGNING"
    assert a["predicate_code"] == "SIGN"
    assert {x["role_code"] for x in a["arguments"]} == {"SUPPLIER", "CONTRACT_OBJECT"}
    # WHY: 엔티티 해소는 별도 소관 — 모델은 사내 식별자를 모른다. text 가 해소의 입력이고
    # entity_id 를 여기서 채우면 모델이 식별자를 지어낸 걸 통과시키는 셈이다.
    assert all(x["entity_id"] is None for x in a["arguments"])


def test_completeness_marks_unsatisfied_required_roles():
    # WHY: 필수역할이 빈 assertion 을 확정 사건과 구분 못 하면, 후속 조립이 반쪽 사건을
    # 완전한 것으로 취급한다. partial 표시가 그 구분의 유일한 근거다.
    ev = _event(arguments=[{"role_code": "SUPPLIER", "text": "에코프로비엠"}])
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "EVENT", "events": [ev]}))
    [a] = result["assertions"]
    assert a["completeness"] == "partial"
    assert a["missing_required_roles"] == ["CONTRACT_OBJECT"]


def test_non_event_doc_class_yields_no_assertions():
    # WHY: 논평·시황을 사건으로 태깅하면 있지도 않은 사건이 가격 설명의 원인 후보가 된다.
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "OPINION_OR_ANALYSIS", "events": []}))
    assert result["status"] == "ok"
    assert result["assertions"] == []


# ── 추출: 모델이 계약을 어겼을 때 ────────────────────────────

def test_invented_event_type_is_dropped_with_reason():
    # WHY: 온톨로지 밖 타입을 통과시키면 라벨 구속이 무의미해지고, 다운스트림이 모르는
    # event_type_code 를 받는다. 버리되 사유는 남겨야 태깅 품질이 보인다(Rule 12).
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "EVENT", "events": [_event(event_type_code="COMPANY.계약체결")]}))
    assert result["assertions"] == []
    assert "unknown_event_type" in result["reasons"]


def test_role_outside_type_is_dropped_but_assertion_survives():
    # WHY: 역할 하나 환각했다고 사건 전체를 버리면 멀쩡한 주장까지 잃는다. 반대로 조용히
    # 통과시키면 타입별 역할 계약이 깨진다 — 그 역할만 떨어뜨리고 사유를 남긴다.
    ev = _event(arguments=[{"role_code": "SUPPLIER", "text": "에코프로비엠"},
                           {"role_code": "CONTRACT_OBJECT", "text": "양극재"},
                           {"role_code": "REPORTING_PERIOD", "text": "2026년"}])
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "EVENT", "events": [ev]}))
    [a] = result["assertions"]
    assert {x["role_code"] for x in a["arguments"]} == {"SUPPLIER", "CONTRACT_OBJECT"}
    assert "role_not_allowed" in result["reasons"]


def test_predicate_outside_type_is_dropped_not_assertion():
    # WHY: 술어는 타입이 이미 정한 성격의 보조 정보라 사건 식별에 필수가 아니다 — 불량이면
    # 그것만 비우고 사건은 살린다.
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "EVENT", "events": [_event(predicate_code="RELEASE")]}))
    [a] = result["assertions"]
    assert a["predicate_code"] is None
    assert "predicate_not_allowed" in result["reasons"]


def test_events_on_non_event_doc_class_are_refused():
    # WHY: doc_class 가 EVENT 가 아닌데 사건을 낸 건 모델 자기모순이다. 둘 중 하나를 조용히
    # 택하면 그 모순이 안 보인다 — 사건은 안 쓰되 사유로 드러낸다.
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "NO_EVENT_MARKET_COMMENTARY", "events": [_event()]}))
    assert result["assertions"] == []
    assert "events_on_non_event_doc_class" in result["reasons"]


def test_bad_doc_class_is_surfaced():
    # WHY: 허용 어휘 밖 doc_class 를 EVENT 아님으로 뭉개면, 모델이 어휘를 못 지킨 사실이
    # 0건 결과로 위장된다.
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "뉴스", "events": []}))
    assert result["status"] == "bad_doc_class"


@pytest.mark.parametrize("payload", ["not json", "[]", '{"doc_class": "EVENT"'])
def test_unparseable_response_is_surfaced_not_silent(payload):
    # WHY: 파싱 실패를 '사건 0건'으로 처리하면 모델·프롬프트 회귀가 조용한 커버리지 저하로
    # 나타난다. status 로 구분돼야 집계에서 보인다(Rule 12).
    result = extract.extract_assertions(_article(), complete_fn=_fn(payload))
    assert result["status"] == "llm_unparseable"
    assert result["assertions"] == []


def test_code_fence_is_tolerated():
    # WHY: 코드펜스는 지시를 어겨도 흔한 형식 위반이라 여기서 막으면 정상 추출까지 버린다.
    # 관대함은 여기까지 — 그 너머(정규식으로 JSON 긁기)는 안 한다.
    body = json.dumps({"doc_class": "EVENT", "events": [_event()]}, ensure_ascii=False)
    result = extract.extract_assertions(_article(), complete_fn=_fn(f"```json\n{body}\n```"))
    assert result["status"] == "ok"
    assert len(result["assertions"]) == 1


def test_llm_failure_is_isolated_per_article():
    # WHY: 한 기사의 LLM 실패가 배치를 죽이면 나머지 기사가 통째로 유실된다. 격리하되
    # 성공으로 위장하지 않는다.
    def boom(system, user):
        raise RuntimeError("429 rate limit")

    result = extract.extract_assertions(_article(), complete_fn=boom)
    assert result["status"] == "llm_error"
    assert "429 rate limit" in result["reasons"][0]


def test_missing_title_skips_the_call_entirely():
    # WHY: 제목이 없으면 뽑을 근거가 없다 — 호출은 돈이고, 결측은 사유로 드러나야 한다.
    called = []
    extract.extract_assertions(_article(title=None),
                               complete_fn=lambda s, u: called.append(1) or "{}")
    assert called == []


def test_confidence_rejects_bool_and_out_of_range():
    # WHY: bool 은 int 의 하위형이라 isinstance(True, int) 가 True — 신뢰도 자리에 True 가
    # 오면 1.0 으로 강제돼 '최고 확신'으로 둔갑한다(coerce-to-passing).
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "EVENT", "events": [_event(confidence=True)]}))
    assert result["assertions"][0]["confidence"] is None

    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "EVENT", "events": [_event(confidence=1.5)]}))
    assert result["assertions"][0]["confidence"] is None
    assert "confidence_out_of_range" in result["reasons"]


@pytest.mark.parametrize("events", [None, "문자열", [None], [[]], [42]])
def test_malformed_events_do_not_crash(events):
    # WHY: 모델 응답은 신뢰경계 밖 입력이다. 비객체 원소가 .get() 에서 터지면 한 이상치가
    # 배치를 무너뜨린다(crash-before-gate).
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "EVENT", "events": events}))
    assert result["status"] == "ok"
    assert result["assertions"] == []


def test_argument_without_text_is_dropped():
    # WHY: 역할만 있고 값이 없으면 근거가 없다 — 빈 역할을 채운 걸로 세면 completeness 가
    # 거짓말을 한다.
    ev = _event(arguments=[{"role_code": "SUPPLIER", "text": "에코프로비엠"},
                           {"role_code": "CONTRACT_OBJECT", "text": "   "}])
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "EVENT", "events": [ev]}))
    [a] = result["assertions"]
    assert a["completeness"] == "partial"
    assert "argument_text_missing" in result["reasons"]


# ── LLM 어댑터 ────────────────────────────

def test_missing_key_fails_loud_not_silently_disabled():
    # WHY: 키가 없을 때 조용히 no-op 하면 태깅 0건이 '사건이 없었다'로 위장된다. 배선 실수는
    # 즉시 터져야 한다(Rule 12).
    from data_pipeline.tagging import llm

    with pytest.raises(RuntimeError, match="api_key"):
        llm.openai_compatible_complete_fn(api_key="")


def test_malformed_200_response_is_not_mistaken_for_empty(monkeypatch):
    # WHY: 200 인데 응답 형태가 규약 밖일 때 빈 문자열을 내면 extract 가 llm_unparseable 로
    # 잡아 '모델이 JSON 을 못 냈다'로 오독된다. 벤더 계약 위반과 모델 품질 문제는 다른 원인이라
    # 다른 사유로 드러나야 한다.
    import contextlib
    import io

    from data_pipeline.tagging import llm

    @contextlib.contextmanager
    def fake_urlopen(request, timeout=None):
        yield io.BytesIO(json.dumps({"error": "quota"}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    fn = llm.openai_compatible_complete_fn(api_key="k")
    with pytest.raises(RuntimeError, match="응답 형태 이상"):
        fn("s", "u")


def test_event_doc_class_without_events_is_flagged():
    # WHY: EVENT 라 해놓고 사건을 못 내는 것도 자기모순이다 — 반대 방향(비-EVENT 인데 사건
    # 냄)만 잡으면 비대칭이다. 골든 eval 에서 이 조합 2건이 둘 다 티처 기준 비-사건이었다:
    # 오탐 EVENT 의 신호라 집계에 남아야 한다. 단 라벨을 코드가 뒤집지는 않는다(Rule 5).
    result = extract.extract_assertions(_article(), complete_fn=_fn(
        {"doc_class": "EVENT", "events": []}))
    assert result["status"] == "ok"
    assert result["doc_class"] == "EVENT"
    assert "event_doc_class_without_events" in result["reasons"]
