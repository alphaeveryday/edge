# Tenant Console 권한 매트릭스 (역할 4종 × 기능)

> 역할 4종·인증 하이브리드는 [../adr/0025](../adr/0025-onprem-auth-hybrid.md)에서 확정됐고, 이 문서는 잔여였던 권한 매트릭스를 확정한다. 화면·메뉴 구조는 [tenant-console.md](tenant-console.md)가 SSOT.

## 결정 — 직무 분리 모델 (2026-07-22)

Tenant Admin 은 전권 superset 이 아니다. **검수 결정과 정책 변경은 Compliance
Reviewer 전용**이며 Admin 도 우회할 수 없다. "검수 없이 고객 노출 문구가 변경되는
경로가 존재하지 않는다"([../domain/state-machine.md](../domain/state-machine.md))의
권한 계층 대응물 — 준법감시 직무 분리는 금융권 감사에서 통제권의 근거가 된다.

역할의 성격:

| 역할 | 성격 |
|---|---|
| Tenant Admin | 시스템 관리 — 사용자·연결·커버리지 설정 |
| Compliance Reviewer | 컴플라이언스 판단 — 검수 결정·정책 발행·문구 통제 |
| Operator | 운영 대응 — 이관·중단 등 노출 축소 방향의 즉시 조치 |
| Read Only | 열람 전용 |

## 원칙

- **fail-closed** — 매트릭스에 없는 기능은 거부가 기본이다. 새 기능은 행을 추가하며
  역할을 명시적으로 부여받는다. 미인증 전 화면 차단(ADR-0025)과 같은 결.
- **조회는 기본 허용, 민감 정보만 예외** — 업무 화면 조회는 4역할 공통. 예외는
  Users & Roles(계정·역할 정보)로, Tenant Admin 전용이다.
- **Operator 의 쓰기는 노출 축소 방향만** — 이관(검수 요청)·제공 중단은 보수적
  방향이라 신속 대응을 위해 허용하고, 노출을 만들거나 문구를 바꾸는 액션은 없다.
- **데모에서도 동일 적용** — 데모 자체 계정(ADR-0025)도 같은 역할 클레임으로 이
  매트릭스를 그대로 따른다. 간소화를 이유로 우회 경로를 만들지 않는다.

## 매트릭스

TA = Tenant Admin, CR = Compliance Reviewer, OP = Operator, RO = Read Only.

| 메뉴 | 기능 | TA | CR | OP | RO |
|---|---|:-:|:-:|:-:|:-:|
| Dashboard | 현황·요약 조회 | ✓ | ✓ | ✓ | ✓ |
| Price Movement Explanations | 목록·상세 조회 | ✓ | ✓ | ✓ | ✓ |
| | 최종 문구 수정 | – | ✓ | – | – |
| | 검수로 이관 | – | ✓ | ✓ | – |
| | 제공 중단 | – | ✓ | ✓ | – |
| Review Queue | 대기 목록·상세 조회 | ✓ | ✓ | ✓ | ✓ |
| | 검수 액션(임시 저장·승인·수정 후 승인·반려·차단) | – | ✓ | – | – |
| Compliance Policy | 정책 조회(금칙어·처리 기준·면책 문구·버전 이력) | ✓ | ✓ | ✓ | ✓ |
| | 정책 변경(금칙어 등록·토글, 처리 기준, 면책 문구 = 새 버전 발행) | – | ✓ | – | – |
| Settings — 제공 범위 | 조회 | ✓ | ✓ | ✓ | ✓ |
| | 커버리지 변경(시장·채널 토글) | ✓ | – | – | – |
| | 이해상충 제외 변경(종목·섹터 토글) | – | ✓ | – | – |
| Settings — Cloud Sync | 연결 상태·이력·인증서 조회 | ✓ | ✓ | ✓ | ✓ |
| | 관리(인증서 교체·재연결) | ✓ | – | – | – |
| Settings — Users & Roles | 사용자 조회·등록·비활성화·역할 부여 | ✓ | – | – | – |

- 제공 범위 변경을 두 행으로 나눈 이유: 성격이 다른 두 통제가 한 화면에 있다 —
  시장·채널 토글은 시스템 커버리지 설정(TA), 종목·섹터 제외는 이해상충
  컴플라이언스 통제(CR, [../adr/0023](../adr/0023-customer-validation.md))다.
  경계는 `serving_scope.scope_type` 과 1:1 이다(MARKET·CHANNEL = TA,
  INSTRUMENT·SECTOR = CR) — TA 가 CR 의 이해상충 제외를 되돌릴 수 없다.
- 최종 문구 수정이 CR 전용인 이유: 노출 문구에 관한 판단은 검수
  경로이며(state-machine.md), Operator 의 쓰기는 노출 축소 방향으로 한정된다.
  Cloud Sync 관리(인증서 교체·재연결)는 노출 축소가 아닌 시스템 관리라 TA 전용이다.
- 역할 부여의 직무 분리 보강: TA 가 스스로에게 CR 을 부여해 검수·정책 권한을
  얻는 우회를 막기 위해 **자기 자신에 대한 역할 변경은 금지**하고, 역할
  부여·회수는 전건 콘솔 감사 로그(console_action_log)에 기록한다 — 타인 계정을
  경유한 우회는 차단이 아니라 감사로 추적한다(이중 통제 워크플로는 MVP 범위
  밖). 운영 SSO/AD 연동 시 역할의 원천(IdP 클레임 vs 콘솔 부여)은
  ALPHA-118 설계에서 확정한다.

## API 매핑

권한의 **강제 지점은 API 다** — 화면의 버튼 숨김은 UX 이고, 콘솔
API(tenant-console-api)가 세션의 역할 클레임으로 아래 표를 강제한다(이중 방어).
**강제 구현 = `ConsoleAuthFilter`(ALPHA-118)**: 미인증은 전 표면 401(fail-closed),
아래 표는 필터의 라우트 정책과 1:1 이며 엔드포인트 추가 시 표와 필터에 함께
행을 더한다. 표에 없는 표면은 인증돼도 403 거부가 기본이다(fail-closed).

| 엔드포인트 | 매트릭스 행 | 요구 역할 |
|---|---|---|
| `POST /api/v1/auth/login` | 인증(로그인) | **공개** — 유일한 비인증 표면 |
| `POST /api/v1/auth/logout` | 인증(로그아웃) | 인증된 전 역할 |
| `GET /api/v1/auth/session` | 인증(세션 조회) | 인증된 전 역할 |
| `GET /api/v1/session` · `PATCH /api/v1/session/profile` | 세션·프로필 조회·표시 이름 변경 (member 원장, ALPHA-500) | 인증된 전 역할 |
| `GET /api/v1/dashboard/traffic` | Dashboard 트래픽 KPI (24시간 요청·에러) | TA·CR·OP·RO |
| `GET /api/v1/explanations` · `GET /api/v1/explanations/feed-status` | Price Movement Explanations 목록·상세·반입 상태 조회 (실전환 ALPHA-607) | TA·CR·OP·RO |
| `PATCH /api/v1/explanations/{id}/final` | 최종 문구 정정(게시본 published_summary in-place, ALPHA-613) | CR |
| `POST /api/v1/explanations/{id}/stop` · `.../move-to-review` | 노출 축소 조치(제공 중단 — 사유 필수 · 검수로 이관, ALPHA-613) | CR·OP |
| `GET /api/v1/review/items` | Review Queue 조회 | TA·CR·OP·RO |
| `GET /api/v1/review/items/{id}` | 검수 상세(근거·사유·검사 결과·상태 이력 — 감사 열람, ALPHA-436) | TA·CR·OP·RO |
| `POST /api/v1/review/items/{id}/approve` | 검수 액션 — 승인(선택 의견) | CR |
| `POST /api/v1/review/items/{id}/approve-edited` | 검수 액션 — 수정 승인(edited_summary 필수) | CR |
| `POST /api/v1/review/items/{id}/reject` | 검수 액션 — 반려(사유 필수) | CR |
| `POST /api/v1/review/items/{id}/block` | 검수 액션 — 차단(사유 필수) | CR |
| `GET /api/v1/members` | Users & Roles 조회 | TA |
| `POST /api/v1/members` | 사용자 등록 | TA |
| `POST /api/v1/members/{id}/deactivate` | 사용자 비활성화 | TA |
| `PATCH /api/v1/members/{id}/role` | 역할 부여·변경 | TA — 자기 자신 대상은 서비스가 403(직무 분리) |
| `GET /api/v1/screening/words` · `.../criteria` · `.../disclaimer` · `.../versions` | 정책 조회(금칙어·처리 기준·면책 문구·버전 이력) | TA·CR·OP·RO |
| `POST /api/v1/screening/words` · `POST .../words/{id}/toggle` · `PATCH .../criteria` · `PATCH .../disclaimer` | 정책 변경(= 새 버전 발행, ALPHA-438) | CR |
| `GET /api/v1/scope/markets` · `GET /api/v1/scope/stocks` | 제공 범위 조회 | TA·CR·OP·RO |
| `POST /api/v1/scope/markets/{market}/toggle` | 커버리지 변경(시장 토글, ALPHA-606) | TA |
| `POST /api/v1/scope/stocks/{code}/toggle` | 이해상충 제외 변경(종목 토글, ALPHA-606) | CR |

### 콘솔 mock 한시 예외 — 종결 (2026-07-29)

tenant-console-ui 화면 계약대로 mock 표면을 먼저 열고 **인증만 강제(전 역할 허용)**
하던 한시 예외(ALPHA-513, 2026-07-23 사용자 결정)는 도메인별 DB 전환으로 **전부
해제됐다** — 사용자·세션=ALPHA-500, 정책=ALPHA-438, 제공 범위=ALPHA-606,
explanations=ALPHA-607(읽기)·613(쓰기). 모든 표면이 위 "API 매핑" 표의 실 역할을
강제하고 실 원장을 읽고 쓴다.

explanations **쓰기**(최종 문구 정정·검수 이관·제공 중단)도 원장 전이·행위자·감사로
실전환됐다(ALPHA-613) — ExplanationMockStore 삭제로 콘솔 mock 이 완전히 소멸했다
(잔여 seam 0). 화면·IA 에 없던 approve·reject·draft(ALPHA-513 잔재)는 표면째
제거됐다: 판정 게이트(승인·반려)는 Review Queue 소관이고 explanations 는 현황판+사후
운영에 한정된다(역할 분담, 사용자 결정 2026-07-29). 시장·등락률 컬럼 복원은
별건이다(ALPHA-497 materialization 후).

인증 방식은 하이브리드(ADR-0025): 데모 = 자체 계정(부트스트랩 시드, BCrypt),
운영 = SSO/AD(같은 세션 추상화로 수렴, 실계약 시점 구현). **의도적 생략(데모
범위)**: 로그인 레이트리밋·계정 잠금은 온프렘 내부망 전제의 데모 경로라 두지
않는다 — 운영 SSO 모드에서는 IdP 정책이 담당한다.

## 후속

- ALPHA-118(콘솔 인증) — 역할 클레임을 세션에 싣는 구현. SSO/AD 와 데모 자체
  계정이 같은 클레임 형태로 수렴한다.
- ALPHA-119(Users & Roles) — 사용자 등록 API 구현됨. 역할 부여·변경 API 는
  ALPHA-499(자기 자신 변경 403·전건 감사), 화면 실데이터 전환(역할 변경 UI·세션
  실전환)은 ALPHA-500 으로 구현 완료. 이 매트릭스의 Users & Roles 행이 요구
  권한이다.
