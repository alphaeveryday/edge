# serving-api

증권사 백엔드가 호출하는 조회 표면 — `GET /api/v1/explanations/{etf_ticker}?trade_date=` [edge-onprem].
계약은 [docs/contracts/serving-api.md](../../../docs/contracts/serving-api.md)가 SSOT이고, 이 README는 이 모듈만의 비자명한 규율만 적는다.

## 지켜야 할 로컬 불변식

- **Published 외 상태는 이 모듈에 존재하지 않는다** — 저장소(`ExplanationStore`)가 Published만 알고, 컨트롤러·서비스에 상태 필터 분기가 없다. 검수 대기/차단분이 응답에 나갈 수 있는 코드 경로 자체가 없어야 한다(제품 보장).
- **200 = Exposure 기록** — 응답을 만든 그 지점(`ExplanationService.serve`)에서 문구 스냅샷·고객 해시·채널을 기록한다. 204·에러는 기록하지 않는다(노출이 없었으므로).
- **원본 고객 ID를 받는 표면을 만들지 않는다** — 고객 식별은 `X-Customer-Hash`(증권사 생성)뿐. 해시 생성 규칙·salt는 증권사 관리 영역이다.
- **응답 봉투는 에러에만** — 성공(200) 본문은 계약 형상 그대로(jvm-common `ApiResponse`는 4xx/5xx만, 도메인 코드 `ServingErrorStatus`).

## 재작성 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
| `ExplanationStore` (인메모리 시드 — 069500 게시분 1건, 305720 상장·설명없음) | 온프렘 도메인 마이그레이션(state-machine ERD) 후 | 온프렘 DB의 Published 조회 (+ datasource) |
| `ExposureLogRecorder` (인메모리 + 구조화 로그) | 온프렘 `exposure_log` 테이블 도입 후 | DB 기록 — 쓰기 경로 설계(유실 불가·저지연)는 오너 영역 |
| `ExplanationService.DISCLAIMER` (상수) | 컴플라이언스 정책 테이블 도입 후 | 테넌트 정책의 기본 안내 문구 조회 |

## 실행·확인

```bash
# src/ 에서
./gradlew :apps:onprem:serving-api:bootRun
curl -H "X-Customer-Hash: h" -H "X-Channel: MTS" -i localhost:8080/api/v1/explanations/069500  # 200
curl -H "X-Customer-Hash: h" -H "X-Channel: MTS" -i localhost:8080/api/v1/explanations/305720  # 204
# compose 로는 루트에서: docker compose up --build serving-api (host 18084)
```

테스트 6건 — 계약 형상(snake_case·disclaimer 필수), 조회=노출 기록, 204는 기록 없음, 404, 400 봉투(SERV4001~4004)를 인코딩한다.
