"""이벤트 조립 — 분석엔진 추출 체인의 이식분 (ALPHA-412, ADR-0028).

로직 정본은 분석엔진(daily_pipeline)이다 — 이 패키지는 그 로직의 실행 위치만
feature 페이즈로 옮긴다. 온톨로지 어휘는 `edge_ontology` lib 이 SSOT 다 — 구
ontology_ref.txt 미러는 ALPHA-539 로 은퇴했다(미러는 5개 타입의 STAGE 마커를
잃어버린 드리프트가 있었고, lib 승계가 그걸 고쳤다).

v4(ALPHA-545)부터 `amounts` 모듈이 실험실 normalize/amounts.py 의 결정적 KR 금액 파서를
싣는다 — event_measure.value/unit 은 LLM 이 아니라 이 코드가 소유한다.
"""
