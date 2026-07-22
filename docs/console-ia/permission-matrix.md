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
| | 정정 등록 | – | ✓ | – | – |
| Review Queue | 대기 목록·상세 조회 | ✓ | ✓ | ✓ | ✓ |
| | 검수 액션(임시 저장·승인·수정 후 승인·반려·차단) | – | ✓ | – | – |
| Compliance Policy | 정책 조회(금칙어·처리 기준·면책 문구) | ✓ | ✓ | ✓ | ✓ |
| | 정책 변경(금칙어 등록·토글, 처리 기준, 면책 문구 = 새 버전 발행) | – | ✓ | – | – |
| Settings — 제공 범위 | 조회 | ✓ | ✓ | ✓ | ✓ |
| | 변경(시장·종목·섹터·채널 토글) | ✓ | ✓ | – | – |
| Settings — Cloud Sync | 연결 상태·이력·인증서 조회 | ✓ | ✓ | ✓ | ✓ |
| | 관리(인증서 교체·재연결) | ✓ | – | ✓ | – |
| Settings — Users & Roles | 사용자 조회·등록·비활성화·역할 부여 | ✓ | – | – | – |

- 제공 범위 변경에 TA·CR 을 함께 둔 이유: 한 기능에 두 성격이 겹친다 — 시장
  커버리지 토글은 시스템 설정(TA), 이해상충 종목·섹터 제외는 컴플라이언스 통제(CR,
  [../adr/0023](../adr/0023-customer-validation.md))다.
- 최종 문구 수정·정정 등록이 CR 전용인 이유: 노출 문구에 관한 판단은 검수
  경로이며(state-machine.md), Operator 의 쓰기는 노출 축소 방향으로 한정된다.

## API 매핑

콘솔 API(tenant-console-api)는 세션의 역할 클레임으로 위 행을 강제한다. 화면의
버튼 숨김은 UX 이고, **권한의 강제 지점은 API 다**(이중 방어). 현행 엔드포인트:

| 엔드포인트 | 매트릭스 행 | 허용 역할 |
|---|---|---|
| `GET /api/v1/review/items` | Review Queue 조회 | TA·CR·OP·RO |
| `POST /api/v1/review/items/{id}/approve` | 검수 액션 | CR |
| `POST /api/v1/review/items/{id}/reject` | 검수 액션 | CR |

이후 추가되는 엔드포인트는 구현 PR 에서 이 표에 행을 더한다 — 표에 없는
엔드포인트는 배포 전 매핑이 의무다(fail-closed).

## 후속

- ALPHA-118(콘솔 인증) — 역할 클레임을 세션에 싣는 구현. SSO/AD 와 데모 자체
  계정이 같은 클레임 형태로 수렴한다.
- ALPHA-119(Users & Roles) — 사용자 등록·역할 부여 화면·API. 이 매트릭스의
  Users & Roles 행이 요구 권한이다.
