# ETF Redis failover 검증 — 2026-09-13

로컬 수정본, 커밋·push 없음. Spring Boot 4.1.0 / Lettuce 7.5.2 / MySQL 8.4 / Redis 7, master 1 + replica 2 + Sentinel 3. Docker Desktop 단일 호스트 실험이며 운영 성능의 보장은 아니다.

## 시나리오 결과

| 시나리오 | 실행 | 결과 |
|---|---|---|
| S1 DEFAULT / 5초 | 실행 | 실패 0이지만 Tomcat 스레드 200/200 포화·p99 5,009ms·부하 유실 139건. 아래 실측 참조 |
| S2 REJECT / 500ms | 실행, 통과 | 아래 실측 참조 |
| S3 S2 + retry | 실행, 통과 | 재시도 시도 1,070회 포함 중복 집계 0. 아래 실측 참조 |
| S4 네트워크 분리 | 미실행 | runner에 장애 주입 경로 제공 |
| S5 replica preferred | 미실행 | 설정 전환 제공. 승격 직후 replica stale read의 별도 측정 필요 |

> 아래 S2 수치는 JPA 전환 이전 JDBC 구현의 기록이다. JPA 전환 후 통합 테스트는 4개 통과(skipped 0)했다. Redis 호출 전에 다른 DB 트랜잭션에서 표를 조회해 선행 커밋도 검증했다. 부하 실험은 재실행하지 않았다.

> 이후 `/ping`을 제거했다. 아래 ping 측정값은 당시 구현의 기록이며 현재 실험 스크립트는 투표 부하만 실행한다.

## S1·S3 실측 — 2026-09-13 (JPA 구현)

`S1-20260913-015030`, `S3-20260913-015635`. 투표 50rps 3분, 부하 60초 후 master SIGKILL. S2와 달리 JPA 전환·응답 공통 포맷 적용 후 빌드에서 측정했으므로, 정책 간 직접 비교는 같은 빌드인 S1 vs S3 가 기준이다.

| 측정 | S1 DEFAULT / 5s / timeoutCommands=false | S3 REJECT / 500ms / retry 1회 |
|---|---|---|
| 요청 결과 | 8,861건 전부 201, **부하 유실(dropped) 139건** | 9,000건 전부 201, 유실 0 |
| 투표 지연 | p99 **5,009ms**, 최대 5,352ms | p99 **6.62ms**, 최대 78.05ms |
| `tomcat.threads.busy` 최대 | **200 (풀 포화, 69.7~74.8초 구간)** | **2** |
| Sentinel `+switch-master` | 장애 후 6.37초 | 장애 후 7.31초 |
| Redis 계층 실패 로그 | 587건 (5초 블로킹 후 타임아웃) | 1,070건 (시도 수, 즉시 실패×최대 2회) |
| 재조정 복구 | delta 586 반영, 최종 DB=Redis | delta 535 반영, 최종 DB=Redis |
| DB 중복 / voted 정합 | 0 / 8,861=합계 | 0 / 9,000=합계 |

- S1: 모든 요청이 결국 201인 이유는 DB-first 쓰기 + Redis 실패 무시 설계다. 대신 장애 구간에서 Redis 명령이 커맨드 타임아웃 5초까지 블로킹돼 Tomcat 스레드 풀(200)이 완전 포화됐고, k6 VU 300으로도 50rps를 못 받아 139 iteration 이 유실됐다. 이 상태에선 Redis 를 안 쓰는 무관 API도 스레드가 없어 함께 멈춘다 — 장애 전파의 실측.
- S3: 즉시 실패(REJECT/500ms)라 스레드 점유 최대 2, HTTP 지연은 평시 수준. 장애 구간 재시도 시도 1,070회(실패 요청당 최대 2회)에도 SADD 가드 멱등 Lua 로 중복 집계 0 — DB 4,500/4,500 = Redis = voted 9,000.
- k6 종료 코드: S1 은 dropped_iterations 임계 위반으로 99, S3 는 0. runner 정합 검증은 둘 다 통과(`checks.json` db_redis_equal=true).

### 장애 구간 phase 분리 (samples.json 후처리)

HTTP 상태 실패는 두 시나리오 모두 0이므로(DB-first 설계의 의도), 실패는 클라이언트 SLO 초과로 정의해 fault 주입 시각(`fault.json`) 기준 before / during(주입 후 20초) / after 로 나눠 셌다.

| during 구간 | S1 | S3 |
|---|---|---|
| 요청 수 | 861 (유실 139건은 미포함) | 1,000 |
| SLO 1초 초과 | **586건 (68.1%)** | **0건 (0%)** |
| SLO 3초 초과 | 499건 (58.0%) | 0건 |
| 구간 최대 지연 | 5,352ms | 10ms |

before / after 구간은 두 시나리오 모두 SLO 초과 0 (S1 after max 14ms) — 영향이 장애 구간에 국한됨을 확인했다. 교차 검증: S1 의 SLO 초과 586건 = Redis 쓰기 실패 로그 587건(±1) = 재조정 delta 586건으로 세 독립 측정이 수렴한다.

### 무관 요청 실측 (재실행 `S1-20260913-021816` · `S3-20260913-022446`)

무관 API 전파를 직접 재기 위해 k6 에 무관 요청 시나리오(정적 `/` 50rps — Redis·DB 무관, 같은 Tomcat 풀. actuator health 는 Redis 인디케이터 포함이라 부적합)를 추가하고 S1·S3 를 재실행했다. 투표 측 수치는 1차 실행을 재현했다(S1 during SLO 초과 67.9% vs 1차 68.1%, S3 0% 유지, 스레드 200 vs 3).

| during 구간 (주입 후 20초) | S1 | S3 |
|---|---|---|
| 무관 요청 p99 | **972.1ms** (최대 1,028ms, SLO 1s 초과 4건) | **2.5ms** (최대 8.8ms) |
| 무관 요청 before 기준선 | p99 2.4ms | p99 2.7ms |
| 무관 요청 실패(비 200) | 0 | 0 |

S1 의 무관 요청은 p50 1.1ms 로 다수는 정상이었으나 스레드 풀 포화 구간(약 5초)에 걸린 요청이 최대 1초까지 대기했다 — p99 기준 약 400배 악화. S3 는 장애 구간에도 평시와 동일했다.

## S2 실측

- 실행: `S2-20260913-011022`, 투표 50rps + ping 50rps, 3분. 부하 시작 약 60초 후 SIGKILL.
- 투표 **9,001건 모두 201**, 409·5xx·전송 실패 0. k6의 실행 경계에서 명목 9,000건보다 1건 더 실행됐다. 누락 iteration 0.
- 투표 지연 p99 **7.18ms**, 최대 **303.76ms** — NFR-3 통과.
- ping p99: 장애 전 **2.55ms**, 장애 주입~첫 `+switch-master` 구간 **1.94ms**(315개 표본), 장애 이후 전체 **2.19ms** — NFR-1 통과.
- Sentinel 최초 `+switch-master`: 장애 후 **6.32초**. Sentinel별 관측 시각은 서로 다르며 가장 이른 로그를 기준으로 했다.
- `tomcat.threads.busy`: 1초 간격 179개 표본에서 최대 **3**. 샘플 사이 순간 최대치는 측정하지 않았다.
- 승격 후 약 **4.55초**에 DB snapshot 기준 미반영 **539건**(AGREE 269, DISAGREE 270)을 재조정, 작업 9ms. 약 **10.04초** 후 재조정 delta는 선택지별 모두 0. NFR-4 범위 안에서 복구를 관측했다.
- 부하 종료 후 DB = Redis: AGREE **4,500**, DISAGREE **4,501**, 합계 **9,001**. voted 집합 **9,001**, DB 중복 레코드 **0** — NFR-2 통과.
- k6 종료 코드 0, runner의 DB·Redis·voted 비교와 지연 검증 통과.

[원본 summary](runs/S2-20260913-011022/summary.json), [검증](runs/S2-20260913-011022/checks.json), [DB](runs/S2-20260913-011022/db.tsv), [Redis](runs/S2-20260913-011022/after-reconcile.json), [애플리케이션 로그](runs/S2-20260913-011022/app.log), [Sentinel 로그](runs/S2-20260913-011022/sentinel.log). 원본은 로컬 runs 디렉터리에 보존하며 Git에서 제외한다. 실행한 실험 컨테이너·네트워크는 정리했다.

첫 실행 `S2-20260913-010618`은 k6 VU마다 Date.now()를 기준으로 ID를 생성해 ID가 겹쳤다. 409가 987건 발생한 **실험 스크립트 오류**로, 신규 투표 실험의 근거에서 제외한다. 수정본은 모든 VU에 동일한 시작값과 시나리오 전체의 고유 iterationInTest를 사용한다([k6 공식 설명](https://grafana.com/docs/k6/latest/javascript-api/k6-execution/)).

## 서킷 브레이커 도입 검증 (`S2-20260913-033006`)

Resilience4j 서킷(쓰기=repository, 읽기=service, replace 는 서킷 밖) 도입 후 같은 조건(master SIGKILL) 재실행. 기대대로 **수치 불변**: during 투표 p99 7.2ms·SLO 초과 0%·무관 요청 p99 2.4ms·스레드 최대 3·유실 0·최종 DB=Redis. 서킷은 실제로 열렸다 — `CallNotPermittedException` 1,196건(open 동안 Redis 호출 자체 생략), 쓰기 폴백 638건 ≈ 재조정 delta 637(±1)로 검산 일치. 열린 서킷이 재조정을 막지 않는 것(replace 미보호 설계)도 복구 완료로 확인.

## 계약 개편 후 재실측 (`S1-20260913-205812` · `S2-20260913-210503`) — 현행 정본

3지선다(BUY/HOLD/SELL)·재투표=변경(choices 해시 Lua)·네이티브 upsert·`@TransactionalEventListener(AFTER_COMMIT)` 캐시 갱신·서킷 브레이커가 모두 들어간 현행 빌드로 S1(DEFAULT/5s)·S2(REJECT/500ms)를 재실행했다. retry 축(구 S3)은 코드 제거로 종결. **이 섹션이 현행 코드와 대응하는 정본이고, 위 구 섹션들은 당시 빌드의 역사적 기록이다.**

| during (주입 후 20초) | S1 DEFAULT/5s | S2 REJECT/500ms |
|---|---|---|
| SLO 1초 초과 | **352건 (39.7%)** | **0건 (0%)** |
| 투표 p99 / 최대 | 6,803ms / 9,817ms | 7.4ms / 11.0ms |
| 무관 요청 p99 | 346.0ms | 2.2ms |
| `tomcat.threads.busy` 최대 | 195 | 3 |
| 부하 유실 | 118건 | 0건 |
| Redis 실패 로그 ≈ 재조정 delta | 697건 | 639건 ≈ 640(±1) |
| 최종 DB=Redis | 통과 | 통과 |

- 구 실측 대비 S1이 개선(SLO 초과 68.1%→39.7%, 무관 p99 972ms→346ms)된 것은 **서킷 브레이커 단독 효과**다 — DEFAULT 정책에서도 실패율 임계 도달 후 서킷이 열려 후속 호출이 즉시 폴백된다. 그래도 열리기 전까지의 블로킹으로 스레드 195·유실 118이 남아, 즉시 실패 정책의 우월성은 유지된다.
- S1 최대 지연이 9.8초로 커진 것(9건)은 Redis 5초 블록에 대기가 겹친 꼬리로 보인다. AFTER_COMMIT 리스너는 커밋 후·커넥션 반환 전에 실행되므로 블로킹이 커넥션 점유와 겹치는 구조인데, Hikari 풀 고갈 에러는 0건이라 단정하지 않는다.
- 재투표=변경 계약에서도 검산(실패 로그 ≈ 재조정 delta)과 최종 정합은 그대로 성립했다.

## 설정과 남은 검증

현재 기본값은 REJECT_COMMANDS / 500ms / timeoutOptions=true / MASTER / retry=false다. S1 vs S3 실측으로 즉시 실패 정책의 우월성(스레드 200→2, p99 5,009ms→6.62ms, 부하 유실 139→0)을 같은 빌드에서 확인했다. S3의 “중복 집계 관측이 성공”은 FR-1의 멱등 Lua와 충돌하므로 재시도 중복 방지를 검증하는 것으로 정정했다.

`./gradlew :apps:cloud:app-api:test`: 통합 테스트 **3개 통과, skipped 0**. 동시 16회 중복 요청, 선택지 변경 거부, Lua 재실행, SCRIPT FLUSH 후 EVAL fallback, Redis pause 중 DB 저장·fallback·ping, 데이터 유실 및 연결 단절 후 자동 복구, 관리자 권한과 수동 재조정을 검증했다. 단독 Redis 컨테이너 재시작은 호스트 포트 변경으로 첫 테스트가 실패하여 데이터 유실+CLIENT KILL로 수정했으며, 실제 master 종료·Sentinel 승격은 위 S2에서 별도로 검증했다.

대규모 voted 집합의 Lua 실행 시간·메모리, 다중 앱 인스턴스 간 재조정 경합, S4·S5의 실험 결과는 미검증이다. 진행 중 투표와 DB snapshot의 경합은 PRD대로 다음 주기에 보정한다. 기존 전망 API 및 PostgreSQL 데이터의 자동 이관은 제공하지 않는다.
