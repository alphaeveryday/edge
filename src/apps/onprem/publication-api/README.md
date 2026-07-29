# publication-api

증권사 백엔드가 호출하는 조회 표면 — `GET /api/v1/explanations/{etf_ticker}?trade_date=` [edge-onprem].
계약은 [docs/contracts/publication-api.md](../../../../docs/contracts/publication-api.md)가 시맨틱 SSOT이고, 문법 명세는 코드 옆 [openapi.yaml](openapi.yaml)(OpenAPI 3.1)이며, 이 README는 이 모듈만의 비자명한 규율만 적는다.

## 지켜야 할 로컬 불변식

- **Published 외 상태는 이 모듈에 존재하지 않는다** — 저장소(`ExplanationStore`)가 Published만 알고, 컨트롤러·서비스에 상태 필터 분기가 없다. 검수 대기/차단분이 응답에 나갈 수 있는 코드 경로 자체가 없어야 한다(제품 보장).
- **200 = Exposure 기록** — 응답을 만든 그 지점(`ExplanationService.serve`)에서 문구 스냅샷·고객 해시·채널을 기록한다. 204·에러는 기록하지 않는다(노출이 없었으므로).
- **원본 고객 ID를 받는 표면을 만들지 않는다** — 고객 식별은 `X-Customer-Hash`(증권사 생성)뿐. 해시 생성 규칙·salt는 증권사 관리 영역이다.
- **공통 응답 포맷은 에러에만** — 성공(200) 본문은 계약 형상 그대로(jvm-common `ApiResponse`는 4xx/5xx만, 도메인 코드 `PublicationErrorStatus`).

## 데이터 소스

- **영속성** — Spring Data JPA(Hibernate). 스키마 SSOT 는 Flyway(`libs/schema`)라 `ddl-auto=validate` 로 검증만 하고 스키마를 생성/변경하지 않는다([ADR-0038](../../../../docs/adr/0038-jpa-onprem-read-standard.md)). 읽기 엔티티(`Publication`·`AnalysisItem`)는 `@Immutable` 로 쓰기를 봉인한다.
- `ExplanationStore` — 온프렘 Published Store(migrations-onprem: `publication ⋈ analysis_item`)를 `PublicationRepository`(JPQL join fetch)로 조회하고 `PublishedExplanation` record 로 매핑한다. WHERE 절이 PUBLISHED + 노출 가능 상태(AUTO_PUBLISHED·APPROVED)만 허용한다. `trade_date` 생략 시 **최신 거래일** 게시분(게시 시각 아님 — 화면 시맨틱). 노출 문구는 `publication.published_summary` 스냅샷 우선(검수 수정 승인, ALPHA-437) — NULL(자동 게시·기존 행)이면 `analysis_item.summary` 폴백.
- **조회 캐시**(ALPHA-433) — `findPublished` 를 `(ticker, trade_date)` 키 Caffeine 인프로세스 캐시(TTL `publication.serve-cache-ttl`, 기본 3s)로 감싼다. 급등 hot-key 중복 읽기 제거가 목적이며 "게시분 없음"(empty)도 캐시한다(204 폭주 방어). **프로세스 간 무효화 경로가 없어 TTL 이 곧 차단·정정 반영 지연의 상한**이다 — 늘릴 때는 컴플라이언스 검토 선행. Exposure 기록은 캐시와 무관하게 요청마다 남는다(조회=노출 — 캐시는 read path 만 가린다).
- `ExposureLogRecorder` — `exposure_log` 저장(`ExposureLogRepository.save`, 스키마가 `summary_snapshot NOT NULL` 강제).
- `RequestMetricFilter` — `/api/**` 요청의 수·상태·에러 코드를 `serving_request_metric` 에 기록(ALPHA-501, Dashboard ALPHA-128 데이터 소스). route=MVC 매핑 패턴(PII·카디널리티 통제), 에러 코드=실패 응답 봉투의 문자열 `code` 만. **기록 실패는 로그로 드러내되 서빙을 죽이지 않는다** — 감사(exposure_log, 동기·fail-loud)와 의도적으로 다른 선택. 비동기 핸들러 도입 시 필터 재설계 필요(현 표면 없음).
- 상장 판별(404)은 설정 allowlist `publication.known-tickers` — 종목 마스터 동기화 도입 전 임시.
- 로컬 데이터: 동기화 경로가 실적재한다 — cloud 시드(`libs/schema/seed-local-cloud`)의 전달 레코드가 sync-agent→intake→screening-worker 를 거쳐 `analysis_item`·`publication` 에 도착한다(온프렘 로컬 시드 없음).

## 재작성 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
| `ExplanationService.DISCLAIMER` (상수) | 컴플라이언스 정책 테이블 도입 후 | 테넌트 정책의 기본 안내 문구 조회 |
| known-tickers allowlist (설정) | 종목 마스터 동기화 도입 후 | DB 기반 상장 판별 |
| 조회 캐시 TTL-only 무효화 (`ExplanationStore`) | 상태 전이(차단·정정) 무효화 훅 도입 시 | state-machine.md 의 "Publication Cache 제거 + 즉시 비노출" 목표 충족 — 현행은 TTL(3s)이 반영 지연 상한 |

## 실행·확인

```bash
# 루트에서 (온프렘 PG + 스키마 + 시드 포함)
docker compose up --build publication-api   # host 18084
curl -H "X-Customer-Hash: h" -H "X-Channel: MTS" -i localhost:18084/api/v1/explanations/069500  # 200
curl -H "X-Customer-Hash: h" -H "X-Channel: MTS" -i localhost:18084/api/v1/explanations/305720  # 204
# bootRun 은 postgres-onprem(:55433) 이 떠 있어야 한다 (src/ 에서 :apps:onprem:publication-api:bootRun)
```

테스트 27건 — HTTP 계약 5건(계약 형상 snake_case·disclaimer 필수, 조회=노출 기록, 204는 기록 없음, 404, 400 공통 포맷 SERV4001~4004)은 시드 대역(standaloneSetup)으로 검증한다. 조회 캐시 4건(ExplanationStoreCacheTest — positive/negative 캐싱·TTL 만료 스테일 상한·키 분리)은 로더 대역 + Ticker 주입으로 검증한다. 요청 메트릭 계약 8건(라우트 패턴·전 상태 기록·에러 코드 파싱 게이트·미처리 예외 500·이중 기록 차단·기록 실패 시 서빙 유지·비 API 경로 스킵)은 필터 단위로 검증한다. DB 경로 8건은 Testcontainers Postgres 에 onprem 마이그레이션을 적용해 검증한다 — 엔티티↔스키마 정합(`ddl-auto=validate`) 1건 + 리포지토리 조회·노출 적재·published_summary 스냅샷·요청 메트릭 적재 통합 7건. 번들 경계면 계약 2건(EventBundleContractTest)은 evidences 파싱 형상을 고정한다. 프로덕션 형상 전 구간은 compose E2E 로 확인한다.
