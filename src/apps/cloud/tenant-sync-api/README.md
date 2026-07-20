# tenant-sync-api

온프렘 Sync Agent가 Pull하는 Cloud 표면 — `GET /api/v1/sync/bundle?after={cursor}&limit={n}` (스캐폴드 — 엔드포인트 계약은 미확정, 영서 설계 예정).
계약은 [docs/contracts/sync-protocol.md](../../../docs/contracts/sync-protocol.md)·[event-bundle-schema.md](../../../docs/contracts/event-bundle-schema.md)가 SSOT이고, 이 README는 이 모듈만의 비자명한 규율만 적는다.

## 지켜야 할 로컬 불변식

- **체크섬은 바이트에 대한 것** — `BundleSerializer`가 직렬화를 딱 한 번 하고, 그 바이트로 SHA-256을 계산하고, 같은 바이트를 body로 보낸다. 응답을 다시 직렬화하거나 body를 가공하는 필터를 끼우면 소비자의 무결성 검증이 전부 깨진다.
- **테넌트 식별은 `TenantResolver`로만** — 쿼리·경로·헤더에서 테넌트를 받지 않는다(계약). 컨트롤러에 테넌트 파라미터를 추가하는 변경은 신뢰경계 위반이다.
- **응답 봉투는 에러에만** — 4xx/5xx는 jvm-common `ApiResponse` 봉투(도메인 코드 `SyncErrorStatus`, 글루는 `GlobalExceptionHandler`). 성공(200) 번들 본문은 봉투로 감싸지 않는다 — 계약 와이어 포맷 자체가 응답이고 체크섬이 그 바이트 대상이라, 봉투를 씌우면 둘 다 깨진다.

## 구조 (layered)

`controller`(HTTP 검증·상태코드) → `service`(SyncBundleService 오케스트레이션 + BundleSerializer) → `repository`(BundleEntryRepository — 전달 레코드 조회) / `dto`(계약 와이어 포맷 레코드 — DB 엔티티 아님) / `tenant`(보안 횡단 — TenantResolver). 인터페이스 이음새 없이 구체 클래스 직결 — 교체는 해당 클래스를 직접 재작성한다. **번들 조립은 이 모듈이 경계면 테이블을 직접 조회해 수행한다(ADR-0026) — 외부에서 만들어진 번들을 받지 않는다.**
`entity` 층은 아직 없다 — 영속성이 없어서다. JDBC 리더 교체 시 entity가 함께 들어온다.

## 스텁 → 실구현 교체 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
| `BundleEntryRepository` (인메모리 시드 3건: NEW·CORRECTION·INVALIDATION) | 전달 레코드 저장 구조·fan-out 설계(영서 고도화 영역) 확정 후 | 경계면 테이블 DB 조회 + 조립 (+ datasource 설정, build.gradle에 JDBC 의존성, entity 도입) |
| `TenantResolver` (데모 테넌트 `1L` 고정) | sync-auth 티켓 | mTLS 인증서 fingerprint → 테넌트 바인딩 조회, 요청별 인가 검증 |

## 실행·확인

```bash
# src/ 에서
./gradlew :apps:cloud:tenant-sync-api:bootRun
curl -i "localhost:8080/api/v1/sync/bundle?after=0"   # 200 + X-Bundle-Checksum
curl -i "localhost:8080/api/v1/sync/bundle?after=3"   # 204
# compose 로는 루트에서: docker compose up --build tenant-sync-api (host 18083)
```

테스트 8건 — 체크섬=수신 바이트, snake_case 형상, 204, fail-loud 400(바인딩 실패 포함) 을 인코딩한다.
