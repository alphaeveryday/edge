# MTS/HTS 연동 방식 — Serving API

- MTS/HTS는 증권사 소유 UI이며 벤더 widget을 임베드하지 않는다.
- 경로: **MTS/HTS → 증권사 Backend/API Gateway → On-Premise Serving API**
- Serving API는 **Published(AUTO_PUBLISHED, APPROVED) 상태만 반환**. 검수 대기/차단/반려/무효화/노출중단 상태는 절대 반환하지 않는다.
- Serving API 호출 시 증권사 백엔드가 고객 식별 해시를 전달 → Exposure Log 기록 ([../domain/exposure-log.md](../domain/exposure-log.md)).
- Serving API 자체의 인증은 증권사 내부망 정책(내부 API GW)에 위임한다. 벤더 API Key 없음.
