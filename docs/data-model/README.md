# 데이터 모델

Cloud 도메인 간 흐름은 overview로, 상세 물리 FK는 각 도메인의 ERD로 구분한다.

## Cloud 도메인 개요

![Cloud 데이터 모델 개요](cloud-overview.svg)

## Cloud 도메인별 물리 관계

- [기준정보와 금융상품](domains/reference/)
- [문서와 공시 사실](domains/documents/)
- [시장 데이터와 수집 상태](domains/market/)
- [이벤트와 근거](domains/events/)
- [설명 생성과 계보](domains/explanation/)
- [테넌트 전송](domains/delivery/)
- [운영 원장](domains/operations/)

도메인 경계를 넘는 FK의 대상 테이블은 관계 이해에 필요한 컨텍스트로 상세 ERD에 포함한다.
