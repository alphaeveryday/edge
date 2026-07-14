# Event Bundle 인터페이스 계약 (진기-영서)

> **계약 문서** — 이 파일의 변경은 진기·영서 공동 승인 대상이다(CODEOWNERS).

진기-영서 인터페이스는 **"Cloud Event Store 스키마 + 번들 생성 규칙"** 하나로 고정한다. 이 계약 변경은 반드시 양자 합의.

- 오너십 경계: **김진기** — Data Pipeline → Common Analysis Engine → Cloud Event Store + **Event Bundle 생성까지** / **조영서** — **Tenant Sync API부터 이후 전부** ([../README.md](../README.md)의 팀 오너십 참조).
- **번들 생성 규칙(cursor 발번·outbox fan-out)** 은 이 계약의 일부다 — 확정 스펙은 [sync-protocol.md](sync-protocol.md): 테넌트별 outbox fan-out 시점(공통 이벤트 1건 → 테넌트별 전달 레코드 N건 생성)에 각 테넌트의 sequence를 발번하고, Event Bundle은 이 outbox를 cursor 순으로 묶어 생성한다.
- 전송 단위: Event Bundle (신규 이벤트 + 정정 이벤트 + 무효화 이벤트 포함). 무결성은 번들 단위 SHA-256 체크섬.

## Cloud Event Store 스키마

> **미확정** — 필드 수준 스키마는 아직 합의되지 않았다. 확정되는 대로 이 파일에 기록한다(변경은 양자 합의).
