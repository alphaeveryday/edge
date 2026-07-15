# ADR-0026: 팀 오너십 경계 정정 — 진기는 DB 적재까지, 인터페이스는 DB 스키마

- 상태: 승인됨
- 날짜: 2026-07-15
- 대체: [0019](0019-team-ownership-interface.md)

## 맥락
ADR-0019(노션 확정 결정 #8 이관)는 진기 범위를 "Cloud Event Store + **Event Bundle 생성까지**"로 기록했으나, 실제 분담은 처음부터 **데이터 파이프라인 → 분석 → Cloud Event Store 적재까지**였다(PM 확인, 2026-07-15). "번들 생성"이 진기 범위로 기록된 것은 이관 과정의 오기다. 번들의 양단(생성하는 Tenant Sync API, 수신하는 Sync Agent)이 모두 영서 소유이므로, 번들 와이어 포맷은 애초에 양자 계약 대상이 아니다.

## 결정
- **김진기**: Data Pipeline → Common Analysis Engine → **Cloud Event Store 적재까지** (DB 에 쓰는 것까지).
- **조영서**: DB 에 적재된 데이터를 소비하는 **이후 전부** — Event Bundle 생성(tenant-sync-api 내부 DB 조회·조립), 전달 레코드(fan-out), Tenant Sync API, Sync Agent, 온프렘 전체.
- **진기-영서 인터페이스 = Cloud Event Store DB 스키마 하나** ([ADR-0005](0005-db-as-contract.md) db-as-contract). 스키마 변경은 양자 합의(CODEOWNERS `/src/libs/schema/`), 번들 JSON·체크섬·프로토콜은 영서 단독 스펙.
- 유지되는 것: Sync 프로토콜 양단 단일 오너(영서), 정준영 = AI/ML (ADR-0019 의 나머지 내용).

## 대안
ADR-0019 문구 유지(진기가 번들 생성) — 번들 조립을 위해 진기가 영서의 와이어 계약에 결합되고, 두 사람의 인터페이스가 스키마+와이어 두 겹이 된다. 실제 분담과도 다르므로 배제.

## 결과
- docs/README 팀 오너십·CODEOWNERS(sync-protocol.md 공동 게이트 해제)·event-bundle-schema.md 오너십 문구 갱신.
- 번들 생성기는 영서 백로그로 — tenant-sync-api 가 경계면 테이블을 직접 조회해 조립한다.
- 진기 승인 대상은 스키마(libs/schema)와 경계면 서술로 축소된다.
