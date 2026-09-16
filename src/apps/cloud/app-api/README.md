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

재조정은 시작 후 1초, 이후 5분 주기, Lettuce 연결 활성화, 관리자 호출(200, 비동기)로 실행된다. 관리자 토큰 미설정 시 수동 호출은 403이다. 동시에 들어온 트리거는 합친다. DB snapshot의 count(선택지별 집계)와 choices(사용자→선택 해시)를 단일 Lua로 교체한다. snapshot 이후 투표와의 경합은 다음 주기에 보정한다. 대규모 전망의 전체 사용자 목록을 메모리와 Lua로 처리하므로 실험 규모를 넘어서는 성능은 별도 검증해야 한다.

기본값은 REJECT_COMMANDS / 500ms / timeoutOptions=true / MASTER다. 환경 변수는 application.yaml과 compose에 외부화했다. S3 실험용 재시도(`VOTE_REDIS_RETRY`)는 실험 종결 후 제거했다 — 실측·해석은 experiments/FAILOVER_RESULTS.md 기록이 정본이다. Lua가 choices 해시의 이전 선택과 비교해 같으면 no-op, 다르면 이전 카운터 -1·새 카운터 +1 하므로 동일 투표 재실행은 중복 집계되지 않는다.

Redis 접근에는 Resilience4j 서킷 브레이커(인스턴스 `redis`)를 얹었다. 쓰기 서킷은 `VoteCountRepository.vote()`에 있어 열려도 DB 저장은 막지 않고 폴백이 실패를 삼킨다(메트릭+로그). 읽기 서킷은 `VoteService.counts()`에 있고 폴백이 DB 집계로 대체한다. 재조정 `replace()`는 복구 경로라 서킷 밖이다. 서킷이 열리면 Lettuce 타임아웃 대기 없이 즉시 폴백한다(REJECT가 못 잡는 "연결은 살아 있는데 느려지는" 유형의 이중 방어).

클라이언트 timeout 옵션 의미는 [Lettuce 공식 문서](https://github.com/redis/lettuce/blob/main/docs/advanced-usage/client-options.md)를 참고했다. 500ms는 커맨드 제한이며, 전체 HTTP 지연·5분 복구는 부하 실험으로 판정해야 한다.

```sh
# src 디렉터리에서: 실제 MySQL·Redis Docker 컨테이너 필요
./gradlew :apps:cloud:app-api:test
# 기존 compose를 내려 포트 8080, 55440, 6390을 비운 후 experiments에서
python3 run-failover.py S2  # S1~S5, 각 3분 부하, 60초 후 장애
```

실험은 k6 투표 50rps, 별도 compose project를 사용한다. 종료 후 데이터 확인을 위해 컨테이너를 남기며 출력된 down 명령으로 정리한다. 결과는 experiments/runs에 기록한다. S4는 해당 프로젝트의 원래 master만 네트워크에서 분리한다. 매 실행 새 사용자·전망 ID를 사용한다.

측정: k6 samples의 응답 코드·투표 지연, threads.json의 최대 busy, fault.json과 sentinel.log의 +switch-master 차이, DB와 Redis 선택지별 차이, app.log의 재조정 delta/duration. Redis 재조정 전 차이는 장애 직후 재연결 트리거가 먼저 보정할 수 있으므로 **app.log delta를 함께 사용**한다. 합계만 같아도 선택지별 오류가 있을 수 있어 최종 판정은 각 선택지와 choices 해시까지 확인해야 한다. NFR-4는 fault부터가 아니라 failover 완료부터 복구 완료까지 판정한다. 과거 RESULTS.md와 load.py는 이전 전망·Redis 선저장 구현의 기록으로 이번 구현의 근거가 아니다.

실측과 미검증 범위: [ETF failover 검증 결과](experiments/FAILOVER_RESULTS.md).

DB 접근은 Spring Data JPA의 `VoteRepository extends JpaRepository<Vote, Long>`과 `@Query`를 사용한다. Vote는 auto-increment 대리 키 엔티티이고 사용자당 1행은 `UNIQUE(forecast_id, user_id)` 제약이 강제한다 — 신규/변경 판정은 SELECT 선검사가 아니라 네이티브 `INSERT ... ON DUPLICATE KEY UPDATE`(원자 upsert)로 한다. VoteService의 트랜잭션이 커밋된 뒤 `VoteCacheListener`(`@TransactionalEventListener`, AFTER_COMMIT)가 Redis를 갱신한다 — 커밋 전 캐시 갱신(롤백 시 유령 표)이 구조적으로 불가능하다. Flyway가 스키마를 관리하고 Hibernate는 validate만 수행한다. 기존 S2 부하 수치는 JDBC 구현에서 측정했으므로 JPA 성능 수치로 해석하지 않는다.

코드 스타일은 로컬 kuke-board/service/view를 참고했다. 서비스는 트랜잭션 쓰기+이벤트 발행, event 패키지의 리스너가 커밋 후 캐시 갱신, JPA Repository는 쿼리 선언, VoteCountRepository는 Redis 명령을 담당한다. 참고 코드의 Redis 선저장·주기적 백업 방식은 적용하지 않았다.

`/ping`은 제거했다. 실험 시작 준비 확인은 기존 `/actuator/health`를 사용한다. 무관 요청 지연(NFR-1)은 부하 실험의 별도 k6 시나리오가 정적 `/`(50rps)로 측정한다 — actuator health는 Redis 인디케이터를 포함해 무관 요청으로 부적합하다.
