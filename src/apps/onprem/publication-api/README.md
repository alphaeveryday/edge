# publication-api

MTS 위젯이 직접 호출하는 조회 표면 — `GET /api/v1/explanations/{etf_ticker}?trade_date=` [edge-onprem] ([ADR-0053](../../../../docs/adr/0053-widget-direct-serving-no-personalization.md)).
계약은 [docs/contracts/publication-api.md](../../../../docs/contracts/publication-api.md)가 시맨틱 SSOT이고, 문법 명세는 코드 옆 [openapi.yaml](openapi.yaml)(OpenAPI 3.1)이며, 이 README는 이 모듈만의 비자명한 규율만 적는다.

## 지켜야 할 로컬 불변식

- **Published 외 상태는 이 모듈에 존재하지 않는다** — 저장소(`ExplanationStore`)가 Published만 알고, 컨트롤러·서비스에 상태 필터 분기가 없다. 검수 대기/차단분이 응답에 나갈 수 있는 코드 경로 자체가 없어야 한다(제품 보장).
- **제공 범위 차단은 게시분 조회 앞단** — `serve()` 가 `serving_scope`(writer = tenant-console-api, 이 모듈은 read-only reader)를 조회해 차단이면 게시분을 읽지 않고 204 로 수렴한다(기존 "설명 없음" 계약과 동일 표면이라 제외 사실이 고객에 노출되지 않는다). 소비 범위는 MARKET(XKRX)=KRX 단일 유니버스 전제([ADR-0024](../../../../docs/adr/0024-scope-domestic-etf.md))의 전역 차단 스위치(상위 우선) + INSTRUMENT(ticker) 종목 차단뿐 — CHANNEL·SECTOR 는 판정하지 않는다(no-op, ALPHA-614). 콘솔 토글 즉시 반영이 신선도 우선이라 **캐시 없음**(요청당 PK 룩업 2회 — 조회 캐시와 대비).
- **면책 문구는 조회 시점 최신값** — 응답 `disclaimer` 는 활성 정책 버전(`policy_version.disclaimer_text`)에서 매 요청 읽는다. 게시분 캐시 **밖**이라 콘솔 발행이 기존 게시분 조회에도 즉시 반영된다(면책 문구는 게시분의 내용이 아니라 노출 화면에 동반되는 현행 안내). 활성 0건(첫 발행 전)은 정상이라 기본 문구로 수렴하며 그 값은 콘솔 `ScreeningService.DEFAULT_DISCLAIMER` 와 **같아야 한다** — 갈리면 아무도 아무것도 바꾸지 않은 상태에서 콘솔 화면과 고객 노출이 어긋난다. 활성 행의 문구가 공백이면(콘솔은 거부하므로 콘솔 밖 경로가 만든 이상) 기본 문구로 응답하되 error 로그로 드러낸다.
- **고객 식별을 어떤 형태로도 받지 않는다** — 인증 없는 공개 읽기 표면([ADR-0053](../../../../docs/adr/0053-widget-direct-serving-no-personalization.md), 고객 해시·채널 헤더 폐지). 구 헤더가 와도 무시한다. 남용 통제(rate limit·인증 헤더 strip)는 증권사 엣지가 전제다(계약 문서 명시). CORS 는 별도 API 호스트 형상의 테넌트만 `publication.cors.allowed-origins` 로 등록한다(기본 빈 값 = 미등록 — 기본형은 동일 오리진 프록시라 불요).
- **공통 응답 포맷은 에러에만** — 성공(200) 본문은 계약 형상 그대로(jvm-common `ApiResponse`는 4xx/5xx만, 도메인 코드 `PublicationErrorStatus`).

## 데이터 소스

- **영속성** — Spring Data JPA(Hibernate). 스키마 SSOT 는 Flyway(`libs/schema`)라 `ddl-auto=validate` 로 검증만 하고 스키마를 생성/변경하지 않는다([ADR-0038](../../../../docs/adr/0038-jpa-onprem-read-standard.md)). 읽기 엔티티(`Publication`·`AnalysisItem`)는 `@Immutable` 로 쓰기를 봉인한다.
- `ExplanationStore` — 온프렘 Published Store(migrations-onprem: `publication ⋈ analysis_item`)를 `PublicationRepository`(JPQL join fetch)로 조회하고 `PublishedExplanation` record 로 매핑한다. WHERE 절이 PUBLISHED + 노출 가능 상태(AUTO_PUBLISHED·APPROVED)만 허용한다. `trade_date` 생략 시 **최신 거래일** 게시분(게시 시각 아님 — 화면 시맨틱). 노출 문구는 `publication.published_summary` 스냅샷 우선(검수 수정 승인, ALPHA-437) — NULL(자동 게시·기존 행)이면 `analysis_item.summary` 폴백.
- **조회 캐시**(ALPHA-433) — `findPublished` 를 `(ticker, trade_date)` 키 Caffeine 인프로세스 캐시(TTL `publication.serve-cache-ttl`, 기본 3s)로 감싼다. 급등 hot-key 중복 읽기 제거가 목적이며 "게시분 없음"(empty)도 캐시한다(204 폭주 방어). **프로세스 간 무효화 경로가 없어 TTL 이 곧 차단·정정 반영 지연의 상한**이다 — 늘릴 때는 컴플라이언스 검토 선행.
- `ServingScopeRepository`·`ServingScopeEntity` — `serving_scope` 제공 범위 토글을 판정용으로 읽는 read-only reader(`@Immutable` 부분 매핑, 마커 `Repository`). writer 는 tenant-console-api(단일 writer, [ADR-0005](../../../../docs/adr/0005-db-as-contract.md))이고 이 모듈은 `serve()` 앞단 차단 판정 전용으로 조회만 한다.
- `PolicyVersionRepository`·`PolicyVersionEntity` — `policy_version` 활성 버전의 `disclaimer_text` 를 응답에 실을 면책 문구로 읽는 read-only reader(`@Immutable` 부분 매핑, 마커 `Repository`). writer 는 tenant-console-api(단일 writer, [ADR-0005](../../../../docs/adr/0005-db-as-contract.md)). 활성 술어(`activated_at IS NOT NULL AND deactivated_at IS NULL`)는 콘솔 writer 의 `findActive()` 전사다 — 같은 행을 가리켜야 콘솔이 편집·표시하는 문구와 고객에게 나가는 문구가 어긋나지 않는다(ALPHA-772).
- `RequestMetricFilter` — `/api/**` 요청의 수·상태·에러 코드를 `serving_request_metric` 에 기록(ALPHA-501, Dashboard ALPHA-128 데이터 소스). route=MVC 매핑 패턴(PII·카디널리티 통제), 에러 코드=실패 응답 봉투의 문자열 `code` 만. Exposure Log 은퇴(ADR-0053) 후 서빙 경로의 유일한 기록 축이다 — 공개 표면이라 자체 기록 상한(미매칭 경로 미기록·보존/집계)이 ADR-0053 의 구현 과제로 걸려 있다. **기록 실패는 로그로 드러내되 서빙을 죽이지 않는다.** 비동기 핸들러 도입 시 필터 재설계 필요(현 표면 없음).
- 상장 판별(404)은 설정 allowlist `publication.known-tickers` — 종목 마스터 동기화 도입 전 임시.
- 로컬 데이터: 동기화 경로가 실적재한다 — cloud 시드(`libs/schema/seed-local-cloud`)의 전달 레코드가 sync-agent→intake→screening-worker 를 거쳐 `analysis_item`·`publication` 에 도착한다(온프렘 로컬 시드 없음).

## 재작성 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
| known-tickers allowlist (설정) | 종목 마스터 동기화 도입 후 | DB 기반 상장 판별 |
| 제공 범위 MARKET 전역 스위치 (`ExplanationService`) | 다중 시장 도입 시 | 시장 식별 컬럼 공급과 함께 종목별 시장 매핑 판정으로 교체 (현행은 KRX 단일 유니버스 전제 [ADR-0024](../../../../docs/adr/0024-scope-domestic-etf.md) 로 XKRX OFF = 전체 차단) |
| 조회 캐시 TTL-only 무효화 (`ExplanationStore`) | 상태 전이(차단·무효화) 무효화 훅 도입 시 | state-machine.md 의 "Publication Cache 제거 + 즉시 비노출" 목표 충족 — 현행은 TTL(3s)이 반영 지연 상한 |

## 실행·확인

```bash
# 루트에서 (온프렘 PG + 스키마 + 시드 포함)
docker compose up --build publication-api   # host 18084
curl -i localhost:18084/api/v1/explanations/069500  # 200 (무헤더 — ADR-0053)
curl -i localhost:18084/api/v1/explanations/305720  # 204
# bootRun 은 postgres-onprem(:55433) 이 떠 있어야 한다 (src/ 에서 :apps:onprem:publication-api:bootRun)
```

테스트 45건 — HTTP 계약 5건 + CORS 설정 게이트 3건(와일드카드 기동 거부·미등록 no-op·GET 한정 매핑)(계약 형상 snake_case·disclaimer 필수, 폐지 헤더 무시, 204, 404, 400 공통 포맷 SERV4004)은 시드 대역(standaloneSetup)으로 검증한다. 조회 캐시 4건(ExplanationStoreCacheTest — positive/negative 캐싱·TTL 만료 스테일 상한·키 분리)은 로더 대역 + Ticker 주입으로 검증한다. 요청 메트릭 계약 10건(라우트 패턴·전 상태 기록·에러 코드 파싱 게이트·미처리 예외 500·이중 기록 차단·기록 실패 시 서빙 유지·비 API 경로 스킵·미매칭 경로 미기록·정적 리소스 전역 패턴 미기록)은 필터 단위로 검증한다. DB 경로 21건은 Testcontainers Postgres 에 onprem 마이그레이션을 적용해 검증한다 — 엔티티↔스키마 정합(`ddl-auto=validate`) 1건 + 리포지토리 조회·published_summary 스냅샷·요청 메트릭 적재 통합 9건 + 제공 범위 판정 통합 5건(ExplanationScopeIntegrationTest — 행 부재 200, INSTRUMENT OFF 204, 재개 200, MARKET XKRX OFF 전역 차단 204, 미상장 404 계약 불변; 이 클래스만 조회 캐시 off 로 시드 재적재 교차오염을 배제한다) + 면책 문구 소비 통합 6건(ExplanationDisclaimerIntegrationTest — 활성 문구 실림, 재발행 즉시 반영, 미발행·종결·미활성화 버전은 기본 문구, 공백 문구는 기본 문구+error 로그; 이 클래스는 캐시를 **켠 채** 돌려 캐시가 문구를 가리지 않음까지 확인하므로 전용 티커·전용 컨텍스트·클래스당 1회 시드로 격리한다). 번들 경계면 계약 2건(EventBundleContractTest)은 evidences 파싱 형상을 고정한다. 프로덕션 형상 전 구간은 compose E2E 로 확인한다.
