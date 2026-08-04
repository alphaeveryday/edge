"""단건 event 조립 어댑터 테스트 (ALPHA-727).

의도: 추출 결과(표면형뿐, entity_id 없음)를 assemble 의 분류(cls) 형상으로 되접는
변환이 이 모듈의 전부다 — 여기가 틀리면 배치와 단건 두 writer 가 같은 기사에 다른
계보를 세운다. 고정하는 것: ①참여자 해소·primary 선정 ②수량 역할의 measures 분리
(태깅은 arguments 에 섞어 낸다) ③primary 미해소 assertion 은 조립하지 않는다(지어내지
않는다) ④검증·적재 함수는 assemble 의 것을 재사용한다(형상만 이 모듈 소관).
"""
from __future__ import annotations

from data_pipeline.minute import event_assembly
from data_pipeline.minute.event_assembly import NewsEventAssembler

_CONTRACT = "COMPANY.CONTRACT.SIGNING"

# 해소 인덱스 대역 — resolve(index, text) 를 monkeypatch 로 갈아끼운다.
_RESOLUTION = {"삼성전자": "inst_SAMSUNG", "한화솔루션": "inst_HANWHA"}
_ENTITY_INDEX = {"005930": "inst_SAMSUNG", "009830": "inst_HANWHA"}


def _fake_resolve(index, text):
    entity = _RESOLUTION.get(str(text).strip())
    return (entity, "matched" if entity else "unmatched")


def _assembler() -> NewsEventAssembler:
    from data_pipeline.config import DbConfig
    return NewsEventAssembler(db=DbConfig(host="x", port=5432, name="x", user="x",
                                          password="x", sslmode="disable"))


def _cls(monkeypatch, assertion: dict) -> dict | None:
    monkeypatch.setattr(event_assembly, "resolve", _fake_resolve)
    view = event_assembly._process_registry()
    return _assembler()._to_classification(
        assertion, article_id="a1", view=view, entity_index=_ENTITY_INDEX,
        ticker_by_entity={v: k for k, v in _ENTITY_INDEX.items()},
        res_index=object(),
    )


def test_participants_resolve_and_measures_split(monkeypatch):
    """태깅은 수량 역할(CONTRACT_VALUE)을 arguments 에 섞어 낸다 — 분리 없이 넘기면
    _validate_extraction 의 참여자 메뉴가 그 역할을 버려 측정값이 조용히 사라진다."""
    cls = _cls(monkeypatch, {
        "event_type_code": _CONTRACT,
        "predicate_code": "SIGN",
        "confidence": 0.9,
        "arguments": [
            {"role_code": "SUPPLIER", "text": "삼성전자", "entity_id": None},
            {"role_code": "CUSTOMER", "text": "한화솔루션", "entity_id": None},
            {"role_code": "CONTRACT_OBJECT", "text": "배터리 셀", "entity_id": None},
            {"role_code": "CONTRACT_VALUE", "text": "2,734억원", "entity_id": None},
        ],
    })
    assert cls is not None
    assert cls["primary_ticker"] == "005930"          # 첫 해소 instrument
    assert cls["entity_id"] == "inst_SAMSUNG"
    by_role = {a["role_code"]: a for a in cls["arguments"]}
    assert by_role["SUPPLIER"]["entity_id"] == "inst_SAMSUNG"
    assert by_role["CUSTOMER"]["entity_id"] == "inst_HANWHA"
    # 개념 역할은 assemble 의 concept 채번을 그대로 탄다 — 미해소로 지어내지 않는다.
    assert by_role["CONTRACT_OBJECT"]["entity_id"] is not None
    assert "CONTRACT_VALUE" not in by_role            # 참여자가 아니라 측정값
    [measure] = cls["measures"]
    assert measure["role_code"] == "CONTRACT_VALUE"
    assert measure["value"] is not None               # 금액 파싱까지 assemble 재사용
    assert cls["lifecycle_stage"] is None             # 태깅은 stage 를 묻지 않는다


def test_primary_follows_role_priority_not_argument_order(monkeypatch):
    """LLM 인자 순서는 보장이 없다 — '첫 해소 instrument' 규칙이면 CUSTOMER 가
    SUPPLIER(primary 역할 1순위)를 제치고 사건 주체로 영구 고정된다(멱등 게이트가
    재조립을 막는다). primary_roles → required_roles 순의 결정적 선정을 고정한다."""
    cls = _cls(monkeypatch, {
        "event_type_code": _CONTRACT,
        "predicate_code": "SIGN",
        "confidence": 0.9,
        "arguments": [
            {"role_code": "CUSTOMER", "text": "한화솔루션", "entity_id": None},
            {"role_code": "SUPPLIER", "text": "삼성전자", "entity_id": None},
        ],
    })
    assert cls is not None
    assert cls["primary_ticker"] == "005930"  # SUPPLIER — 인자 순서(한화 먼저)와 무관


def test_unresolved_primary_drops_the_assertion(monkeypatch):
    """참여자 전원이 마스터 밖(유니버스 무관 기사)이면 조립하지 않는다 —
    document_entity.entity_id 가 NOT NULL 이라 지어낸 primary 는 계보 오염이다."""
    cls = _cls(monkeypatch, {
        "event_type_code": _CONTRACT,
        "predicate_code": "SIGN",
        "confidence": 0.9,
        "arguments": [
            {"role_code": "SUPPLIER", "text": "듣도보도못한상사", "entity_id": None},
            {"role_code": "CONTRACT_OBJECT", "text": "배터리 셀", "entity_id": None},
        ],
    })
    assert cls is None


def test_unknown_event_type_is_dropped(monkeypatch):
    """온톨로지 밖 타입은 조립 불가 — 메뉴 밖 라벨로 계보를 세우지 않는다."""
    assert _cls(monkeypatch, {
        "event_type_code": "COMPANY.MADE.UP",
        "predicate_code": "SIGN",
        "arguments": [{"role_code": "SUPPLIER", "text": "삼성전자", "entity_id": None}],
    }) is None
