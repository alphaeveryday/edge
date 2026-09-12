# 장애 주입 실험 결과 — 1회전 (2026-09-12)

PRD 1단계(HA·전체 장애 복구)의 초기 설정값 기준 측정. 부하는 `load.py`(투표 PUT 20rps,
유저 100명 무작위 동의/반대), 대상은 단일 전망.

## 설정값 (이 회전의 기준)

| 계층 | 설정 | 값 |
|---|---|---|
| Sentinel | down-after-milliseconds | 5000 |
| Sentinel | failover-timeout | 10000 |
| Sentinel | quorum | 2 (sentinel 3대) |
| Client | command timeout | 500ms |
| Client | connect timeout | 1s (실험 중 추가 — 아래 발견 1) |
| 서킷 | 실패율/창/최소호출 | 50% / 20 / 10 |
| 서킷 | open 대기 / half-open 시험 | 5s / 3건 |
| 재조정 잡 | 주기 | 10s |

## 층 1 — master SIGKILL (페일오버)

20rps 부하 중 t=20s에 master SIGKILL. 총 1,195건.

| 지표 | 값 |
|---|---|
| 투표 성공률 (전 구간) | **100.00%** (비200 0건) |
| kill 전 기준선 | p50 22.1 / p95 32.0 ms |
| 느린 요청 | kill+0.14s 10건(≈655ms, 명령 타임아웃) + kill+5.8s 3건(≈520ms, half-open 시험) — 총 13건 |
| 서킷 열림 (저하 전환) | kill+0.77s |
| sentinel 장애 판정 (+odown) | kill+5.2s (down-after 5000ms와 일치) |
| replica 승격 (+switch-master) | kill+6.4s |
| Lettuce 새 master 재연결 | kill+6.55s |
| 서킷 CLOSED (Redis 경로 복귀) | **kill+11.4s** |
| 회복 후 지연 | p50 10.2 / p95 18.6 ms |
| 복구 후 집계 오차 | **0** (SCARD 62/38 = DB COUNT 62/38, rebuild 90건 전부 DONE) |

무마스터 구간(감지→승격) 동안 요청은 서킷이 열려 있어 DB 경로로 전부 성공했고,
지연 페널티는 서킷이 열리기 전 창의 10여 건(명령 타임아웃 500ms)에 국한됐다.
Redis 경로 복귀(kill+11.4s)가 승격(+6.4s)보다 늦은 것은 서킷 open 대기 5s 때문 —
빠른 복귀를 원하면 open 대기를 줄이는 대신 시험 호출 실패가 늘어나는 상충이 있다.

## 층 2 — Redis 전체 중지 30초 (저하 모드)

20rps 부하 중 t=20s에 데이터 노드 3대 전부 stop, t=50s에 재기동(+sentinel 재기동).
총 1,780건.

| 구간 | 성공률 | p50 / p95 (ms) |
|---|---|---|
| 정상 (중지 전) | 100.00% | 11.4 / 18.8 |
| **중지 중 (저하 모드)** | **100.00%** | **6.7 / 19.0** (max 611 — 서킷 열리기 전 10건) |
| 재기동 후 | 100.00% | 7.8 / 149.6 (회복 과도기 half-open 시험 창) |

| 지표 | 값 |
|---|---|
| 서킷 열림 | stop+0.78s |
| 서킷 최종 CLOSED | 재기동+10.3s |
| 복구 후 집계 오차 | **0** (SCARD 50/50 = DB COUNT 50/50, rebuild 101건 전부 DONE) |

저하 모드의 p50(6.7ms)이 정상(11.4ms)보다 낮다 — Redis 왕복 없이 DB COUNT 만 하기
때문. 저하 모드의 비용은 평시 지연이 아니라 DB 로 몰리는 집계 부하(이 부하 수준에서는
관측 불가)와 서킷 열리기 전·회복 창의 타임아웃 스파이크다.

## 발견

1. **connect timeout 미설정이 스레드를 분 단위로 블록** — 1차 시도에서
   `spring.data.redis.timeout`(명령 타임아웃 500ms)만으로는 부족했다. 죽은 노드
   IP로의 TCP 연결 수립은 명령 타임아웃의 적용 밖이라 OS 재시도에 맡겨지고,
   재조정 잡 스레드가 약 8분 블록되며 서킷이 계속 OPEN 에 머물렀다(HTTP 스레드도
   같은 경로로 잠식돼 부하 클라이언트까지 멈춤). `connect-timeout: 1s` 추가 후
   동일 시나리오에서 Redis 경로 복귀가 kill+11.4s 로 줄었다. PRD 설정표의
   "connect·command timeout" 상충 항목의 실측 사례.
2. **성공률 방어의 주역은 서킷이 아니라 DB 관문 구조** — 서킷이 열리기 전 창에서도
   요청은 실패하지 않고 느려질 뿐이다(명령 타임아웃 → rebuild 기록 → DB COUNT 응답).
   서킷의 역할은 그 500ms 페널티를 이후 요청에서 제거하는 것.
3. **재기동 직후 서킷이 한 번 더 열림** (층 2, CLOSED→OPEN→CLOSED) — 재기동 직후
   일부 명령 실패(노드 기동 과도기)로 재열림. 최종 안정까지 재기동+10.3s.

## 비교 회전 — 한 축씩 조정 (층 1, 동일 시나리오 반복)

| | 회전 1 (기준) | 회전 2 | 회전 3 |
|---|---|---|---|
| down-after | 5000ms | **2000ms** | 2000ms |
| 서킷 open 대기 | 5s | 5s | **2s** |
| 장애 판정 (+odown) | kill+5.2s | **kill+2.2s** | kill+2.1s |
| replica 승격 | kill+6.4s | **kill+3.4s** | kill+4.4s |
| Redis 경로 복귀 | kill+11.4s | **kill+5.8s** | kill+5.5s |
| 투표 성공률 | 100% | 100% | 100% |
| 오탐 페일오버 | 0 | 0 | 0 |
| 복구 후 집계 오차 | 0 | 0 | 0 |

- **down-after 5000→2000ms 가 지배적 개선**: Redis 경로 복귀 11.4s → 5.8s. 이 부하
  수준(20rps)에선 오탐 없음 — 더 낮추는 것은 원격/실환경 지연 변동을 본 뒤에.
- **서킷 open 대기 5→2s 는 이득 미미**(5.8→5.5s): 복귀의 하한이 승격 시간(3.4~4.4s)이라
  open 대기를 줄여도 half-open 시험만 한 번 더 실패한다. 5s 유지가 무난.

## 발견 4 — sentinel 이중 정체성: 승격 노드가 자기 자신의 replica 로 강등

상세 사고 기록: [sentinel-dual-identity.md](sentinel-dual-identity.md)

비교 회전 중 **`monitor` 를 hostname 으로 등록한 노드가 승격 대상이 된 2개 런에서 모두**
승격 0.9~2초 뒤 sentinel 이 그 노드에 `REPLICAOF <자기 주소>` 를 보내 자기 자신의
replica 로 강등시키는 사고가 재현됐다 (replica-N 컨테이너가 승격될 땐 발생하지 않음).

- 원인: monitor 라인의 hostname(redis-master)과 INFO 로 발견된 IP 가 같은 노드의
  **두 정체성으로 이중 등록** → 승격은 한쪽 정체성으로 처리되고, slaves 목록에 남은
  다른 쪽 정체성에 reconf-slaves 가 날아간다. `announce-hostnames` 제거만으로는
  해소되지 않았다(REPLICAOF 가 IP 로 와도 자기 자신).
- 증상 체인: 승격 직후 자기 강등 → 앱 명령 전부 실패(서킷 ~30초 플래핑) → sentinel 이
  새 master 를 다시 down 판정 → 2차 페일오버로 자가 수습. **이 동안에도 투표 성공률은
  100%** — DB 관문·저하 경로가 방어(발견 2의 극단 사례).
- 수정: sentinel 기동 시 서비스명을 **IP 로 해석해 monitor 에 박음**(entrypoint 의
  `getent hosts`). 수정 후 같은 노드를 강제 승격(잔여 후보 1개 구성)해 재현 소멸·40초
  이상 안정 유지 확인.

## 미조정 항목 (다음 회전 후보)

- down-after 2000ms 미만 — 실환경(네트워크 지연 변동) 기준으로만 의미
- 층 2 회복 과도기 p95(149.6ms) 축소 — half-open 시험을 부하 요청이 아닌 별도 프로브로
- 재조정 잡 주기 10s 단축 vs DB 부하 — 스탬피드 실험(2단계 판정)과 함께
