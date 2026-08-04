# tenant-sync-api

온프렘 Sync Agent가 Pull하는 Cloud 표면 — `GET /api/v1/sync/bundle?after={cursor}&limit={n}` (엔드포인트 계약 확정 — sync-protocol.md "엔드포인트 계약" 절, ALPHA-397).
계약은 [docs/contracts/sync-protocol.md](../../../../docs/contracts/sync-protocol.md)·[event-bundle-schema.md](../../../../docs/contracts/event-bundle-schema.md)가 SSOT이고, 경로·필드·타입 문법 명세는 이 모듈의 [openapi.yaml](openapi.yaml)(OpenAPI 3.1)이며, 이 README는 이 모듈만의 비자명한 규율만 적는다.

## 지켜야 할 로컬 불변식

- **snake_case 는 DTO `@JsonNaming` 으로** — BundleSerializer 제거(ADR-0040) 후 `EventBundle`·`BundleEntry`·`ExplanationResult`·`ExplanationRun` 각 record 의 `@JsonNaming(SnakeCaseStrategy)`가 유일한 naming 소스다. 애너테이션이 빠지면 Spring 기본 mapper 가 camelCase 로 내보내 계약이 깨진다(계약 테스트가 가드레일).
- **테넌트 식별은 `TenantResolver`로만** — 쿼리·경로·헤더에서 테넌트를 받지 않는다(계약). 컨트롤러에 테넌트 파라미터를 추가하는 변경은 신뢰경계 위반이다.
- **응답은 공통 응답 포맷으로** — 성공은 항상 200 `ApiResponse`(번들 있으면 `result` 아래, 신규 없음은 `result` 필드 생략 — ADR-0042 로 204 폐지), 에러(4xx/5xx)도 `ApiResponse`(도메인 코드 `SyncErrorStatus`, 글루는 jvm-common `ExceptionAdvice`). ADR-0040 으로 byte[]·체크섬 특례가 폐기돼 성공도 다른 엔드포인트와 동일 포맷이다.

## 구조 (layered)

`controller`(HTTP 검증·공통 응답 포맷) → `service`(SyncBundleService 오케스트레이션) → `repository`(TenantDeliveryRepository — `tenant_delivery` ⋈ 경계면 테이블 JPQL 프로젝션 + BundleEntryStore — 와이어 매핑·delivery_type 분기) / `entity`(`@Immutable` 부분 매핑 — `ddl-auto=validate`, 스키마는 Flyway SSOT, ADR-0038) / `dto`(계약 와이어 포맷 레코드 — DB 엔티티 아님) / `tenant`(보안 횡단 — TenantResolver). **번들 조립은 이 모듈이 경계면 테이블을 직접 조회해 수행한다(ADR-0026) — 외부에서 만들어진 번들을 받지 않는다.** 이 모듈의 DB 접근은 **읽기 전용**이다(outbox writer 는 둘 — NEW 는 analysis-engine write-time fan-out(ALPHA-493), INVALIDATION 은 super-admin-api 무효화 액션(ALPHA-440), 같은 advisory lock 으로 cursor 채번 직렬화) — 리포지토리는 `Repository` 마커 상속으로 쓰기 표면을 봉인한다.
`source_events`·`evidences` 는 배치 조회(`TenantDeliveryRepository.findSourceEventRows`·`findEvidenceRows`)로 조립돼 실린다(ALPHA-718 — 경계면 컬럼 선별은 ALPHA-395). evidences 는 lineage 두 갈래(이벤트 근거·공시 정규화 사실) UNION, source_events 는 근거의 `source_event_id` 로 도달하며 소비자는 screening-worker 출처 수 정책 게이트다.

## 스텁 → 실구현 교체 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
| `TenantResolver` (데모 테넌트 `1L` 고정) | sync-auth 티켓 | mTLS 인증서 fingerprint → 테넌트 바인딩 조회, 요청별 인가 검증 |

## 실행·확인

```bash
# 루트에서 (cloud PG + 스키마 + 로컬 시드 포함)
docker compose up --build tenant-sync-api   # host 18083
curl -i "localhost:18083/api/v1/sync/bundle?after=0"   # 200 (공통 응답 포맷, result 아래 번들)
curl -i "localhost:18083/api/v1/sync/bundle?after=3"   # 200 (신규 없음 — result 필드 생략)
# bootRun 은 postgres(:55432) 가 떠 있어야 한다 (src/ 에서 :apps:cloud:tenant-sync-api:bootRun)
```

로컬 데이터는 `libs/schema/seed-local-cloud`(SSOT 밖, compose 만 마운트)의 전달 레코드다 — NEW 자동 발번은 analysis-engine 소관이지만(ALPHA-493) 엔진이 로컬 compose 에 없어 시드를 유지한다. INVALIDATION 은 super-admin-api 무효화 액션(ALPHA-440)으로 발번된다(CORRECTION 은 폐지 — ADR-0044).

테스트 24건 — 공통 응답 포맷·snake_case 형상(계약 테스트가 `@JsonNaming` 가드레일)·신규 없음 result 생략·fail-loud 400(바인딩 실패 포함) 에 더해, 실 DB 조회 경로는 Testcontainers 통합 테스트(실 Postgres + Flyway `migrations-cloud`)가 delivery_type 분기·keyset 페이지네이션·테넌트 격리·evidences/source_events 조립(두 갈래 DISTINCT·런 경계·NULL 필드)을 고정한다(ALPHA-572·718).
