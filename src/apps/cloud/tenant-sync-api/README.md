# tenant-sync-api

온프렘 Sync Agent가 Pull하는 Cloud 표면 — `GET /api/v1/sync/bundle?after={cursor}&limit={n}` (엔드포인트 계약 확정 — sync-protocol.md "엔드포인트 계약" 절, ALPHA-397).
계약은 [docs/contracts/sync-protocol.md](../../../../docs/contracts/sync-protocol.md)·[event-bundle-schema.md](../../../../docs/contracts/event-bundle-schema.md)가 SSOT이고, 경로·필드·타입 문법 명세는 이 모듈의 [openapi.yaml](openapi.yaml)(OpenAPI 3.1)이며, 이 README는 이 모듈만의 비자명한 규율만 적는다.

## 지켜야 할 로컬 불변식

- **체크섬은 바이트에 대한 것** — `BundleSerializer`가 직렬화를 딱 한 번 하고, 그 바이트로 SHA-256을 계산하고, 같은 바이트를 body로 보낸다. 응답을 다시 직렬화하거나 body를 가공하는 필터를 끼우면 소비자의 무결성 검증이 전부 깨진다.
- **테넌트 식별은 `TenantResolver`로만** — 쿼리·경로·헤더에서 테넌트를 받지 않는다(계약). 컨트롤러에 테넌트 파라미터를 추가하는 변경은 신뢰경계 위반이다.
- **공통 응답 포맷은 에러에만** — 4xx/5xx는 jvm-common `ApiResponse`(도메인 코드 `SyncErrorStatus`, 글루는 jvm-common 공통 `ExceptionAdvice`). 성공(200) 번들 본문은 `ApiResponse`로 감싸지 않는다 — 계약 와이어 포맷 자체가 응답이고 체크섬이 그 바이트 대상이라, 감싸면 둘 다 깨진다.

## 구조 (layered)

`controller`(HTTP 검증·상태코드) → `service`(SyncBundleService 오케스트레이션 + BundleSerializer) → `repository`(BundleEntryRepository — `tenant_delivery` ⋈ 경계면 테이블 JDBC 조회) / `dto`(계약 와이어 포맷 레코드 — DB 엔티티 아님) / `tenant`(보안 횡단 — TenantResolver). 인터페이스 이음새 없이 구체 클래스 직결 — 교체는 해당 클래스를 직접 재작성한다. **번들 조립은 이 모듈이 경계면 테이블을 직접 조회해 수행한다(ADR-0026) — 외부에서 만들어진 번들을 받지 않는다.** 이 모듈의 DB 접근은 **읽기 전용**이다(outbox writer 는 fan-out 발번기 — 후속).
`source_events`·`evidences` 는 경계면 컬럼 선별 미확정(ALPHA-363)이라 빈 배열로 실린다 — 확정 시 조회에 lineage 조인을 추가한다.

## 스텁 → 실구현 교체 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
| `TenantResolver` (데모 테넌트 `1L` 고정) | sync-auth 티켓 | mTLS 인증서 fingerprint → 테넌트 바인딩 조회, 요청별 인가 검증 |

## 실행·확인

```bash
# 루트에서 (cloud PG + 스키마 + 로컬 시드 포함)
docker compose up --build tenant-sync-api   # host 18083
curl -i "localhost:18083/api/v1/sync/bundle?after=0"   # 200 + X-Bundle-Checksum
curl -i "localhost:18083/api/v1/sync/bundle?after=3"   # 204
# bootRun 은 postgres(:55432) 가 떠 있어야 한다 (src/ 에서 :apps:cloud:tenant-sync-api:bootRun)
```

로컬 데이터는 `libs/schema/seed-local-cloud`(SSOT 밖, compose 만 마운트)의 전달 레코드 4건(NEW·CORRECTION·INVALIDATION·최종 NEW)이다 — fan-out 발번기 도입 시 시드 제거.

테스트 8건 — 체크섬=수신 바이트, snake_case 형상, 204, fail-loud 400(바인딩 실패 포함) 을 인코딩한다.
