# 오늘의 급등주 해석 (truth-movers)

etforca.com/truth/today. 오늘 급등한 국내 주식마다 **급등을 일으킨 사건**을 찾고, 그 사건을 내세운 회사·기관의 주장이 원문으로 확인되는지 판정해 공개한다. 목적은 투자자가 기관·회사의 과장·허위 설명에 당하지 않게 하는 것이다. 기존 기만 사건 검증([truth-service.md](truth-service.md))과 같은 서비스(truth-agent·truth-web)에서 돈다.

## 1. 판정 태그

| 태그 | 값 | 기준 |
|---|---|---|
| 유망주 | `promising` | 급등 사건(공시·실적·계약·정책)이 원문으로 확인되고, 주장과 원문이 맞는다 |
| 허위매물 | `false` | 사건을 내세운 주장이 원문과 어긋나거나, 원문 근거가 없는 테마·풍문으로만 올랐다 |
| 판단 보류 | `pending` | 자료가 모자라 어느 쪽도 원문으로 세우지 못했다 |

태그마다 판정 이유(최대 3줄)와 출처를 함께 싣는다. 화면에는 "AI 판단이며 투자 권유가 아니다"를 고지한다.

## 2. 실행 (매시 정각, 장중)

1. **급등 목록 (코드):** 한국투자증권 등락률 순위 API(`FHPST01700000`)로 전체 시장 상승률 상위를 받는다. ETF·ETN·스팩·우선주는 뺀다. 거래대금 하한을 넘는 종목만 남긴다.
2. **재료 모으기 (코드):** 종목마다 다음을 모은다.
   - 장중 5분 시세: KIS 당일 분봉(`FHKST03010200`)
   - 최근 공시 목록·원문: DART OpenAPI
   - 관련 보도: 뉴스 레이크 말뭉치
   - 회사 로고: DART 기업개황의 홈페이지에서 아이콘을 받아 랜딩 버킷 `truth/data/logos/<code>.<확장자>` 에 저장(PNG·JPG·ICO·WebP 만, SVG 는 스크립트를 담을 수 있어 받지 않는다)
3. **판정 (모델):** 종목마다 세션 하나를 연다. 공시·보도·웹 원문을 오가며 급등 사건과 주장을 찾고 `set_mover_verdict` 로 태그·확신도·이유·출처를 남긴다.
   - 그날 이미 판정한 종목은 새 공시·보도가 없으면 다시 열지 않는다.
4. **게시 (코드):** `truth/data/movers/latest.json` 과 `truth/data/movers/<날짜>.json`.

## 3. 순위 (파급력 근사)

"우리 판단이 맞고 시장이 틀렸을 때 되돌려질 폭"의 근사값이다: `impact = 오늘 상승률(%) × 확신도`.
- 허위매물만 이 값을 갖는다. 근거가 확인된 급등은 되돌릴 이유가 없어 0이다.
- 같은 값이면 상승률 순이다.

증거가 아니라 정렬용 근사값이며, 화면에 그렇게 적는다.

## 4. 공개 데이터 형식

`truth/data/movers/latest.json` (스키마: `apps/etforca/truth-agent/src/edge_truth_agent/movers.schema.json`)

```json
{
  "date": "2026-10-22",
  "generatedAt": "2026-10-22T11:00:42+09:00",
  "items": [{
    "code": "001470", "name": "삼부토건", "market": "KOSPI",
    "logo": "data/logos/001470.png",
    "price": 5500, "prevClose": 4230, "changePct": 30.02, "tradeValue": 1234.5,
    "series": [["09:00", 4300], ["09:05", 4620]],
    "tag": "false", "confidence": 0.7,
    "event": {"title": "우크라이나 재건 MOU 체결 발표", "kind": "news", "at": "2026-10-22T09:12:00+09:00"},
    "claim": "회사는 재건 사업 참여가 확정됐다고 밝혔다",
    "finding": "MOU 상대 기관의 공식 발표나 공시는 확인되지 않는다",
    "reasons": ["공시 없이 보도자료로만 알렸다", "상대 기관 원문이 없다"],
    "sources": [{"title": "…", "publisher": "…", "url": "https://…", "kind": "news", "publishedAt": "2026-10-22"}],
    "impact": 21.0,
    "judgedAt": "2026-10-22T11:03:10+09:00"
  }]
}
```

- `tradeValue` 단위는 억원, `series` 는 오늘 5분 간격 `[시각, 종가]`.
- `logo` 는 사이트 기준 상대 경로이고 없으면 null(화면이 글자 배지로 대신한다).

## 5. 비밀값

truth 전용 키는 `edge-dev-truth-agent/runtime` 시크릿에 둔다. 데이터 파이프라인 키와 섞지 않는다.
- `TRUTH_KIS_APP_KEY`, `TRUTH_KIS_APP_SECRET`: KIS 실전 앱
- `TRUTH_DART_API_KEY`: OpenDART
