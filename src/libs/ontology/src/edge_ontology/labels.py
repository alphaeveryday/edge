"""사건 유형 한국어 라벨 — edge 로컬 증보 리소스의 조회 뷰 (ALPHA-942).

라벨은 고객 산문의 "과거에 {라벨} 소식이 있었던 N건" 자리에 들어가는 명사구다.
타입 YAML(process/types/) 이 아니라 별도 리소스(resources/labels/)에 사는 이유:
타입 리소스는 상류 실험실 스냅샷으로 **통째 교체**되므로 거기 넣은 라벨은 소실된다
(resources/relation/ 과 같은 로컬 증보 지위).

결측은 폴백으로 접되 **조용히 접지 않는다** — `exact=False` 를 함께 돌려주므로
호출자(산문 렌더러)가 폴백 사용을 관측할 수 있다(Rule 12). 완전성 자체는
tests/test_labels.py 가 registry 대조로 강제한다: 상류 교체로 새 타입이 오면
테스트가 깨져 라벨 추가를 요구한다.
"""
from __future__ import annotations

from functools import lru_cache
from types import MappingProxyType
from typing import Mapping, NamedTuple

from ._resource import load_yaml_resource

_GENERIC_LABEL = "비슷한 유형"


class EventTypeLabel(NamedTuple):
    text: str
    exact: bool


@lru_cache(maxsize=1)
def _payload() -> tuple[Mapping[str, str], Mapping[str, str]]:
    payload = load_yaml_resource("labels", "event_type_labels_ko.yaml")
    types = payload.get("types") or {}
    families = payload.get("families") or {}
    if not isinstance(types, dict) or not isinstance(families, dict):
        raise ValueError("event_type_labels_ko: types/families 는 매핑이어야 한다")
    # lru_cache 로 공유되는 객체다 - 가변 dict 를 그대로 내보내면 소비자의 수정이
    # 전 프로세스의 어휘를 바꾼다. 읽기 전용 뷰로 감싼다.
    return (MappingProxyType({str(k): str(v) for k, v in types.items()}),
            MappingProxyType({str(k): str(v) for k, v in families.items()}))


def event_type_labels_ko() -> Mapping[str, str]:
    """정확 라벨 전체 매핑(type_id → 라벨). 완전성 테스트가 registry 와 대조한다."""
    return _payload()[0]


def event_type_label_ko(type_id: str) -> EventTypeLabel:
    """type_id 의 한국어 라벨. 결측이면 family 라벨→일반어 순 폴백 + exact=False.

    폴백 라벨도 같은 산문 문맥("과거에 {라벨} 소식이 있었던")에서 읽히도록
    family 는 "기업 관련" 처럼 '관련'을 붙인다.
    """
    types, families = _payload()
    exact = types.get(type_id)
    if exact is not None:
        return EventTypeLabel(exact, True)
    family = str(type_id).split(".", 1)[0]
    family_label = families.get(family)
    if family_label is not None:
        return EventTypeLabel(f"{family_label} 관련", False)
    return EventTypeLabel(_GENERIC_LABEL, False)
