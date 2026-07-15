# 가상 MTS — AI 분석 탭 (데모)

증권사 MTS의 AI 분석 탭 데모. 빌드 도구 없이 정적 html+js로 동작한다.

## 실행

```bash
python3 -m http.server 8000 --directory demo/mts-ai-tab
# http://localhost:8000
```

## 구조 — 계약의 신뢰경계 재현

[docs/contracts/serving-api.md](../../docs/contracts/serving-api.md)의 원칙(MTS는 Serving API를 직접 호출하지 않는다)을 레이어로 재현한다.

```
app.js (MTS 화면)
  → broker-api.js   증권사 자체 제작 API 모킹 — 고객 해시·채널 부착, 폴백 처리 (실제로는 증권사 서버)
    → serving-api-mock.js   On-Premise Serving API 모킹 — 계약 응답 형상(200/204/400/404/5xx)
```

- 고객 해시는 데모 스텁이다 — 실제 생성 규칙·salt는 증권사 서버 관리 영역(ADR-0013), 브라우저에 두지 않는다.
- 실제 연동 시 `broker-api.js` 레이어는 증권사 백엔드 구현으로 통째로 대체된다(화면 코드는 그대로). 브라우저에서 Serving API를 직접 fetch하는 경로는 계약 위반이라 데모에도 두지 않는다.

## 데모 조작 (쿼리 파라미터)

| URL | 재현 상태 |
|---|---|
| `/` | 200 — KODEX 200 설명 노출 |
| `/?ticker=091160` | 200 — KODEX 반도체 설명 |
| `/?ticker=114800` | 204 — 설명 없음(정상) 안내 |
| `/?ticker=999999` | 5xx — 폴백 문구 |
| `/?ticker=000001` | 404 — 미지원 종목 안내 |
| `/?trade_date=2026-07-01` | 204 — 해당 기준일 게시분 없음 |
| `/?trade_date=2026-7-1` | 400 — 형식 오류(폴백 문구 + 콘솔 경고) |
