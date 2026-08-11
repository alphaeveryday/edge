# ADR-0017: 데모/개발 토폴로지 — 별도 EC2 + Docker Compose 가상 온프렘

- 상태: 승인됨 (스택 열거 중 Redis 는 [0051](0051-byoc-deployment-topology.md) 결정 6이 대체)
- 날짜: 2026-07-12
- 결정 로그: 확정 결정 #6 (2026-07-12)

## 맥락
8월 중간평가, 11월 데모데이까지 실제 증권사 On-Premise 환경 없이 하이브리드 구조를 시연·개발해야 한다.

## 결정
별도 EC2 + Docker Compose = 가상 온프렘. PostgreSQL + Redis 유지.

- 가상 온프렘 = 별도 EC2 1대 + Docker Compose로 온프렘 스택 전체 구동 (Serving API, Compliance Engine, Tenant Console, Sync Agent, PostgreSQL, Redis).
- Cloud 측은 기존 AWS 구조(ECS, Step Functions, RDS, 3-layer subnet) 유지하되 serving cluster 구성을 신규 컴포넌트에 맞게 개정.

상세는 [../implementation.md](../implementation.md).

## 대안
원문(컨텍스트 문서 v2.0)에 검토 대안이 별도로 기록되지 않았다.

## 결과
- 딜리버리 스토리가 생긴다: "증권사 서버에 Compose 파일 하나로 설치된다."
- Cloud 측 AWS 배포 토폴로지([ADR-0009](0009-aws-deployment-topology.md))는 유지·개정 대상이다.
