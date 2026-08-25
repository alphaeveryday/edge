# publication-api 부하 baseline 측정 절차 (ALPHA-496)

> 다중 인스턴스(LB 뒤 4대) 캐시 전략 비교 실험의 하네스·판독은 [publication/](publication/)
> 이 별도로 담는다 — 이 문서는 단일 인스턴스 baseline(ALPHA-496) 절차다.

로컬 compose 스택의 publication-api 를 대상으로 조회 폭증 시나리오의 baseline 을 측정한다.
개선(ALPHA-433 캐시) 전후 비교의 측정 기반이므로, **같은 절차로 재측정 가능해야 한다** — 절차를 바꾸면 이 문서를 갱신한다. (구 ALPHA-434 Exposure Log 개선 축은 ADR-0053 의 exposure_log 폐지로 사문.)

## 왜 이 시나리오인가

- 요청당 서버 쓰기는 `serving_request_metric` 1행(ALPHA-501)이다 — `exposure_log` INSERT 는 ADR-0053 으로 폐지됐다. 이 쓰기 경로는 캐시로 가릴 수 없다 — 캐시 도입 전후로 무엇이 줄고 무엇이 남는지 가르는 것이 baseline 의 목적이다.
- 측정 대상은 publication-api 직접 호출이다. mock-broker(`:18090`, 로컬 passthrough 경유 — ALPHA-992)는 데모 경로라 측정에 끼우지 않는다.

## 전제

1. 로컬 스택 기동: 저장소 루트에서 `docker compose up --build`
2. 동기화 경로(sync-agent→intake→screening-worker)가 게시 데이터를 적재할 때까지 대기 후, 200 확인:

   ```bash
   curl -i "localhost:18084/api/v1/explanations/069500"
   ```

   **result 가 실린 200 이 나와야 측정 의미가 있다**(ADR-0054 — 게시분 없음도 200, result 부재로 구분) — 404 는 종목 마스터(`etf_instrument`) 미시드. (헤더 불필요 — ADR-0053 으로 고객 식별·채널 헤더 폐지, 200 쓰기는 serving_request_metric 1건.)

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
     -c "SELECT count(*) AS metric FROM serving_request_metric"
   ```

2. `RATE` 를 단계적으로 올리며(예: 50 → 100 → 200 → 400) 각 단계를 같은 `DURATION` 으로 실행한다.
3. 단계마다 기록: `http_req_duration` p95·p99, `http_reqs`(실제 처리율), `http_req_failed`, `checks`(k6 출력값은 **성공 비율** — 100% 미만이면 기대 응답(200) 밖이 있었다는 뜻이고 threshold 가 실행을 실패로 승격), `dropped_iterations`(도착률을 못 따라간 수 — **포화 신호**), `ok_responses`(result 실린 200 응답 수).
4. DB 카운트 재조회로 교차 검증 — **부하 종료 후 서버의 in-flight 처리가 끝나도록 잠시(수 초) 기다렸다가 조회한다**(`RequestMetricFilter` 는 응답 완료 후 저장하므로 즉시 조회하면 진행 중이던 요청분이 빠져 보인다). 대기 후에도 serving_request_metric 증가분이 `http_reqs` 보다 적으면 — ① 포화 구간의 연결 실패·클라이언트 타임아웃으로 서버 미도달, ② 서버가 메트릭 저장 실패를 삼킨 경우(`RequestMetricFilter` 는 저장 실패 시 로그만 남기고 응답은 정상 유지). publication-api 로그의 저장 실패 유무로 구분해 기록한다.
5. 수치는 Jira ALPHA-496 코멘트로 기록한다(전후 비교의 기준선이므로 실행 파라미터·머신 사양 포함).

## 재측정 (개선 전후 비교)

ALPHA-433(캐시) 적용 후 **같은 파라미터로** 위 절차를 반복하고 같은 티켓에 비교 수치를 남긴다. 기대: 조회 지연(p95)은 캐시로 줄고, serving_request_metric 쓰기량은 그대로 남는다(캐시 밖 경로).
