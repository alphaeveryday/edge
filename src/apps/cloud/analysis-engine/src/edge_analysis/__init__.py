"""EDGE analysis-engine: KODEX 반도체 ETF 당일 설명 생성.

통합 파이프라인의 feature 산출물 소비자다(ADR-0028, ALPHA-411/412). 파이프라인이 쓴
``price_movement_trigger``(L0 게이트)와 조립된 ``source_event`` 계보를 읽고, S3 레이크에서
ETF 등락을 분해해 당일 설명을 만든다. Step Functions 상태머신의 ``analyze`` 페이즈로
단일 ECS Fargate 태스크로 돈다.

레이어:
    domain/     순수 로직·모델 (I/O 없음)
    adapters/   I/O 경계 (S3 레이크·Event Store·DeepSeek·런 아카이브)
    pipeline    오케스트레이션 (의존성 주입)
    cli         composition root
"""
