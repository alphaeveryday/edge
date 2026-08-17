# publication-api 다중 인스턴스 캐시 실험

publication-api 를 4대까지 띄우고 캐시 모드(none·caffeine·redis·two-level)를 갈아끼우며
조회 폭증 시 지연·DB 부하가 어떻게 갈리는지 측정한다.

**로컬 탐색 단계다** — 여기서 나온 수치로 무엇을 채택할지 정하는 것이 목적이고, 채택 결과만
코드·문서에 반영한다. 실험 산출물(`results/`)은 커밋하지 않는다.

측정 대상은 nginx(`:18100`) 뒤의 publication-api 다. 루트 스택의 mock-broker 2홉 경로는 끼우지 않는다
(상위 문서 [tests/loadtest/README.md](../README.md) 의 baseline 절차와 같은 원칙).

## 포트

루트 `docker-compose.yml` 스택과 전부 어긋나게 잡아 **동시에 띄울 수 있다**.

| 포트 | 대상 |
|---|---|
| 18100 | nginx — k6 가 때리는 진입점 |
| 18101~18104 | api-1 ~ api-4 직결(1대 실험·인스턴스별 확인용) |
| 55434 | postgres (루트 온프렘 55433 과 분리) |
| 63790 | redis |
| 19090 | Prometheus |
| 13000 | Grafana (익명 Admin — 로컬 전용) |

## 기동

```bash
cd tests/loadtest/publication
docker compose -p edge-pubcache up --build -d
```

첫 실행은 gradle 빌드로 느리다. 컨테이너 이미지에 curl·wget 이 없어 compose healthcheck 를 두지
않았다 — 기동 확인은 host 포트 폴링으로 한다.

```bash
curl -fsS localhost:18101/actuator/health
```

## 데이터 준비

```bash
scripts/prepare-data.sh
```

게시 데이터를 넣고 나면 **조회가 200 이어야 측정 의미가 있다.**

```bash
curl -i -H "X-Customer-Hash: smoke" -H "X-Channel: MTS" \
  "localhost:18100/api/v1/explanations/069500"
```

- 204 만 나오면 캐시가 채워질 원본이 없다는 뜻 — 히트율도 쓰기 부하도 측정되지 않는다.
- 404 는 `PUBLICATION_KNOWN_TICKERS` 와 종목 불일치.

## 실험 축 갈아끼우기

캐시 축은 전부 env 다. 값을 바꾸고 `up -d` 하면 api 컨테이너만 재생성된다.

```bash
CACHE_MODE=redis docker compose -p edge-pubcache up -d
CACHE_MODE=two-level CACHE_L2_TTL=30s CACHE_L2_JITTER=5s docker compose -p edge-pubcache up -d
```

| env | 기본값 | 의미 |
|---|---|---|
| `CACHE_MODE` | `caffeine` | `none` · `caffeine` · `redis` · `two-level` |
| `CACHE_TTL` | `3s` | L1(로컬) TTL |
| `CACHE_L2_TTL` | `10s` | L2(Redis) TTL |
| `CACHE_L2_JITTER` | `0s` | L2 만료 분산 폭 |
| `REQUEST_METRIC_ENABLED` | `true` | 요청 메트릭 쓰기 |

**1대 실험**은 인스턴스를 줄이고 nginx 를 우회한다 — LB 홉이 지연에 섞이지 않게.

```bash
docker compose -p edge-pubcache stop api-2 api-3 api-4
# k6 BASE_URL=http://localhost:18101
```

`REQUEST_METRIC_ENABLED` 를 `false` 로 둔 것이 **read suite**, 기본값 그대로가
**full suite** 다. 이 토글의 **기본값은 true 이며 이 실험이 그 계약을 바꾸지 않는다** — 끄는 것은
읽기 경로만 분리해 보기 위한 실험 조작이지 운영 프로필 변경이 아니다. (노출 로그 토글은 없다 —
ADR-0053 은퇴로 앱에 소비자가 없어, 초기 하네스가 걸었던 `EXPOSURE_ENABLED` 는 no-op 이었다.
read/full 의 실측 차이는 전부 요청 메트릭 쓰기 축이다.)

## suite

| id | mode | 인스턴스 | 시나리오 | 확인할 것 |
|---|---|---|---|---|
| B0 | none | 1 | hot-key | 캐시 없는 단일 인스턴스 기준선 |
| B1 | none | 4 | hot-key | 스케일아웃만으로 얼마나 가는가 (DB 가 먼저 막히는지) |
| C1 | caffeine | 1 · 4 | hot-key | 로컬 캐시 이득, 4대일 때 인스턴스별 중복 미스 |
| R1 | redis | 4 | hot-key | 공유 캐시로 중복 미스가 걷히는가, 대신 붙는 왕복 비용 |
| T1 | two-level | 4 | hot-key | L1+L2 합산 이득이 R1·C1 을 넘는가 |
| E1 | 전 모드 | 4 | cold-start | 빈 캐시에서 첫 요청 폭이 DB 를 때리는 크기 |
| E2 | caffeine · two-level | 4 | synchronized-expiry | TTL 동시 만료로 미스가 한 점에 몰리는지 |
| E3 | two-level | 4 | synchronized-expiry | `CACHE_L2_JITTER` 가 E2 의 스파이크를 흩는가 — 단일 hot-key 한정, 다종 키 동시 만료는 사정거리 밖(일지 참조) |
| E4 | 채택 후보 | 3→4 | hot-key | api-4 정지→재합류 — 합류 인스턴스의 콜드 미스를 L2 가 흡수하는가 (1·2대 처리량은 미측정) |
| E5 | redis · two-level | 4 | hot-key | `docker compose stop redis` — L2 장애 시 죽는지 원본으로 떨어지는지 |
| E6 | 채택 후보 | 4 | publication-change | 게시·차단 반영 지연(캐시가 낡은 응답을 얼마나 오래 붙드는가) |
| F1 | 채택 후보 | 4 | hot-key (full) | 쓰기 토글 켠 채 — 캐시로 못 가리는 잔여 쓰기 부하 |

시나리오는 `k6/{hot-key,synchronized-expiry,cold-start,publication-change}.js` (공통 코드는 `k6/lib.js`).

## 실행·수집

```bash
scripts/run-matrix.sh        # suite 격자 실행 — run 마다 collect-result.sh 를 자동 호출한다
scripts/collect-result.sh <run_id> <start_epoch> <end_epoch>   # 수동 재수집 시에만 직접 실행
```

관측: Grafana http://localhost:13000 (대시보드 `publication-cache`), Prometheus http://localhost:19090.
scrape 간격 5s — 짧은 실험 창에서도 초 단위 파동이 남게 잡았다.

## 정리

```bash
docker compose -p edge-pubcache down -v
```

DB 볼륨을 두지 않았으므로 `down` 만으로도 데이터는 사라진다. 실험 간 상태가 새면 비교가
무의미해지기 때문에 그렇게 잡았다 — 매 실행은 `prepare-data.sh` 부터 다시 시작한다.
