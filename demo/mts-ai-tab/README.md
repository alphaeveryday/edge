# 가상 MTS — AI 분석 탭 (데모)

증권사 MTS의 AI 분석 탭 데모. 빌드 도구 없이 정적 html+js로 동작하며,
정적 서빙과 증권사 API는 [demo/mock-broker](../mock-broker/server.js)가 담당한다.

## 실행

```bash
docker compose up --build mock-broker publication-api
# http://localhost:18090
```

compose 없이 로컬 프로세스로 띄우려면 (publication-api 는 18084 에 떠 있어야 한다):

```bash
PORT=18090 node demo/mock-broker/server.js
```

## 구조 — 계약의 신뢰경계 재현

[docs/contracts/publication-api.md](../../docs/contracts/publication-api.md)의 원칙(MTS는 Publication API를 직접 호출하지 않는다)을 실 HTTP 홉으로 재현한다 (ADR-0035 런타임 경로).

```
app.js (MTS 화면)
  → broker-api.js   /api/broker/* fetch — 얇은 래퍼(브라우저 mock 없음)
    → mock-broker (demo/mock-broker/server.js)   증권사 자체 제작 API — 고객 해시·채널 부착, 상태 매핑, 폴백 처리
      → publication-api (src/apps/onprem/publication-api)   On-Premise Publication API — 실 컨테이너
```

- 고객 해시는 mock-broker 서버가 부착한다 — 실제 생성 규칙·salt는 증권사 서버 관리 영역(ADR-0013), 브라우저에 두지 않는다.
- 실제 연동 시 mock-broker 레이어는 증권사 백엔드 구현으로 통째로 대체된다(화면 코드는 그대로). 브라우저에서 Publication API를 직접 fetch하는 경로는 계약 위반이라 데모에도 두지 않는다.

## 데모 조작 (쿼리 파라미터)

응답 데이터는 cloud 시드(`src/libs/schema/seed-local-cloud`)의 전달 레코드가 동기화 경로(sync-agent→intake→screening-worker)를 관통해 만든 온프렘 게시분이다.
게시 상태를 바꾸면 화면에 즉시 반영된다: `docker exec edge-postgres-onprem psql -U edge -d edge_onprem -c "UPDATE publication SET status='UNPUBLISHED' WHERE status='PUBLISHED';"` → 조회가 204(NO_DATA)로 바뀐다.

| URL | 재현 상태 |
|---|---|
| `/` | 200 — KODEX 200 설명 노출 |
| `/?ticker=305720` | 204 — 상장 종목이나 설명 없음(정상) 안내 |
| `/?ticker=000001` | 404 — 미지원 종목 안내 |
| `/?trade_date=2026-07-01` | 204 — 해당 기준일 게시분 없음 |
| `/?trade_date=2026-7-1` | 400 — 형식 오류(폴백 문구 + mock-broker 로그 경고) |
| publication-api 중지 후 조회 | 5xx/통신 실패 — 폴백 문구 |
