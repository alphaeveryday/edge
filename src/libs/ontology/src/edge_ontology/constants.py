"""edge 온톨로지 상수.

ONTOLOGY_VERSION 은 어휘(53타입·술어·역할)의 판번이다 — 태깅 산출물 provenance 에 박혀
재태깅 판정(tag_news._is_current)의 입력이 된다. 어휘가 실제로 바뀌는 리소스 개정에서만
올린다. 현재 리소스는 alphamale 0.1.0 스냅샷과 어휘 전 필드(술어·필수/선택/identity 역할·
stage_sensitive·family·lifecycle_model) 프로그램 대조로 동일함을 확인했다(ALPHA-539) —
그래서 0.1.0 을 승계하며, 버전만 올려 전 코퍼스 재태깅(기사당 LLM 1콜)을 유발하지 않는다.

리소스는 존재 층위별로 갈라 담는다(resources/<층>/). 층 이름이 곧 이 lib 의 패키지
이름이다 — entity·attribute·relation·process.
"""
from __future__ import annotations

ONTOLOGY_VERSION = "0.1.0"
DEFAULT_VERSION = ONTOLOGY_VERSION
RESOURCE_PACKAGE = "edge_ontology.resources"

ENTITY_DIR = "entity"
ATTRIBUTE_DIR = "attribute"
RELATION_DIR = "relation"
PROCESS_DIR = "process"
