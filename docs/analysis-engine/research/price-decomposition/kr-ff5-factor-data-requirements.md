---
doc_type: design
status: Draft
owner: research
created: 2026-07-11
updated: 2026-07-11
related:
  - STATE.md
---

> **모듈 스펙:** KR FF5 팩터 빌드 — `src/alphamale/analytics/factors/` 패키지와 `uv run alphamale analytics factors ...` surface.
> **상태:** 요구사항/타깃-스테이트 문서 — 구현된 `ff5/`와 일부 차이 (코드 대조 2026-06-20).
> **드리프트:** ① rf 소스 — 본 문서는 FinanceDataReader `KR3YT=RR`, 실제 코드는 pykrx `국고채3년`(KRX 장외 MDCSTAT11402; `src/alphamale/analytics/factors/external_data.py:399-418`, `src/alphamale/analytics/factors/config.py:15`), US rf는 Ken French RF(`src/alphamale/analytics/factors/load_us_public_ff5.py`). ② 편성일 — 문서는 6월 말일/7월~익년6월 보유, 코드는 6/1 이후 첫 거래일(`src/alphamale/analytics/factors/factors.py:20-34`). ③ full-market 히스토리/코퍼레이트액션 테이블 미구현 — 코드는 KIND/status+가격이력 기반 strict universe(`src/alphamale/analytics/factors/universe.py`).
> **통합:** 구 'FF Factor User Decisions Before Implementation.md'는 아래 [부록 A]로 흡수됨.

# Korea Fama-French Factor Data Requirements

## 1. 목적

이 문서는 **한국 시장 전체 기준 Fama-French 5요인(FF5)을 정확하게 구현하기 위해 추가로 필요한 데이터와 운영 규칙**을 정의한다. 특히 SMB(size) 구현에서 중요한 점은, **현재 내부에서 사용하는 선택 종목 universe 기반 FF 호환 팩터**와 **한국 시장 전체 기준 FF 팩터**가 같지 않다는 점이다.

이 문서의 범위는 다음과 같다.

- 대상 시장: 한국 시장 전체 기준 FF5
- 권장 기본 universe: **KOSPI 보통주 전체**
- 대체 universe: 사용자가 별도로 정의한 **target investable universe 전체**
- 산출 주기: 일별 factor return
- 편성 기준: 연 1회 정기 편성(권장), 월별 편성(대안)

이 문서는 선택 종목 몇 개만 뽑아 만든 내부 요인을 부정하지 않는다. 다만 그것은 **internal/selected-universe factor**이며, **full Korean-market factor**와는 별도 산출물로 관리해야 한다.

---

## 2. 기존 문서와의 관계

`Data Source & Dataset Construction Spec.md`는 현재 운영 데이터셋과 선택 종목 기반 내부 FF 호환 팩터를 정의한다. 이 문서는 그 문서를 대체하지 않고, **한국 시장 전체 기준 FF 정확도 요건을 추가로 정의하는 부속 문서**다.

핵심 구분:

- 기존 문서의 `FF_*`: 현재 선택 종목 universe와 기존 운영 데이터로 계산하는 **내부 호환 버전**
- 이 문서의 `KR market FF_*`: KOSPI 전체 또는 사전에 정의한 전체 investable universe를 기준으로 계산하는 **시장대표 버전**

### 2.1 링크 방식

`docs/archive/research/price-decomposition/data-source-dataset-construction-spec.md` [INFERENCE]의 **8장(Fama-French 호환 팩터 정의) 첫 문단 바로 아래**에 다음 링크를 추가해 연결한다.

```md
- 한국 시장 전체 기준 FF 정확구현 요구사항: [Korea Fama-French Factor Data Requirements](./kr-ff5-factor-data-requirements.md)
```

권장 문구:

```md
주의: 본 문서의 FF_*는 선택 종목 universe 기준 내부 호환 팩터다. KOSPI 전체 기준 한국시장 FF 정확구현 요구사항은 [Korea Fama-French Factor Data Requirements](./kr-ff5-factor-data-requirements.md)를 따른다.
```

---

## 3. 왜 선택 종목 기반 FF와 한국시장 FF가 다른가

### 3.1 SMB breakpoint는 전체 시장 분포를 봐야 한다

SMB는 단순히 “현재 우리가 들고 있는 종목 중 작은 종목”을 고르는 요인이 아니다. SMB의 핵심은 **편성일 시점 전체 기준 universe의 시가총액 분포**에서 small/big를 가르는 것이다.

예를 들어 현재 선택 종목이 대형주 위주라면:

- 선택 종목 universe 중앙 시가총액은 실제 KOSPI 중앙값보다 훨씬 커질 수 있다.
- 그러면 실제로는 중형주인 종목이 내부 계산에서는 `Small`로 잘못 분류될 수 있다.
- 반대로 KOSPI 전체에서 진짜 소형주인 종목은 애초에 universe 밖이라 SMB에 반영되지 않는다.

이 경우 내부 `SMB`는 아래 의미가 된다.

```text
SMB_internal != SMB_KOSPI_market
SMB_internal = "선택 종목 집합 내부에서 작은 종목 minus 큰 종목"
SMB_market   = "KOSPI 전체에서 작은 종목 minus 큰 종목"
```

즉, **selected-universe SMB는 full-market SMB의 대체재가 아니다.**

### 3.2 HML/RMW/CMA도 전체 시장 coverage가 필요하다

SMB만 왜곡되는 것이 아니다.

- HML: book-to-market 분포의 상/중/하 breakpoint가 달라진다.
- RMW: profitability 분포가 대형 수익주 중심으로 치우칠 수 있다.
- CMA: 자산증가율 분포가 특정 업종/성장주 위주로 왜곡될 수 있다.
- MKT-RF: 엄밀하게는 전체 eligible universe의 value-weighted market return이 필요하다.

따라서 정확한 KR FF5를 원하면 **편성 대상 전체 universe의 가격·시총·재무·가용일자**가 필요하다.

---

## 4. 최소 내부 구현 vs 정확한 시장 구현

| 구분 | 최소 내부 구현 | 정확한 한국시장 구현 |
|---|---|---|
| 대상 universe | 현재 선택 종목 | KOSPI 전체 또는 사전 정의한 target investable universe 전체 |
| Size breakpoint | 선택 종목 집합 내부 median | 전체 universe 시가총액 median |
| Value/OP/INV breakpoint | 선택 종목 분포 기반 30/40/30 | 전체 universe 분포 기반 30/40/30 |
| 종목 coverage | 운영 중인 일부 종목만 | 상장/상폐 포함 전체 eligible 종목 |
| 시장수익률 | KOSPI 지수 proxy 가능 | **전체 eligible 종목 value-weighted return 권장** |
| 재무 coverage | 선택 종목만 | 전체 eligible 종목의 as-of 재무값 |
| survivorship 처리 | 약함 | 과거 constituent, 상폐종목 포함 필수 |
| 용도 | 내부 모델 feature, 상대 비교 | 시장대표 factor research, 백테스트, 논문형 비교 |

운영 원칙:

1. 기존 내부 `FF_*`를 유지해도 된다.
2. 다만 **시장대표 KR FF5는 별도 테이블/코드로 분리**한다.
3. 같은 이름으로 덮어쓰지 않는다.

---

## 5. 권장 운영 기본값

월별/연간 편성 모두 가능하지만, **정확성과 재현성을 우선하면 연 1회 정기 편성**을 기본값으로 둔다.

### 5.1 권장 기본값

- 기준 universe: `KOSPI 보통주 전체`
- 편성일: **매년 6월 마지막 거래일**
- 보유기간: **해당 연도 7월 첫 거래일 ~ 다음 해 6월 마지막 거래일**
- Size 기준: 편성일 `market_equity_june`
- B/M 기준: 직전 회계연도 `book_equity / previous_december_market_equity`
- OP 기준: 직전 회계연도 `operating_profitability`
- INV 기준: 직전 회계연도 `asset_growth`
- 포트폴리오 수익률: **일별 lagged market cap 가중(value-weighted)**
- 시총 기준: **free-float 조정값을 쓸지 여부를 사전에 고정**하고, 전 기간 동일하게 사용

### 5.2 대안

| 옵션 | 설명 | 장점 | 단점 |
|---|---|---|---|
| 월별 full re-sort | 매월 size/value/op/inv breakpoint 재산출 | 빠른 반영 | 표준 FF와 거리, turnover 증가 |
| 연간 6월 re-sort | 6월 1회 편성 후 1년 유지 | 표준 FF와 가장 유사, 안정적 | 최신 재무 반영이 느림 |

이 문서의 권장 기본값은 **연간 6월 re-sort**다.

---

## 6. 데이터 요구사항 요약 표

| 데이터 영역 | 필수 필드/개념 | 소스 후보 | 적재 대상 제안 (`edge.etf`) | 목적 |
|---|---|---|---|---|
| 역사적 universe | 기준일별 구성종목, 편입/편출 | KRX, Naver Finance, 거래소 데이터 벤더, 수집 스냅샷 | `kr_universe_constituent_history` | 해당 시점 실제 eligible universe 복원 |
| 종목 마스터 | 표준 ticker, 종목명, 보통주/우선주 구분, 상장시장 | KRX 상장법인 목록, DART corp code, 내부 매핑 | `kr_security_master` | 종목 identity 고정, 우선주/ETF 제외 |
| ticker 매핑 | KRX code, 내부 ticker, DART corp_code, ISIN | KRX + DART | `kr_ticker_map` | 소스 간 join 안정화 |
| 상장/상폐 이력 | listing_date, delisting_date, trading_halt | KRX, DART, 벤더 master | `kr_listing_lifecycle` | survivorship bias 제거 |
| 기업행위 | 액면분할, 병합, 배당락, 무상/유상증자, 권리락 | KRX, DART, 가격 벤더 corporate action feed | `kr_corporate_action` | price/share continuity 보정 |
| 일별 가격/수익률 | open, high, low, close, adj_close, volume, return | KRX/벤더 OHLCV | `kr_price_daily` | 개별 종목 수익률, market equity 계산 |
| 일별 시가총액 | close, shares, market_cap, free_float_mkt_cap | KRX, FnGuide류, 벤더 | `kr_market_cap_daily` | SMB breakpoint, value weighting |
| 주식수 | shares_outstanding, treasury_shares, free_float_shares | DART, KRX, 벤더 | `kr_shares_daily` | market equity / free-float 시총 계산 |
| 재무제표 | 자본, 자산, 매출, 매출원가, 판관비, 이자비용 등 | DART, dartlab, 벤더 fundamentals | `raw_fundamental_statement`, `security_fundamental_daily` | B/M, OP, INV 산출 |
| 공시 가용일자 | disclosure_date, available_date | DART 공시수신시각 + ingestion 기록 | `raw_fundamental_statement`, `security_fundamental_daily` | look-ahead 방지 |
| 무위험수익률 | 한국 3년 국고채 yield | FinanceDataReader | `raw_risk_free_yield_daily`, `risk_free_daily` | `RF_t` 계산 |
| 편성 스냅샷 | 편성일 breakpoint, bucket 결과 | 내부 계산 산출물 | `kr_ff_formation_snapshot` | 재현성/검증 |
| 포트폴리오 수익률 | bucket별 일별 value-weighted return | 내부 계산 산출물 | `kr_ff_portfolio_return_daily` | FF5 factor 계산 |
| 최종 팩터 | MKT-RF, SMB, HML, RMW, CMA | 내부 계산 산출물 | `kr_ff_factor_daily` | 모델/리서치 입력 |

---

## 7. KOSPI universe 데이터 요구사항

정확한 SMB를 위해 필요한 minimum KOSPI market-wide 데이터는 아래와 같다.

### 7.1 역사적 constituent list

필수 이유:

- breakpoint는 **그 시점 실제 KOSPI 구성종목 전체**를 기준으로 계산해야 한다.
- 현재 시점 구성종목만 있으면 과거 상폐/편출 종목이 사라져 survivorship bias가 생긴다.

필수 필드:

| 필드 | 설명 |
|---|---|
| `universe_code` | 예: `KOSPI_COMMON` |
| `as_of_date` | 구성 유효 기준일 |
| `ticker` | 내부 표준 ticker |
| `membership_status` | `IN` / `OUT` |
| `effective_from` | 편입 효력 시작일 |
| `effective_to` | 편출 효력 종료일 |
| `inclusion_reason` | 신규상장, 지수편입, 시장이동 등 |
| `exclusion_reason` | 상폐, 지수편출, 관리종목 제외 등 |
| `source_vendor` | 출처 |

### 7.2 ticker mapping

필수 이유:

- 한국 데이터는 소스마다 ticker 체계가 다를 수 있다.
- DART, KRX, 벤더, 내부 DB를 안정적으로 join하려면 영속 식별자 매핑이 필요하다.

필수 필드:

| 필드 | 설명 |
|---|---|
| `ticker` | 내부 표준 ticker |
| `krx_code` | 6자리 거래소 코드 |
| `dart_corp_code` | DART 법인코드 |
| `isin` | 가능하면 저장 |
| `security_name_kr` | 한글 종목명 |
| `share_class` | `COMMON`, `PREFERRED`, 기타 |
| `market_code` | `KOSPI`, 필요시 `KOSDAQ` |
| `valid_from` | 매핑 시작일 |
| `valid_to` | 매핑 종료일 |

### 7.3 listing/delisting lifecycle

필수 필드:

| 필드 | 설명 |
|---|---|
| `ticker` | 종목 |
| `listing_date` | 최초 상장일 |
| `delisting_date` | 상장폐지일 |
| `last_trade_date` | 마지막 거래일 |
| `trading_status` | 정상, 정지, 상폐예정 등 |
| `status_reason` | 사유 |

### 7.4 corporate actions

필수 이유:

- 분할/병합/유무상증자 누락 시 price continuity와 shares continuity가 깨진다.
- SMB는 시총 기반이므로 가격과 주식수 둘 다 보정되어야 한다.

필수 필드:

| 필드 | 설명 |
|---|---|
| `ticker` | 종목 |
| `action_type` | split, reverse_split, rights, bonus, cash_dividend 등 |
| `ex_date` | 권리락/배당락 기준일 |
| `record_date` | 기준일 |
| `pay_date` | 지급일 |
| `split_ratio` | 분할비율 |
| `share_change_ratio` | 주식수 변동비율 |
| `cash_amount_per_share` | 현금배당 단가 |
| `source_vendor` | 출처 |

### 7.5 prices / returns

필수 필드:

| 필드 | 설명 |
|---|---|
| `trade_date` | 거래일 |
| `ticker` | 종목 |
| `open` | 시가 |
| `high` | 고가 |
| `low` | 저가 |
| `close` | 종가 |
| `adj_close` | 기업행위 반영 종가 |
| `volume` | 거래량 |
| `trading_value` | 거래대금 |
| `ret_1d` | 일수익률 |
| `currency_code` | `KRW` |

권장:

- factor return 계산용 종가와 market equity 계산용 종가를 같은 기준으로 맞춘다.
- total return까지 구현하지 않을 것이면, 배당 처리 기준을 문서에 고정한다.

### 7.6 market cap / shares outstanding / free float

필수 이유:

- SMB breakpoint 자체가 시총 분포에서 결정된다.
- value-weighted portfolio return도 시총이 필요하다.
- free-float 기준을 쓰면 전 기간 동일해야 하며, 그냥 shares outstanding 기준과 혼용하면 안 된다.

필수 필드:

| 필드 | 설명 |
|---|---|
| `trade_date` | 거래일 |
| `ticker` | 종목 |
| `close` | 기준 종가 |
| `shares_outstanding` | 발행주식수 |
| `treasury_shares` | 자기주식 |
| `free_float_shares` | 유통주식수(사용 시) |
| `market_cap` | `close * shares_outstanding` 또는 벤더 제공값 |
| `free_float_market_cap` | `close * free_float_shares` |
| `cap_basis` | `TOTAL` 또는 `FREE_FLOAT` |

권장 기본값:

- 거래소/벤더에서 free-float 표준이 안정적으로 제공되면 `FREE_FLOAT` 사용 가능
- 그렇지 않으면 **`TOTAL` basis를 고정**하고, 전체 기간 동일 기준 유지

---

## 8. FF5 구현에 필요한 재무 데이터

정확한 FF5를 위해 필요한 최소 펀더멘털은 아래와 같다.

### 8.1 필수 재무 필드

| 필드 | 정의 | 용도 |
|---|---|---|
| `fiscal_year_end` | 회계연도 종료일 | 어떤 재무를 어느 편성연도에 사용할지 결정 |
| `announcement_date` | 공시 발표일 | 공시 시점 추적, 정정공시 우선순위 판단 |
| `report_available_date` | 실제 사용 가능일 | look-ahead 방지 |
| `book_equity` | 보통주주 귀속 장부가치 | HML 분모/분자 |
| `market_equity` | 시가총액 | Size, B/M |
| `operating_profitability` | 수익성 지표 | RMW |
| `total_assets_current` | 당기 총자산 | CMA |
| `total_assets_prior` | 전기 총자산 | CMA |
| `investment` | 자산증가율 | CMA |

### 8.2 정확도를 높이기 위한 원시 계정 권장 필드

`operating_profitability`를 단순 벤더 산식에만 의존하지 않으려면 아래 raw 계정도 권장한다.

| 필드 | 설명 |
|---|---|
| `revenue` | 매출액 |
| `cogs` | 매출원가 |
| `sga` | 판매비와관리비 |
| `interest_expense` | 이자비용 |
| `total_equity_attributable` | 지배주주지분 |
| `preferred_stock` | 우선주 조정값 |
| `deferred_tax_assets_liabilities` | book equity 보정 시 사용 가능 |

### 8.3 권장 산식

```text
market_equity_t = close_t * shares_basis_t

book_to_market_for_june_y
  = book_equity_fye_{y-1} / market_equity_dec_{y-1}

operating_profitability_fye_{y-1}
  = operating_income_fye_{y-1} / book_equity_fye_{y-1}

investment_fye_{y-1}
  = (total_assets_fye_{y-1} - total_assets_fye_{y-2})
    / total_assets_fye_{y-2}
```

운영 메모:

- `report_available_date <= formation_date`를 만족하는 재무만 사용한다.
- 동일 회계연도에 정정공시가 여러 번 있으면, 편성일 이전에 실제 이용 가능했던 가장 최신 공시를 사용한다.
- book equity가 0 이하인 종목은 HML 편성에서 제외하는 것이 안전하다.

---

## 9. 무위험수익률 요구사항

한국 무위험수익률은 **FinanceDataReader의 3년 국고채 수익률**을 사용한다.

### 9.1 소스

| 항목 | 값 |
|---|---|
| source_vendor | `FinanceDataReader` |
| source_symbol | `KR3YT=RR` |
| internal benchmark_code | `KR_GOVT_3Y` |
| 단위 | 연율 `%` |

### 9.2 일별 변환식

권장 일별 변환식:

```text
RF_t = (1 + annual_yield_pct_t / 100)^(1 / 252) - 1
```

조인 규칙:

1. 같은 거래일 관측치가 있으면 그 값을 사용한다.
2. 국채시장이 휴장인 날에는 `t` 이전 최근 관측치를 carry-forward 할 수 있다.
3. 미래 관측값 backfill은 금지한다.

---

## 10. 한국시장 FF5 공식

이 절은 **KOSPI 전체 또는 target investable universe 전체**를 기준으로 하는 공식이다.

### 10.1 표기

```text
S / B = Small / Big
H / N / L = High / Neutral / Low book-to-market
R / N / W = Robust / Neutral / Weak profitability
C / N / A = Conservative / Neutral / Aggressive investment
```

### 10.2 breakpoint 정의

권장 기본값(연간 6월 편성):

- Size: 편성일 전체 universe의 `market_equity_june` 중앙값 기준 `S/B`
- Value: eligible 종목의 `book_to_market` 분포 상위 30%, 중위 40%, 하위 30%
- Profitability: eligible 종목의 `operating_profitability` 상위 30%, 중위 40%, 하위 30%
- Investment: eligible 종목의 `investment` 하위 30%, 중위 40%, 상위 30%

중요:

- 위 breakpoint는 **전체 eligible universe**에서 계산한다.
- 현재 내부 selected universe에서 계산하면 시장대표 FF가 아니다.

### 10.3 MKT-RF

엄밀한 권장식:

```text
VWRET_market_t
  = Σ_i (w_{i,t-1} * ret_{i,t})
  where i ∈ 전체 eligible universe

MKT_RF_t = VWRET_market_t - RF_t
```

여기서:

```text
w_{i,t-1} = market_cap_{i,t-1} / Σ_j market_cap_{j,t-1}
```

주의:

- `^KS11` 또는 KOSPI 지수 수익률은 **internal proxy**로는 쓸 수 있어도, strict full-market FF의 최우선 정의는 아니다.
- 정확 구현에서는 **동일 universe의 value-weighted market return**을 권장한다.

### 10.4 SMB

먼저 3개 independent sort에서 SMB 하위 구성요인을 만든다.

```text
SMB_BM_t  = (SH_t + SN_t + SL_t) / 3 - (BH_t + BN_t + BL_t) / 3
SMB_OP_t  = (SR_t + SN_t + SW_t) / 3 - (BR_t + BN_t + BW_t) / 3
SMB_INV_t = (SC_t + SN_t + SA_t) / 3 - (BC_t + BN_t + BA_t) / 3

SMB_t = (SMB_BM_t + SMB_OP_t + SMB_INV_t) / 3
```

각 leg return은 bucket 내부 종목의 value-weighted return이다.

### 10.5 HML

```text
HML_t = (SH_t + BH_t) / 2 - (SL_t + BL_t) / 2
```

### 10.6 RMW

```text
RMW_t = (SR_t + BR_t) / 2 - (SW_t + BW_t) / 2
```

### 10.7 CMA

```text
CMA_t = (SC_t + BC_t) / 2 - (SA_t + BA_t) / 2
```

### 10.8 월별/연간 편성 선택지

| 항목 | 월별 편성 | 연간 편성(권장) |
|---|---|---|
| Size breakpoint | 매월 재계산 | 6월 1회 계산 |
| B/M, OP, INV breakpoint | 매월 재계산 가능 | 6월 1회 계산 |
| bucket membership | 매월 변경 | 1년 고정 |
| turnover | 높음 | 낮음 |
| 표준 FF 유사성 | 낮음 | 높음 |
| 운영 복잡도 | 높음 | 중간 |

권장 운영 기본값은 **연간 편성 + 일별 value-weighted portfolio return**이다.

---

## 11. PostgreSQL `edge.etf` 데이터 모델 제안

기존 내부 selected-universe FF와 혼동하지 않도록, 한국시장 전체 버전은 별도 테이블로 두는 것이 안전하다.

### 11.1 universe / master 계층

```sql
CREATE TABLE etf.kr_security_master (
    ticker TEXT PRIMARY KEY,
    krx_code TEXT NOT NULL,
    dart_corp_code TEXT,
    isin TEXT,
    security_name_kr TEXT NOT NULL,
    share_class TEXT NOT NULL,
    market_code TEXT NOT NULL,
    listing_date DATE,
    delisting_date DATE,
    is_etf BOOLEAN NOT NULL DEFAULT FALSE,
    is_spac BOOLEAN NOT NULL DEFAULT FALSE,
    is_reit BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE etf.kr_ticker_map (
    ticker TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_symbol TEXT NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE,
    PRIMARY KEY (ticker, source_system, source_symbol, valid_from)
);

CREATE TABLE etf.kr_listing_lifecycle (
    ticker TEXT NOT NULL,
    listing_date DATE,
    delisting_date DATE,
    last_trade_date DATE,
    trading_status TEXT,
    status_reason TEXT,
    source_vendor TEXT NOT NULL,
    PRIMARY KEY (ticker)
);

CREATE TABLE etf.kr_universe_constituent_history (
    universe_code TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    membership_status TEXT NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    inclusion_reason TEXT,
    exclusion_reason TEXT,
    source_vendor TEXT NOT NULL,
    PRIMARY KEY (universe_code, as_of_date, ticker)
);
```

### 11.2 price / cap 계층

```sql
CREATE TABLE etf.kr_price_daily (
    trade_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    open NUMERIC(20,8),
    high NUMERIC(20,8),
    low NUMERIC(20,8),
    close NUMERIC(20,8) NOT NULL,
    adj_close NUMERIC(20,8),
    volume NUMERIC(28,4),
    trading_value NUMERIC(28,4),
    ret_1d NUMERIC(20,12),
    source_vendor TEXT NOT NULL,
    PRIMARY KEY (trade_date, ticker)
);

CREATE TABLE etf.kr_shares_daily (
    trade_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    shares_outstanding NUMERIC(28,8),
    treasury_shares NUMERIC(28,8),
    free_float_shares NUMERIC(28,8),
    source_vendor TEXT NOT NULL,
    PRIMARY KEY (trade_date, ticker)
);

CREATE TABLE etf.kr_market_cap_daily (
    trade_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    cap_basis TEXT NOT NULL,
    close NUMERIC(20,8) NOT NULL,
    shares_basis NUMERIC(28,8) NOT NULL,
    market_cap NUMERIC(28,8) NOT NULL,
    source_vendor TEXT NOT NULL,
    PRIMARY KEY (trade_date, ticker, cap_basis)
);
```

### 11.3 corporate action 계층

```sql
CREATE TABLE etf.kr_corporate_action (
    ticker TEXT NOT NULL,
    action_type TEXT NOT NULL,
    ex_date DATE NOT NULL,
    record_date DATE,
    pay_date DATE,
    split_ratio NUMERIC(20,8),
    share_change_ratio NUMERIC(20,8),
    cash_amount_per_share NUMERIC(20,8),
    source_vendor TEXT NOT NULL,
    PRIMARY KEY (ticker, action_type, ex_date)
);
```

### 11.4 FF 편성/산출 계층

```sql
CREATE TABLE etf.kr_ff_formation_snapshot (
    formation_date DATE NOT NULL,
    universe_code TEXT NOT NULL,
    ticker TEXT NOT NULL,
    cap_basis TEXT NOT NULL,
    market_equity_june NUMERIC(28,8) NOT NULL,
    market_equity_dec_prev NUMERIC(28,8),
    book_equity NUMERIC(28,8),
    book_to_market NUMERIC(28,12),
    operating_profitability NUMERIC(28,12),
    investment NUMERIC(28,12),
    report_available_date DATE,
    size_bucket TEXT,
    value_bucket TEXT,
    profitability_bucket TEXT,
    investment_bucket TEXT,
    PRIMARY KEY (formation_date, universe_code, ticker, cap_basis)
);

CREATE TABLE etf.kr_ff_portfolio_return_daily (
    trade_date DATE NOT NULL,
    universe_code TEXT NOT NULL,
    formation_date DATE NOT NULL,
    portfolio_code TEXT NOT NULL,
    leg_type TEXT NOT NULL,
    constituent_count INTEGER NOT NULL,
    vwret NUMERIC(20,12),
    PRIMARY KEY (trade_date, universe_code, formation_date, portfolio_code)
);

CREATE TABLE etf.kr_ff_factor_daily (
    trade_date DATE NOT NULL,
    universe_code TEXT NOT NULL,
    formation_date DATE NOT NULL,
    rf NUMERIC(20,12) NOT NULL,
    mkt_rf NUMERIC(20,12) NOT NULL,
    smb NUMERIC(20,12),
    hml NUMERIC(20,12),
    rmw NUMERIC(20,12),
    cma NUMERIC(20,12),
    eligible_universe_count INTEGER NOT NULL,
    cap_basis TEXT NOT NULL,
    rebalance_frequency TEXT NOT NULL,
    PRIMARY KEY (trade_date, universe_code, formation_date, cap_basis, rebalance_frequency)
);
```

설계 원칙:

1. `universe_code`, `cap_basis`, `rebalance_frequency`를 명시 저장한다.
2. 내부 selected-universe 팩터와 같은 테이블에 섞지 않는다.
3. formation snapshot을 남겨 breakpoint 재현성을 확보한다.

---

## 12. 편성 eligibility 규칙

권장 기본 eligibility:

- `share_class = COMMON`
- ETF, ETN, REIT, SPAC 제외
- 편성일 기준 상장 상태가 유효해야 함
- 편성일 이전에 가격/시총 확보 가능해야 함
- HML 편성에는 `book_equity > 0` 권장
- RMW/CMA 편성에는 필요한 재무값이 모두 존재해야 함
- `report_available_date <= formation_date` 필수

Universe 정의는 문서 첫머리에 고정하고, 중간에 바꾸지 않는다.

---

## 13. 검증 체크리스트

### 13.1 universe 완전성

- 각 편성일 `eligible_universe_count`가 외부 기준 KOSPI 종목 수와 크게 다르지 않아야 한다.
- 상폐 종목이 과거 구간에서 사라지지 않아야 한다.
- 특정 날짜에 constituent 수가 비정상 급변하면 source 변경 또는 매핑 오류를 점검한다.

### 13.2 시총 일관성

검증식:

```text
abs(market_cap - close * shares_basis) / market_cap < tolerance
```

권장 tolerance:

- 벤더 제공 시총 사용 시: `1%`
- 직접 계산 시: `0.1%`

### 13.3 재무 as-of 무누수

- 모든 편성행에서 `report_available_date <= formation_date`
- 정정공시가 있는 경우 편성일 시점에 아직 공개되지 않은 값은 사용 금지
- 미래 재무값 backfill 금지

### 13.4 breakpoint sanity

각 편성일마다 다음을 확인한다.

- `S`와 `B` 모두 비어 있지 않음
- `H/N/L`, `R/N/W`, `C/N/A` 모두 최소 표본 수 확보
- median/quantile 계산이 전체 eligible universe 기준으로 수행됨
- selected universe 기준 breakpoint와 혼용되지 않음

### 13.5 factor arithmetic

- `MKT_RF_t + RF_t = VWRET_market_t`가 허용오차 내에서 성립
- `SMB`, `HML`, `RMW`, `CMA`가 각 leg return으로 재계산 가능
- 결측 bucket 때문에 factor가 비는 날짜는 원인을 로그로 남김

### 13.6 내부 factor와의 차이 검증

의도된 차이를 확인한다.

- 내부 selected-universe SMB와 market-wide SMB가 동일하게 나오면 오히려 breakpoint 범위가 잘못되었을 가능성이 있다.
- 특히 대형주 편중 시기에는 market-wide SMB와 internal SMB의 level/volatility 차이가 나타나는 것이 자연스럽다.

---

## 14. 수용 기준

이 문서 기준 구현이 준비되었다고 보려면 아래를 만족해야 한다.

1. **Universe 복원 가능**
   - 과거 임의의 편성일에 KOSPI 전체 eligible 종목 목록을 재생성할 수 있다.
2. **SMB breakpoint 재현 가능**
   - 편성일 전체 universe 시총 median을 저장/재계산할 수 있다.
3. **재무 무누수 보장**
   - 모든 FF 편성에 `report_available_date <= formation_date`가 강제된다.
4. **MKT-RF 시장정합성 확보**
   - KOSPI index proxy가 아니라 전체 eligible universe value-weighted return으로 `MKT-RF`를 계산할 수 있다.
5. **FF5 전 항목 산출 가능**
   - `MKT-RF`, `SMB`, `HML`, `RMW`, `CMA` 모두 daily series 생성 가능
6. **재현성 확보**
   - 각 편성연도별 breakpoint와 bucket membership이 `kr_ff_formation_snapshot`에 남는다.
7. **내부 factor와 분리 저장**
   - 기존 selected-universe `FF_*`와 market-wide KR FF5가 별도 산출물로 구분된다.

---

## 15. 구현상 결론

정확한 한국시장 Fama-French, 특히 SMB를 만들려면 필요한 것은 “몇 종목의 가격”이 아니라 아래 전체다.

- **해당 시점 전체 KOSPI 또는 target investable universe constituent history**
- **그 전체 universe의 일별 시가총액과 주식수**
- **상장/상폐/기업행위 이력**
- **회계연도 기준 재무와 실제 사용가능일자**
- **한국 3년 국고채 기반 일별 RF**

따라서 현재 선택 종목 universe 기반 내부 FF는 계속 사용할 수 있지만, 그것을 한국시장 FF로 명명하거나 SMB benchmark로 사용하는 것은 부정확하다. **시장대표 FF가 필요하면 breakpoint와 weighting 모두 전체 universe 기준으로 다시 계산해야 한다.**

---

## 부록 A: FF 설계 확정 결정 (구 *FF Factor User Decisions Before Implementation*)

구현 전 사용자 결정 중 `ff5/`에 반영/대조된 항목 (코드 대조 2026-06-20):

- **고정 universe(모델 입력):** KR IT 삼성전자·SK하이닉스·삼성전기 / KR 산업재 SK스퀘어·LG에너지솔루션·삼성물산 / KR 금융 삼성생명·KB금융·신한금융 / US IT NVDA·AAPL·MSFT / US 산업재 CAT·GE·RTX / US 금융 BRK-B·JPM·V. 운영 시장요인 US=Nasdaq, KR=KOSPI 고정. High-Corr·Sentiment 팩터 미생성.
- **KR 편성 universe:** KOSPI 보통주 전체(권장 기본값) — 코드 기본 scope KOSPI(`ff5/config.py`).
- **시총 기준:** 총시가총액(권장) — 코드도 총시총 가중.
- **재편성 주기:** 연 1회 6월 re-sort — 코드 annual June formation(`ff5/factors.py`).
- **수익률 기준:** 문서 권장은 배당조정 total return이나 코드는 close-to-close 가격수익률(`ff5/database.py`) → **미반영 드리프트**.
- **금융주 처리:** 문서 권장 'MKT-RF 포함 / HML·RMW·CMA 정렬 제외'이나 코드는 strict universe에서 금융·보험 전면 제외(`src/alphamale/analytics/factors/universe.py`) → **드리프트**.
