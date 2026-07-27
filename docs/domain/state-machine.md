# 상태 머신 — 데이터 플로우 · 정정/무효화 · 상태값

콘텐츠가 수신부터 노출·정정까지 거치는 상태와 전이, 리비전 모델을 정의한다.
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
| 정정 | CORRECTED (아래 [컴플라이언스 플로우](#컴플라이언스-플로우--정정무효화-처리-확정-결정) 참조) | **기존 발행분 노출 중단 후 정정분 재점검**(정책 평가 — 청정이면 자동 재게시) |
| 무효화 | INVALIDATED | Publication Cache 제거 + publications.status → INVALIDATED 전이, 즉시 비노출 |

## 컴플라이언스 플로우 — 정정/무효화 처리 (확정 결정)

**원칙: 고객 노출 문구는 활성 점검 정책(policy_version·screening_rule)을 통과한 것만 나간다 — 신규·정정 동일.**
(구 원칙 "정정은 무조건 재검수"는 2026-07-27 결정으로 대체됐다 — ALPHA-438 온보딩 철학
"기본 자동 제공, 점검에 걸린 것만 검수"의 일관 적용. 결정 기록은 ALPHA-430 코멘트.)

- **무효화(INVALIDATED)**: 노출 "제거"는 점검·검수 불요. 온프렘이 무효화 이벤트 수신 즉시 Publication Cache에서 제거하고 상태 전이. (제거는 보수적 방향이므로 자동 허용. Publication Cache는 내부망 자원이므로 처리 주체는 Intake·온프렘 내부 흐름이지 DMZ의 Sync Agent가 아니다.)
- **정정(CORRECTED)**: 노출 "변경"은 정정분을 신규와 동일하게 재점검한다.
    1. Cloud가 정정 이벤트 발행 (Super Admin은 정정 시 사유 입력 필수)
    2. On-Prem이 수신 → 기존 발행 콘텐츠 즉시 **UNPUBLISHED** (고객 화면에서 제거)
    3. 정정 문구(새 리비전)가 **활성 정책 평가**를 거친다 — 룰 히트 시 REVIEW_REQUIRED/BLOCKED, 청정 통과 + 자동 제공 기준 충족 시 AUTO_PUBLISHED 로 같은 grain 에 재게시
    4. REVIEW_REQUIRED 로 간 정정분은 검수자 승인 후에만 재발행
- 확장 로드맵(MVP 아님): "정정분은 항상 검수" 를 테넌트 정책 옵션(정책 버전 스위치)으로 분기 — 보수적 증권사용.

## ERD 방향 및 상태값

핵심 도메인 추상화 (기능 확장을 막지 않되 MVP UI는 가격 변동 설명에 집중):

`event` / `evidence` / `analysis_item` / `screening_check` / `review_task` / `publication` / `exposure_log`

- `analysis_item.analysis_type`: 현재 **PRICE_MOVEMENT**만 사용. 향후 MARKET_BRIEFING, DISCLOSURE_SUMMARY 확장 가능하나 MVP UI 비노출.
- **정정 시 레코드 모델 (확정, 2026-07-13)**: 정정은 단일 레코드의 상태 왕복이 아니라 **리비전 분리**로 처리한다. ① 기존 analysis_item은 **CORRECTED로 종결(terminal 상태)** ② 해당 publication은 UNPUBLISHED로 전이 ③ 정정 문구는 원본을 참조하는(`supersedes_item_id`) **새 analysis_item 리비전**으로 생성되어 신규와 동일한 정책 평가로 진입한다(2026-07-27 결정 — 청정이면 AUTO_PUBLISHED 재게시, 룰 히트 시 REVIEW_REQUIRED/BLOCKED). 즉 위 [데이터 플로우](#데이터-플로우) 표의 CORRECTED는 구 리비전의 최종 상태이고, 재점검 대상은 신규 리비전이다. 감사 재현은 리비전 체인을 따라 "어느 시점에 어느 문구가 노출되었는지"를 완전 복원한다.

**상태값**:

| 엔티티 | 상태 |
| --- | --- |
| analysis_items.status | RECEIVED, AUTO_PUBLISHED, REVIEW_REQUIRED, APPROVED, REJECTED, BLOCKED, UNPUBLISHED, CORRECTED, INVALIDATED |
| review_tasks.status | PENDING, APPROVED, EDITED_APPROVED, REJECTED, CANCELLED |
| publications.status | PUBLISHED, UNPUBLISHED, INVALIDATED |
