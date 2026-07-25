"""edge_ontology 계약 테스트 — 리소스 통째 교체(승격)가 깨뜨리면 안 되는 것들."""

from __future__ import annotations

from pathlib import Path

import edge_ontology as O

FIXTURE = """types:
  TYPE.ALPHA:
    predicates: [DO, REDO]
    stage_sensitive: true
    note: "alpha note"
    roles:
      required: [ISSUER, TARGET]
  TYPE.GAMMA:
    predicates: [RUN]
    roles:
      required: [ISSUER]
"""


def _fixture_dir(tmp_path: Path) -> Path:
    types_dir = tmp_path / "types"
    types_dir.mkdir()
    (types_dir / "fixture.yaml").write_text(FIXTURE, encoding="utf-8")
    return types_dir


def test_load_registry_folds_yaml_into_typespec(tmp_path):
    # WHY: path 오버라이드는 실험실 리소스를 edge 로 승격하기 **전에** 검증하는 통로다 —
    #      roles.required/predicates/stage_sensitive 가 TypeSpec 으로 정확히 접혀야
    #      assemble 게이트·프롬프트 카탈로그가 같은 어휘를 본다.
    registry = O.load_registry(_fixture_dir(tmp_path))
    alpha = registry.types["TYPE.ALPHA"]
    assert alpha.predicates == ["DO", "REDO"]
    assert alpha.required_roles == ["ISSUER", "TARGET"]
    assert alpha.stage is True and alpha.note == "alpha note"
    assert registry.types["TYPE.GAMMA"].stage is False


def test_validate_names_the_violation(tmp_path):
    # WHY: assemble-events 가 이 메시지로 모델 출력을 거른다 — 위반이 익명이면
    #      리뷰큐에서 무엇을 고칠지 알 수 없다.
    registry = O.load_registry(_fixture_dir(tmp_path))
    violations = registry.validate("TYPE.ALPHA", predicate="FLY", roles=["ISSUER"])
    assert violations == [
        "Disallowed predicate 'FLY' for TYPE.ALPHA",
        "Missing required role TARGET for TYPE.ALPHA",
    ]
    assert registry.validate("TYPE.NOPE") == ["Unknown type_id: TYPE.NOPE"]


def test_real_resources_are_the_alphamale_lineage():
    # WHY: 이 lib 은 alphamale 0.1.0 어휘의 승계다(ALPHA-539 프로그램 대조로 동일 확인).
    #      타입 수·버전이 조용히 변하면 태깅 재실행 비용(기사당 LLM 1콜)과 계보 단절이
    #      따라오므로, 통째 교체는 이 테스트를 의도적으로 갱신해야만 통과한다.
    registry = O.load_registry()
    assert len(registry.types) == 53
    assert O.ONTOLOGY_VERSION == "0.1.0"
    guidance = registry.types["COMPANY.EARNINGS.GUIDANCE_CHANGE"]
    assert guidance.predicates[:2] == ["ISSUE", "REVISE"]  # default_predicate=첫 원소 계약


def test_bundle_cross_validates_views():
    # WHY: 리소스 통째 교체 실수(프로파일-레지스트리 불일치·feature 참조 깨짐)를
    #      반입 시점에 시끄럽게 잡는 게 bundle 로더의 존재 이유다(Rule 12).
    bundle = O.load_ontology_bundle()
    assert set(bundle["registry"].types) == set(bundle["profiles"])
    assert bundle["feature_registry"]["meta"]["type_count"] == 53


def test_view_exposes_quantity_and_identity_axes():
    # WHY: 정규화(수량 파서)와 스레딩(identity 키)이 이 축을 소비한다 — 스냅샷 JSON 엔
    #      없던 정보라, 뷰가 잃으면 v4 정규화 통합이 설 자리가 없다.
    view = O.load_ontology_view()
    signing = view.types["COMPANY.CONTRACT.SIGNING"]
    assert signing.quantity_roles == {"CONTRACT_VALUE", "CONTRACT_DURATION"}
    assert signing.required_quantity_roles == {"CONTRACT_VALUE"}  # completeness 판정 축(#255)
    assert signing.currency_roles == {"CONTRACT_VALUE"}
    assert signing.quantity_unit_families == {
        "CONTRACT_VALUE": "CURRENCY", "CONTRACT_DURATION": "DURATION_DAYS"}  # 단위 정합 축(#255)
    assert signing.identity_required == ("SUPPLIER", "CUSTOMER", "CONTRACT_OBJECT")


def test_thread_contract_novelty_vocab_matches_db_check():
    # WHY: event_thread_link.novelty_status 의 DB CHECK(V202607150001)와 이 어휘가
    #      동형이어야 threading 이 무엇을 내든 제약 위반이 없다. 실험실이 어휘를
    #      넓히면(8종안) 여기가 먼저 깨져 스키마 확장이 선행됨을 강제한다.
    view = O.load_ontology_view()
    assert view.novelty_statuses == {
        "FIRST_IN_THREAD", "FOLLOW_UP_STAGE", "CORRECTION", "DUPLICATE_REBROADCAST", "UNKNOWN",
    }


def test_ref_txt_drift_five_stage_types_stay_sensitive():
    # WHY: 은퇴한 ontology_ref.txt 미러는 이 5개 타입의 STAGE 마커를 잃어버려 조립
    #      프롬프트가 stage 를 묻지 않았다(ALPHA-539 에서 발견한 기저 드리프트).
    #      정본 승계가 그 드리프트를 고쳤음을 고정한다 — 재발하면 여기서 깨진다.
    registry = O.load_registry()
    for tid in (
        "EXOGENOUS.ACCIDENT.OPERATIONAL_DISRUPTION",
        "EXOGENOUS.CYBER.SERVICE_DISRUPTION",
        "MARKET_STRUCTURE.INDEX.INCLUSION",
        "MARKET_STRUCTURE.TRADING_HALT",
        "POLICY.COURT.RULING",
    ):
        assert registry.types[tid].stage is True, tid
