# publication-api

증권사 백엔드가 호출하는 조회 표면 — `GET /api/v1/explanations/{etf_ticker}?trade_date=` [edge-onprem].
계약은 [docs/contracts/publication-api.md](../../../../docs/contracts/publication-api.md)가 SSOT이고, 이 README는 이 모듈만의 비자명한 규율만 적는다.

## 지켜야 할 로컬 불변식

- **Published 외 상태는 이 모듈에 존재하지 않는다** — 저장소(`ExplanationStore`)가 Published만 알고, 컨트롤러·서비스에 상태 필터 분기가 없다. 검수 대기/차단분이 응답에 나갈 수 있는 코드 경로 자체가 없어야 한다(제품 보장).
- **200 = Exposure 기록** — 응답을 만든 그 지점(`ExplanationService.serve`)에서 문구 스냅샷·고객 해시·채널을 기록한다. 204·에러는 기록하지 않는다(노출이 없었으므로).
- **원본 고객 ID를 받는 표면을 만들지 않는다** — 고객 식별은 `X-Customer-Hash`(증권사 생성)뿐. 해시 생성 규칙·salt는 증권사 관리 영역이다.
- **공통 응답 포맷은 에러에만** — 성공(200) 본문은 계약 형상 그대로(jvm-common `ApiResponse`는 4xx/5xx만, 도메인 코드 `PublicationErrorStatus`).

## 데이터 소스

- `ExplanationStore` — 온프렘 Published Store(migrations-onprem: `publication ⋈ analysis_item`) JDBC 조회. WHERE 절이 PUBLISHED + 노출 가능 상태(AUTO_PUBLISHED·APPROVED)만 허용한다. `trade_date` 생략 시 **최신 거래일** 게시분(게시 시각 아님 — 화면 시맨틱).
- `ExposureLogRecorder` — `exposure_log` INSERT (스키마가 `summary_snapshot NOT NULL` 강제).
- 상장 판별(404)은 설정 allowlist `publication.known-tickers` — 종목 마스터 동기화 도입 전 임시.
- 로컬 데이터: 동기화 경로가 실적재한다 — cloud 시드(`libs/schema/seed-local-cloud`)의 전달 레코드가 sync-agent→intake→screening-worker 를 거쳐 `analysis_item`·`publication` 에 도착한다(온프렘 로컬 시드 없음).

## 재작성 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
| `ExplanationService.DISCLAIMER` (상수) | 컴플라이언스 정책 테이블 도입 후 | 테넌트 정책의 기본 안내 문구 조회 |
| known-tickers allowlist (설정) | 종목 마스터 동기화 도입 후 | DB 기반 상장 판별 |

## 실행·확인

```bash
# 루트에서 (온프렘 PG + 스키마 + 시드 포함)
docker compose up --build publication-api   # host 18084
curl -H "X-Customer-Hash: h" -H "X-Channel: MTS" -i localhost:18084/api/v1/explanations/069500  # 200
curl -H "X-Customer-Hash: h" -H "X-Channel: MTS" -i localhost:18084/api/v1/explanations/305720  # 204
# bootRun 은 postgres-onprem(:55433) 이 떠 있어야 한다 (src/ 에서 :apps:onprem:publication-api:bootRun)
```

테스트 6건 — 계약 형상(snake_case·disclaimer 필수), 조회=노출 기록, 204는 기록 없음, 404, 400 공통 포맷(SERV4001~4004)를 인코딩한다. HTTP 계약은 시드 대역으로 검증하고, 실 DB 경로는 compose E2E 로 확인한다.
