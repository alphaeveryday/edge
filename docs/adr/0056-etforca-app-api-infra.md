# ADR-0056: ETF Orca 앱 API(app-api) 클라우드 인프라: 별도 PostgreSQL·ElastiCache·N 인스턴스·단일 호스트

- 상태: 승인됨 (2026-09-28 — 인프라 배선 머지로 승인 전환)
- 날짜: 2026-09-28
- 관련: ADR-0009(AWS 배포 토폴로지) · ADR-0034(ALB 호스트 1:1) · ADR-0055(앱 계층 다중 인스턴스, ShedLock 선례) · `src/apps/cloud/app-api/README.md` · `src/apps/cloud/app-ui/screens.md`

## 맥락

app-api 는 ETF 전망 투표의 Redis 장애 실험 트랙으로 시작해 자체 compose(MySQL 8.4 + Redis)로만 돌았다. ETF Orca 앱(app-ui, Expo)의 B2C 서버로 확장하기로 했고, 화면은 mock 으로 관통을 마쳤다. 백엔드 계약 설계에 앞서 인프라를 먼저 올린다. 결정할 것은 넷이다. 인스턴스 수, DB 분리, DB 엔진, 도메인.

현재 클라우드는 ECS Fargate 상시 클러스터(`edge-dev-service`), 재사용 모듈 `ecs-service`·`alb`·`rds`(PostgreSQL), 와일드카드 ACM `*.edgesignal.dev`, RDS 한 대(`edge-dev`, 파이프라인·콘솔 공용)다. Redis 모듈은 없다.

## 결정

1. **인스턴스 1~N.** `ecs-service` 의 `desired_count` 로 시작하고 target tracking 오토스케일(초기 최소 1·최대 2)을 모듈에 추가한다. 앱의 `@Scheduled` 셋(VoteReconciler·VoteWarmer·VoteFlusher)은 ShedLock 으로 한 대만 실행한다(ADR-0055 와 같은 방식). Redis 는 ElastiCache(클러스터 모드 끔, 프라이머리 1 + 레플리카 1, Multi-AZ)로 신설 모듈을 둔다. dev 는 상시 과금을 줄이려 단일 노드로 시작하고, 페일오버 실측이 필요할 때 2 로 올린다.
2. **DB 는 별도 RDS 인스턴스.** `edge-dev` 와 인스턴스를 나눈다. 같은 인스턴스 안의 스키마 분리는 택하지 않는다.
3. **엔진은 PostgreSQL.** app-api 를 MySQL 에서 전환한다. 전환 시점은 지금이다. Flyway 파일이 1개뿐이다.
4. **도메인은 `etforca.edgesignal.dev`.** ALB 호스트 1:1 규약(ADR-0034)대로 전용 ALB 하나, 경로는 `/api/v1`. dev 도 같은 이름을 쓴다. 와일드카드 인증서로 덮이므로 foundation 변경은 없다.

## 근거

### DB 분리
- 폭발 반경. `edge-dev` 는 `apply_immediately` 라 스키마 모듈 머지가 곧 재부팅이고 1분 레인 5종을 세운다(ALPHA-924). B2C 스키마 변경을 장 마감 시각에 묶지 않고, 파이프라인 변경이 앱 사용자를 끊지 않게 한다.
- 부하 성격. 배치 쓰기와 B2C 읽기 급증은 스케일 방향이 다르다.
- 경계. 사용자 PII·동의 기록과 파이프라인 데이터의 권한을 인스턴스 단위로 가른다. 파이프라인 데이터는 앱 DB 로 명시적 동기화로만 들어오며, 그 한 지점이 마지막 단계의 실데이터 연동 지점이 된다.

### PostgreSQL
데이터 특성에서 오는 이유 셋과 운영 이유 둘이다.
- 투표 재투표는 "없으면 넣고 있으면 갱신"이다. `INSERT … ON CONFLICT DO UPDATE` 가 한 문장으로 원자적이다. InnoDB 는 유니크 키 경합 시 갭 락으로 동시 삽입 데드락이 난다.
- 분석 본문(요인 5축·근거·오늘 추가된 것·출처)은 배열·중첩이 많고 화면마다 형태가 다르다. JSONB + GIN 으로 인덱스를 유지하며 유연하게 둔다. MySQL JSON 은 생성 컬럼으로 우회해야 한다.
- 피드 조회는 "삭제 안 된 글 중 이 ETF" 류 조건이 대부분이다. 부분 인덱스와 배열 컬럼으로 조인을 줄인다.
- 실데이터 연동. 파이프라인 DB 가 PostgreSQL 이라 논리 복제·`pg_dump` 로 끝난다. 엔진이 다르면 타입 매핑 ETL 을 따로 짠다.
- 인프라 재사용. `rds` 모듈·로테이션 창(ALPHA-986)·db-query 경로·메모리 경보가 PostgreSQL 기준으로 검증돼 있다. 한 엔진이면 장애 판단이 빠르다.

MySQL 이 나은 점도 있다. Redis 장애 실험이 MySQL 기준 실측이고 익숙하다. 다만 그 실험은 Redis·커넥션 풀 동작을 본 것이라 엔진 종속 결론이 아니고, JPA 를 쓰면 익숙함의 차이는 방언·드라이버·Flyway 타입 몇 개로 줄어든다. 이 규모에서 성능은 결정 근거가 아니다.

### 도메인
브랜드 이름을 호스트로 쓰고, 기존 존·인증서를 그대로 쓴다. 모바일 앱은 쿠키·same-origin 문제가 없어 admin 콘솔처럼 CloudFront 프록시가 필요 없다. 브랜드 전용 도메인(별도 존)은 뒤로 미루며, 그때도 앱은 환경 변수 하나만 바뀐다.

## 결과

- 신설: `modules/elasticache`, `ecs-service` 오토스케일 입력, `envs/dev` 에 `app_rds`·`app_redis`·`app_alb`·`app_api` 모듈 인스턴스.
- app-api: PostgreSQL 전환(드라이버·방언·Flyway `db/etf-migration` 타입), ShedLock, Boot 4 헬스체크 함정 반영, `X-User-Id` 헤더 인증은 계약 단계에서 교체.
- CD: super-admin-api 와 같은 OIDC 이미지 빌드·롤아웃. JWT 서명 키는 Secrets Manager 에 선생성해 ECS secrets 로 주입한다(부트스트랩 비밀번호와 같은 규율).
- 비용: RDS `db.t4g.micro` + ElastiCache `cache.t4g.micro` 1 + ALB 1 이 추가된다.
- 미결: 브랜드 도메인 시점, 오토스케일 상한, 실데이터 동기화 방식(논리 복제 vs 배치)은 계약 설계 뒤로 넘긴다.

## 후속(2026-10-06)
- write-behind 모드 제거(ALPHA-1179)에 따른 VoteWarmer·VoteFlusher 삭제
- ShedLock 대상 스케줄 작업은 VoteReconciler 하나
