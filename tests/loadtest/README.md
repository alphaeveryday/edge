# publication-api 부하 baseline 측정 절차 (ALPHA-496)

로컬 compose 스택의 publication-api 를 대상으로 조회 폭증 시나리오의 baseline 을 측정한다.
개선(ALPHA-433 캐시·ALPHA-434 Exposure Log) 전후 비교의 측정 기반이므로, **같은 절차로 재측정 가능해야 한다** — 절차를 바꾸면 이 문서를 갱신한다.

## 왜 이 시나리오인가

- 조회 = 노출 = `exposure_log` INSERT (ADR-0013). 여기에 요청당 `serving_request_metric` 1행(ALPHA-501)이 더해져 **성공 조회 1건 = INSERT 2건**이다. 이 쓰기 경로는 캐시로 가릴 수 없다 — 캐시 도입 전후로 무엇이 줄고 무엇이 남는지 가르는 것이 baseline 의 목적이다.
- 측정 대상은 publication-api 직접 호출이다. mock-broker 2홉(`:18090`)은 데모 경로라 측정에 끼우지 않는다.

## 전제

1. 로컬 스택 기동: 저장소 루트에서 `docker compose up --build`
2. 동기화 경로(sync-agent→intake→screening-worker)가 게시 데이터를 적재할 때까지 대기 후, 200 확인:

   ```bash
   curl -i -H "X-Customer-Hash: smoke" -H "X-Channel: MTS" \
     "localhost:18084/api/v1/explanations/069500"
   ```

   **200 이 나와야 측정 의미가 있다** — 204 만 나오면 exposure_log 쓰기 경로가 타지 않아 쓰기 병목이 측정되지 않는다. 404 는 `PUBLICATION_KNOWN_TICKERS` 와 종목 불일치.

## 실행

k6 는 미설치라면 `brew install k6`, 또는 docker 로 실행한다.

```bash
# 로컬 바이너리
RATE=50 DURATION=1m k6 run tests/loadtest/publication-baseline.js

# docker (macOS — host 포트로 우회)
docker run --rm -i -e BASE_URL=http://host.docker.internal:18084 -e RATE=50 \
  grafana/k6 run - < tests/loadtest/publication-baseline.js
```

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `BASE_URL` | `http://localhost:18084` | publication-api 주소 |
| `RATE` | 50 | 초당 도착 요청 수 (open model — 응답 지연과 무관하게 유지) |
| `DURATION` | 1m | 지속 시간 |
| `HOT_TICKER` | 069500 | 급등 종목 (집중 조회 대상) |
| `COLD_TICKERS` | 305720,091160 | 분산 조회 종목 |
| `HOT_RATIO` | 0.9 | HOT_TICKER 집중 비율 |

## baseline 측정 절차

1. DB 카운트 스냅샷(측정 전):

   ```bash
   docker exec edge-postgres-onprem psql -U edge -d edge_onprem \
     -c "SELECT (SELECT count(*) FROM exposure_log) AS exposure, (SELECT count(*) FROM serving_request_metric) AS metric"
   ```

2. `RATE` 를 단계적으로 올리며(예: 50 → 100 → 200 → 400) 각 단계를 같은 `DURATION` 으로 실행한다.
3. 단계마다 기록: `http_req_duration` p95·p99, `http_reqs`(실제 처리율), `http_req_failed`, `dropped_iterations`(도착률을 못 따라간 수 — **포화 신호**), `exposure_writes`(200 응답 수).
4. DB 카운트 재조회로 교차 검증: exposure_log 증가분 ≈ `exposure_writes`, serving_request_metric 증가분 ≈ 총 요청 수. 어긋나면 쓰기 누락이 있다는 뜻이다(fail-loud 감사 원장 — 그 자체가 finding).
5. 수치는 Jira ALPHA-496 코멘트로 기록한다(전후 비교의 기준선이므로 실행 파라미터·머신 사양 포함).

## 재측정 (개선 전후 비교)

ALPHA-433(캐시)·ALPHA-434(Exposure Log 개선) 적용 후 **같은 파라미터로** 위 절차를 반복하고 같은 티켓에 비교 수치를 남긴다. 기대: 조회 지연(p95)은 캐시로 줄고, exposure_log 쓰기량은 그대로 남는다 — 남는 쪽이 ALPHA-434 의 대상이다.
