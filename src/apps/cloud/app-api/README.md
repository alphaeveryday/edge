# app-api — ETF 전망 컨센서스 투표

PRD_ETF_투표_Redis_Failover(2026-09-13)의 로컬 실험용 API. 투표 단위는 전망(forecast)이다 — 같은 ETF에 전망이 여러 개 열릴 수 있으므로 ETF가 아니라 전망에 표가 붙는다. 전망 엔티티는 아직 없고 forecastId는 Long 식별자다. 사용자당 1표를 유지하되 재투표로 선택을 바꿀 수 있다(마지막 선택 우선, 같은 선택 재투표는 no-op). 기존 PostgreSQL 데이터는 자동 이관하지 않는다. MySQL용 `db/etf-migration`과 별도 Flyway history table을 사용한다. 과거 `db/migration`은 기존 스키마 기록이다.

```sh
# 이 디렉터리에서
VOTE_ADMIN_TOKEN=local-experiment docker compose up --build -d
curl -i -X POST localhost:8080/api/v1/forecasts/69500/votes -H 'X-User-Id: 1' -H 'Content-Type: application/json' -d '{"choice":"BUY"}'
curl localhost:8080/api/v1/forecasts/69500/votes/count
curl -i -X POST localhost:8080/api/v1/admin/votes/reconcile -H 'X-Admin-Token: local-experiment'
```

사용자 ID는 양의 정수, 선택지는 BUY/HOLD/SELL(산다·기다린다·판다)이다. forecast ID는 Long 숫자이며 별도 존재 검증은 없다(발번은 전망 엔티티 도입 시 확정). 숫자가 아닌 경로 값은 공통 400 이다. 사용자 헤더는 로컬 실험용 식별자이며 로그인 인증은 구현하지 않는다. POST는 DB 커밋 후 Redis Lua를 실행하고 200을 반환한다 — 신규·변경·no-op 모두 200이다. GET 응답의 `result`는 `{buy,hold,sell,source}`다. Redis 실패 시 source=db이고, 정상 Redis에 키가 없으면 0을 반환하며 다음 재조정이 복구한다.

재조정은 시작 후 1초, 이후 5분 주기, Lettuce 연결 활성화, 관리자 호출(200, 비동기)로 실행된다. 관리자 토큰 미설정 시 수동 호출은 403이다. 동시에 들어온 트리거는 합친다. DB snapshot의 count(선택지별 집계)와 choices(사용자→선택 해시)를 단일 Lua로 교체한다. snapshot 이후 커밋된 표는 그 재조정의 교체에 덮여 다음 주기(또는 재연결·관리자 트리거)까지 집계에서 빠질 수 있다 — 복구 경로에만 있는 창이라 버전 검사는 두지 않았다. 대규모 전망의 전체 사용자 목록을 메모리와 Lua로 처리하므로 실험 규모를 넘어서는 성능은 별도 검증해야 한다.

기본값은 REJECT_COMMANDS / 500ms / timeoutOptions=true / MASTER다. 환경 변수는 application.yaml과 compose에 외부화했다. S3 실험용 재시도(`VOTE_REDIS_RETRY`)는 실험 종결 후 제거했다 — 실측·해석은 experiments/FAILOVER_RESULTS.md 기록이 정본이다. Lua가 choices 해시의 이전 선택과 비교해 같으면 no-op, 다르면 이전 카운터 -1·새 카운터 +1 하므로 동일 투표 재실행은 중복 집계되지 않는다.

Redis 접근에는 Resilience4j 서킷 브레이커(인스턴스 `redis`)를 얹었다. 쓰기 서킷은 `VoteCountRepository.vote()`에 있어 열려도 DB 저장은 막지 않고 폴백이 실패를 삼킨다(메트릭+로그). 읽기 서킷은 `DbFirstVoteService.counts()`에 있고 폴백이 DB 집계로 대체한다. 재조정 `replace()`는 복구 경로라 서킷 밖이다. 서킷이 열리면 Lettuce 타임아웃 대기 없이 즉시 폴백한다(REJECT가 못 잡는 "연결은 살아 있는데 느려지는" 유형의 이중 방어). 서킷 범위는 `VOTE_CIRCUIT_SCOPE`(기본 `global`)로 고른다 — `shard`면 `RedisCircuit`이 키 슬롯을 소유한 마스터의 첫 슬롯 번호로 서킷(`redis-shard-0`·`redis-shard-5461`·…)을 골라 장애 샤드만 차단한다. 슬롯 표는 Lettuce `Partitions`를 그대로 쓰므로 페일오버(노드만 바뀜)엔 상태가 이어지고 리샤딩(소유자 바뀜)엔 옮긴 슬롯이 새 샤드 서킷으로 따라간다. Cluster가 아니면 `shard`여도 전역 `redis`로 동작한다.

클라이언트 timeout 옵션 의미는 [Lettuce 공식 문서](https://github.com/redis/lettuce/blob/main/docs/advanced-usage/client-options.md)를 참고했다. 500ms는 커맨드 제한이며, 전체 HTTP 지연·5분 복구는 부하 실험으로 판정해야 한다.

```sh
# src 디렉터리에서: 실제 MySQL·Redis Docker 컨테이너 필요
./gradlew :apps:cloud:app-api:test
# 기존 compose를 내려 포트 8080, 55440, 6390을 비운 후 experiments에서
python3 run-failover.py S2  # S1·S2·S4·S5(S3 retry 는 제거돼 거부), 각 3분 부하, 60초 후 장애
# Redis Cluster 부분 장애(6노드 compose, docker-compose.cluster.yaml)
HOT=failed FAULT=kill python3 run-cluster.py C4  # C1 기본값 / C2 500ms·REJECT / C3 전역 서킷 / C4 샤드 서킷
```

Cluster 실험은 마스터 3+replica 3 에 `cluster-require-full-coverage=no`로, 한 샤드의 마스터·replica를 SIGKILL·`docker pause`·네트워크 분리(`FAULT`)하거나 마스터만 죽여 replica 승격(`KILL_REPLICA=false`)을 본다. k6가 Redis와 같은 CRC16으로 종목을 샤드별 10개 배치해 요청마다 샤드를 태깅하고, 인기 종목(트래픽 50%)을 장애 샤드/정상 샤드(`HOT`)에 둬 전역 실패율 67%/17%를 만든다. 결과는 장애 전·중·후 × 샤드 등급별 SLO 초과·DB 폴백·서킷 개방으로 집계한다. Sentinel 실험은 k6 투표 50rps, 별도 compose project를 사용한다. 종료 후 데이터 확인을 위해 컨테이너를 남기며 출력된 down 명령으로 정리한다. 결과는 experiments/runs에 기록한다. S4는 해당 프로젝트의 원래 master만 네트워크에서 분리한다. 매 실행 새 사용자·전망 ID를 사용한다.

측정: k6 samples의 응답 코드·투표 지연, threads.json의 최대 busy, fault.json과 sentinel.log의 +switch-master 차이, DB와 Redis 선택지별 차이, app.log의 재조정 delta/duration. Redis 재조정 전 차이는 장애 직후 재연결 트리거가 먼저 보정할 수 있으므로 **app.log delta를 함께 사용**한다. 합계만 같아도 선택지별 오류가 있을 수 있어 최종 판정은 각 선택지와 choices 해시까지 확인해야 한다. NFR-4는 fault부터가 아니라 failover 완료부터 복구 완료까지 판정한다. 과거 RESULTS.md와 load.py는 이전 전망·Redis 선저장 구현의 기록으로 이번 구현의 근거가 아니다.

실측과 미검증 범위: [ETF failover 검증 결과](experiments/FAILOVER_RESULTS.md).

## Redis Cluster 부분 장애 실측 (2026-09-20)

마스터 3+replica 3, `cluster-require-full-coverage=no`. 투표·조회·무관 요청 각 50rps 를 3분 넣고 60초 후 샤드 0 의 마스터·replica 에 장애를 주입했다. 정상 샤드 요청이 장애를 느끼는지를 주입 후 20초 구간의 투표 SLO(1초) 초과율과 조회 DB 폴백 비율로, 전파 원인을 Tomcat busy·HikariCP 대기(풀 10)로 쟀다. 인기 종목(트래픽 50%)을 장애 샤드에 두면 전역 실패율 67%, 정상 샤드에 두면 17% 다. 원본은 `experiments/runs/C*`(gitignore) 의 result.json 이고 표의 값은 hot=failed 기준이다.

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
- C1: 장애 슬롯 명령이 60초 동안 큐에 남고 그 대기가 AFTER_COMMIT 리스너에서 일어나 DB 커넥션 반환이 밀렸다. 풀 10개가 소진되자 정상 샤드 투표와 정적 `/`(28~40% SLO 초과)까지 밀렸고 커넥션 획득 타임아웃(30초)으로 DB 트랜잭션 생성 실패 111~528건이 났다. 부하 종료 후에도 버퍼가 남아 hot=healthy 런은 재조정 검산이 제한 시간 안에 끝나지 않았다(이후 구성은 전 런 30/30 일치).
- C2: SIGKILL 은 연결 종료를 감지해 즉시 거부되지만 pause·partition 은 TCP 가 살아 있어 요청마다 500ms 를 기다린다. 장애 샤드 투표 33rps × 0.5s 의 동시 대기가 풀 10개를 넘겨 스레드 200 까지 포화됐다.
- C3: 서킷이 대기를 끊어 SLO 초과는 0% 지만 전역 실패율이 판단 기준이라 정상 샤드 조회까지 폴백됐다. hot=healthy(17%)에서는 반대로 장애 샤드가 전건 실패해도 임계치 50% 에 못 미쳐 거의 열리지 않았다(우연 개방 150·204건, 폴백 9qps 는 전부 장애 샤드).
- C4: 서킷 이름이 슬롯 소유 마스터라 장애 샤드만 차단된다(`redis-shard-0` 만 개방, hot=healthy 도 0.7초 만에 차단). 폴백 qps 49→33 은 정상 샤드 조회가 Redis 로 돌아간 몫이다.

replica 승격(`KILL_REPLICA=false`, C4, 10런): 승격 7.0~9.0초, 정상 샤드 조회 폴백은 전 런 0%. 승격 뒤 장애 샤드 조회가 Redis 로 돌아오기까지는 0.5~2.7초 또는 6.9~7.4초의 두 무리로 갈리며 topology refresh 설정 유무와 무관했다 — 원인은 확인하지 않았다.

개념 설명은 [블로그](https://choyoungseo20.github.io/posts/redis-cluster/)에 있다.

DB 접근은 Spring Data JPA의 `VoteRepository extends JpaRepository<Vote, Long>`과 `@Query`를 사용한다. Vote는 auto-increment 대리 키 엔티티이고 사용자당 1행은 `UNIQUE(forecast_id, user_id)` 제약이 강제한다 — 신규/변경 판정은 SELECT 선검사가 아니라 네이티브 `INSERT ... ON DUPLICATE KEY UPDATE`(원자 upsert)로 한다. `DbFirstVoteService`의 트랜잭션이 커밋된 뒤 `VoteCacheListener`(`@TransactionalEventListener`, AFTER_COMMIT)가 Redis를 갱신한다 — 커밋 전 캐시 갱신(롤백 시 유령 표)이 구조적으로 불가능하다. 커밋 순서와 리스너 실행 순서는 요청 간에 직렬화되지 않으므로, 같은 사용자의 서로 다른 선택이 동시에 들어오면 DB 와 Redis 가 다음 재조정까지 어긋날 수 있다(같은 선택의 동시 재투표는 no-op 이라 무관). Flyway가 스키마를 관리하고 Hibernate는 validate만 수행한다. 기존 S2 부하 수치는 JDBC 구현에서 측정했으므로 JPA 성능 수치로 해석하지 않는다.

코드 스타일은 로컬 kuke-board/service/view를 참고했다. 서비스(`VoteService` 의 db-first 구현)는 트랜잭션 쓰기+이벤트 발행과 서킷 폴백 집계, event 패키지의 리스너가 커밋 후 캐시 갱신, JPA Repository는 쿼리 선언, VoteCountRepository는 Redis 명령을 담당한다. 참고 코드의 Redis 선저장·주기적 백업 방식은 적용하지 않았다.

`/ping`은 제거했다. 실험 시작 준비 확인은 기존 `/actuator/health`를 사용한다. 무관 요청 지연(NFR-1)은 부하 실험의 별도 k6 시나리오가 정적 `/`(50rps)로 측정한다 — actuator health는 Redis 인디케이터를 포함해 무관 요청으로 부적합하다.

## write-behind 모드 (실험용)

`vote.mode=write-behind`(env `VOTE_MODE`)로 켜면 투표가 DB 대신 Redis 에 먼저 기록되고, 스케줄러가 dirty 표를 DB 에 뒤늦게 반영한다. 기본값(`db-first`, 미설정)은 위 구조 그대로다. `VoteService` 는 인터페이스이고 모드별 구현(`DbFirstVoteService` / `WriteBehindVoteService`)이 `@ConditionalOnProperty` 로 하나만 뜬다. 실험용 택일이라 조회 `counts()` 는 두 구현에 같은 코드로 중복돼 있다 — 실험 종료 후 한쪽을 지운다. 단일 인스턴스·Sentinel 전용이다: 인스턴스가 둘이면 flush 가 겹쳐 오래된 배치가 최신 표를 덮을 수 있고(소유권·버전 검사 없음), Cluster 에선 전역 `vote:dirty-forecasts` 와 전망별 키가 다른 슬롯이라 다중 키 Lua 가 CROSSSLOT 으로 실패하므로 기동 시 거부한다. dirty 배치 읽기(HSCAN)도 `VOTE_REDIS_READ_FROM=MASTER`(기본) 전제다 — replica 읽기(S5)와 조합하면 지연 replica 의 낡은 dirty 가 최신 DB 표를 덮을 수 있다.

- 쓰기: `WriteBehindVoteService.vote()` 가 `VoteBufferRepository` 의 Lua 한 번으로 count·choices 갱신 + `vote:{fid}:dirty` 해시·`vote:dirty-forecasts` 집합 마킹을 한다. DB 트랜잭션을 열지 않는다. Redis 실패는 서킷 폴백이 503(`VOTE5030`)으로 즉시 반려한다 — DB 우회는 없다.
- flush: `VoteFlusher` 가 `vote.flush.interval-ms`(기본 3000, 첫 실행도 한 주기 뒤) 마다 전망별로 `vote.flush.batch-size`(기본 500) 만큼 HSCAN 으로 읽어 `forecast_vote` 에 다중행 upsert 하고, 읽었던 choice 와 같은 항목만 dirty 에서 지운다. 한 주기에 전망당 한 배치. 전망 하나의 실패는 다른 전망을 막지 않는다.
- warm: `VoteWarmer` 가 기동 완료·Lettuce 재연결·`vote.warm.interval`(기본 PT5M) 마다 DB 표를 `HSETNX` 로 병합한다 — Redis 에 없는 사용자만 채우고 살아 있는 표(미flush dirty 포함)는 덮지 않는다. 페일오버로 낡아진 살아 있는 표는 되돌리지 않는다(검산으로 크기만 측정하는 것이 실험 설계).
- 사라지는 것(db-first 조건부 빈): `VoteReconciler`·`POST /api/v1/admin/votes/reconcile`(404)·`VoteCacheListener`. 조회 `counts()` 는 같은 로직이지만, Redis 폴백의 `source=db` 는 flush 지연분만큼 낡은 값이다.
- 메트릭: `vote.flush.size`·`vote.flush.duration`·`vote.flush.failures`·`vote.dirty.size`·`vote.warm.loaded`·`vote.warm.failures`·`vote.redis.write.failures`.

```sh
# 이 디렉터리에서: override 를 겹쳐 기동
docker compose -f docker-compose.yaml -f docker-compose.write-behind.yaml up --build -d
# experiments 에서: VOTE_MODE 가 override 를 자동으로 덧붙인다. USER_POOL 은 재투표 축(같은 사용자가 라운드마다 다른 choice) —
# 500 미만이면 같은 사용자의 요청이 겹쳐 ack 순서와 커밋 순서가 달라지므로 거부한다.
VOTE_MODE=write-behind USER_POOL=3000 python3 run-failover.py S2
```

write-behind 실행은 DB snapshot 전에 `vote:dirty-forecasts` 가 빌 때까지(최대 60초) 기다린다. 판정은 기존 선택지별 합계·`HLEN`·중복행에 더해 사용자별 최종 choice 를 k6 ack(200 의 마지막 시각)·DB·Redis 세 방향으로 대조해 `per-user.json` 에 남긴다. `ack_db_mismatch` 가 유실 측정값이며, `drain_complete`·`db_redis_mismatch`·`ack_db_mismatch` 는 `checks.json` 과 종료 코드에 반영된다. 테스트는 `vote.mode` 별 컨텍스트(기본에서 write-behind 빈 부재·write-behind 에서 재조정기 부재)와 버퍼 Lua·flush·warm·503 경로를 Testcontainers 로 검증한다.
