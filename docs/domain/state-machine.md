# 상태 머신 — 데이터 플로우 · 정정/무효화 · 상태값

콘텐츠가 수신부터 노출·정정까지 거치는 상태와 전이, 리비전 모델을 정의한다.
전체 구조는 [../context.md](../context.md), Sync 채널 계약은 [../contracts/sync-protocol.md](../contracts/sync-protocol.md).

## 데이터 플로우

1. Vendor Cloud Data Pipeline이 뉴스/공시/시세/수급 데이터를 수집한다.
2. Common Analysis Engine이 가격 변동 이벤트를 생성한다.
3. AI 설명 후보와 근거 데이터를 생성한다.
4. Cloud Event Store에 비개인화 이벤트와 설명 후보를 저장한다.
5. Event Bundle을 생성한다.
6. On-Premise Sync Agent가 Tenant Sync API를 Pull한다.
7. 수신한 Event Bundle을 Raw Event Store에 저장한다.
8. Compliance Engine이 증권사 정책을 적용한다.
9. 결과에 따라 상태를 분기한다.

**상태 분기**:

| 분기 | analysis_items.status | 후속 처리 |
| --- | --- | --- |
| 저위험/정책 통과 | AUTO_PUBLISHED | Published Store 저장 → Serving Cache 반영 → MTS/HTS 조회 가능 |
| 검수 필요 | REVIEW_REQUIRED | Review Queue 표시, 고객 화면 비노출 |
| 차단 | BLOCKED | 고객 화면 비노출 |
| 검수 승인 | APPROVED | Published Store 저장 → Serving Cache 반영 → 조회 가능 |
| 반려 | REJECTED | 고객 화면 비노출 |
| 정정 | CORRECTED (아래 [컴플라이언스 플로우](#컴플라이언스-플로우--정정무효화-처리-확정-결정) 참조) | **기존 발행분 노출 중단 후 재검수** |
| 무효화 | INVALIDATED | Serving Cache 제거 + publications.status → INVALIDATED 전이, 즉시 비노출 |

## 컴플라이언스 플로우 — 정정/무효화 처리 (확정 결정)

**원칙: 검수 없이 고객 노출 문구가 변경되는 경로는 존재하지 않는다.**

- **무효화(INVALIDATED)**: 노출 "제거"는 검수 불요. Sync Agent가 무효화 이벤트 수신 즉시 Serving Cache에서 제거하고 상태 전이. (제거는 보수적 방향이므로 자동 허용)
- **정정(CORRECTED)**: 노출 "변경"은 반드시 재검수.
    1. Cloud가 정정 이벤트 발행 (Super Admin은 정정 시 사유 입력 필수)
    2. On-Prem이 수신 → 기존 발행 콘텐츠 즉시 **UNPUBLISHED** (고객 화면에서 제거)
    3. 정정 문구가 새 검수 대상으로 **Review Queue 회귀** (REVIEW_REQUIRED)
    4. 검수자 승인 후에만 재발행
    - AUTO_PUBLISHED였던 콘텐츠도 동일하게 처리한다. 정정 건에 자동 노출 경로는 없다.
- 확장 로드맵(MVP 아님): 위험등급/정정 유형별 자동 반영 vs 재검수를 테넌트 정책으로 분기.

## ERD 방향 및 상태값

핵심 도메인 추상화 (기능 확장을 막지 않되 MVP UI는 가격 변동 설명에 집중):

`event` / `evidence` / `analysis_item` / `compliance_check` / `review_task` / `publication` / `exposure_log`

- `analysis_item.analysis_type`: 현재 **PRICE_MOVEMENT**만 사용. 향후 MARKET_BRIEFING, DISCLOSURE_SUMMARY 확장 가능하나 MVP UI 비노출.
- **정정 시 레코드 모델 (확정, 2026-07-13)**: 정정은 단일 레코드의 상태 왕복이 아니라 **리비전 분리**로 처리한다. ① 기존 analysis_item은 **CORRECTED로 종결(terminal 상태)** ② 해당 publication은 UNPUBLISHED로 전이 ③ 정정 문구는 원본을 참조하는(`supersedes_item_id`) **새 analysis_item 리비전**으로 생성되어 REVIEW_REQUIRED로 진입한다. 즉 위 [데이터 플로우](#데이터-플로우) 표의 CORRECTED는 구 리비전의 최종 상태이고, 재검수 대상은 신규 리비전이다. 감사 재현은 리비전 체인을 따라 "어느 시점에 어느 문구가 노출되었는지"를 완전 복원한다.

**상태값**:

| 엔티티 | 상태 |
| --- | --- |
| analysis_items.status | RECEIVED, AUTO_PUBLISHED, REVIEW_REQUIRED, APPROVED, REJECTED, BLOCKED, UNPUBLISHED, CORRECTED, INVALIDATED |
| review_tasks.status | PENDING, APPROVED, EDITED_APPROVED, REJECTED, CANCELLED |
| publications.status | PUBLISHED, UNPUBLISHED, INVALIDATED |
