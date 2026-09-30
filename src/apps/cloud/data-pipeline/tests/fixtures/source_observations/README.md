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

설계 §10.8 의 실측이다. 구조·필드명·값은 실응답 그대로고, 행만 줄였다(재무제표는 아래 "DART 재무제표 축약" 규칙,
KIS 마스터는 선택 종목 줄만 재압축, 업종명 파일은 전체, EIA 는 16행 중 4행). 인증키·요청 URL 은 없다. 원문 전체는 레포 밖에 보존한다(아래 "원문 보존").

| 파일 | 원천 | 확인한 것 |
|---|---|---|
| `ecos_kr_10y_yield.json` | ECOS 817Y002/D/010210000 (발급 키, 09-15~29) | ITEM_NAME1=국고채(10년), UNIT_NAME=연%, TIME=YYYYMMDD. 추석 휴장 09-24·25 행 없음 |
| `ecos_usd_krw_ecos_731Y003.json` | ECOS 731Y003/D/0000003 (발급 키, 09-15~29) | 원/달러(종가 15:30), 단위 원. KRX 휴장일(09-24·25)에도 행이 있다(출처 미확인) |
| `kosis_cpi_yoy.json`·`kosis_meta_itm.json` | KOSIS DT_1J22042 T03 데이터(2026-06~08)·항목 메타(getMeta ITM) | T03=전년동월비(%)·Change over the same month of last year·총지수. **UNIT_NM 은 데이터·메타 모두 없음**(T02 만 %) |
| `eia_brent_spot.json` | EIA v2 petroleum/pri/spt RBRTE 2026-09 (16행 중 4행) | series-description=Europe Brent Spot Price FOB (Dollars per Barrel), units=$/BBL, daily, 최신 09-22 |
| `dart_shares_*_2025_11011.json` | 사업보고서 주식총수(삼성·하이닉스) | stlm_dt=2025-12-31 — 기준일=보고기간 말 검사와 일치 |
| `fmp_treasury_rates.json` | FMP stable treasury-rates (dev 키) | 만기별 % 숫자, `year10` |
| `kis_*.mst.zip` | KIS 공개 마스터 | 고정폭 227/221, 업종명 `[5:45]`(헤더가 맞고 공식 샘플 `[3:43]` 은 틀림), 소분류는 전 종목 0000 |
| `dart_*` | OpenDART (dev 키) 삼성전자·SK하이닉스·고려제강 | 3개월/누적 필드, KRW, 연결/별도, IS 없는 회사(하이닉스)는 CIS, 주식총수 행·stlm_dt, 정정본 접수번호 |


## DART fixture 검토(2026-09-30, PR #1012)

### 파일별 역할

| 파일 | 원천·표본 특징 | 쓰는 테스트(`test_source_observations_live.py`) | 검증하는 것 | 대체 가능? |
|---|---|---|---|---|
| `dart_stmt_samsung_2026_11012_CFS` | 삼성 반기 연결, IS 있음, 우선주 있는 회사 | `quarter_vs_cumulative`·`bps_common_share_only` | 3개월/누적 필드, EPS, 지배기업 소유주지분(연결 BPS 분자) | 아니오 — 같은 회사 OFS·다른 기간과 값이 다르다 |
| `dart_stmt_samsung_2026_11012_OFS` | 같은 보고서 별도 | `quarter_vs_cumulative` | 별도 EPS 가 연결과 섞이지 않고 따로 남는다 | 아니오(연결/별도 분리의 유일한 실표본) |
| `dart_stmt_samsung_2025_11011_CFS` | 사업보고서(연간) | `quarter_vs_cumulative`·`annual_report_share_table` | 연간 `thstrm_amount`, Q4 = FY − 9M, 연간 주식총수 표와의 결합 | 아니오 |
| `dart_stmt_samsung_2025_11014_CFS` | 3분기보고서 | `quarter_vs_cumulative` | Q4 유도의 9M 누적 입력 | 아니오 |
| `dart_stmt_hynix_2026_11012_CFS` | IS 가 없고 CIS 만 있는 회사, 우선주 없음 | `company_without_income_statement`·`bps_common_share_only` | IS→CIS 대체, 보통주 BPS 산출 | 아니오 |
| `dart_stmt_goryeo_2026_11012_CFS` | 반기보고서 정정본(재무 API 는 정정본만 준다) | `correction_receipt` | 가시시각 = 정정 접수일 | 아니오 |
| `dart_list_*_p1` | 정기보고서 목록(원본·[기재정정] 포함) | 위 여섯 모두(수집 입력) | 접수번호 → 접수일, 정정본 판별 | 아니오 |
| `dart_shares_*` | 주식총수(반기 06-30·사업보고서 12-31) | `bps_*`·`annual_report_share_table` | 우선주·자기주식, 기준일 = 보고기간 말 | 아니오 |
| `source_observation_fakes.py` | **합성**(개발가이드 형태, 값은 예시) | `test_source_observations_financial.py`·PG e2e | 경계 조건(접수번호 혼재·음수·지표 0개·우선주 EPS 줄 등) — 실응답에 없는 경우 | 실응답으로 대체 불가(해당 사례가 실데이터에 없다) |

### DART 재무제표 축약

- 규칙: 파서가 값을 읽는 계정 6개(`ifrs-full_Revenue`·`dart_OperatingIncomeLoss`·기본/희석 EPS·
  `ifrs-full_EquityAttributableToOwnersOfParent`·`ifrs-full_Equity`)의 행은 전부 남긴다. SCE·CF 행도 남긴다. 나머지 계정은
  **구조가 다른 행만 하나씩** 남긴다(구조 = `sj_div` × 필드 집합 × 금액 필드 6개의 값 모양 — 빈 값·`-`·숫자).
  필드·값·중첩은 원문 그대로이고, 포맷은 원문과 같은 `indent=1` 이다.
- 규모: 6파일 67~85행 → 6~11행, 8,501 → 1,104줄. `dart_list_*`·`dart_shares_*` 는 한 줄 JSON 이던 것을 같은 포맷으로 펼쳤다
  (값은 왕복 비교로 동일 확인, 7파일 합 540줄).
- 축약 전후 검증력(`tests/` 밖 스크립트로 한 번 실행, 결과를 여기 남긴다). 방어를 하나씩 제거한 코드로 실응답 DART 테스트를 돌려
  두 fixture 모두 같은 수로 실패했다:

  | 제거한 방어(결함) | 원본 fixture | 축약 fixture |
  |---|---|---|
  | 분기 값을 누적 필드에서 읽음 | 2 실패 | 2 실패 |
  | IS 없을 때 CIS 대체 제거 | 2 실패 | 2 실패 |
  | 우선주 회사 보통주 BPS 차단 해제 | 1 실패 | 1 실패 |
  | 연결 BPS 분자를 자본총계로 바꿈 | 1 실패 | 1 실패 |
  | 가시시각을 목록의 최초 공시일로 | 2 실패 | 2 실패 |
  | 주식총수 기준일을 06-30 고정 | 1 실패 | 1 실패 |
  | (제거 없음) | 5 통과 | 5 통과 |

### 축약 전에도 잡지 못하던 것(새로 확인한 공백 — 이번 변경의 검증력과 별개)

- **같은 계정의 우선주 EPS 줄**: 실응답 여섯 파일 모두 대상 계정이 한 줄씩이다(원문 전체도 같다). `_pick_line` 의 우선주 제외는
  합성 테스트(`test_ambiguous_eps_lines_pick_common_share_or_refuse`)만 검증한다.
- **SCE 의 `ifrs-full_Equity`**: 원문에는 SCE 에 `ifrs-full_Equity` 가 5~8줄 있다(별도 BPS 분자와 같은 계정). "BS 에서만 고른다"는
  필터가 이것을 가르지만, 커밋된 fixture 는 이전 축약에서 그 줄들이 빠졌고, 별도(OFS) BPS 를 단언하는 테스트도 없다. 필요하면
  원문에서 그 줄을 되살려 OFS BPS 단언을 더한다(후속).
- **접수번호 혼재**: 실응답은 한 파일 한 접수번호라 `mixed_rcept_no` 는 합성 테스트만 검증한다.

### 원문 보존

- 위치: `~/Desktop/Development/edge/.dev/alpha-1130-live/`(메인 체크아웃의 미추적 `.dev`, 워크트리 `.dev` 는 심링크 — 워크트리를
  지워도 남는다). `dart/stmt_*` 가 축약 전 원문(145~247행)이다. 무결성은 `shasum -a 256 -c SHA256SUMS`(31건).
- CI 는 커밋된 fixture 만 쓴다(원문·실 API 에 의존하지 않는다). 원문으로 다시 축약하려면 위 규칙을 원문에 적용한다.
