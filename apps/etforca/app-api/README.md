# app-api — ETF Orca 앱 API

ETF Orca 투자자용 앱(app-ui)의 서버다. 회원·관심 ETF·커뮤니티·알림·분석 조회와 ETF 전망 투표(BUY·WAIT·SELL)를 제공한다.

투표 집계는 Redis 에 두고 원본은 PostgreSQL 에 둔다. Redis 장애가 투표와 무관한 API 로 번지지 않게 격리하는 것이 이 모듈의 주된 실험 주제다. 실측은 [실측](#실측) 절에 있다.

## 디렉터리

```
app-api/
├── openapi.yaml                 계약 정본 (태그 11개 = 도메인 패키지)
├── docs/erd.md                  DB 설계
├── docker-compose.yaml          PostgreSQL + Redis Sentinel(마스터 1·복제본 2·Sentinel 3)
├── docker-compose.cluster.yaml  Redis Cluster(마스터 3·복제본 3) 부분 장애 실험
├── experiments/                 장애 주입 실험 (k6 시나리오·러너·결과 기록)
│   ├── run-failover.py          Sentinel 시나리오 S1·S2·S4·S5
│   ├── run-cluster.py           Cluster 시나리오 C1~C4
│   ├── failover.js · cluster.js k6 부하
│   ├── token.js                 k6 용 회원 JWT 서명
│   └── FAILOVER_RESULTS.md      Sentinel 실측 정본
└── src/main/
    ├── resources/
    │   ├── vote.lua · reconcile.lua · withdraw.lua   집계 Lua
    │   ├── db/etf-migration/    Flyway (현행 스키마)
    │   ├── db/migration/        이전 스키마 기록
    │   └── static/              약관·처리방침·지원 페이지
    └── java/com/edge/app/
        ├── common/              인증 필터, 에러 코드, 커서, Redis·ShedLock 설정, 메일
        ├── community/
        │   ├── vote/            전망 투표와 Redis 집계 ← 실험 대상
        │   ├── post/ · report/ · block/
        ├── auth/ · member/ · onboarding/ · watch/ · notification/
        ├── home/ · etf/ · explore/ · analysis/ · theme/
        └── sync/                파이프라인 → 앱 DB 동기화
```

## 투표 흐름

| API | 동작 |
|---|---|
| `PUT /api/v1/etfs/{code}/vote` | 투표·재투표(마지막 선택 우선, 같은 선택은 no-op) |
| `DELETE /api/v1/etfs/{code}/vote` | 철회 |
| `GET /api/v1/etfs/{code}/vote` | 내 투표 (DB) |
| `GET /api/v1/etfs/{code}/vote/count` | 집계 (공개) |
| `POST /api/v1/admin/votes/reconcile` | 수동 재조정 (`X-Admin-Token`) |

```
PUT ─▶ VoteFacade ─▶ VoteService.vote()  @Transactional, INSERT ... ON CONFLICT DO UPDATE
                │        (커밋·커넥션 반납)
                └──▶ VoteService.updateCount() → VoteCountRepository.vote()  vote.lua, 서킷 안
                         실패 시 로그만 남기고 응답은 성공 → 재조정이 복구

GET count ─▶ VoteService.counts()  Redis, 실패 시 DB 집계로 폴백 (source=db)

재조정 ─▶ VoteReconciler  DB 스냅샷으로 count·choices 해시를 reconcile.lua 한 번에 교체
         트리거: 시작 1초 후 · 5분 주기 · Lettuce 재연결 · 관리자 호출 (동시 트리거는 합침)
```

- **원본과 파생.** 사용자당 1행은 `UNIQUE(etf_code, member_id)` 제약이 강제한다. 신규·변경 판정은 SELECT 선검사가 아니라 원자 upsert 로 한다. Redis 는 파생 집계라 유실돼도 DB 에서 다시 만든다.
- **멱등 Lua.** `vote.lua` 가 choices 해시(사용자 → 선택)의 이전 선택과 비교해 같으면 no-op, 다르면 이전 카운터 -1·새 카운터 +1 한다. 같은 투표를 다시 실행해도 중복 집계되지 않는다.
- **커밋 뒤 갱신.** `VoteFacade` 가 트랜잭션 커밋과 커넥션 반납 뒤 Redis 를 갱신한다. 커밋 전 캐시 갱신(롤백 시 유령 표)이 생기지 않고, Redis 대기가 DB 커넥션을 쥐지 않는다.
- **응답에 집계 없음.** 쓰기 경로에 Redis 읽기가 붙으면 장애 실측 조건(요청당 Redis 호출 1회)이 달라진다. 앱은 성공 뒤 count 를 따로 조회한다.
- **탈퇴.** `VotesRemoved` 이벤트를 받은 `VoteCacheListener` 가 영향받은 ETF 의 집계를 남은 표 기준으로 교체한다.
- **읽기 응답.** `{buys, waits, sells, source}`. Redis 에 키가 없으면 0 을 반환하고 다음 재조정이 복구한다.

## 장애 대응 설계

**Lettuce 즉시 실패.** 기본값은 `REJECT_COMMANDS` / 명령·연결 타임아웃 500ms / `timeoutOptions=true` / `MASTER` 읽기다. 연결이 끊긴 동안의 명령을 큐에 쌓지 않고 바로 실패시켜, 재연결을 기다리는 요청이 톰캣 스레드를 붙잡지 않게 한다. 옵션 의미는 [Lettuce 공식 문서](https://github.com/redis/lettuce/blob/main/docs/advanced-usage/client-options.md)를 따랐다. 500ms 는 명령 단위 제한이며 전체 HTTP 지연은 부하 실험으로 판정한다.

**서킷 브레이커 (Resilience4j).**
- 쓰기 서킷은 `VoteCountRepository.vote()` 에 있다. 열려도 DB 저장은 막지 않고 폴백이 실패를 삼킨다(메트릭+로그).
- 읽기 서킷은 `VoteService.counts()` 에 있고 폴백이 DB 집계로 대체한다.
- 재조정 `replace()` 는 복구 경로라 서킷 밖이다.
- 서킷은 REJECT 가 못 잡는 "연결은 살아 있는데 응답이 없는" 장애(pause·네트워크 분리)에서 500ms 대기를 반복하지 않게 막는다.

**서킷 범위.** `VOTE_CIRCUIT_SCOPE` 로 고른다(기본 `shard`).
- `global`: 인스턴스 `redis` 하나로 Redis 전체를 한 단위로 판단한다.
- `shard`: `RedisCircuit` 이 키 슬롯을 소유한 마스터의 첫 슬롯 번호로 서킷(`redis-shard-0`·`redis-shard-5461`·…)을 골라 장애 샤드만 차단한다. 슬롯 표는 Lettuce `Partitions` 를 그대로 쓰므로 페일오버(노드만 바뀜)엔 상태가 이어지고 리샤딩(소유자 바뀜)엔 옮긴 슬롯이 새 샤드 서킷을 따른다. Cluster 가 아니면 `shard` 여도 전역으로 동작한다.

## 실측

모두 Docker Desktop 단일 호스트 실험이며 운영 성능의 보장은 아니다. 무관 요청은 Redis 를 쓰지 않는 정적 경로로 쟀다. `/actuator/health` 는 Redis 인디케이터를 포함해 무관 요청으로 부적합하다.

### Redis Sentinel 마스터 장애 (2026-09-13, MySQL)

마스터 1·복제본 2·Sentinel 3. k6 투표·무관 요청 각 50rps 를 3분 넣고 60초 후 마스터를 SIGKILL 했다. 주입 후 20초 구간의 값이다. 당시 빌드는 DB 가 MySQL 이었고 Redis 갱신은 `AFTER_COMMIT` 리스너에서 했다. 서킷은 두 시나리오 모두 켜져 있다.

| 주입 후 20초 | S1 DEFAULT / 5초 | S2 REJECT / 500ms |
|---|---|---|
| SLO 1초 초과 | **352건 (39.7%)** | **0건 (0%)** |
| 투표 p99 / 최대 | 6,803ms / 9,817ms | 7.4ms / 11.0ms |
| 무관 요청 p99 | 346.0ms | 2.2ms |
| 톰캣 busy 최대 | 195 | 3 |
| 부하 유실 | 118건 | 0건 |
| Redis 실패 로그 ≈ 재조정 delta | 697건 | 639건 ≈ 640 |
| 최종 DB = Redis | 통과 | 통과 |

- 서킷이 없던 빌드의 S1 은 SLO 초과 68.1%·무관 p99 972ms 였다. 68.1% → 39.7% 가 서킷 단독 효과이고, 39.7% → 0% 는 즉시 실패 설정의 효과다.
- 상세 기록과 이전 빌드의 측정은 [FAILOVER_RESULTS.md](experiments/FAILOVER_RESULTS.md) 에 있다.

### Redis Cluster 부분 장애 (2026-10-04)

측정 코드는 태그 [vote-cluster-isolation-2026-10-04](https://github.com/alphaeveryday/edge/tree/vote-cluster-isolation-2026-10-04/src/apps/cloud/app-api)에 있다. 수정 전 코드는 커밋 ecd74dc8 이다.

마스터 3+replica 3, `cluster-require-full-coverage=no`. 투표·조회·무관 요청(`/terms.html`) 각 50rps 를 3분 넣고 60초 후 샤드 0 의 마스터·replica 에 장애를 주입했다. 주입 후 20초 구간의 정상 샤드 투표 SLO(1초) 초과율과 조회 DB 폴백 비율을, 전파 원인으로 Tomcat busy·HikariCP 대기(풀 10)를 쟀다. 인기 종목(트래픽 50%)을 장애 샤드에 두면 전역 실패율 67%, 정상 샤드에 두면 17% 다. 조합마다 3런이고 표의 값은 런 범위, 별도 표기가 없으면 hot=failed 다. 원본은 `experiments/runs/`(gitignore) 의 result.json 이다.

| 구성 | 코드 | 장애 | 정상 샤드 투표 SLO 초과 | 정상 샤드 조회 DB 폴백 | busy | HikariCP 대기 | 조회 폴백 qps |
|---|---|---|---|---|---|---|---|
| C1 기본값(60s·버퍼링·서킷 off¹) | 수정 전 | SIGKILL | **91.1~95.0%** (hot=healthy 76.5~80.3%) | 0% | 164~169 | 91~94 | 0 |
| C2 500ms·REJECT·서킷 off | 수정 전 | SIGKILL | 0% | 0% | 4 | 0 | 32.6~32.9 |
| C2 | 수정 전 | pause | **70.1~85.7%** | 0~28.3%² | 200 | 181~186 | 19.2~32 |
| C2 | 수정 전 | partition | **82.4~85.9%** | 0% | 200 | 183~187 | 18.7~19.5 |
| C2 | 수정 후 | pause·partition | 0% | 0% | 39~40 | 0 | 31.7~32 |
| C3 전역 서킷 | 수정 후 | SIGKILL | 0% | **94.4~96.5%** | 4 | 0 | 48.9~49.3 |
| C3 hot=healthy | 수정 후 | SIGKILL | 0% | 0% | 4 | 0 | 8.8 |
| C4 샤드 서킷 | 수정 후 | SIGKILL | 0% | **0%** | 4 | 0 | 32.7~32.9 |
| C4 | 수정 후 | pause·partition | 0% | 0% | 13~17 | 0 | 32.8 |

¹ 서킷 off 는 실패율 임계치 100% 로 무력화한 것이라 창(20건)이 전부 실패하면 열린다.
² 두 런은 서킷이 주입 후 17~19초에 열려 정상 샤드 조회 일부가 폴백했고, 그만큼 SLO 초과가 낮았다.

- 수정 전: AFTER_COMMIT 리스너가 커밋 직후 커넥션을 쥔 채 Redis 를 기다렸다. C1 은 60초 대기로 풀이 고갈돼 무관 요청까지 27~40% SLO 초과가 났다. C2 SIGKILL 은 연결 종료를 감지해 즉시 거부되지만, pause·partition 은 TCP 가 살아 있어 요청마다 500ms 를 기다렸고 같은 경로로 풀이 소진됐다.
- 수정 후: `VoteFacade` 가 커밋과 커넥션 반납 뒤 Redis 를 갱신한다. 서킷 없이도 pause·partition 의 정상 샤드 영향이 사라졌다. 남은 busy 39~40 은 장애 샤드 요청이 500ms 를 기다린 몫이다.
- C3: 전역 실패율이 판단 기준이라 정상 샤드 조회까지 폴백됐다. hot=healthy 에서는 3런 모두 서킷이 열리지 않았고, 폴백 8.8qps 는 전부 장애 샤드 몫이다.
- C4: 장애 샤드 서킷(`redis-shard-0`)만 열린다. pause·partition 에서 주입 후 0.8초에 열려, 400ms 이상 기다린 장애 샤드 요청이 99.2~99.8%(C2 수정 후)에서 7.0% 로, 중앙값이 504ms 에서 3ms 로 줄었다. 남은 7% 는 half-open 시험 호출이라 p99 는 505ms 로 남는다.
- 재조정 검산: C1 은 부하 종료 후에도 버퍼가 남아 제한 시간 안에 끝나지 않는다. 수정 전 C2 partition 한 런은 9건 불일치로 끝났지만 같은 런의 주기 재조정 delta 는 0 이었다. 원인은 확인하지 않았다. 나머지 런은 30/30 일치다.

#### 이전 측정 (2026-09-20, MySQL)

측정 코드는 태그 [vote-cluster-isolation-2026-09-20](https://github.com/alphaeveryday/edge/tree/vote-cluster-isolation-2026-09-20/src/apps/cloud/app-api)에 있다. 리스너 수정 전 코드다.

| 구성 | 장애 | 정상 샤드 투표 SLO 초과 | 정상 샤드 조회 DB 폴백 | busy | HikariCP 대기 | 조회 폴백 qps |
|---|---|---|---|---|---|---|
| C1 기본값(60s·버퍼링·서킷 off¹) | SIGKILL | **94.2%** (hot=healthy 80.6%) | 0% (폴백 없이 대기) | 168~176 | 102~122 | 0 |
| C2 500ms·REJECT·서킷 off | SIGKILL | 0% | 0% | 2~4 | 0 | 33 |
| C2 | pause·partition | **82~86%** | 0% | 200 | 185~187 | 18~19 |
| C3 전역 서킷 | SIGKILL | 0% | **94.5%** (재현 95.3%) | 4 | 0 | 49 |
| C3 | pause·partition | 0% | 89.2% | 42 | 15 | 48 |
| C4 샤드 서킷 | SIGKILL | 0% | **0%** | 4 | 0 | 33 |
| C4 | pause·partition | 0% | 0% | 13 | 0 | 33 |

¹ 서킷 off 는 실패율 임계치 100% 로 무력화한 것이라 완전한 off 가 아니다 — 창(20건)이 전부 실패하면 열린다. C1 두 런은 주입 60초 뒤(during 창 밖), C2 kill 한 런(021853)은 0.2초 만에 열렸다. 표에 쓴 C2 런(022148)은 개방 0건이다.

replica 승격(`KILL_REPLICA=false`, C4, 10런): 승격 7.0~9.0초, 정상 샤드 조회 폴백은 전 런 0%. 승격 뒤 장애 샤드 조회가 Redis 로 돌아오기까지는 0.5~2.7초 또는 6.9~7.4초의 두 무리로 갈리며 topology refresh 설정 유무와 무관했다 — 원인은 확인하지 않았다.

Cluster 개념 설명은 [블로그](https://choyoungseo20.github.io/posts/redis-cluster/)에 있다.

### write-behind 모드 (제거)

투표를 Redis 에 먼저 기록하고 DB 에 뒤늦게 반영하던 실험용 모드는 db-first 와의 비교 실측 후 제거했다. 코드는 태그 `vote-write-behind-2026-09-19` 에, 실측과 해석은 [FAILOVER_RESULTS.md](experiments/FAILOVER_RESULTS.md) 에 있다.

## 한계

- 같은 사용자의 서로 다른 선택이 동시에 들어오면 커밋 순서와 Redis 반영 순서가 직렬화되지 않아, 다음 재조정까지 DB 와 Redis 가 어긋날 수 있다. 같은 선택의 동시 재투표는 no-op 이라 무관하다.
- 재조정 스냅샷 이후 커밋된 표는 그 재조정의 교체에 덮여 다음 주기까지 집계에서 빠질 수 있다. 복구 경로에만 있는 창이라 버전 검사는 두지 않았다.
- 재조정은 ETF 하나의 전체 사용자 목록을 메모리와 Lua 로 처리한다. 실험 규모를 넘는 성능은 별도 검증이 필요하다.
- 미측정: Sentinel 네트워크 분리(S4)·복제본 읽기(S5), PostgreSQL 기준 replica 승격.

## 실행

```sh
# 이 디렉터리에서
VOTE_ADMIN_TOKEN=local-experiment docker compose up --build -d
curl -i -X PUT localhost:8080/api/v1/etfs/069500/vote -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"choice":"buy"}'
curl localhost:8080/api/v1/etfs/069500/vote/count
curl -i -X POST localhost:8080/api/v1/admin/votes/reconcile -H 'X-Admin-Token: local-experiment'
```

관리자 토큰을 설정하지 않으면 수동 재조정은 403 이다. 경로의 ETF 코드는 6자리 문자열이다.

```sh
# src 디렉터리에서: 실제 PostgreSQL·Redis Docker 컨테이너 필요
./gradlew :apps:cloud:app-api:test
# 기존 compose를 내려 포트 8080, 55440, 6390을 비운 후 experiments에서
python3 run-failover.py S2  # S1·S2·S4·S5(S3 retry 는 제거돼 거부), 각 3분 부하, 60초 후 장애
# Redis Cluster 부분 장애(6노드 compose, docker-compose.cluster.yaml)
HOT=failed FAULT=kill python3 run-cluster.py C4  # C1 기본값 / C2 500ms·REJECT / C3 전역 서킷 / C4 샤드 서킷
```

**Cluster 실험.** 한 샤드의 마스터·replica 를 SIGKILL·`docker pause`·네트워크 분리(`FAULT`)하거나 마스터만 죽여 replica 승격(`KILL_REPLICA=false`)을 본다. k6 가 Redis 와 같은 CRC16 으로 종목을 샤드별 10개 배치해 요청마다 샤드를 태깅하고, 인기 종목(트래픽 50%)을 장애 샤드/정상 샤드(`HOT`)에 둔다. 결과는 장애 전·중·후 × 샤드 등급별 SLO 초과·DB 폴백·서킷 개방으로 집계한다.

**Sentinel 실험.** k6 투표 50rps, 별도 compose project 를 쓴다. S4 는 해당 프로젝트의 원래 master 만 네트워크에서 분리한다. 매 실행 새 사용자·전망 ID 를 쓴다. 재투표 부하는 `USER_POOL=3000 python3 run-failover.py S2` 로 준다. 500 미만이면 같은 사용자의 요청이 겹쳐 ack 순서와 커밋 순서가 달라지므로 거부한다.

**공통.** 종료 후 데이터 확인을 위해 컨테이너를 남기며 출력된 down 명령으로 정리한다. 결과는 `experiments/runs` 에 기록한다. k6 스크립트는 `token.js` 가 compose 와 같은 `APP_JWT_SECRET` 으로 회원별 액세스 JWT 를 직접 서명해 투표한다.

**측정.** k6 samples 의 응답 코드·투표 지연, threads.json 의 최대 busy, fault.json 과 sentinel.log 의 +switch-master 차이, DB 와 Redis 선택지별 차이, app.log 의 재조정 delta/duration. Redis 재조정 전 차이는 장애 직후 재연결 트리거가 먼저 보정할 수 있으므로 **app.log delta 를 함께 사용**한다. 합계만 같아도 선택지별 오류가 있을 수 있어 최종 판정은 각 선택지와 choices 해시까지 확인한다. 재투표 판정은 선택지별 합계·`HLEN`·중복행에 더해 사용자별 최종 choice 를 k6 ack·DB·Redis 세 방향으로 대조해 `per-user.json` 과 `checks.json` 에 남긴다. NFR-4 는 fault 부터가 아니라 failover 완료부터 복구 완료까지 판정한다. 과거 RESULTS.md 와 load.py 는 이전 Redis 선저장 구현의 기록으로 현재 구현의 근거가 아니다.

## 앱 API 구조 규칙

계약 정본은 [openapi.yaml](openapi.yaml)(계약 우선, ADR-0056). springdoc 의 `/v3/api-docs` 는 yaml 과 대조하는 검증용으로만 쓰고, 애노테이션으로 yaml 내용을 중복 기술하지 않는다.

**패키지는 도메인 단위, 안은 layered.** 도메인은 openapi.yaml 의 태그 11개와 1:1 이라 operationId 와 클래스가 바로 대응된다. 각 도메인 안은 계층 폴더 `controller/`·`service/`·`repository/`·`entity/`·`event/`·`dto/` 로 나눈다(없는 계층 폴더는 만들지 않는다). 도메인 안에 기능이 여럿이면 기능 하위 패키지로 먼저 묶고 그 안을 계층 폴더로 나눈다(`community/vote/controller/`). 엔티티는 접미사 없이 이름 그대로(`Post`, `Vote`). 조회 하나짜리 도메인(home)은 Repository 없이 Service 가 다른 도메인 Repository 를 읽는다.

**계층 두께는 투표 API 기준.** Controller 는 검증(`@Valid`)·호출·`ApiResponse.onSuccess` 반환만, Service 는 트랜잭션 경계와 규칙, Repository 는 JPA. 트랜잭션 밖 순서 조율이 필요하면 Facade(`VoteFacade`)를 둔다. DTO 는 record, 이름은 `XxxRequest`/`XxxResponse`. 저장소 반환형 record(`VoteCounts`·`VoteReconcileResult` 같은 Redis·Lua 결과)는 `dto/` 가 아니라 `repository/` 에 둔다.

**Service 는 구체 클래스가 기본.** 구현을 바꿔 끼울 사정이 없으면 클래스 하나. 미리 두는 인터페이스는 추측성 추상화라 두지 않는다.

**도메인 간 참조.** 다른 도메인 것은 Service 가 아니라 Repository 를 직접 읽는다(Service 끼리 부르면 순환·계층 비대). 쓰기는 자기 도메인만 한다. 부수 효과(글 작성 후 알림 생성 등)는 `MemberWithdrawn` 처럼 이벤트로 넘긴다.

**인증.** 회원 `Authorization: Bearer` 액세스 JWT + DB 저장 리프레시, 게스트 `X-Device-Id`. 둘 다 있으면 토큰 우선. common 의 `AuthFilter` 가 해석하고 리졸버가 인자 타입으로 넘긴다. `MemberPrincipal` 은 회원만, `AppPrincipal` 은 회원 또는 게스트, 익명이면 COMMON401. 필터는 DB 를 보지 않는다.

**에러 코드.** `AppErrorStatus` enum 이 도메인 코드를 소유하고 openapi.yaml 의 `x-error-codes` 와 1:1 을 유지한다. 형식 `{도메인}{HTTP}{일련}`(예 `ETF4001`, `ANALYSIS4001`). 앱은 code 를 번역 없이 그대로 분기한다.

**구현 순서는 스텁 → 실 구현.** 계약의 operation 전부를 컨트롤러·DTO 로 먼저 만들고 서비스는 고정 예시 데이터를 반환한다. 도메인을 구현하면 그 서비스 본문을 교체한다. mock 모드(플래그로 mock·real 병존)는 두지 않는다. 앱 쪽 mock 클라이언트가 이미 도메인을 덮고 있어 서버 mock 은 가짜 데이터 두 벌이 되고, 플래그·분기가 영구히 남기 때문이다. 실데이터가 아직 없는 도메인은 스텁이 예시를 주거나 준비 중 코드(`ANALYSIS4001`)를 준다.

**데이터.**
- 쓰기 소유: 사용자·관심·게시물·투표·알림.
- 동기화 테이블(ETF·분석·순위): 쓰기는 `sync` 패키지 하나, 다른 도메인은 읽기만.
- `sync` Repository 는 JdbcTemplate. 파이프라인 테이블에 엔티티가 없고 쓰기가 일괄 upsert.
- 파이프라인 접속 env: `APP_PIPELINE_URL`·`APP_PIPELINE_USERNAME`·`APP_PIPELINE_PASSWORD`. 없으면 동기화 미동작.
- 원천 없는 값(선별 목록·테마·운용사): `etf_curation`·`theme` 마이그레이션.
- 스키마 소유: `db/etf-migration` Flyway(별도 history table), Hibernate 는 `validate`. `db/migration` 은 이전 스키마 기록이다.

**운영.** `@Scheduled` 작업(재조정 등)은 ShedLock 리스(`shedlock` 테이블)로 다중 인스턴스에서 한 대만 돈다. ElastiCache 에는 `SPRING_DATA_REDIS_SSL_ENABLED=true` 로 붙는다.
