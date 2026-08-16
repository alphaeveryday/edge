# ADR-0010: 하이브리드 On-Premise 피벗 — 고객 접점·컴플라이언스의 증권사 이전

- 상태: 승인됨 — "노출 이력" 항목은 대체됨 → [ADR-0053](0053-widget-direct-serving-no-personalization.md) (노출 이력 축 자체가 폐지 — 플레인 분리·비반출·Pull 단방향은 불변, 2026-08-17)
- 날짜: 2026-07-12

## 맥락
기존 구조는 모든 기능을 Vendor Cloud에 두고 증권사 MTS/HTS에 임베드 widget을 제공하는 방식이었다 (widget-api, 클라우드 tenant-console-api, 범용 gateway, Super Admin의 API Key 관리 포함). gateway가 widget·console·admin 트래픽을 모두 받는 단일 엣지였고([ADR-0006](0006-gateway-single-edge.md)), 서비스들은 하나의 공유 DB로 결합하며 단일 스키마 PostgreSQL RLS로 멀티테넌시를 격리하는 설계였다.

금융권 고객 데이터 비반출 요구와 준법감시인 통제 요구를 이 구조로는 충족할 수 없었다. 2026년 망분리 규제 완화 흐름으로 "클라우드는 승인이 어려우니 온프렘"이라는 회피 논거는 시효가 짧아지고 있으며, EDGE의 온프렘 배치 근거는 규제 회피가 아니라 ① 개인신용정보를 직접 처리하는 핵심 시스템은 완화 이후에도 엄격한 통제가 유지되고 ② 사고 책임은 금융사에 남으므로 통제권·감사 재현성을 금융사 내부에 두는 구조 자체가 제품 가치라는 데 있다 ([../context.md](../context.md)).

## 결정
**고객 접점·컴플라이언스·검수·노출 이력을 전부 증권사 On-Premise로 이동**하는 하이브리드 구조로 전환한다. Cloud = 비개인화 공통 분석만 / On-Prem = 고객 접점·컴플라이언스 전부. 데이터는 항상 Cloud → On-Prem 단방향으로 흐르고, 연결은 항상 On-Prem → Cloud outbound(mTLS Pull)로만 열린다. MVP는 종목 단위 가격 변동 이유 설명 기능 하나에 집중한다.

여기서 On-Prem은 물리 서버가 아니라 **증권사가 통제하는 실행 환경**(자체 IDC·내부 가상화·증권사 명의의 전용/금융 클라우드 포함)을 뜻한다 — 배제 대상은 클라우드 일반이 아니라 벤더 통제 환경에 고객 데이터·검수·노출을 두는 구조다. 용어 정의는 [../context.md](../context.md).

## 대안
기존 Cloud-only / embed widget 구조 유지 — 고객 데이터 비반출·준법감시인 통제 요구를 충족하지 못해 배제. 이 구조를 기술한 기존 문서는 전부 폐기된 방향이다.

## 결과
- widget-api **제거**, tenant-console-api **이동(On-Prem)**, gateway **축소**(Cloud에는 Super Admin·Tenant Sync API용만), Tenant Sync API·Sync Agent·Compliance Engine·Serving API **신규** — 서비스/API 변경표는 [../context.md](../context.md).
- [ADR-0006](0006-gateway-single-edge.md)(gateway 단일 엣지)은 이 결정으로 대체된다. RLS 멀티테넌시 폐기는 [ADR-0011](0011-rls-to-physical-isolation.md).
- 구 구조 상세: docs/architecture.md, docs/schema.md (이 브랜치에서 삭제됨 — `git log --follow`로 열람 가능).
