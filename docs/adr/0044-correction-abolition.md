# ADR-0044: 정정(CORRECTION) 전달 폐지 — 무효화(INVALIDATION) 단독

- 상태: 승인됨
- 날짜: 2026-08-01
- 대체: [ADR-0041](0041-correction-same-screening.md) (정정 리비전도 신규와 동일한 정책 평가) 전체,
  [ADR-0021](0021-design-reinforcement.md) 중 "정정 리비전 모델" 결정 부분

## 맥락

정정(CORRECTION) 전달 라이프사이클은 스키마(`tenant_delivery` CHECK 3분기)·와이어
계약(event-bundle oneOf 3형상)·서빙 매핑(tenant-sync-api)·소비자 상태기계
(screening-worker 정정 재점검, ADR-0041)까지 전 구간이 구현돼 있었지만, **생산자(발번)는
한 번도 구현된 적이 없다** — analysis-engine fan-out 은 NEW 만 발번하고(ALPHA-493), 정정
전달은 로컬 시드로만 시연됐다. 2026-08-01 기능 완성도 전수 감사가 이 "소비자만 살아있는
발번 대기" 상태를 확정했고, 사용자가 정정 개념 자체를 재검토했다.

재검토의 근거: 이 시스템의 산출물은 **당일 등락 설명이라는 소멸성 콘텐츠**다. 정정본이
검수를 다시 거쳐 나갈 시점이면 설명의 효용 시한이 거의 끝나 있어 재게시의 실익이 없고,
오류를 발견했을 때 "고쳐서 다시 내보내는 것"보다 "내리는 것"(INVALIDATION)이 컴플라이언스
관점에서 더 보수적이며 증권사 검수 조직에 설명하기 쉽다.

## 결정

**CORRECTION 전달 유형을 계약에서 폐지하고, 전달 라이프사이클을 NEW·INVALIDATION 2형상으로
축소한다** (사용자 확정 2026-08-01).

1. **INVALIDATION 의 의미는 "특정 설명(리비전) 단위 무효화"다** — (종목, 거래일) 슬롯을
   봉인하는 것이 아니다. 목적이 실시간에 가까운 제공이므로, 무효화 이후 같은 종목에 새
   설명(NEW)을 발번할 수 있는지는 **발번 정책 소관**이며 이 결정의 범위 밖이다(현행
   day-grain 게이트·게시 grain 선점은 그대로 유지).
2. 온프렘 정정 재점검 경로(ADR-0041 `screenCorrection`), 리비전 체인
   (`supersedes_item_id`·`correction_reason`), CORRECTED terminal 상태를 제거한다.
   틀린 게시의 유일한 종결 경로는 INVALIDATED(즉시 비노출)다.
3. **Cloud 운영자 정정 오버레이(admin_activity_log, ALPHA-602)는 유지한다** — 그것은
   cloud 콘솔 내부의 감사·표시 모델이고 테넌트 전파와 무관하다. 다만 "오버레이 정정의
   테넌트 전파" 후속은 더 이상 존재하지 않는다 — 전파 수단은 INVALIDATION 발번뿐이다
   (ALPHA-440 을 INVALIDATION 발번 단독으로 현행화).

## 대안

- **CORRECTION 발번을 구현해 라이프사이클 완성** — 소멸성 콘텐츠에 정정 재게시의 실익이
  없고, 발번·정정 등록 UI·재점검 유지 비용만 남는다. 배제.
- **INVALIDATION 후 같은 grain 재발번을 지금 설계** — day-grain 게이트를 뚫는 재발번은
  사실상 CORRECTION 의 재구현이다. 필요가 실증되면 발번 정책에서 별도 결정한다. 이연.
- **소비자 코드를 남겨두고 발번만 안 함** — 죽은 라이프사이클이 계약·스키마·코드에 남아
  감사 때마다 미구현으로 오인된다(Rule 12 — 어중간한 상태를 남기지 않는다). 배제.

## 결과

- 계약: `event-bundle.schema.json` oneOf 3→2 형상, `tenant_delivery` CHECK 2분기
  (마이그레이션 `V202608011200`), [event-bundle-schema.md](../contracts/event-bundle-schema.md)·
  [sync-protocol.md](../contracts/sync-protocol.md) 전달 유형 서술 축소.
- 온프렘: `analysis_item` 에서 CORRECTED 상태·`supersedes_item_id`·`correction_reason` 제거
  (`V202608011210` — 기존 CORRECTED 행은 INVALIDATED 로 이관하고 이관 이력을 남긴다).
  `analysis_item_status_history` 의 CHECK 어휘는 append-only 원장 보존을 위해 유지한다 —
  과거 이력 전용 어휘이며 새 쓰기는 없다.
- 코드: screening-worker `screenCorrection`·tenant-sync-api CORRECTION 매핑·검수 콘솔
  정정본 표시(UI·DTO) 제거. CORRECTION entry 수신은 미지 타입과 동일하게 fail-loud.
- 문서: [state-machine.md](../domain/state-machine.md) 정정 플로우·리비전 모델 절 제거,
  [permission-matrix.md](../console-ia/permission-matrix.md)·[tenant-console.md](../console-ia/tenant-console.md)
  "정정 등록" 액션 제거. "최종 문구 수정"(콘솔 내 게시본 수정, ALPHA-613)은 별개 기능으로
  유지된다.
- 로컬 시드(`R__seed_local_demo_delivery.sql`)의 전달 서사는 NEW·INVALIDATION 2경로로 축소.
- 결정 주체·경위: 사용자 확정(2026-08-01) — 기능 완성도 감사 후속, Refs: ALPHA-673.
