"""이벤트 타입 온톨로지 — 허용 라벨의 SSOT 접근자 (ALPHA-138, lib 이관 ALPHA-539).

어휘 정본은 `edge_ontology` lib(`src/libs/ontology`)의 resources/ 다 — 구 alphamale JSON
스냅샷(`event_type_profiles_v0_1.json`)은 ALPHA-539 로 은퇴했다. 갱신 규약은 lib 소관:
실험실(event-ontology repo)에서 확정한 리소스를 통째 교체하고(부분 발췌·현지 수정 금지),
어휘가 바뀌었으면 `ONTOLOGY_VERSION` 을 함께 올린다. 이 모듈은 태깅이 쓰는 파생 뷰
(허용 집합·프롬프트 카탈로그)만 남긴다.

태깅이 이 온톨로지를 쓰는 이유는 **모델이 라벨을 발명하지 못하게** 하기 위해서다. 추출은
모델의 일(분류·추출)이지만, 나온 라벨이 허용 집합에 드는지 판정하는 건 코드의 일이다
(AGENTS Rule 5 — code answers). 그래서 여기서 파생하는 허용 집합이 프롬프트 구속과 사후
검증 **양쪽의 같은 출처**가 된다 — 프롬프트와 검증이 서로 다른 목록을 보면 모델이 프롬프트를
지켜도 검증에서 떨어지는 모순이 생긴다.

프로필의 나머지 축(`lifecycle_model`·quantities 등)은 태깅 범위 밖이다 — lib 의 사건층 뷰
(`ProcessType`) 소관이고 여기선 안 읽는다. `identity_roles` 는 thread 스텝
(assemble_events)이 `identity_roles()` 로 읽는다(ALPHA-457) — 태깅 자체는 여전히 안 쓴다.
"""

from __future__ import annotations

import edge_ontology

# 문서 성격 라벨 — alphamale 골든 데이터(ko_gold_title.jsonl)의 doc_class 어휘와 같다.
# EVENT 만 assertion 을 낸다(나머지는 사건이 아니라 논평·홍보라 주장 추출 대상이 아니다).
DOC_CLASSES = (
    "EVENT",
    "OPINION_OR_ANALYSIS",
    "NO_EVENT_MARKET_COMMENTARY",
    "PROMOTIONAL_OR_SOLICITATION",
)


def process_types() -> edge_ontology.ProcessRegistry:
    """사건 타입 레지스트리 — lib 의 4. 사건층 뷰를 그대로 쓴다(lib 이 캐시한다).

    타입당 술어·역할·속성·라이프사이클이 한 객체(`ProcessType`)에 접혀 있다. 태깅은 그중
    허용 집합(술어·역할)만 본다.
    """
    return edge_ontology.load_process_registry()


def ontology_version() -> str:
    """어휘 판번 — 산출물 provenance 에 박아 어느 온톨로지로 태깅했는지 남긴다.

    lib 상수(ONTOLOGY_VERSION)가 SSOT 다. 값이 바뀌면 tag_news._is_current 가 전 기사
    재태깅(기사당 LLM 1콜)을 트리거하므로, 어휘가 실제로 바뀔 때만 올린다 — 0.1.0 어휘
    동일성은 ALPHA-539 에서 프로그램 대조로 확인했다.
    """
    return edge_ontology.ONTOLOGY_VERSION


def event_type_codes() -> tuple[str, ...]:
    """허용 event_type_code 전량(정렬). 프롬프트 구속과 검증이 같이 쓴다."""
    return tuple(sorted(process_types().types))


def allowed_roles(event_type_code: str) -> frozenset[str]:
    """그 타입이 가질 수 있는 role_code 집합(required ∪ optional).

    타입마다 역할이 다르다 — CONTRACT.SIGNING 의 SUPPLIER 를 EARNINGS.RESULT_RELEASE 에
    붙이면 안 된다. 그래서 검증은 '86개 전역 역할'이 아니라 **타입별** 집합으로 한다.
    """
    pt = process_types()[event_type_code]
    return frozenset(pt.required_roles) | frozenset(pt.optional_roles)


def required_roles(event_type_code: str) -> frozenset[str]:
    """그 타입이 요구하는 role_code — 다 채워지면 completeness=complete."""
    return frozenset(process_types()[event_type_code].required_roles)


def identity_roles(event_type_code: str) -> tuple[str, ...]:
    """그 타입의 thread 정체성 역할 — thread_key 구성 재료(ALPHA-457, 순서 보존).

    `required_roles`(event completeness 판정)와 **다른 축**이다: 계약 불변식 3이 둘의 분리를
    강제한다 — 다른 계약이 같은 스레드로 뭉개지지 않게. 예: CONTRACT.SIGNING 은 required 가
    SUPPLIER·CONTRACT_OBJECT 지만 identity 는 SUPPLIER·CUSTOMER·CONTRACT_OBJECT 라, CUSTOMER
    가 다르면 다른 thread 다. `frozenset`(required)과 달리 **tuple 로 순서를 보존**한다 —
    thread_key 가 역할 순서에 의존하는 결정적 문자열이라 집합이면 안 된다.
    """
    return process_types()[event_type_code].identity_roles


def allowed_predicates(event_type_code: str) -> frozenset[str]:
    """그 타입이 허용하는 predicate_code."""
    return frozenset(process_types()[event_type_code].predicates)


def default_predicate(event_type_code: str) -> str | None:
    """그 타입의 기본 술어 — `allowed_predicates` 의 첫 원소.

    모델이 술어를 안 주거나 허용 밖을 줬을 때 쓴다. **값을 지어내는 게 아니다** — 기본값이
    그 타입 자신의 계약에서 나오고, 타입은 모델이 고른 것이다. alphamale 의 결정론 어셈블러가
    같은 규약을 쓴다(`assemble.py`: `spec.predicates[0]` + `predicate_source=ontology_default`)
    — 산출물이 그쪽과 호환되게 같은 관례를 따른다.

    목록이 비면 None — 호출부가 사유로 드러낸다(조용한 빈 문자열 금지).
    """
    predicates = process_types()[event_type_code].predicates
    return predicates[0] if predicates else None


def prompt_catalog() -> str:
    """프롬프트에 실을 타입 카탈로그 — `타입 | 술어 | 필수역할 | 선택역할` 한 줄씩.

    스냅샷 원본(48KB)엔 태깅이 안 쓰는 필드가 절반 이상이라 그대로 실으면 토큰만 태운다.
    허용 집합과 **같은 함수들**에서 파생하므로 프롬프트와 검증이 갈라지지 않는다.
    """
    lines = []
    for code in event_type_codes():
        pt = process_types()[code]
        lines.append(
            f"{code} | 술어: {','.join(pt.predicates)}"
            f" | 필수: {','.join(pt.required_roles) or '-'}"
            f" | 선택: {','.join(pt.optional_roles) or '-'}"
        )
    return "\n".join(lines)
