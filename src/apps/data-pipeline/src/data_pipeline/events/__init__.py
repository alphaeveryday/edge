"""이벤트 온톨로지·조립 — 분석엔진 추출 체인의 이식분 (ALPHA-412, ADR-0028).

로직 정본은 분석엔진(daily_pipeline)이다 — 이 패키지는 그 로직의 실행 위치만
feature 페이즈로 옮긴다. 온톨로지(ontology_ref.txt)는 엔진 동봉본과 같은 alphamale
0.1.0 스냅샷이며, tagging/event_type_profiles_v0_1.json 과의 단일화는 로직 소유자
결정 후 별건이다.
"""
