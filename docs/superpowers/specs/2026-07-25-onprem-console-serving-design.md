# 온프렘 콘솔 서빙 — tenant-console UI+API 박스 co-host (설계)

- **날짜**: 2026-07-25
- **Jira**: ALPHA-554 (epic: ALPHA-423 온프렘 콘솔)
- **범위**: 서빙·패키징만 (콘솔 기능·인증 배선은 범위 밖)

## 1. 문제

`tenant-console` 은 온프렘 플레인이다 — 증권사 환경 안에서 검수자가 쓰는 내부 도구로, 온프렘 잔류 데이터(검수 대기 설명·노출 이력·최종 문구)를 다룬다. API(`tenant-console-api`, Spring Boot)와 UI(`tenant-console-ui`, Vite SPA)는 이미 존재하지만 **박스에서 서빙되지 않는다**:

- UI 는 백엔드를 상대경로 `/api/v1` 로 호출한다(dev 는 vite proxy → `:18081`). 즉 **UI 와 API 가 같은 오리진에 co-host 되는 것을 전제**한다.
- 온프렘 compose 스택(`demo/onprem/docker-compose.yml`)에 tenant-console 이 없다.
- 직전(ALPHA-552)에 cloud 에 잘못 올라가 있던 정적사이트를 제거했다 — cloud CDN 은 `/api` 백엔드가 없어 죽은 껍데기였다(SPA fallback 만 반환). 그 상대경로 설계가 온프렘 co-host 를 가리킨다.

**채워야 할 것**: 박스에서 UI 를 서빙하고 `/api/v1` 을 API 로 라우팅하는 방법 + 검수자가 도달하는 경로.

## 2. 결정 (방안 B — nginx 리버스 프록시)

작은 **nginx 컨테이너**가 SPA 정적을 서빙하고 `/api` 를 `tenant-console-api` 로 프록시한다. API 는 자기 컨테이너 그대로.

**기각한 대안 — 방안 A (Spring Boot 가 SPA 까지 서빙, 단일 컨테이너)**: 컨테이너는 하나로 줄지만 (a) UI/API 이미지가 결합돼 UI 한 줄 수정에 JVM 이미지를 재빌드하고, (b) SPA fallback 이 `/api` 를 삼키는 함정이 있다 — cloud 콘솔이 죽은 바로 그 실패 모드. 방안 B 는 프록시 레이어에서 `/api` 와 fallback 을 명확히 분리해 이 함정을 구조적으로 차단하고, MTS 를 mock-broker 가 "정적 + `/api` 프록시" 하는 기존 온프렘 서빙 선례와도 일치한다.

## 3. 아키텍처

### 3.1 컴포넌트 (온프렘 compose 에 +2)

| 컨테이너 | 종류 | 역할 |
|---|---|---|
| `tenant-console-api` | JVM (기존 코드·Dockerfile, CD 가 신규 빌드) | 온프렘 PG 읽기/쓰기(`review_task`·`analysis_item`·`member`·`policy_version`·`console_action_log` 등). `flyway-onprem` 완료 후 기동. 내부 `:8080`. |
| `tenant-console-ui` | nginx (신규 이미지) | `dist/` 정적 서빙 + `/api` → `tenant-console-api:8080` 프록시. host `127.0.0.1:8090:80` 만 바인딩. |

### 3.2 한 오리진 서빙 (nginx.conf 골자)

```nginx
location /api/ {
    proxy_pass http://tenant-console-api:8080;
    proxy_set_header Host $host;
    proxy_set_header Cookie $http_cookie;   # 세션 쿠키 통과 (ConsoleAuthFilter=세션 기반)
}
location / {
    try_files $uri /index.html;             # SPA fallback (API 매칭 뒤라 /api 를 안 삼킴)
}
```

`/api/` 가 `/` fallback 보다 먼저 매칭되므로 **API 404 는 진짜 404**(index.html 로 가려지지 않음).

### 3.3 접근 흐름 (SSM 터널, 공개 노출 0)

```
운영자 브라우저  localhost:8090
      │  aws ssm start-session --document-name AWS-StartPortForwardingSession
      │    --target i-... --parameters portNumber=8090,localPortNumber=8090
      ▼  (SSM 에이전트 경유 — SG 인바운드 불필요)
박스 127.0.0.1:8090 → nginx ┬─ /         → dist/ (SPA)
                            └─ /api/v1/*  → tenant-console-api:8080 → postgres-onprem
```

- nginx 가 `127.0.0.1:8090` 에만 바인딩 → 박스 사설 IP 로도 안 열리고 **오직 SSM 터널로만 도달**. 프로덕션의 "내부망 전용" 자세와 동일, SG·CloudFront 새 표면 없음.
- 공개 MTS/mock-broker 와 완전 분리 — 콘솔은 내부 도구.

## 4. 패키징 · CD

### 4.1 신규 파일

- `src/apps/onprem/tenant-console-ui/Dockerfile` — 멀티스테이지: `node`(pnpm 워크스페이스에서 tenant-console-ui 빌드 → `dist/`) → `nginx`(dist + nginx.conf 복사). context 는 `src`(pnpm 워크스페이스 루트, 다른 온프렘 이미지와 동일).
- `src/apps/onprem/tenant-console-ui/nginx.conf` — §3.2.

### 4.2 compose

`demo/onprem/docker-compose.yml` 에 두 서비스 추가:

- `tenant-console-api`: `image: .../edge/tenant-console-api:${IMAGE_TAG}`, `SPRING_DATASOURCE_*` → `postgres-onprem`, `depends_on: flyway-onprem(completed)`. host 포트 비노출(내부망만).
- `tenant-console-ui`: `image: .../edge/tenant-console-ui:${IMAGE_TAG}`, `ports: ["127.0.0.1:8090:80"]`, `depends_on: tenant-console-api`.

### 4.3 CD (`deploy-demo-onprem.yml`)

images 매트릭스에 2줄 추가:

```yaml
- { svc: tenant-console-api, dockerfile: src/apps/onprem/tenant-console-api/Dockerfile, context: src }
- { svc: tenant-console-ui,  dockerfile: src/apps/onprem/tenant-console-ui/Dockerfile,  context: src }
```

compose job 은 이미 `demo/onprem/docker-compose.yml` 을 번들하므로 서비스 추가만으로 배포된다. **서비스 running 단언 목록에 `tenant-console-api`·`tenant-console-ui` 추가**. MTS 같은 S3/CloudFront job 은 없다(박스 서빙).

### 4.4 접근 문서화

SSM 포트포워딩 커맨드를 `demo/onprem/README`(또는 스크립트)에 기록 — 공개 URL 아님.

## 5. 완료 조건 (DoD)

이 작업은 **서빙 배선 + 데모 로그인**까지 보장한다:

- ✅ 터널로 `localhost:8090` → 콘솔 SPA 로드.
- ✅ `/api/v1/auth/session` 이 `tenant-console-api` 에 도달해 **진짜 API 응답**(미인증 401/세션없음) 반환 — co-host + 프록시 배선 증명(SPA fallback 도, 연결 실패도 아님).
- ✅ **브라우저 클릭스루** — 서빙 빌드(`VITE_DEMO_AUTOSESSION=true`)가 로드 시 `ensureDevSession`(임시 브릿지)으로 `admin@demo.edge.local` 자동 로그인(`POST /api/v1/auth/login` → 세션 쿠키)해, 대시보드·검수 등 화면이 데이터와 함께 뜬다. tenant-console-api 가 기동 시 bootstrap-accounts 를 `member` 로 자동 시드하므로 별도 시드 불필요. (로그인 화면 ALPHA-486/423 도입 전까지의 임시 경로 — 실 온프렘 빌드는 이 플래그 없이 빌드.)
- ✅ 온프렘 8컨테이너 정상 `running`(기존 6 + console 2).

## 6. 범위 밖 (후속 증분)

- **콘솔 기능 완성**(검수 워크플로·정책·감사 화면 등) — ALPHA-423 epic. 로그인 후 도달하는 화면들의 실데이터 연결·완성이 여기 속한다.
- **인증 하드닝** — 현재 데모용(bootstrap-accounts·세션 기반, `secure` 쿠키·표준 CSRF 미적용 — application.yaml 주석 "실계약 시점"). AD/SSO 연동·secure 쿠키·CSRF 는 프로덕션 증분(ADR-0025). 서빙 자체는 데모 로그인까지 동작.

> 탐색 정정: 최초 spec 은 "인증은 로그인 벽까지, 별도 증분"으로 적었으나 `console.auth.bootstrap-accounts` 발견으로 **데모 로그인이 이 증분 안에서 실동작**한다(§5). 남는 건 화면 완성(기능)과 프로덕션 인증 하드닝뿐이다.
- **static-site 모듈 force_destroy** — 무관(콘솔은 박스 서빙, S3 아님).

## 7. 리스크 · 주의

- **세션 쿠키 통과**: ConsoleAuthFilter 가 HttpSession 기반이라 nginx 가 Cookie 헤더를 프록시해야 한다(§3.2). 누락 시 로그인해도 세션 유지 안 됨.
- **API 포트 확정**: tenant-console-api 가 실제로 `:8080` 을 리슨하는지 구현 단계에서 확인(Spring 기본 8080 가정).
- **ECR·배포역할 선행 apply(edge-review 발견)**: CD 가 `edge/tenant-console-api`·`edge/tenant-console-ui` 로 push 하려면 (1) `edge/tenant-console-ui` 저장소 존재(foundation/ecr.tf 신규 — **수동 foundation apply**), (2) 데모 배포역할 `demo_image_names` 에 콘솔 2종 포함(deploy-role.tf — **수동 demo-onprem apply**)가 **CD 실행보다 먼저**여야 한다. 안 그러면 repo 부재·PutImage AccessDenied 로 images job 이 실패해 배포가 안 뜬다. 배포 순서: foundation apply → demo-onprem apply → `deploy-demo-onprem.yml` 실행.
- **t3.small 용량**: 온프렘에 JVM 1개(+nginx)가 더 붙는다. JVM 5개가 되므로 리사이징(ALPHA-543)의 스왑 여유를 재확인 — 배포 후 `free -h`·OOM 관찰.
- **이미지 태그**: CD 가 `${IMAGE_TAG}=github.sha` 로 통일(기존 매트릭스와 동일) — compose 의 tenant-console-* 도 같은 태그를 받게 배선.
