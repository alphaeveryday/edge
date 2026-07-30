# ADR-0012: Sync 인증서 부트스트랩 — CSR 방식, 개인키 비반출

- 상태: 승인됨
- 날짜: 2026-07-12
- 결정 로그: 확정 결정 #1 (2026-07-12)

## 맥락
MVP에서 일반적인 API Keys 메뉴는 존재하지 않는다. MTS/HTS가 벤더 클라우드를 호출하지 않으므로 MTS/HTS별 벤더 API Key가 필요 없다. 유일한 벤더-증권사 간 인증은 Sync 채널의 mTLS다. 이 mTLS 클라이언트 인증서를 누가 어떻게 발급·보유하는지 정해야 했다.

## 결정
증권사 키쌍 생성 → CSR 제출 → 벤더 Private CA 서명. 개인키 비반출.

개인키는 증권사 환경 밖으로 나가지 않는다 (고객 데이터 비반출 원칙과 동일한 서사). 상세 플로우·관리 경계는 [../contracts/sync-auth.md](../contracts/sync-auth.md).

## 대안
원문(컨텍스트 문서 v2.0)에 검토 대안이 별도로 기록되지 않았다.

## 결과
- Super Admin은 인증서 원문이나 키를 관리하지 않는다 — 보는 것은 테넌트 연결 상태·동기화 결과까지.
- 인증서 교체는 신규 CSR → 재발급, grace period 동안 신구 병행 (Tenant Console → Settings → Cloud Sync).
- Tenant Sync API는 매 요청마다 인증서-테넌트 바인딩을 인가 검증한다 ([ADR-0011](0011-rls-to-physical-isolation.md)의 클라우드 측 격리 지점).
