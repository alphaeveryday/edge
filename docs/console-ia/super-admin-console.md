# Super Admin Console IA (Vendor Cloud)

> 콘솔 재설계 IA 기준으로 정렬 ([../architecture/information-architecture.md](../architecture/information-architecture.md) 슈퍼 어드민 콘솔). 이 문서가 현행 SSOT이고 뷰는 논리 개요다 — 충돌 시 이 문서 우선.

```
Super Admin Console
├── Run Overview (첫 화면)
├── Tenants
└── Event Pipeline
```

**Run Overview** (= 오늘 운영 현황, ALPHA-683)

- 첫 화면. 레인(시장·뉴스)별 최신 런의 운영 상태(판정 스펙 §7: IN_PROGRESS/READY/DEGRADED/BLOCKED/UNKNOWN), 필수 작업 귀결 수(단위 명기), 결함 목록(단계 순 — 같은 단계 안 순서는 실행 순서가 아니며, 정확한 최초 결함 지점은 드릴다운 소관), 드릴다운 링크
- 발행 분포(자동 제공/검수 대기/차단)는 증권사 관리 환경 콘솔 소관 — 이 화면은 파이프라인 원장 범위까지만 답한다

**Tenants** (= 테넌트 관리)

- 생성: 테넌트명, 환경 구분(PoC/Production), 초기 Tenant Admin 이름·이메일, 메모
- 목록: 테넌트명, 환경, 연결 상태(Sync 채널 기준: 정상 / 동기화 지연 / 오류 / 미연결(온보딩 중) — 테넌트 활성·비활성 개념 아님, 사용 중지/재개 기능 없음과 일관), 마지막 이벤트 동기화 시각, 생성일. 필터: 상태, 테넌트 검색
- 상세: 위 + 최근 동기화 결과, 최근 오류 메시지, 최근 24시간 전달 이벤트 수, 최근 24시간 무효화 이벤트 수
- **금지**: 테넌트 API Key 관리 ✕, 시장/종목 커버리지 세부 제어 ✕, 사용 중지/재개 버튼 ✕

**Event Pipeline** (= 가격 변동 분석 관리)

- 데이터 소스 수집 상태: 뉴스 / 공시 / 시세 / 수급 / 실적·재무 / ETF 구성 종목 — 각 상태, 마지막 수집·정상 처리 시각, 최근 오류
- 변동(분석) 이벤트 목록: 종목, 시장, 등락률, 방향, 생성 시각, 근거 개수, 상태. 필터: 상태(전체 / 분석 완료 / 분석 대기 / 분석 실패 / 제외됨), 시장, 종목 검색
- 변동 이벤트 상세: 종목명, 티커, 시장, 등락률, 기준 시각, 관련 뉴스/공시, 시세/수급 변화, AI 공통 설명 후보, 신뢰도, 반대 요인, 분석 정보(변동 기준 시각·분석 완료 시각)
- 관리 액션: 정정 등록(분석 결과 정정), 무효화 등록(분석 대상 제외). **정정/무효화 시 사유 입력 필수** — 사유는 이벤트 레코드에 보존된다

## API 매핑 (super-admin-api)

위 화면 표면의 코드 대응물 — `AdminAuthFilter` RULES 와 1:1 이며, 엔드포인트 추가
시 이 표와 필터에 함께 행을 더한다(매핑 없는 표면은 fail-closed 403). 운영자는
단일 역할이라 전 표면이 "인증된 운영자"다 — 역할 열이 없다. 응답 원천은 도메인
단위로 DB 전환 중이다(ALPHA-515 mock 출발): tenants=JPA(ALPHA-526) ·
sources=운영 원장 조회(ALPHA-514) · analyses 읽기=설명 원장 조회(ALPHA-601) ·
session=인증 세션 주체(SessionOperator) 투영(ALPHA-608) · analyses 쓰기=운영자 작업 원장 전이(ALPHA-602).

| 화면 | 엔드포인트 |
|---|---|
| Tenants 목록/생성 | `GET /api/v1/tenants` · `POST /api/v1/tenants` |
| Run Overview — 오늘 운영 현황 | `GET /api/v1/sources/overview` |
| Event Pipeline — 수집 상태 | `GET /api/v1/sources/report` |
| Event Pipeline — 파이프라인 실행 이력 | `GET /api/v1/sources/grid` |
| Event Pipeline — 분석 목록/정정/제외/복원 | `GET /api/v1/analyses` · `PATCH /api/v1/analyses/{id}/result` · `POST /api/v1/analyses/{id}/exclude` · `POST /api/v1/analyses/{id}/restore` |
| 운영자 컨텍스트(헤더·프로필) | `GET /api/v1/session` · `PATCH /api/v1/session/profile` |
| 인증 | `POST /api/v1/auth/login`(유일 공개) · `POST /api/v1/auth/logout` · `GET /api/v1/auth/session` |

> 정정/무효화 **사유 입력 필수**는 쓰기 실전환(ALPHA-602)과 함께 계약에 편입됐다 — 정정은
> 새 결과·사유가, 제외는 사유가 필수다(빈 값 400). UI 가 사유 입력을 받는다. 복원 사유는 선택.
> 작업자·사유·변경 전후는 감사 원장(`admin_activity_log`)에 보존된다.

> **운영자 작업 감사는 별도 메뉴가 아니다 — 데이터는 존치, 전용 열람 화면만 제거.** 구 Admin Activity Log 브라우징 메뉴는 재설계에서 두지 않는다. 운영자 작업 감사 레코드 자체는 **DB에 보존**된다(context.md의 Admin Activity Log 컴포넌트, super-admin-api `admin_activity_log` 원장 — 현재 분석 정정/제외/복원이 append 되고 테넌트 생성 감사는 후속): 테넌트 생성·이벤트 정정·이벤트 무효화가 작업 시각·작업자·유형·대상·사유·변경 전후 내용과 함께 기록된다. 정정/무효화 사유는 Event Pipeline 상세에서도 확인된다. 운영자 작업 감사의 **콘솔 열람 UI**는 후속 UI 설계 수령 시 확정된다 — 현재는 UI-less(데이터 DB 보존)가 기준.
