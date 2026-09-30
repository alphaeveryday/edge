# 원천 관측 테스트 fixture (ALPHA-1130)

실호출 없이 만든 응답이다. **공급자 실응답 원문이 아니다.**

| 파일 | 형태의 근거 | 값의 근거 |
|---|---|---|
| `ecos_usdkrw.json` | ECOS 731Y003 **실응답 구조**(`live/ecos_usd_krw_ecos_731Y003.json`) | 날짜만 2026-07-24~28 로 바꿨다. 값은 dev `fx_daily` USDKRW 실측(1459.66·1464.671·1461.35) — 계열은 다르다(FMP vs ECOS 15:30 종가) |
| `fmp_treasury.json` | FMP stable `treasury-rates` 목록 형태 | year10 은 dev `rates_daily` 실측(07-24~30). 나머지 만기는 생략 |
| `ecos_kr10y.json` | ECOS StatisticSearch 실응답 구조와 같다 | 예시값 |
| `kosis_cpi.json` | KOSIS 실응답 구조(`live/kosis_cpi_yoy.json`) — **UNIT_NM 없음**, 단위는 ITM_NM "전년동월비(%)" | 실측 2026-06 행(3.2) + 2026-07 행(2.8)을 202605 로 옮긴 것 |
| `eia_brent.json` | EIA 실응답 구조(`live/eia_brent_spot.json`, series-description·units 포함) | 실측값, 날짜만 2026-09→07 로 옮김 |

실응답이 이 형태와 다르면 파서가 거부 사유로 드러낸다(`sources/macro_series.py` 도크스트링).

## `live/` — 2026-09-30 소량 실호출 원문(축약, 2회차 7호출 포함)

설계 §10.8 의 실측이다. 구조·필드명·값은 실응답 그대로고, 줄 수만 줄였다(재무제표는 BS·IS·CIS 전부 + SCE 2줄 + CF 1줄,
EIA 는 16행 중 4행). 인증키·요청 URL 은 없다.

| 파일 | 원천 | 확인한 것 |
|---|---|---|
| `ecos_kr_10y_yield.json` | ECOS 817Y002/D/010210000 (발급 키, 09-15~29) | ITEM_NAME1=국고채(10년), UNIT_NAME=연%, TIME=YYYYMMDD. 추석 휴장 09-24·25 행 없음 |
| `ecos_usd_krw_ecos_731Y003.json` | ECOS 731Y003/D/0000003 (발급 키, 09-15~29) | 원/달러(종가 15:30), 단위 원. KRX 휴장일(09-24·25)에도 행이 있다(출처 미확인) |
| `kosis_cpi_yoy.json`·`kosis_meta_itm.json` | KOSIS DT_1J22042 T03 데이터(2026-06~08)·항목 메타(getMeta ITM) | T03=전년동월비(%)·Change over the same month of last year·총지수. **UNIT_NM 은 데이터·메타 모두 없음**(T02 만 %) |
| `eia_brent_spot.json` | EIA v2 petroleum/pri/spt RBRTE 2026-09 (16행 중 4행) | series-description=Europe Brent Spot Price FOB (Dollars per Barrel), units=$/BBL, daily, 최신 09-22 |
| `dart_shares_*_2025_11011.json` | 사업보고서 주식총수(삼성·하이닉스) | stlm_dt=2025-12-31 — 기준일=보고기간 말 검사와 일치 |
| `fmp_treasury_rates.json` | FMP stable treasury-rates (dev 키) | 만기별 % 숫자, `year10` |
| `dart_*` | OpenDART (dev 키) 삼성전자·SK하이닉스·고려제강 | 3개월/누적 필드, KRW, 연결/별도, IS 없는 회사(하이닉스)는 CIS, 주식총수 행·stlm_dt, 정정본 접수번호 |

### 재무제표 원문을 더 줄이지 않은 이유(2026-09-30 검토)

`dart_stmt_*` 여섯 파일은 67~85행(행당 15~17필드, 필드마다 한 줄이라 파일당 1,187~1,651줄)이다. 파서가 값을 읽는 계정은
6개(`ifrs-full_Revenue`·`dart_OperatingIncomeLoss`·기본/희석 EPS·지배기업 소유주지분·자본총계)뿐이지만 줄이지 않았다.
- 검사가 원문 구조에 기대고 있다 — 같은 계정의 우선주 줄(`_pick_line`), IS 가 없는 회사의 CIS 대체(하이닉스),
  연결/별도 두 기준, 정정본 접수번호. 지금 쓰는 계정만 남기면 그 구조를 다음 규칙이 다시 쓸 때 실응답 없이 검증해야 한다.
- 원본 대조: 축약 전 원문과 SHA256 은 레포 밖 `~/Desktop/Development/edge/.dev/alpha-1130-live/`(SHA256SUMS)에 있다.
- 줄 수를 줄이려고 한 행을 한 줄로 접는 포맷 변경은 하지 않는다(리뷰에서 내용을 가린다).
