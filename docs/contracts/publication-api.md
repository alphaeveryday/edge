# MTS/HTS 연동 방식 — Publication API

> 이 문서는 증권사 연동 담당자에게 전달되는 연동 기준의 원본이다(대외 산출물 — 표현은 [../writing-rules.md](../writing-rules.md) 준수).
>
> **문법 명세(기계가독)** — 경로·필드·타입의 기계가독 명세는 [publication-api/openapi.yaml](../../src/apps/onprem/publication-api/openapi.yaml)(OpenAPI 3.1, ALPHA-498). 이 문서(시맨틱 계약)가 상위 SSOT이고 openapi 는 하위 문법 층이다(TODO §5 2층 구조) — 의미가 충돌하면 이 문서가 이긴다.

## 원칙 (확정)

- MTS/HTS는 증권사 소유 UI다. 벤더가 **서빙·호스팅하는** widget 런타임은 임베드하지 않는다 — EDGE 위젯 UI는 빌드 산출물로 납품되어 증권사가 자기 환경에서 임베드·호스팅한다([ADR-0035](../adr/0035-widget-ui-build-artifact.md)). 위젯이 벤더 클라우드를 직접 호출하지 않는 것은 그대로다 — Publication API 는 증권사 환경 안에 있다.
- 경로: **MTS/HTS(위젯) → 증권사 엣지(프록시) → On-Premise Publication API**([ADR-0053](../adr/0053-widget-direct-serving-no-personalization.md)). 위젯이 Publication API 를 직접 호출하며, 증권사 백엔드의 중계 구현은 필요 없다. 진입 기본형은 **위젯 배포 도메인과 동일 오리진의 경로 프록시**다(별도 API 호스트를 여는 경우에만 Publication API 의 CORS 설정 `publication.cors.allowed-origins` 에 위젯 오리진을 등록한다).
- Publication API는 **Published(AUTO_PUBLISHED, APPROVED) 상태만 반환**. 검수 대기/차단/반려/무효화/노출중단 상태는 절대 반환하지 않는다 — 응답에 존재할 수 없는 것이 제품 보장이다.
- **고객 식별은 어떤 형태로도 받지 않는다**(ADR-0053 — 고객 해시 폐지). 인증 없는 공개 읽기 표면이며, 응답은 전 고객 동일한 비개인화 게시 콘텐츠뿐이다. Exposure Log(조회=노출 기록)는 폐지됐다 — 서빙 기록은 요청 메트릭(`serving_request_metric`, 고객 식별자·문구 비적재)뿐이다.
- **엣지 통제는 증권사 소관이며, 다음 두 가지는 공개 노출의 필수 전제다**: ① rate limit(요청당 서버 기록이 있어 무제한 트래픽을 받지 않는다) ② 프록시의 **쿠키·Authorization 등 인증 헤더 미전달(strip)** — 위젯 도메인의 세션 토큰이 Publication API 로그에 흘러들지 않게 한다. WAF 등 추가 방어는 권장이다. 벤더 API Key 없음.

## 엔드포인트 스펙 (초안 — ALPHA-366)

> `[확정 필요]` 표기 항목 외에는 기확정 결정에서 도출된 제안값이다. 데모 페이지(가상 MTS)는 이 스펙 기준으로 mock을 만든다.

### 조회 — ETF 가격 변동 설명 (단건)

```
GET /api/v1/explanations/{etf_ticker}?trade_date={yyyy-MM-dd}
```

요청 헤더 없음 — 구 계약의 `X-Customer-Hash`·`X-Channel` 은 폐지됐고(ADR-0053), 보내도 무시된다.

- `etf_ticker`: 국내 상장 ETF 종목코드 (MVP 커버리지 — [../adr/0024](../adr/0024-scope-domestic-etf.md)). `[확정 필요 — 식별자 체계: 종목코드 vs ISIN. 초안은 종목코드]`
- `trade_date` 생략 시 **최신 거래일**의 게시분 — 화면(AI 분석 탭)은 "가장 최근 거래일의 분석"을 원하므로 게시 시각이 아니라 거래일 기준이다. 과거 거래일 분석이 나중에 게시(지연 검수)돼도 최신 거래일 게시분이 우선한다. 같은 거래일에 유효 스냅샷이 여럿 공존하면 `explanation_as_of` 최신이 이긴다(무효화분 제외 — "유효 최신 승리", ADR-0045 결정 3·ALPHA-743). 최신이 무효화되면 직전 유효 스냅샷이 이 규칙만으로 자동 재노출된다. 응답의 `explanation_as_of`가 스냅샷 기준시각을 말한다. 동률은 게시 시각으로 해소.
**응답 200** — 성공은 항상 200 + 공통 응답 포맷(jvm-common `ApiResponse`)이다([ADR-0054](../adr/0054-publication-explanations-uniform-response.md)). 설명이 있으면 `result` 에 실리고, 없으면 `result` 키 자체가 생략된다.

노출 가능한 설명이 있을 때 — `result` 내용:

```json
{
  "isSuccess": true,
  "code": "COMMON200",
  "message": "성공입니다.",
  "result": {
    "publication_id": "...",
    "etf": { "ticker": "069500", "name": "KODEX 200" },
    "trade_date": "2026-07-15",
    "summary": "반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.",
    "confidence_level": "MEDIUM",
    "evidences": [
      { "kind": "NEWS", "title": "반도체 수출 반등", "source": "...", "published_at": "..." }
    ],
    "disclaimer": "본 내용은 공개 정보 기반의 변동 요인 후보이며 투자 권유가 아닙니다.",
    "published_at": "2026-07-15T16:40:00+09:00",
    "explanation_as_of": "2026-07-15T16:00:00+09:00",
    "content_as_of": "2026-07-15T10:30:00+09:00"
  }
}
```

- `content_as_of`는 **콘텐츠 기준시각** — 설명 본문이 서술하는 구간의 끝이다(ALPHA-918). `explanation_as_of`(생성 시각·스냅샷 축)와 달리 본문 내용의 시점을 말한다. 구형 게시분은 `null` — 소비자는 `explanation_as_of`로 폴백한다.
- `summary`는 검수를 거친 **최종 노출 문구**다(원본 AI 문구가 아니라 검수자 수정 반영본). 원천은 Cloud `explanation_result.summary`(물리 스키마의 유일한 고객 노출 텍스트 필드) — 번들로 온프렘에 수신된 뒤 검수를 거친 값이다.
- `disclaimer`는 **활성 정책 버전**(`policy_version.disclaimer_text`, 콘솔 "점검 기준 관리 → 면책 문구"의 발행분)의 안내 문구 — 화면에 반드시 함께 노출한다. **조회 시점 최신값**이라 문구를 새로 발행하면 이미 게시된 설명의 조회에도 즉시 적용된다(면책 문구는 게시분의 내용이 아니라 노출 화면에 동반되는 현행 안내이기 때문 — 게시분 캐시는 이 값을 가리지 않는다). 활성 버전이 없는 구간(첫 발행 전 — 면책 문구는 테넌트 컴플라이언스 콘텐츠라 시드로 발행하지 않는다)에는 콘솔이 편집 화면에 투영하는 것과 같은 기본 문구가 실린다.
- `evidences` 요소 형상은 `{kind, title, source, published_at}`(근거 뉴스/공시 문서 목록)로 **확정**(ALPHA-395 — [event-bundle-schema.md](event-bundle-schema.md) "경계면 컬럼" 절). 번들 `evidences`가 온프렘 저장(`analysis_item.evidences`)을 거쳐 이 응답으로 서빙된다(`ExplanationStore`가 파싱하는 형상 — 저장분에는 `source_uri`(ALPHA-739, 검수 콘솔용)도 있으나 **서빙 계약에는 싣지 않는다**: 내부 lineage URI 를 고객 표면에 노출하지 않음). 반대 요인 등 부가 텍스트는 물리 스키마에 전용 컬럼이 없어(candidate: `stage_results` JSONB) 계약에 넣지 않는다 — 필요해지면 스키마 확장(양자 합의) 후 추가.
- 노출 이력은 기록하지 않는다(ADR-0053 — 고객 단위 감사 요건 폐지). 민원 대응의 재구성 근거는 게시·정정 이력(`publication`·`console_action_log`)과 정책 이력(`policy_version` 활성 구간)이다 — 특정 고객에게 무엇이 나갔는가는 재구성 대상이 아니다.

**설명 없음** (해당 ETF·일자에 노출 가능한 설명이 없을 때): 정상 상태다 — 모든 ETF가 매일 설명을 갖지 않는다. 200 + `result` 키 생략으로 응답한다(`{ "isSuccess": true, "code": "COMMON200", "message": "..." }`). `"result": null` 명시가 아니라 **키 부재**다 — 소비자는 키 존재로 판별한다. 구 204 응답은 폐지됐다(ADR-0054).

**에러**:

| 코드 | 의미 |
|---|---|
| 400 | 잘못된 `trade_date` 형식 (`COMMON400`) |
| 404 | 알 수 없는 ETF 종목코드 (`SERV4040`) |
| 5xx | 서버 오류 — 위젯은 폴백 문구 처리 권장(설명 미제공이 고객 화면 오류로 보이지 않게) |

- 에러 body 형상은 jvm-common 공통 응답 포맷 `ApiResponse` — `{ "isSuccess": false, "code": "SERV4040", "message": "..." }`(result 생략). 도메인 코드는 `SERV4040`(404)뿐이고, `trade_date` 형식 위반(400)은 프레임워크 변환 실패라 공통 코드 `COMMON400` 이다. `SERV4001`~`4003`(헤더 검증, ADR-0053)·`SERV4004`(구 trade_date 도메인 코드 — 파싱을 프레임워크 변환에 위임하며 폐지)는 은퇴됐고 번호는 재사용하지 않는다. 그 외 프레임워크 예외도 상태코드 기반 공통 코드(`COMMON404` 등). (코드 `PublicationErrorStatus`·openapi `ErrorResponse`)

### 미확정 목록

1. 식별자 체계 (종목코드 vs ISIN)
2. 목록/배치 조회(여러 ETF 한 번에) 필요 여부 — MTS 관심목록 화면이 요구하면 추가
3. 캐싱 지시(ETag 등) — 조회=노출 시맨틱 폐지(ADR-0053)로 엣지 캐시의 감사 왜곡 우려는 사라졌다. 남는 검토 축은 차단·정정의 반영 지연 상한(서버 캐시 TTL 과의 합산)뿐이다
