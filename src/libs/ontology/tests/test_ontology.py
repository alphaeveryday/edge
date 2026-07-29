"""edge_ontology 계약 테스트 — 리소스 통째 교체(승격)가 깨뜨리면 안 되는 것들.

존재 4층(실체·속성·관계·사건) 순서로 묶는다. 층 경계가 살아 있는지가 이 lib 의 형상이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import edge_ontology as O

FIXTURE = """types:
  TYPE.ALPHA:
    predicates: [DO, REDO]
    stage_sensitive: true
    note: "alpha note"
    roles:
      required: [ISSUER, TARGET]
      primary: [ISSUER]
  TYPE.GAMMA:
    predicates: [RUN]
    roles:
      required: [ISSUER]
      primary: [ISSUER]
"""


FIXTURE_SLOTS = """meta: {version: 0.0.0, pair_count: 3}
known_collisions: []
slots:
  TYPE.ALPHA: {ISSUER: subject, TARGET: object}
  TYPE.GAMMA: {ISSUER: subject}
"""


def _fixture_dir(tmp_path: Path, body: str = FIXTURE) -> Path:
    types_dir = tmp_path / "types"
    types_dir.mkdir()
    (types_dir / "fixture.yaml").write_text(body, encoding="utf-8")
    return types_dir


def _fixture_slots(tmp_path: Path, body: str = FIXTURE_SLOTS) -> Path:
    path = tmp_path / "slots.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ── 1. 실체(Entity) ────────────────────────────────────────────────────────

def test_every_entity_kind_declares_a_persistence_key():
    # WHY: persistence_key 가 그 실체를 무엇으로 적재할지 정한다 — ISSUER 는 ticker,
    #      기관은 정규화 문자열이다. 키 없는 종별이 들어오면 적재부가 경로를 못 고르고
    #      그 종별의 실체는 조용히 안 실린다.
    kinds = O.load_entity_kinds()
    assert len(kinds) == 8
    assert kinds["ISSUER"].persistence_key == "ticker"
    assert kinds["AUTHORITY_OR_RULE"].persistence_key == "normalized_authority_or_rule"
    assert all(k.persistence_key for k in kinds.values())


def test_authority_registry_aliases_are_unambiguous():
    # WHY: 별칭이 겹치거나 '당국'·'법원' 같은 모호어가 들어오면 엉뚱한 기관으로 해소되고,
    #      그 오해소가 thread_key 에 박혀 서로 다른 사건이 한 스레드로 뭉개진다. 로더가
    #      이미 막지만(중복·모호어 ValueError), 그 방어가 살아 있음을 고정한다.
    registry = O.load_authority_registry()
    assert len(registry.entries) >= 40
    assert O.resolve_authority("AUTHORITY", "공정위") == "actor_auth_kr_ftc"
    assert O.resolve_authority("AUTHORITY", " 금융 감독원 ") == "actor_auth_kr_fss"  # 정규화
    assert O.resolve_authority("CENTRAL_BANK", "Fed") == "actor_cb_us_fed"          # casefold
    assert O.resolve_authority("AUTHORITY", "당국") is None                          # 모호어
    assert O.resolve_authority("AUTHORITY", "공정거래위원회 산하 기관") is None        # contains 금지
    # 열린 집합 역할은 레지스트리로 해소하지 않는다 — 규칙·사안은 계속 새로 생긴다.
    assert O.resolve_authority("RULE", "공정위") is None
    assert O.resolve_authority("ISSUER", "공정위") is None


def test_authority_registry_matches_seed_migration():
    # WHY: entity_id 는 FK 대상이다. 레지스트리에만 있고 시드에 없는 기관을 해소하면
    #      적재가 FK 위반으로 터진다. 반대로 시드에만 있으면 죽은 행이다. 한쪽만 고치는
    #      실수를 여기서 잡는다 — 마이그레이션은 레지스트리의 투영이다.
    import re

    seed = Path(__file__).resolve().parents[3] / (
        "libs/schema/migrations-cloud/V202607291730__seed_authority_actors.sql")
    sql = seed.read_text(encoding="utf-8")
    registry = O.load_authority_registry()
    for entity_id, entry in registry.entries.items():
        assert f"'{entity_id}'" in sql, entity_id
        assert f"'{entry.actor_type}'" in sql, entry.actor_type
    seeded = set(re.findall(r"'(actor_(?:auth|court|cb|inst)_[a-z0-9_]+)'", sql))
    assert seeded == set(registry.entries)


# ── 2. 속성(Attribute) ─────────────────────────────────────────────────────

def test_common_attribute_pool_carries_units_not_just_names():
    # WHY: 파생 속성(derived)의 formula 가 이 풀을 분모로 쓴다. 단위 계열이 없으면
    #      CURRENCY/PERCENT 를 섞은 식이 조용히 통과해 무의미한 수가 나온다.
    pool = O.load_common_attributes()
    assert pool["market_cap"].unit_family == "CURRENCY"
    assert pool["op_margin_ttm"].unit_family == "PERCENT"
    assert pool["is_listed"].dtype == "bool"
    assert all(a.desc for a in pool.values())


def test_type_attributes_are_typed_by_section():
    # WHY: 속성의 갈래가 값의 출처를 정한다 — QUANTITY 는 원문에서 파싱하고, STATE 는
    #      외부 재무자산에서 join 하고, DERIVED 는 계산한다. 갈래가 뭉개지면 조립부가
    #      원문에 없는 값을 원문에서 찾으려 든다.
    signing = O.load_process_registry()["COMPANY.CONTRACT.SIGNING"]
    assert signing.quantities["CONTRACT_VALUE"].kind == "QUANTITY"
    assert signing.quantities["CONTRACT_VALUE"].required is True
    assert signing.derived["revenue_share"].kind == "DERIVED"
    assert signing.derived["revenue_share"].inputs == ("annualized_value", "revenue_ttm")
    stake = O.load_process_registry()["COMPANY.INVESTMENT.STAKE_ACQUISITION"]
    assert stake.entity_state["controlling_stake_target"].scope == "TARGET_COMPANY"


# ── 3. 관계(Relation) ──────────────────────────────────────────────────────

def test_relation_vocabulary_binds_roles_to_entity_kinds():
    # WHY: 적재부가 종별별로 다른 키를 써야 한다 — ISSUER 는 ticker 로 해소되지만
    #      AUTHORITY 는 정규화 문자열이 키다. 종별이 뒤섞이면 규제기관이 종목으로
    #      해소되는 사고가 난다.
    vocabulary = O.load_relations()
    assert vocabulary.kind_of("ISSUER") == "ISSUER"
    assert vocabulary.kind_of("CUSTOMER") == "COMPANY_ENTITY"
    assert vocabulary.kind_of("AUTHORITY") == "AUTHORITY_OR_RULE"
    assert vocabulary.kind_of("CONTRACT_OBJECT") == "PRODUCT_OR_CONCEPT"
    # 시간·수치 역할은 실체가 아니다 — entity_id 자리에 넣으면 FK 가 터진다.
    assert vocabulary.kind_of("REPORTING_PERIOD") is None
    assert vocabulary.non_entity_roles["REPORTING_PERIOD"] == "TIME"
    assert vocabulary.non_entity_roles["CONTRACT_VALUE"] == "VALUE"
    assert not vocabulary.entity_roles & set(vocabulary.non_entity_roles)


def test_relation_vocabulary_is_not_derived_from_event_types():
    # WHY: 관계는 사건보다 아래 층이다. 어휘를 타입에서 역파생하면 "타입이 쓰는 역할이
    #      어휘 안인가"라는 검사가 항진명제가 되어 아무것도 못 잡는다. 어휘가 타입 사용분의
    #      진상위집합이어야 그 검사가 힘을 갖는다.
    vocabulary = set(O.load_relations().relations)
    used: set[str] = set()
    for process_type in O.load_process_registry().types.values():
        used |= set(process_type.required_roles) | set(process_type.optional_roles)
    assert used < vocabulary


def test_registry_lookup_is_narrowed_by_role_section():
    # WHY: 명부 조회가 절을 안 좁히면 별칭 평면 하나를 공유해 **법원 자리에 규제기관이
    #      해소된다**(COURT+"공정거래위원회" → actor_auth_kr_ftc). 그 오해소가 thread_key 에
    #      박히면 서로 다른 사건이 한 스레드로 뭉개진다. 실제로 그렇게 동작하던 버그다.
    assert O.resolve_authority("COURT", "공정거래위원회") is None
    assert O.resolve_authority("CENTRAL_BANK", "공정위") is None
    assert O.resolve_authority("AUTHORITY", "대법원") is None
    # 제 절에서는 정상 해소된다 — 좁히기가 기능을 죽이지 않았음을 함께 고정한다.
    assert O.resolve_authority("COURT", "대법원") == "actor_court_kr_supreme"
    assert O.resolve_authority("AUTHORITY", "공정위") == "actor_auth_kr_ftc"
    assert O.resolve_authority("CENTRAL_BANK", "Fed") == "actor_cb_us_fed"


def test_exchange_resolves_to_one_identity_not_two():
    # WHY: 거래소는 명부에 있는데(actor_inst_kr_krx) EXCHANGE 역할이 채번만 하면 같은 기관이
    #      AUTHORITY 자리에선 명부 id, EXCHANGE 자리에선 '한국거래소' 개념으로 갈린다.
    #      한 실체가 역할에 따라 둘이 되면 스레드가 쪼개진다(불변식 G1).
    assert O.resolve_authority("EXCHANGE", "한국거래소") == "actor_inst_kr_krx"
    assert O.resolve_authority("MARKET", "한국거래소") == "actor_inst_kr_krx"
    # 미등재 해외 거래소는 채번 폴백으로 남는다 — 명부에 없다고 버리지 않는다.
    assert O.resolve_authority("EXCHANGE", "나스닥") is None
    assert O.concept_key("EXCHANGE", "나스닥") == "나스닥"


def test_identity_scheme_is_declared_not_hardcoded():
    # WHY: 예전엔 CLOSED_SET_ROLES·MINTABLE_KINDS 가 파이썬 frozenset 이었고, 그 존재
    #      이유가 "종별이 잘못 묶여서"였다. 해소 방식은 종과 다른 축이므로 리소스가 갖는다.
    #      하드코딩으로 되돌아가면 이 검사가 깨진다.
    vocabulary = O.load_relations()
    assert vocabulary.sections_for("COURT") == ("courts",)
    assert vocabulary.sections_for("ISSUER") == ()          # 티커로 온다
    assert not vocabulary.can_mint("AUTHORITY")             # 명부가 정답을 갖는다
    assert not vocabulary.can_mint("ISSUER")
    assert vocabulary.can_mint("RULE")                      # 열린 집합
    assert vocabulary.can_mint("EXCHANGE")                  # 명부 우선 + 채번 폴백


def test_argument_slot_is_declared_per_type_not_per_role():
    # WHY: slot 은 (타입, 역할)로 결정된다 — 같은 역할이 타입에 따라 자리를 바꾼다. 역할
    #      전역으로 두면 인증(기관이 주역)에서 발행사가 subject 로 잘못 실린다. 이 축이
    #      없으면 같은 종 참여자 둘을 소비자가 구분할 수 없다.
    registry = O.load_process_registry()
    assert registry["COMPANY.CAPITAL.DIVIDEND_DECISION"].slot_of("ISSUER") == "subject"
    assert registry["COMPANY.PRODUCT.CERTIFICATION"].slot_of("ISSUER") == "object"
    assert registry["COMPANY.OWNERSHIP.INSIDER_TRANSACTION"].slot_of("ISSUER") == "qualifier"
    assert registry["MARKET_STRUCTURE.EXCHANGE_OUTAGE"].slot_of("EXCHANGE") == "subject"
    assert registry["COMPANY.CAPITAL.IPO"].slot_of("EXCHANGE") == "qualifier"
    # 비실체 역할은 event_argument 에 실리지 않으므로 자리가 없다.
    assert registry["COMPANY.CONTRACT.SIGNING"].slot_of("CONTRACT_VALUE") is None


def test_slot_collisions_must_be_declared_with_a_reason():
    # WHY: (종, slot) 이 같은 역할 쌍은 소비자가 구분할 수 없다. 그걸 조용히 통과시키면
    #      어휘 결함(PARTNER_2 같은 arity hack)이 영구 거주한다. 사유 없는 면제를 막아야
    #      게이트가 게임 불가다 — 면제 목록 자체가 정리 대상 원장이 된다.
    from edge_ontology.relation.slots import COLLISION_REASONS, load_known_collisions

    collisions = load_known_collisions()
    assert collisions, "면제가 하나도 없으면 게이트가 놀고 있는 것 아닌지 확인할 것"
    for collision in collisions:
        assert collision.reason in COLLISION_REASONS
        assert collision.why.strip()
        assert len(collision.roles) >= 2
    # 두 부류가 섞여 있어야 한다 — 해소 경로가 다르다(어휘 정리 vs slot 어휘 확장).
    assert {c.reason for c in collisions} == set(COLLISION_REASONS)




def test_concept_key_only_for_concept_roles_and_rejects_noise():
    # WHY: 개념은 열린 집합이라 정규화 텍스트가 곧 정체성이다. 한 글자·숫자 같은 잡음을
    #      개념으로 세우면 서로 무관한 사건이 한 스레드로 뭉치고, 반대로 표기 흔들림을
    #      흡수 못 하면 같은 제품이 스레드마다 갈린다. 두 실패를 여기서 고정한다.
    assert O.concept_key("PRODUCT", "갤럭시 S25") == O.concept_key("PRODUCT", "갤럭시S25")
    assert O.concept_key("CONTRACT_OBJECT", "HBM") == "hbm"          # casefold
    assert O.concept_key("METRIC", "영업이익") == "영업이익"
    # 개념 역할이 아닌 것에는 채번하지 않는다 — 회사는 티커로, 기관은 레지스트리로 간다.
    assert O.concept_key("ISSUER", "삼성전자") is None
    assert O.concept_key("AUTHORITY", "공정위") is None
    assert O.concept_key("REPORTING_PERIOD", "2026Q2") is None       # 비실체(TIME)
    # 잡음 차단
    assert O.concept_key("PRODUCT", "A") is None
    assert O.concept_key("PRODUCT", "  ") is None
    assert O.concept_key("PRODUCT", "2026") is None


# ── 4. 사건(Process) ───────────────────────────────────────────────────────

def test_real_resources_are_the_alphamale_lineage():
    # WHY: 이 lib 은 alphamale 0.1.0 어휘의 승계다(ALPHA-539 프로그램 대조로 동일 확인).
    #      타입 수·버전이 조용히 변하면 태깅 재실행 비용(기사당 LLM 1콜)과 계보 단절이
    #      따라오므로, 통째 교체는 이 테스트를 의도적으로 갱신해야만 통과한다.
    registry = O.load_process_registry()
    assert len(registry.types) == 53
    assert O.ONTOLOGY_VERSION == "0.1.0"
    guidance = registry["COMPANY.EARNINGS.GUIDANCE_CHANGE"]
    assert guidance.predicates[:2] == ("ISSUE", "REVISE")  # default_predicate=첫 원소 계약


def test_process_type_folds_yaml_into_one_view(tmp_path):
    # WHY: types_dir 오버라이드는 실험실 리소스를 edge 로 승격하기 **전에** 같은 검사로
    #      굴리는 통로다 — roles/predicates/stage_sensitive 가 정확히 접혀야 assemble
    #      게이트·프롬프트 카탈로그가 같은 어휘를 본다.
    registry = O.load_process_registry(_fixture_dir(tmp_path),
                                       slots_path=_fixture_slots(tmp_path))
    alpha = registry["TYPE.ALPHA"]
    assert alpha.predicates == ("DO", "REDO")
    assert alpha.required_roles == ("ISSUER", "TARGET")
    assert alpha.stage_sensitive is True and alpha.note == "alpha note"
    assert registry["TYPE.GAMMA"].stage_sensitive is False


def test_role_outside_the_relation_vocabulary_kills_the_load(tmp_path):
    # WHY: 어휘 밖 역할은 entity_kind=NULL 로 적재되고, 종별을 모르면 적재 경로
    #      (persistence_key)를 못 고른다 — 그 역할은 조용히 영영 안 실린다. 온톨로지에
    #      역할이 늘면 관계 어휘를 같이 고치도록 반입 시점에 강제한다(Rule 12).
    bogus = FIXTURE.replace("required: [ISSUER, TARGET]", "required: [ISSUER, NOT_A_ROLE]")
    with pytest.raises(ValueError, match="관계 어휘 밖 역할"):
        O.load_process_registry(_fixture_dir(tmp_path, bogus),
                                slots_path=_fixture_slots(tmp_path))


def test_derived_attribute_referencing_nothing_kills_the_load(tmp_path):
    # WHY: derived 의 formula 는 다른 속성 id 를 참조한다. 참조가 깨진 채 통과하면 특징
    #      계산이 런타임에 KeyError 로 죽거나 조용히 NaN 을 낸다 — 반입 시점에 잡는다.
    bogus = FIXTURE + """    derived:
      broken: {formula: 'X / Y', inputs: [no_such_attribute], dtype: float, desc: 깨진 참조}
"""
    with pytest.raises(ValueError, match="미선언 input"):
        O.load_process_registry(_fixture_dir(tmp_path, bogus),
                                slots_path=_fixture_slots(tmp_path))


def test_quantity_and_identity_axes_survive_the_fold():
    # WHY: 정규화(수량 파서)와 스레딩(identity 키)이 이 축을 소비한다 — 스냅샷 JSON 엔
    #      없던 정보라, 뷰가 잃으면 v4 정규화 통합이 설 자리가 없다.
    registry = O.load_process_registry()
    signing = registry["COMPANY.CONTRACT.SIGNING"]
    assert signing.quantity_roles == {"CONTRACT_VALUE", "CONTRACT_DURATION"}
    assert signing.required_quantity_roles == {"CONTRACT_VALUE"}  # completeness 판정 축(#255)
    assert signing.currency_roles == {"CONTRACT_VALUE"}
    assert signing.quantity_unit_families == {
        "CONTRACT_VALUE": "CURRENCY", "CONTRACT_DURATION": "DURATION_DAYS"}  # 단위 정합 축(#255)
    assert signing.identity_required == ("SUPPLIER", "CUSTOMER", "CONTRACT_OBJECT")
    # anchor 폴백 판정 축(#255): primary 가 둘이면 게이트 티커의 역할을 코드가 모른다.
    assert signing.primary_roles == ("SUPPLIER", "CUSTOMER")
    assert registry["COMPANY.EARNINGS.RESULT_RELEASE"].primary_roles == ("ISSUER",)
    # required[0] 와 primary 가 갈리는 타입 — 기업 티커를 AUTHORITY 로 실으면 조작이다.
    regact = registry["COMPANY.LEGAL.REGULATORY_ACTION"]
    assert regact.required_roles[0] == "AUTHORITY" and regact.primary_roles == ("TARGET_COMPANY",)


def test_stage_sequence_is_ordered_not_a_set():
    # WHY: novelty 가 stage 의 **전진**을 본다(seq.index 비교). 순서를 잃으면 후퇴한
    #      단계가 FOLLOW_UP 으로 승격되어 계보가 거꾸로 선다.
    signing = O.load_process_registry()["COMPANY.CONTRACT.SIGNING"]
    assert signing.stages[:3] == ("RUMORED", "PROPOSED", "PREFERRED_BIDDER")
    assert signing.stages[-1] == "CANCELLED"  # terminal 이 순서축 끝에 붙는다


def test_ref_txt_drift_five_stage_types_stay_sensitive():
    # WHY: 은퇴한 ontology_ref.txt 미러는 이 5개 타입의 STAGE 마커를 잃어버려 조립
    #      프롬프트가 stage 를 묻지 않았다(ALPHA-539 에서 발견한 기저 드리프트).
    #      정본 승계가 그 드리프트를 고쳤음을 고정한다 — 재발하면 여기서 깨진다.
    registry = O.load_process_registry()
    for type_id in (
        "EXOGENOUS.ACCIDENT.OPERATIONAL_DISRUPTION",
        "EXOGENOUS.CYBER.SERVICE_DISRUPTION",
        "MARKET_STRUCTURE.INDEX.INCLUSION",
        "MARKET_STRUCTURE.TRADING_HALT",
        "POLICY.COURT.RULING",
    ):
        assert registry[type_id].stage_sensitive is True, type_id


def test_thread_contract_novelty_vocab_matches_db_check():
    # WHY: event_thread_link.novelty_status 의 DB CHECK(V202607150001)와 이 어휘가
    #      동형이어야 threading 이 무엇을 내든 제약 위반이 없다. 실험실이 어휘를
    #      넓히면(8종안) 여기가 먼저 깨져 스키마 확장이 선행됨을 강제한다.
    assert O.load_process_registry().novelty_statuses == {
        "FIRST_IN_THREAD", "FOLLOW_UP_STAGE", "CORRECTION", "DUPLICATE_REBROADCAST", "UNKNOWN",
    }


def test_unfillable_identity_types_are_declared():
    # WHY: identity 가 비실체 역할을 요구하는 타입은 추출을 아무리 고쳐도 영구 UNKNOWN
    #      이다. 조용한 0% 를 계약에 적어 드러낸다(Rule 12). 특히 off_menu 셋은
    #      required∪optional 에도 없는 역할을 identity 로 요구하는 온톨로지 결함이다.
    from edge_ontology._resource import load_yaml_resource
    from edge_ontology.constants import RELATION_DIR
    from edge_ontology.relation.vocabulary import ROLE_BINDINGS_RESOURCE

    declared = load_yaml_resource(RELATION_DIR, ROLE_BINDINGS_RESOURCE)["unfillable_identity"]
    non_entity = O.load_relations().non_entity_roles
    registry = O.load_process_registry()
    observed = {
        type_id for type_id, pt in registry.types.items()
        if any(role in non_entity for role in pt.identity_required)
    }
    listed = {t for group in declared.values() if isinstance(group.get("types"), dict)
              for t in group["types"]}
    assert observed == listed, f"계약 선언과 실제가 갈렸다: {observed ^ listed}"
    for type_id in declared["off_menu"]["types"]:
        pt = registry[type_id]
        assert set(pt.identity_required) - (set(pt.required_roles) | set(pt.optional_roles))
