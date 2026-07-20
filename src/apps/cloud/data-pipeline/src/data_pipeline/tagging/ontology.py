"""이벤트 타입 온톨로지 — 허용 라벨의 SSOT (ALPHA-138).

`event_type_profiles_v0_1.json` 은 분석 담당(정준영) 저장소 `alphamale` 의
`src/alphamale/events/ontology/resources/` 에서 가져온 **스냅샷**이다(dataset_version
0.1.0, 53 타입). 이 파일은 여기서 편집하지 않는다 — 온톨로지의 authoritative owner 는
alphamale 이고, 갱신은 그쪽 버전을 올린 뒤 스냅샷을 통째로 교체하는 방식이다(부분 발췌·
현지 수정은 드리프트를 만든다).

태깅이 이 온톨로지를 쓰는 이유는 **모델이 라벨을 발명하지 못하게** 하기 위해서다. 추출은
모델의 일(분류·추출)이지만, 나온 라벨이 허용 집합에 드는지 판정하는 건 코드의 일이다
(AGENTS Rule 5 — code answers). 그래서 여기서 파생하는 허용 집합이 프롬프트 구속과 사후
검증 **양쪽의 같은 출처**가 된다 — 프롬프트와 검증이 서로 다른 목록을 보면 모델이 프롬프트를
지켜도 검증에서 떨어지는 모순이 생긴다.

프로필의 나머지 필드(`identity_roles`·`lifecycle_model`·`projection`·`activate_hq`)는
스레드 정체성·그래프 투영 소관이라 태깅 범위 밖이다 — 스냅샷엔 남기되 여기선 안 읽는다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PROFILES_FILE = Path(__file__).resolve().parent / "event_type_profiles_v0_1.json"

# 문서 성격 라벨 — alphamale 골든 데이터(ko_gold_title.jsonl)의 doc_class 어휘와 같다.
# EVENT 만 assertion 을 낸다(나머지는 사건이 아니라 논평·홍보라 주장 추출 대상이 아니다).
DOC_CLASSES = (
    "EVENT",
    "OPINION_OR_ANALYSIS",
    "NO_EVENT_MARKET_COMMENTARY",
    "PROMOTIONAL_OR_SOLICITATION",
)


@lru_cache(maxsize=1)
def _snapshot() -> dict:
    """스냅샷 원본을 1회만 읽어 캐시한다.

    스냅샷이 깨졌으면(파일 없음·JSON 불량·items 비정상) 예외로 올린다 — 온톨로지 없이
    태깅하면 라벨 구속이 통째로 풀리므로 조용한 폴백은 금지다(Rule 12).
    """
    data = json.loads(_PROFILES_FILE.read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError(f"온톨로지 스냅샷 items 이상: {type(items).__name__}")
    return data


@lru_cache(maxsize=1)
def load_profiles() -> dict[str, dict]:
    """event_type_id → 프로필 dict."""
    return {item["event_type_id"]: item for item in _snapshot()["items"]}


def ontology_version() -> str:
    """스냅샷의 dataset_version — 산출물 provenance 에 박아 어느 온톨로지로 태깅했는지 남긴다."""
    return _snapshot()["dataset_version"]


def event_type_codes() -> tuple[str, ...]:
    """허용 event_type_code 전량(정렬). 프롬프트 구속과 검증이 같이 쓴다."""
    return tuple(sorted(load_profiles()))


def allowed_roles(event_type_code: str) -> frozenset[str]:
    """그 타입이 가질 수 있는 role_code 집합(required ∪ optional).

    타입마다 역할이 다르다 — CONTRACT.SIGNING 의 SUPPLIER 를 EARNINGS.RESULT_RELEASE 에
    붙이면 안 된다. 그래서 검증은 '86개 전역 역할'이 아니라 **타입별** 집합으로 한다.
    """
    profile = load_profiles()[event_type_code]
    return frozenset(profile["required_roles"]) | frozenset(profile["optional_roles"])


def required_roles(event_type_code: str) -> frozenset[str]:
    """그 타입이 요구하는 role_code — 다 채워지면 completeness=complete."""
    return frozenset(load_profiles()[event_type_code]["required_roles"])


def allowed_predicates(event_type_code: str) -> frozenset[str]:
    """그 타입이 허용하는 predicate_code."""
    return frozenset(load_profiles()[event_type_code]["allowed_predicates"])


def default_predicate(event_type_code: str) -> str | None:
    """그 타입의 기본 술어 — `allowed_predicates` 의 첫 원소.

    모델이 술어를 안 주거나 허용 밖을 줬을 때 쓴다. **값을 지어내는 게 아니다** — 기본값이
    그 타입 자신의 계약에서 나오고, 타입은 모델이 고른 것이다. alphamale 의 결정론 어셈블러가
    같은 규약을 쓴다(`assemble.py`: `spec.predicates[0]` + `predicate_source=ontology_default`)
    — 산출물이 그쪽과 호환되게 같은 관례를 따른다.

    목록이 비면 None — 호출부가 사유로 드러낸다(조용한 빈 문자열 금지).
    """
    predicates = load_profiles()[event_type_code]["allowed_predicates"]
    return predicates[0] if predicates else None


def prompt_catalog() -> str:
    """프롬프트에 실을 타입 카탈로그 — `타입 | 술어 | 필수역할 | 선택역할` 한 줄씩.

    스냅샷 원본(48KB)엔 태깅이 안 쓰는 필드가 절반 이상이라 그대로 실으면 토큰만 태운다.
    허용 집합과 **같은 함수들**에서 파생하므로 프롬프트와 검증이 갈라지지 않는다.
    """
    lines = []
    for code in event_type_codes():
        p = load_profiles()[code]
        lines.append(
            f"{code} | 술어: {','.join(p['allowed_predicates'])}"
            f" | 필수: {','.join(p['required_roles']) or '-'}"
            f" | 선택: {','.join(p['optional_roles']) or '-'}"
        )
    return "\n".join(lines)
