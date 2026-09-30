# 원천 관측 테스트 fixture (ALPHA-1130)

실호출 없이 만든 응답이다. **공급자 실응답 원문이 아니다.**

| 파일 | 형태의 근거 | 값의 근거 |
|---|---|---|
| `ecos_usdkrw.json` | ECOS 731Y003 **실응답 구조**(`live/ecos_usd_krw_ecos_731Y003.json`) | 날짜만 2026-07-24~28 로 바꿨다. 값은 dev `fx_daily` USDKRW 실측(1459.66·1464.671·1461.35) — 계열은 다르다(FMP vs ECOS 15:30 종가) |
| `fmp_treasury.json` | FMP stable `treasury-rates` 목록 형태 | year10 은 dev `rates_daily` 실측(07-24~30). 나머지 만기는 생략 |
| `ecos_kr10y.json` | ECOS StatisticSearch 실응답 구조와 같다 | 예시값 |
| `kosis_cpi.json` | KOSIS statisticsParameterData(PRD_DE·DT·UNIT_NM·ITM_NM) 공개 사용 사례 | 예시값 |
| `eia_brent.json` | EIA API v2 문서(`response.data[]`, period·series·value·units) | 예시값 |

실응답이 이 형태와 다르면 파서가 거부 사유로 드러낸다(`sources/macro_series.py` 도크스트링).

## `live/` — 2026-09-30 소량 실호출 원문(축약)

설계 §10.8 의 실측이다. 구조·필드명·값은 실응답 그대로고, 줄 수만 줄였다(재무제표는 BS·IS·CIS 전부 + SCE 2줄 + CF 1줄,
KIS 마스터는 선택 종목 줄만 재압축, 업종명 파일은 전체). 인증키·요청 URL 은 없다.

| 파일 | 원천 | 확인한 것 |
|---|---|---|
| `ecos_kr_10y_yield.json` | ECOS 817Y002/D/010210000 (샘플 키, 10행 상한) | ITEM_NAME1=국고채(10년), UNIT_NAME=연%, TIME=YYYYMMDD |
| `ecos_usd_krw_ecos_731Y003.json` | ECOS 731Y003/D/0000003 | 원/달러(종가 15:30), 단위 원. KRX 휴장일(09-24·25)에도 행이 있다(출처 미확인) |
| `fmp_treasury_rates.json` | FMP stable treasury-rates (dev 키) | 만기별 % 숫자, `year10` |
| `kis_*.mst.zip` | KIS 공개 마스터 | 고정폭 227/221, 업종명 `[5:45]`(헤더가 맞고 공식 샘플 `[3:43]` 은 틀림), 소분류는 전 종목 0000 |
| `dart_*` | OpenDART (dev 키) 삼성전자·SK하이닉스·고려제강 | 3개월/누적 필드, KRW, 연결/별도, IS 없는 회사(하이닉스)는 CIS, 주식총수 행·stlm_dt, 정정본 접수번호 |
