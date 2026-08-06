"""독립 운영·패널 준비 스크립트 — 프로덕션 진입점이 부르지 않는다.

각 파일이 `python -m edge_analysis.statics.ops.<name>` 로 직접 실행되는 일회성
또는 주기 배치다: 백필(`backfill`)·패널 준비(`fin`·`flowhist`·`pit`)·τ 사이드카
(`tau_sidecar`)·표현력 배치(`batch`·`expressive`)·스모크(`smoke`)·관측 층(`observe`).
`core/`를 소비하지만 어떤 프로덕션·테스트 모듈도 이 패키지를 소비하지 않는다.
"""
