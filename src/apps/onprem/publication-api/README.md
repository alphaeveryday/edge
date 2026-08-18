# publication-api

증권사 MTS가 호출하는 조회 API입니다. 검수를 통과해 게시된 가격 변동 설명만 내보냅니다.

```
GET /api/v1/explanations/{etf_ticker}?trade_date=
```

## 캐시 성능 실험

총 75회의 부하 실험을 수행했고, 네 캐시 전략은 API 서버 4대·1,600 req/s의 공통 조건에서 비교했습니다. 이 조건에서 네 구성의 p99는 모두 1~2ms였고, Caffeine만으로 3분간 DB 조회가 288,000회에서 1,062회로 줄었습니다. 현재 규모에서는 Caffeine 단독 구성을 유지했습니다.

![고정 부하에서 캐시 모드별 응답 지연 비교](../../../../docs/assets/pubcache/1-mode-latency-p95-p99.png)

![캐시 구성별 DB 조회 횟수 비교](../../../../docs/assets/pubcache/3-db-offloading.png)

캐시 임계점과 접근 분포는 후속 29회 실험에서 따로 측정했습니다.

![조회 종목 수를 늘렸을 때의 p99 변화](../../../../docs/assets/pubcache/2-working-set-sweep-p99.png)

실험 과정과 판단 근거는 [75회 부하 실험](https://choyoungseo20.github.io/posts/the-redis-experiment-that-removed-redis), [결정 기록](https://choyoungseo20.github.io/posts/four-instances-caffeine-was-simpler), [캐시 임계점 후속 실험](https://choyoungseo20.github.io/posts/measuring-the-cache-boundary)에 정리했습니다.

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
