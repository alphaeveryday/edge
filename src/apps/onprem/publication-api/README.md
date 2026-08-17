# publication-api

증권사 MTS 위젯이 호출하는 조회 API입니다. 검수를 통과해 게시된 가격 변동 설명만 내보냅니다.

```
GET /api/v1/explanations/{etf_ticker}?trade_date=
```

## 캐시 성능 실험

API 서버를 4대로 늘리는 과정에서 Redis 도입이 필요한지 부하 테스트 104회로 확인했습니다. 결론은 Redis 없이 인프로세스 캐시(Caffeine)만 유지하는 것이었습니다.

![고정 부하에서 캐시 모드별 응답 지연 비교](../../../../docs/assets/pubcache/1-mode-latency-p95-p99.png)

캐시 없음·Caffeine·Redis·two-level 네 구성 모두 p99가 1~2ms로, 응답 속도에는 차이가 없었습니다. 차이는 DB 부하에서 났습니다.

![캐시 구성별 DB 조회 횟수 비교](../../../../docs/assets/pubcache/3-db-offloading.png)

같은 부하 3분 동안 DB 조회가 캐시 없음 288,000회에서 Caffeine 1,062회로 줄었습니다. Redis를 더 얹으면 여기서 몇백 회를 더 줄일 수 있지만, 이미 DB에 부담이 되지 않는 수준입니다.

![조회 종목 수를 늘렸을 때의 p99 변화](../../../../docs/assets/pubcache/2-working-set-sweep-p99.png)

오히려 조회 종목 수를 늘리자 Redis를 겹친 two-level은 캐시 미스마다 네트워크 왕복이 붙어 p99가 2.4ms에서 11.9ms까지 나빠졌습니다. Caffeine만 남긴 이유입니다.

실험 설계와 상세 결과는 [기술 블로그 4부작](https://choyoungseo20.github.io)에 정리했습니다.

## 실행

```bash
# 루트에서 전체 스택 기동 — cloud 시드가 sync-agent → intake → screening-worker 경로로 적재된다
docker compose up --build -d                         # publication-api 는 host 18084
curl -i localhost:18084/api/v1/explanations/069500   # 200 (동기화로 게시분 도착 후)
curl -i localhost:18084/api/v1/explanations/305720   # 204 (게시분 없는 종목)
```

## 개발 문서

- [DEVELOPMENT.md](DEVELOPMENT.md) — 로컬 불변식·데이터 소스·캐시 전략 상세·재작성 지점·테스트 구성
- [openapi.yaml](openapi.yaml) — API 명세 (OpenAPI 3.1)
