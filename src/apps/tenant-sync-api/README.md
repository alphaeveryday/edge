# tenant-sync-api

온프렘 Sync Agent가 Pull하는 Cloud 표면 — `GET /api/v1/sync/bundle?after={cursor}&limit={n}`.
계약은 [docs/contracts/sync-protocol.md](../../../docs/contracts/sync-protocol.md)·[event-bundle-schema.md](../../../docs/contracts/event-bundle-schema.md)가 SSOT이고, 이 README는 이 모듈만의 비자명한 규율만 적는다.

## 지켜야 할 로컬 불변식

- **체크섬은 바이트에 대한 것** — `BundleSerializer`가 직렬화를 딱 한 번 하고, 그 바이트로 SHA-256을 계산하고, 같은 바이트를 body로 보낸다. 응답을 다시 직렬화하거나 body를 가공하는 필터를 끼우면 소비자의 무결성 검증이 전부 깨진다.
- **테넌트 식별은 `TenantResolver`로만** — 쿼리·경로·헤더에서 테넌트를 받지 않는다(계약). 컨트롤러에 테넌트 파라미터를 추가하는 변경은 신뢰경계 위반이다.
- 신규 없음 = **204** (빈 번들 200 금지). 다음 `after` = 응답의 `cursor_to`.

## 스텁 → 실구현 교체 지점

| 스텁 | 교체 시점 | 교체물 |
|---|---|---|
| `InMemoryOutboxReader` (고정 시드 3건: NEW·CORRECTION·INVALIDATION) | Cloud Event Store·outbox Flyway 확정 후 | `tenant_outbox` JDBC 리더 (+ datasource 설정, build.gradle에 JDBC 의존성) |
| `FixedTenantResolver` (`t-demo` 고정) | sync-auth 티켓 | mTLS 인증서 fingerprint → 테넌트 바인딩 조회, 요청별 인가 검증 |

## 실행·확인

```bash
# src/ 에서
./gradlew :apps:tenant-sync-api:bootRun
curl -i "localhost:8080/api/v1/sync/bundle?after=0"   # 200 + X-Bundle-Checksum
curl -i "localhost:8080/api/v1/sync/bundle?after=3"   # 204
# compose 로는 루트에서: docker compose up --build tenant-sync-api (host 18083)
```

테스트 8건 — 계약 시맨틱(체크섬=수신 바이트, snake_case 형상, 순차 소비, 204, fail-loud 400)을 인코딩한다.
