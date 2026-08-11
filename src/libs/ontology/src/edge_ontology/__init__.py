"""이벤트 온톨로지 SSOT lib (ALPHA-539).

존재를 네 층으로 나눈다. 아래 층은 위 층 없이도 성립한다(선험적) — import 방향도 같다:

    4. 사건(Process)   시공간적 상호작용   ← 타입 스키마만. 인스턴스는 이 lib 밖
    3. 관계(Relation)  구조적 연결         ← 역할 어휘 + 종별 결속 + 해소 방식 + 논항 자리
    1. 실체(Entity)    독립적 존재         ← 종별 분류 + 닫힌집합 레지스트리
    2. 속성(Attribute) 실체·사건이 지닌 값 ← 공용 풀 + 값의 모형

이 lib 이 담는 것은 네 층 모두의 **선험적 어휘**다. 사건 타입도 존재의 층위이므로 여기
있다. 이 lib 밖(data-pipeline·analysis-engine)의 몫은 그 어휘로 실제 사건을 세우는 일 —
절차적 지식과 복잡계 전개다.

관계층은 한 역할에 대해 **서로 독립인 세 가지**를 말한다. 하나로 눌러 담으면 코드가 도로
쪼개는 하드코딩이 생긴다(그래서 CLOSED_SET_ROLES·MINTABLE_KINDS 가 있었다):
  - 종별(`role_kinds`)          그 자리에 오는 것이 **무엇인가**
  - 해소 방식(`identity`)        그것을 **무엇으로 키 삼는가**(명부 절·채번·미해소)
  - 논항 자리(`argument_slots`)  사건에서 **어떤 자리인가**(subject·object·qualifier)

어휘 정본은 이 패키지의 resources/<층>/ 다. 갱신 규약: 실험실(event-ontology repo)에서
확정한 리소스를 **통째 교체**하고(부분 발췌·현지 수정 금지 — 구 alphamale 스냅샷 정책
승계), 어휘가 바뀌었으면 constants.ONTOLOGY_VERSION 을 함께 올린다. 예외 둘은
edge 로컬 증보다 — resources/relation/(상류 백포트 대기 중)과 resources/labels/
(사건 유형 한국어 라벨, ALPHA-942 — 통째 교체 대상이 아니며, 교체로 새 타입이
오면 라벨 완전성 테스트가 깨져 추가를 강제한다).
"""
from .attribute import Attribute, load_common_attributes
from .constants import DEFAULT_VERSION, ONTOLOGY_VERSION
from .entity import (AuthorityRegistry, EntityKind, load_authority_registry,
                     load_entity_kinds, normalize_name)
from .labels import EventTypeLabel, event_type_label_ko, event_type_labels_ko
from .process import (ProcessRegistry, ProcessType, load_lifecycle_models,
                      load_process_registry, load_type_definitions)
from .relation import (Relation, RelationVocabulary, concept_key, load_relations,
                       resolve_authority, role_entity_kind)

__all__ = [
    "DEFAULT_VERSION",
    "ONTOLOGY_VERSION",
    "Attribute",
    "AuthorityRegistry",
    "EntityKind",
    "EventTypeLabel",
    "event_type_label_ko",
    "event_type_labels_ko",
    "ProcessRegistry",
    "ProcessType",
    "Relation",
    "RelationVocabulary",
    "concept_key",
    "load_authority_registry",
    "load_common_attributes",
    "load_entity_kinds",
    "load_lifecycle_models",
    "load_process_registry",
    "load_relations",
    "load_type_definitions",
    "normalize_name",
    "resolve_authority",
    "role_entity_kind",
]
