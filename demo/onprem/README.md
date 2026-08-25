# demo/onprem — 가상 온프렘 박스 스택

`demo-onprem` EC2 박스(`infra/terraform/envs/demo-onprem`)가 compose 하나로 온프렘 런타임을 기동하기 위한 박스 전용 `docker-compose.yml`. 루트 `docker-compose.yml`(로컬 풀스택)의 **온프렘/데모 서브셋**만 떼어낸 것으로, 이미지는 ECR 을 참조하고 `sync-agent` 는 실 cloud(`sync-dev.edgesignal.dev`)를 outbound Pull 한다. 근거: ADR-0017·0033.

관통 경로(고객경로): cloud 분석 → `tenant_delivery`(outbox) → **[sync 경계]** → `sync-agent` → `intake` → `screening-worker`(AUTO_PUBLISHED 자동 게시) → `publication-api` → MTS 위젯 직접 호출(CloudFront `/api/v1/*` behavior — ADR-0053, ALPHA-992; 시세·차트·정적은 `mock-broker`).

## 서비스 (9 컨테이너)

고객경로: `postgres-onprem` · `flyway-onprem` · `sync-agent` · `intake` · `screening-worker` · `publication-api` · `mock-broker`.
검수 콘솔(내부 도구): `tenant-console-api`(세션 인증·온프렘 PG) · `tenant-console-ui`(nginx — SPA 정적 + `/api` 프록시 co-host).

## 네트워크 3망 (dmz/data/serving — ALPHA-561, ADR-0036)

compose 네트워크가 ADR-0036 경계를 구조로 강제한다: `sync-agent` 는 **dmz 만**이라 `postgres-onprem` 에 못 닿고(DMZ 컴포넌트의 DB 접근 금지), `intake` 가 dmz+data 브리지로 유일한 수신 경로다. `data` 는 `internal`(외부 라우팅 차단)이라 data 전용 서비스(`postgres-onprem`·`flyway-onprem`·`screening-worker`)는 인터넷 egress 가 불가하다. 서빙 면은 `publication-api`(data+serving, CloudFront /api/v1/* 직행 오리진 — ADR-0053)·`mock-broker`(serving 만 — 시세·차트·정적, 로컬 전용 /api/v1 passthrough), `tenant-console-ui`(serving 만)→`tenant-console-api`(data+serving). 배치 전체와 한계(dmz/serving 경유 egress 잔존)는 compose 헤더 주석 참조.

## 마이그레이션 SQL 번들 (필수)

`flyway-onprem` 은 `${MIGRATIONS_ONPREM_DIR:-./migrations-onprem}` 를 마운트한다. 박스엔 레포 소스가 없으므로 **배포 CD(deploy-demo-onprem.yml, ALPHA-542)가 `src/libs/schema/migrations-onprem/` 를 이 파일 옆 `migrations-onprem/` 로 번들**해 함께 올린다. 번들이 없으면 스키마 미적용 → 앱이 `ddl-auto=validate` 로 부팅 실패하므로, CD 는 `compose up` 전에 SQL 개수 preflight 로 fail-loud 중단한다(ALPHA-560 검증 종결).

## 환경 변수 (기본값)

| 변수 | 기본 | 용도 |
|------|------|------|
| `IMAGE_REGISTRY` | `393229433969.dkr.ecr.ap-northeast-2.amazonaws.com` | ECR 레지스트리 |
| `IMAGE_TAG` | `0.1.0` | 온프렘/데모 이미지 태그 |
| `TENANT_SYNC_API_URL` | `https://sync-dev.edgesignal.dev` | sync-agent Pull 대상(실 cloud) |
| `MIGRATIONS_ONPREM_DIR` | `./migrations-onprem` | flyway 마이그레이션 소스 |
| `ONPREM_DB_PASSWORD` | `edge` | 온프렘 PG 비밀번호(데모) |
| `MOCK_BROKER_PORT` | `8080` | mock-broker 호스트 노출 포트(CloudFront 오리진) |
| `CONSOLE_PORT` | `8090` | 검수 콘솔 호스트 노출 포트(CloudFront 오리진 — ALPHA-627) |
| `PUBLICATION_API_PORT` | `8084` | publication-api 호스트 노출 포트(CloudFront 설명 조회 직행 behavior 오리진 — ADR-0053, ALPHA-992; terraform `publication_api_port` 와 일치) |
| `CONSOLE_BOOTSTRAP_ADMIN_PASSWORD` | **필수(기본값 없음)** | 콘솔 관리자 부트스트랩 비번 — 공개 콘솔이라 미설정이면 compose 가 기동 거부(fail-loud) |
| `CONSOLE_BOOTSTRAP_REVIEWER_PASSWORD` | **필수(기본값 없음)** | 콘솔 검수자 부트스트랩 비번 — 위와 동일 |
| `INTAKE_POLL_MS` / `SCREENING_POLL_MS` | `5000` | 폴링 주기(데모 시연용 짧게) |

콘솔 부트스트랩 비번은 서버(env)만 바꾸면 된다 — UI 자동로그인(빌드타임 baked 값 동조 제약)은 ALPHA-627 로 폐기됐다. 배포 박스는 `/opt/edge-onprem/.env` 에 앱 기본값(`demo-admin-1` 류)이 아닌 값을 수동 1회 주입한다(TOSS 키와 같은 패턴 — 커밋 금지). 공개 콘솔이라 compose 가 이 두 변수를 필수로 강제한다(미설정=기동 거부). 부트스트랩 시드는 `member` 0건일 때 1회라, **이미 시드된 박스에서 비번을 바꾸려면 DB 의 member 를 비우거나 직접 갱신**해야 한다 — env 변경만으로는 회전되지 않는다.

## 검수 콘솔 접근 (CloudFront 공개 — 로그인 게이트)

`tenant-console-ui`(nginx)는 CloudFront(`https://demo-console.edgesignal.dev`)가 박스 `:8090` 오리진으로 프록시한다(ALPHA-627). 박스 SG 인바운드는 CloudFront origin-facing 프리픽스만 허용해 직접 접근은 막힌다. 진입은 로그인 화면(ALPHA-626)뿐 — 자동 세션(autosession) 빌드는 폐기됐고, 계정은 bootstrap-accounts(비번은 위 env 로 override)다.

> 자세 결정(2026-07-30): "증권사 내부망 전용" 재현(SSM 터널)보다 시연 편의를 우선했다. 이 공개는 데모 토폴로지 한정이다 — 이 스택 전체(compose 구동 포함)가 데모 시연용 구성이며, 실 납품의 패키징·접근 형태는 별개 사안이다.

**기존 박스 최초 적용 순서(순서 위반 = 알려진 기본 비번이 공개 창에 노출):** 부트스트랩 시드는 1회뿐이라 이미 시드된 박스는 env 만으로 비번이 안 바뀐다. 반드시 ① 박스 `.env` 에 새 비번 주입 → ② DB `member` 비번 회전(행 삭제 후 재시드 유도 또는 직접 갱신 — 콘솔 API 재기동 시 0건이면 재시드) → ③ `terraform apply`(CloudFront 공개) → ④ 재배포 순서로 진행한다. CloudFront 를 먼저 열면 회전 전까지 문서화된 기본 비번으로 로그인 가능한 창이 생긴다.

SSM 포트포워딩 경로도 여전히 동작한다(비상용):

```bash
aws ssm start-session --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8090"],"localPortNumber":["8090"]}'
# 브라우저: http://localhost:8090  (동일하게 로그인 화면 진입)
```

`tenant-console-api` 는 기동 시 `member` 가 비어 있으면 bootstrap-accounts 를 자동 시드하므로 별도 시드가 필요 없다.

## 로컬에서 검증

```bash
# 이미지가 ECR 에 있어야 하고(ALPHA-533), docker 가 ECR 로그인돼 있어야 한다.
# 콘솔 부트스트랩 비번은 필수 env 라 로컬 검증에도 값을 줘야 한다(로컬은 앱 기본값으로 충분).
MIGRATIONS_ONPREM_DIR=../../src/libs/schema/migrations-onprem \
CONSOLE_BOOTSTRAP_ADMIN_PASSWORD=demo-admin-1 \
CONSOLE_BOOTSTRAP_REVIEWER_PASSWORD=demo-reviewer-1 \
  docker compose -f docker-compose.yml config      # 문법·해석 확인
```

## 재배포 주의 (배포 CD 가 처리 — ALPHA-542)

`flyway-onprem` 은 one-shot·불변 이미지라 재배포(새 이미지·새 SQL) 시 `docker compose up` 이 기존 종료 컨테이너를 재사용해 **새 마이그레이션을 건너뛴다**. 매 릴리스 스키마를 적용하려면 배포가 앱 기동 전에 `docker compose up --force-recreate flyway-onprem`(또는 해당 서비스 rerun)을 먼저 돌려야 한다.

## T4 배포 전 드레인 확인 (ALPHA-587 — 신형 봉투 전용 회귀 시 필수)

`intake`·`screening-worker` 가 **신형 봉투 전용**(ADR-0040 T4, 이중형상 폴백 제거) 빌드로 넘어간 뒤부터 적용된다. `screening-worker` 는 저장된 `received_bundle.body` 를 되읽어 파싱하는데, T4 후엔 구형 direct-root(봉투 아님) body 를 계약 위반으로 **fail-loud** 한다. `ScreeningPoller` 는 순서보존이라 **미점검 구형 행 하나가 첫 실패에서 폴러 전체를 영구 차단**한다(ADR-0040 §⑦). 따라서 T4(ALPHA-587)를 포함한 빌드를 이 박스에 배포하기 **전에**, 커토버 이전 저장된 구형 미점검 행이 모두 소진(=0)됐는지 확인한다.

`postgres-onprem` 은 `data` 내부망 전용이라 호스트 포트가 없다 — 박스에서 SSM(Run Command 또는 세션)으로 compose exec 해 조회한다. 판정은 **시각이 아니라 body 형상**으로 한다 — 신형 봉투는 최상위 `isSuccess` 키가 있고 구형 direct-root 는 없으므로, 미점검 행 중 `isSuccess` 결측(구형)을 직접 센다. cloud ECS 롤링 전환 중엔 구 task 가 커토버 시각 이후에도 direct-root 를 반환할 수 있어 `received_at` 시각 기준은 구형 행을 놓칠 수 있다(ADR-0040 §⑦ 의 실제 조건 = 모든 미점검 direct-root 소진):

```bash
docker compose exec postgres-onprem psql -U edge -d edge_onprem -c \
 "SELECT count(*) FROM received_bundle
  WHERE screened_at IS NULL
    AND (convert_from(body, 'UTF8')::jsonb ->> 'isSuccess') IS NULL;"
```

= **0 이어야 배포**한다. 0 이 아니면 아직 구형 미점검 행이 남은 것 — T1 이중형상 빌드가 계속 소진하도록 두고 나중에 재확인한다. (T4 는 온프렘 전용이라 `dev` 머지가 이 박스를 자동배포하지 않으므로, 이 확인은 다음 `deploy-demo-onprem` 실행 직전 게이트다.)

## etf_instrument 시드 (서빙 전환 빌드 배포 "전" 준비 — ADR-0054 후속)

상장 판별(404)이 설정 allowlist 에서 종목 마스터 `etf_instrument` 로 옮겨졌다. 마이그레이션은
빈 테이블만 만들고, 데이터 소유는 증권사 환경이라 배포 번들에 시드가 실리지 않는다
(`seed-local-onprem` 은 로컬 compose 전용 — CI·CD 미포함). **서빙 전환 빌드가 시드 없이 뜨면
전 종목이 404** 이므로, 전환 빌드를 배포하기 전에 테이블과 데이터를 먼저 준비한다.
스키마 확장과 서빙 전환이 별도 커밋인 것이 이 순서의 장치다:

1. 스키마 확장 커밋까지 포함된 번들(구 서버 — allowlist 판정이라 빈 테이블 무영향)로 재배포
   → flyway 가 `etf_instrument` 를 만든다.
2. SSM 으로 시드 적재. 내용은 레포 로컬 시드와 동일(`src/libs/schema/seed-local-onprem/`
   `R__seed_etf_instrument.sql`, 38종 — 멱등이라 재실행 무해):

   ```bash
   # 시드 SQL 을 박스에 올린 뒤 (또는 SSM Run Command 로 파일 내용 전달)
   docker compose exec -T postgres-onprem psql -U edge -d edge_onprem < R__seed_etf_instrument.sql
   docker compose exec postgres-onprem psql -U edge -d edge_onprem -c "SELECT count(*) FROM etf_instrument;"   # = 38
   ```

3. 서빙 전환 빌드 배포 — 시드가 이미 있어 404 공백 구간이 생기지 않는다.

순서를 건너뛰고 전환 빌드를 곧장 배포했다면, 시드 적재(2)까지 전 종목이 404 다 — 적재
즉시 회복된다(앱 재기동 불요, 판정은 매 요청 DB 조회).

## 로컬 풀스택과의 차이

- `build:` → `image:`(ECR). 박스는 소스 빌드 안 함.
- `SPRING_PROFILES_ACTIVE` override 없음 → 이미지 기본 `prod` = ECS JSON 로깅(ALPHA-531). 로컬은 `""` 로 평문.
- `sync-agent` 대상 = 실 cloud sync ALB(로컬은 컨테이너). trust store 미주입 dev 라 현재 평문 HTTPS — cert·인가는 후속.
- cloud 서비스 없음(AWS 에 있음). 호스트 노출은 CloudFront 오리진 3개 — `mock-broker`(:8080)·`tenant-console-ui`(:8090, ALPHA-627)·`publication-api`(:8084, 설명 조회 직행 — ADR-0053) — 뿐이고 나머지 DB·앱 포트는 내부망만(공개 박스 표면 최소화, SG 는 셋 다 CloudFront origin-facing 프리픽스 한정).
- 네트워크 3망 세분화(위 절) — 로컬 풀스택은 단일 기본 네트워크(ECS Service Connect 토폴로지 재현이 목적, 망분리 미적용).

## 이 문서의 범위 밖 (완료·후속 현황)

박스 `terraform apply`·SSM 배포·CloudFront `/api` 오리진은 **완료**(ALPHA-445 개통 + ALPHA-542 배포 CD — deploy-demo-onprem.yml). `tenant_delivery` NEW 는 **analysis-engine 이 자동 발번**(write-time fan-out, ALPHA-493), INVALIDATION 은 **super-admin-api 무효화 액션이 발번**(ALPHA-440 — CORRECTION 은 폐지(ADR-0044)). 검수 콘솔 서빙(`tenant-console-api` + nginx `tenant-console-ui` co-host)도 ALPHA-554 로 완료. 잔여 후속: mTLS cert·인증서-테넌트 바인딩(ALPHA-447), 콘솔 기능 완성은 ALPHA-423 epic 경로로 진행 중(검수·정책·감사 열람은 436·438·431 완료).
