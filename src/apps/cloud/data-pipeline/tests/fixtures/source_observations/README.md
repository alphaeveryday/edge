# 원천 관측 테스트 fixture (ALPHA-1130)

실호출 없이 만든 응답이다. **공급자 실응답 원문이 아니다.**

| 파일 | 형태의 근거 | 값의 근거 |
|---|---|---|
| `fmp_usdkrw.json` | FMP stable `historical-price-eod/full` 문서의 목록 형태 | 2026-07-24·27 close 는 dev `canonical/market_data/fx_daily` USDKRW 실측(1459.66·1464.671). 나머지 날짜는 예시값 |
| `fmp_treasury.json` | FMP stable `treasury-rates` 목록 형태 | year10 은 dev `rates_daily` 실측(07-24~30). 나머지 만기는 생략 |
| `ecos_kr10y.json` | ECOS StatisticSearch 문서(`StatisticSearch.row[]`, TIME·DATA_VALUE·UNIT_NAME) | 예시값 |
| `kosis_cpi.json` | KOSIS statisticsParameterData(PRD_DE·DT·UNIT_NM·ITM_NM) 공개 사용 사례 | 예시값 |
| `eia_brent.json` | EIA API v2 문서(`response.data[]`, period·series·value·units) | 예시값 |

실응답이 이 형태와 다르면 파서가 거부 사유로 드러낸다(`sources/macro_series.py` 도크스트링).
