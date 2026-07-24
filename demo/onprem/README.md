# demo/onprem — 가상 온프렘 박스 스택

`demo-onprem` EC2 박스(`infra/terraform/envs/demo-onprem`)가 compose 하나로 온프렘 런타임을 기동하기 위한 박스 전용 `docker-compose.yml`. 루트 `docker-compose.yml`(로컬 풀스택)의 **온프렘/데모 서브셋**만 떼어낸 것으로, 이미지는 ECR 을 참조하고 `sync-agent` 는 실 cloud(`sync-dev.edgesignal.dev`)를 outbound Pull 한다. 근거: ADR-0017·0033.

관통 경로(고객경로): cloud 분석 → `tenant_delivery`(outbox) → **[sync 경계]** → `sync-agent` → `intake` → `screening-worker`(AUTO_PUBLISHED 자동 게시) → `publication-api` → `mock-broker` → MTS.

## 서비스 (7 컨테이너)

`postgres-onprem` · `flyway-onprem` · `sync-agent` · `intake` · `screening-worker` · `publication-api` · `mock-broker`. 검수 콘솔(`tenant-console-api`)은 후속 증분.

## 마이그레이션 SQL 번들 (필수)

`flyway-onprem` 은 `${MIGRATIONS_ONPREM_DIR:-./migrations-onprem}` 를 마운트한다. 박스엔 레포 소스가 없으므로 **배포(ALPHA-445 SSM)가 `src/libs/schema/migrations-onprem/` 를 이 파일 옆 `migrations-onprem/` 로 번들**해 함께 올려야 한다. 번들이 없으면 스키마 미적용 → 앱이 `ddl-auto=validate` 로 부팅 실패한다.

## 환경 변수 (기본값)

| 변수 | 기본 | 용도 |
|------|------|------|
| `IMAGE_REGISTRY` | `393229433969.dkr.ecr.ap-northeast-2.amazonaws.com` | ECR 레지스트리 |
| `IMAGE_TAG` | `0.1.0` | 온프렘/데모 이미지 태그 |
| `TENANT_SYNC_API_URL` | `https://sync-dev.edgesignal.dev` | sync-agent Pull 대상(실 cloud) |
| `MIGRATIONS_ONPREM_DIR` | `./migrations-onprem` | flyway 마이그레이션 소스 |
| `ONPREM_DB_PASSWORD` | `edge` | 온프렘 PG 비밀번호(데모) |
| `MOCK_BROKER_PORT` | `8080` | mock-broker 호스트 노출 포트(CloudFront 오리진) |
| `INTAKE_POLL_MS` / `SCREENING_POLL_MS` | `5000` | 폴링 주기(데모 시연용 짧게) |

## 로컬에서 검증

```bash
# 이미지가 ECR 에 있어야 하고(ALPHA-533), docker 가 ECR 로그인돼 있어야 한다.
MIGRATIONS_ONPREM_DIR=../../src/libs/schema/migrations-onprem \
  docker compose -f docker-compose.yml config      # 문법·해석 확인
```

## 로컬 풀스택과의 차이

- `build:` → `image:`(ECR). 박스는 소스 빌드 안 함.
- `SPRING_PROFILES_ACTIVE` override 없음 → 이미지 기본 `prod` = ECS JSON 로깅(ALPHA-531). 로컬은 `""` 로 평문.
- `sync-agent` 대상 = 실 cloud sync ALB(로컬은 컨테이너). trust store 미주입 dev 라 현재 평문 HTTPS — cert·인가는 후속.
- cloud 서비스 없음(AWS 에 있음). 호스트 노출은 `mock-broker` 뿐(공개 박스 표면 최소화).

## 범위 밖 (후속)

박스 `terraform apply`·SSM 배포·CloudFront `/api` 오리진·`tenant_delivery` 시드·mTLS cert 는 **ALPHA-445**. 검수 콘솔(`tenant-console-api` + `tenant-console-ui` 호스팅)은 고객경로 개통 후 증분. 이미지 재빌드·재푸시 자동화(CD 워크플로)는 Phase 3.
