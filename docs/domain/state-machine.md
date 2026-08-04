# 상태 머신 — 데이터 플로우 · 무효화 · 상태값

콘텐츠가 수신부터 노출·무효화까지 거치는 상태와 전이를 정의한다.
전체 구조는 [../context.md](../context.md), Sync 채널 계약은 [../contracts/sync-protocol.md](../contracts/sync-protocol.md).

## 데이터 플로우

1. Vendor Cloud Data Pipeline이 뉴스/공시/시세/수급 데이터를 수집한다.
2. Common Analysis Engine이 가격 변동 이벤트를 생성한다.
3. AI 설명 후보와 근거 데이터를 생성한다.
4. Cloud Event Store에 비개인화 이벤트와 설명 후보를 저장한다.
5. Event Bundle을 생성한다.
6. On-Premise Sync Agent(DMZ)가 Tenant Sync API를 Pull·무결성 검증한다.
7. Intake(내부망)가 검증된 Event Bundle을 넘겨받아 Raw Event Store에 저장한다 (단일 모듈 옵션에서는 Sync Agent가 저장까지 — [ADR-0036](../adr/0036-sync-agent-intake-topology.md)).
8. Screening Worker가 증권사 정책(점검)을 적용한다.
9. 결과에 따라 상태를 분기한다.

**상태 분기**:

| 분기 | analysis_items.status | 후속 처리 |
| --- | --- | --- |
| 저위험/정책 통과 | AUTO_PUBLISHED | Published Store 저장 → Publication Cache 반영 → MTS/HTS 조회 가능 |
| 검수 필요 | REVIEW_REQUIRED | Review Queue 표시, 고객 화면 비노출 |
| 차단 | BLOCKED | 고객 화면 비노출 |
| 검수 승인 | APPROVED | Published Store 저장 → Publication Cache 반영 → 조회 가능 |
| 반려 | REJECTED | 고객 화면 비노출 |
| 무효화 | INVALIDATED | Publication Cache 제거 + publications.status → INVALIDATED 전이, 즉시 비노출 |

## 컴플라이언스 플로우 — 무효화 처리 (확정 결정)

**원칙: 고객 노출 문구는 활성 점검 정책(policy_version·screening_rule)을 통과한 것만 나가고, 오류가 발견된 설명은 고치지 않고 내린다.**

- **무효화(INVALIDATED)**: 노출 "제거"는 점검·검수 불요. 온프렘이 무효화 이벤트 수신 즉시 Publication Cache에서 제거하고 상태 전이. (제거는 보수적 방향이므로 자동 허용. Publication Cache는 내부망 자원이므로 처리 주체는 Intake·온프렘 내부 흐름이지 DMZ의 Sync Agent가 아니다.)
- **무효화의 단위는 특정 설명(리비전)이다** — (종목, 거래일) 슬롯을 봉인하지 않는다. 무효화 이후 같은 종목에 새 설명(NEW)을 발번할 수 있는지는 발번 정책 소관(현행 게이트는 발화(route) 축 — ALPHA-710, 무효화로 게시본이 사라진 발화는 재실행 시 재게시)이다 ([ADR-0044](../adr/0044-correction-abolition.md)).
- **정정(CORRECTION) 전달은 폐지됐다** (2026-08-01, [ADR-0044](../adr/0044-correction-abolition.md) — 구 리비전 분리 모델·정정 재점검(ADR-0041)·`supersedes_item_id` 체인 일괄 제거). 설명은 당일 소멸성 콘텐츠라 정정 재게시의 실익이 없고, 오류 발견 시 비노출이 더 보수적이다. Cloud 콘솔 통제는 무효화 단독이다(ALPHA-737 — 구 정정/제외/복원 오버레이는 은퇴, 기록은 admin_activity_log 에 이력으로 보존).

## ERD 방향 및 상태값

핵심 도메인 추상화 (기능 확장을 막지 않되 MVP UI는 가격 변동 설명에 집중):

`event` / `evidence` / `analysis_item` / `screening_check` / `review_task` / `publication` / `exposure_log`

- `analysis_item.analysis_type`: 현재 **PRICE_MOVEMENT**만 사용. 향후 MARKET_BRIEFING, DISCLOSURE_SUMMARY 확장 가능하나 MVP UI 비노출.
- 감사 재현은 `analysis_item_status_history`(append-only)와 exposure log 로 "어느 시점에 어느 문구가 노출되었는지"를 복원한다. (구 정정 리비전 체인은 폐지 — 과거 이력의 CORRECTED 어휘는 상태 이력 원장에만 남는다, ADR-0044.)

**상태값**:

| 엔티티 | 상태 |
| --- | --- |
| analysis_items.status | RECEIVED, AUTO_PUBLISHED, REVIEW_REQUIRED, APPROVED, REJECTED, BLOCKED, UNPUBLISHED, INVALIDATED |
| review_tasks.status | PENDING, APPROVED, EDITED_APPROVED, REJECTED, CANCELLED |
| publications.status | PUBLISHED, UNPUBLISHED, INVALIDATED |
