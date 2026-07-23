# super-admin-api

벤더 운영자 콘솔(Cloud)의 백엔드 — 운영자 인증 + 콘솔 화면 표면(현재 mock).
화면·금지 항목의 SSOT 는 [docs/console-ia/super-admin-console.md](../../../../docs/console-ia/super-admin-console.md)이고,
이 README 는 이 모듈만의 비자명한 규율만 적는다. DB 는 아직 미배선이다
(DataSource 자동설정 exclude — 연동 시 application.yaml 의 exclude 블록을 지운다).

## 지켜야 할 로컬 불변식

- **인증 게이트는 `AdminAuthFilter` 하나** — 미인증은 전 `/api/**` 표면 401
  (fail-closed, ALPHA-474 앱 방어선). 운영자는 단일 역할이라 역할 인가는 없다 —
  필터 `RULES` 는 표면 등록부다. 엔드포인트를 추가하면 `RULES` 에 행을 함께
  더한다 — 등록 없는 표면은 인증돼도 403 거부가 기본이라, 매핑을 빼먹으면 그
  API 는 배포돼도 닫혀 있다(우회가 아니라 누락이 안전한 쪽으로 실패).
- **경로 매칭은 정규화 후** — 필터는 context-path 를 제거하고 세그먼트별 matrix
  parameter(`;k=v`)를 벗긴 경로로 판정한다. MVC 의 PathPattern 매핑과 같은 기준을
  써야 `/api;x=y/...` 같은 우회로 필터가 통째로 건너뛰어지지 않는다.
- **콘솔 IA 금지 항목** — API Key 관리·테넌트 사용 중지/재개 표면을 만들지
  않는다(super-admin-console.md, epic ALPHA-424).

## 인증 (config 부트스트랩 운영자)

- **데모 경로(구현됨)**: `admin.auth.bootstrap-operators`(application.yaml) 계정을
  기동 시 BCrypt 해시로 들고, 이메일+비밀번호 로그인 → 세션(`SessionOperator`).
  세션 ID 재발급으로 고정(fixation) 차단, 실패 사유는 구분 없는 401 + 미존재
  계정에도 더미 BCrypt 비교(타이밍 오라클 차단). DB 미배선 단계라 시드 없이
  메모리 대조다 — tenant-console 의 DB 시드 방식과 그 지점만 다르다.
- **운영 경로(설계)**: 운영자 IdP 연동(ALPHA-474) — 같은 `SessionOperator` 로
  수렴하는 별도 진입점. Spring Security 본격 도입도 그 시점.
- **의도적 생략(데모 범위)**: 로그인 레이트리밋·계정 잠금 없음 —
  `allowed_cidrs`·WAF(망 제한) 뒤의 운영자 표면 전제. CSRF 는 세션 쿠키
  SameSite=Strict·HttpOnly 로 경량 방어.

## 콘솔 mock 표면 (ALPHA-515)

super-admin-ui 도메인 계약(repository.real.ts)과 1:1 인 화면 표면 4종 —
tenants(테넌트 목록·생성) · sources(데이터 소스 수집 상태) · analyses(가격 변동
분석 목록·정정·제외/복원) · session(운영자 컨텍스트·프로필).

- **응답 원천은 `mock` 패키지** — 도메인별 in-memory 가변 스토어(`*MockStore`) 한
  파일이 UI 구 mock 데이터의 이식본이다. DB 연동은 도메인 단위로 service 의 스토어
  의존을 repository 로 교체하는 방식으로 진행한다(UI 는 계약 불변이라 무변경).
- **JSON 은 camelCase** — UI 타입이 계약의 SSOT 다.
- **정정/무효화 사유 필수·감사 레코드**(콘솔 IA)는 DB 연동 시 UI 계약과 함께
  편입한다 — mock 단계 UI 계약에는 사유 입력이 없다.

## 스텁 → 실구현 교체 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
| `mock` 패키지 `*MockStore` 4종 | 도메인별 DB 연동 | service 의존을 repository 로 교체 (tenants 는 ALPHA-121) |
| config 부트스트랩 운영자 | ALPHA-474 | Spring Security + 운영자 IdP 연동 |

## 실행·확인

```bash
# 루트에서
docker compose up --build super-admin-api      # host 18082
curl localhost:18082/actuator/health
curl -i -X POST localhost:18082/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"operator@edge.local","password":"demo-operator-1"}'   # 200 + 세션 쿠키
```

```bash
./gradlew :apps:cloud:super-admin-api:build    # 테스트 포함 (src/ 에서)
```
