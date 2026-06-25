# EDGE Event-Driven Return Model Specification

> 상태: **확정(개발 착수)**. 기존 `edge_ff5_factor_model_spec.md`(FF5 잔차 산출만 다루는 MVP)를
> 스크린샷 모델링 아키텍처(`스크린샷 2026-06-21 201802.png`) 기준으로 확장·대체한다. 그림이 단일 기준이다.
> 모든 설계 결정은 사용자 승인/인터젝션으로 확정됨(§11).

---

## 0. 한 줄 요약

당일 **종가(close)** 와 **고가(high)** 를, **당일 종가/고가를 입력으로 쓰지 않고** 그날 마감 시점에 확정된
데이터(FF5 팩터 + 당일 뉴스)만으로 예측하고, 예측치마다 **컨피던스** 를 함께 출력한다.

2-arm event-study 구조 + 그림의 3단 회귀를 그대로 유지한다:

```text
[Stage A] 선형회귀(FF5)  : r_t - rf_t = α + β·FF5_t + ε     → normal_return, abnormal_return(=ε)
[Stage B] NN(뉴스)       : 제목 → Stanza구조화 → 임베딩 → projection → MLP → news_score(+μ,σ)
[Stage C] 마지막 선형회귀 : abnormal_return ≈ news_score·β + α  (Stage B의 ε 설명)

predicted_close_logret = normal_return_logret + predicted_abnormal
predicted_close = close_{t-1} * exp(predicted_close_logret)
predicted_high  = predicted_close * exp(max(spread_hat, 0))   # spread=ln(high/close), 선형회귀
+ close_confidence, high_confidence ∈ [0,1]
```

수익률은 **로그 수익률**, 회귀 타깃은 **z-score 표준화** 후 적합하고 출력 시 역변환한다.

---

## 1. 아키텍처 매핑 (스크린샷 → 모듈)

| 그림 노드 | 의미 | 모듈 |
|---|---|---|
| Asset Mapper | 뉴스/가격 → 종목 정렬(alias) | `features/loaders.py` |
| Time Series(Factor Data) → Linear Regression → Abnormal Return | FF5 정상/비정상수익률 | `features/factor_arm.py` |
| `r_t = Factor·β + α + Ab return(ε)` | FF5 회귀식 | `features/factor_arm.py` |
| News Data(Title,1news) → Structurize → Embedding → NN → Scoring | 뉴스 점수화 arm | `features/news_arm.py`, `models/news_nn.py` |
| Filter Layer(`is_news_novelty`) | 중복 뉴스 제거 | `features/news_arm.py` (`common.nlp.dedupe_news`) |
| `Ab return = News Score·β + α + ε` | 최종 선형회귀 | `models/combine.py` |
| Calibration: error<2% & same direction → LLM / else Fail Log | 검증 게이트 | `calibration/gate.py` |
| Fail Log DB | 실패/스킵 적재 | `db/store.py` (SQLite) |
| LLM / Text | 사후 해설 | MVP 범위 밖(인터페이스 예약) |

용어: `ε`=잔차, `α`=절편, `β`=기울기(중요도). **Normal Return**=이벤트 없을 때 정상수익률(FF5 추정),
**Abnormal Return**=`actual − normal`(이벤트 기여).

---

## 2. 예측 목표 / 누설 규칙

### 2.1 타깃 (로그 수익률)

```text
close_logret_t = ln(close_t / close_{t-1})        # 종가 로그수익률
spread_t       = ln(high_t  / close_t)  (>= 0)     # 고가 스프레드 (high >= close 항상 성립)
```

복원:

```text
predicted_close_t = close_{t-1} * exp(predicted_close_logret_t)
predicted_high_t  = predicted_close_t * exp(max(predicted_spread_t, 0))
```

### 2.2 누설 금지 ("당일 종가 없이 그날 끝난 데이터만")

- `close_t, high_t, low_t, volume_t` 등 **당일 가격 실현치는 입력 피처 금지**(타깃).
- 허용 입력: ① 당일 FF5 팩터(EOD 확정), ② 당일 뉴스(마감 전 published), ③ `t-1`까지 가격/수익률 이력.
- β는 `t` 이전 구간으로만 적합(`common.normal_return.clean_window_positions`).
- 프레이밍(**D1**): event-study 설명형 — 당일 FF5로 정상수익률 베이스라인을 만들되 당일 close/high는 입력 배제.

### 2.3 컨피던스 (D2)

- News NN은 z-score 타깃에 대해 `(μ, log σ²)`를 출력(**Gaussian NLL** 학습).
- `confidence = exp(-σ̂_return / s)` (s는 검증셋 보정). 고가는 spread 선형회귀 잔차표준편차 기반.
- 그림 Calibration 게이트를 추가 필터로: `|error|<0.02 AND sign(pred)==sign(real)`.

---

## 3. Universe (US 9종목 고정)

데이터(parquet/뉴스)는 `BRK-B` 표기를 쓰므로 **내부 키 = 데이터 티커**, 표시는 canonical 병기.

| sector | company | ticker(data) | canonical |
|---|---|---|---|
| IT | NVIDIA | NVDA | NVDA |
| IT | Apple | AAPL | AAPL |
| IT | Microsoft | MSFT | MSFT |
| Industrials | Caterpillar | CAT | CAT |
| Industrials | GE Aerospace | GE | GE |
| Industrials | RTX | RTX | RTX |
| Financials | Berkshire Hathaway | BRK-B | BRK.B |
| Financials | JPMorgan Chase | JPM | JPM |
| Financials | Visa | V | V |

---

## 4. 데이터

### 4.1 입력 (로컬 parquet 미러 우선, Postgres는 폴백)

| 소스 | 위치 | 핵심 컬럼 |
|---|---|---|
| 가격 OHLC | `data/price/us_daily_data.parquet` (FMP) | `ticker, trade_date, open, high, low, close, volume` |
| FF5 팩터 | `data/analysis_outputs/us_ff5_public_daily_*.parquet` (최신) | `trade_date, mkt_rf, smb, hml, rmw, cma, rf` |
| 뉴스 | `data/news/us_target_news.parquet` | `ticker, published_at, article_id, content`(=제목), `url` |

- 폴백 Postgres(**D4 확정**): `edge` / `127.0.0.1:15432` / user `edge` / `PG*` 환경변수 / schema `etf`
  (원본 스펙의 `Edge`/5432/postgres/하드코딩 비번은 **폐기**). `etf.news_articles.title`이 뉴스 제목.
- FF5 단위: `FF5_FACTOR_UNIT=percent`면 `/100`로 decimal 변환.

### 4.2 출력 (**D4: 로컬 SQLite, `src/db/` 아래**)

`src/db/edge_analysis.sqlite` 에 SQLite로 저장(이전 duckdb 미사용). 모듈 `src/edge_event_model/db/store.py`.

#### `us_event_return_daily_result`

`trade_date, ticker, sector, prev_close, normal_return, abnormal_return_pred, close_return_pred,
spread_pred, predicted_close, predicted_high, close_confidence, high_confidence, news_score,
news_count, beta_mkt_rf, beta_smb, beta_hml, beta_rmw, beta_cma, alpha, residual_std, r_squared,
window_start_date, window_end_date, split, actual_close, actual_high, is_event_candidate,
calibration_pass, created_at`
— PK/UNIQUE `(trade_date, ticker)`, 재실행 upsert.

#### `us_event_return_fail_log`

`id, trade_date, ticker, news_id, feature_id, error_code, error_message, importance_vector(json),
factor_vector(json), model_prediction_return, real_return, error_return, context(json), created_at`
(그림 Fail Log 필드 반영).

---

## 5. 모델

### 5.1 Stage A — Factor arm (FF5, 로그초과수익률)

```text
y_t = ln(close_t/close_{t-1}) - rf_t
y_t = α + β·[mkt_rf, smb, hml, rmw, cma]_t + ε_t
normal_return_t   = rf_t + α + β·FF5_t
abnormal_return_t = (ln(close_t/close_{t-1})) - normal_return_t   (= ε, Stage B/C 타깃)
```

- 종목별 trailing/clean-window 롤링 OLS. `common.normal_return`(`ols_fit`,`ols_predict`,
  `clean_window_positions`) 재사용. 출력: normal_return, abnormal_return, β5, α, residual_std, r2, n_train,
  window_start/end. 설정 `ROLLING_WINDOW=252`, `MIN_OBS=120`(부족 시 60, **D6**).

### 5.2 Stage B — News arm (NN)

1. 종목별 뉴스 → `published_at`(ET) 기준 trade_date 배정(마감 16:00 ET 이후는 익영업일).
2. `is_news_novelty`: `common.nlp.dedupe_news`로 중복 제거.
3. **Stanza SER**: 제목을 의존구문 핵심절(subject–relation–object)로 구조화(`common.nlp.structure_titles`, en).
4. **Embedding (D7)**: 사전학습 FinBERT(`transformers`, mean-pooled last hidden, 768d) 임베딩. parquet 캐시.
5. (ticker, trade_date) 단위로 임베딩 집계(novelty/시간가중 평균) + `news_count`.
6. **NN**: `Linear(768→64)+ReLU+Dropout`(=학습형 projection) → MLP → `news_score=tanh(·)` + `(μ, logσ²)`.
   z-score된 abnormal_return을 타깃으로 **Gaussian NLL** 학습.

### 5.3 Stage C — 최종 선형회귀 + 복원

```text
abnormal_return_pred = LR_final(news_score)          # Ab return = News Score·β + α (+ε)
close_logret_pred    = normal_return + abnormal_return_pred
spread_pred          = LR_spread(features)           # 고가 = close + 스프레드 (D: 선형회귀)
predicted_close = close_{t-1} * exp(close_logret_pred)
predicted_high  = predicted_close * exp(max(spread_pred, 0))
```

- LR_final: news_score → abnormal_return 선형회귀(그림의 마지막 LR). LR_spread: 입력=[news_score,
  normal_return, 과거 spread 평균/표준편차 등], 타깃=z-score(spread). 둘 다 z-score 표준화 후 적합·역변환.
- 컨피던스: close=NN σ̂ 매핑, high=LR_spread 잔차표준편차 매핑.

### 5.4 학습 데이터/평가 (D3, 평가 baseline)

- 학습 표본 = **전 뉴스일 pooled**(9종목 합산); `|abnormal|>=0.05` 이벤트 임계는 **게이트/후보 표시 전용**.
- chronological split(날짜 기준 train/val/test ≈ 70/15/15), 종목 누설 없음.
- 평가 baseline = `normal_return-only`(뉴스 arm 미적용) 대비 close/high **MAE·방향정확도 증분**.

### 5.5 Calibration 게이트

```text
calibration_pass = (abs(pred_return - real_return) < 0.02) and (sign(pred_return) == sign(real_return))
```

운영 추론 시 real 미정이면 null, 사후 배치에서 채움. 실패분 Fail Log 적재.

---

## 6. 파일 구조

```text
src/
  db/                         # ★ 로컬 SQLite analysis store 위치
    edge_analysis.sqlite      # (런타임 생성)
  edge_event_model/
    __init__.py
    config.py                 # 경로/universe/임계값/env
    errors.py
    db/
      __init__.py
      store.py                # SQLite 스키마 + upsert + fail log
    features/
      __init__.py
      loaders.py              # OHLC/FF5/뉴스 parquet 로드 (Postgres 폴백)
      returns.py              # 로그수익률, spread, z-score 표준화 유틸
      factor_arm.py           # FF5 롤링 OLS normal/abnormal
      news_arm.py             # 뉴스 적재·dedup·Stanza·임베딩·일자집계
      dataset.py              # arm 결합 + 누설 가드 + split
    models/
      __init__.py
      news_nn.py              # torch MLP, (μ,logσ²), Gaussian NLL
      combine.py              # LR_final, LR_spread, z-score 역변환, confidence
      predictor.py            # close/high 복원
    calibration/
      __init__.py
      gate.py
    pipeline/
      __init__.py
      run.py                  # entrypoint
scripts/
  run_event_return_model.py   # CLI 래퍼
tests/
  edge_event_model/
    test_returns.py           # 로그수익률/spread/z-score 역변환
    test_factor_arm.py        # FF5 잔차 = abnormal, 누설 없음
    test_dataset.py           # 당일 close/high 입력 배제 검증
    test_combine_predictor.py # 복원 산식 + confidence
    test_gate.py              # error<2% & same direction 경계
    test_store.py             # SQLite upsert 멱등
```

코드 위치(**D8**): `src/edge_event_model/` 신설 + `scripts/analysis/common` 알고리즘 재사용. 언어 Python.

---

## 7. 처리 순서

```text
1. config (universe/경로/임계값)
2. OHLC 로드 → close_logret, spread 타깃 (당일 close/high는 타깃 전용)
3. FF5 로드 (percent→decimal)
4. Stage A: 롤링 OLS → normal/abnormal/β/α/residual_std/r2
5. 뉴스 로드 → dedup → Stanza 구조화 → FinBERT 임베딩(캐시) → 일자집계
6. dataset 결합(시간순, 누설 가드) + chronological split
7. Stage B: News NN 학습(Gaussian NLL, z-score abnormal)
8. Stage C: LR_final(score→abnormal), LR_spread(→spread)
9. predictor: close/high 복원 + confidence
10. calibration 게이트(평가 구간)
11. SQLite upsert + fail log
```

---

## 8. 에러 처리 (3단계)

- 중단: `DB_UNAVAILABLE`, `FACTOR_DATA_NOT_FOUND`, `PRICE_OHLC_NOT_FOUND`.
- ticker/날짜 스킵: `PRICE_DATA_NOT_FOUND`, `INSUFFICIENT_PRICE_HISTORY`, `INSUFFICIENT_OBSERVATIONS`, `NO_NEWS_FOR_DATE`.
- row 제외: `INVALID_PRICE_ROW`, `MISSING_FACTOR_ROW`, `EMBEDDING_FAILED`.
- 전체 접근 불가만 중단, 종목/행은 Fail Log 후 진행, `(trade_date,ticker)` upsert로 재실행 안전.

---

## 9. 완료 기준

1. 9종목 OHLC·FF5·뉴스를 `(trade_date,ticker)`로 결합하고 **당일 close/high가 입력에 없음**을 테스트로 증명.
2. FF5 롤링 OLS로 normal/abnormal 산출(로그초과수익률).
3. Stanza 구조화 → FinBERT 임베딩 → NN이 news_score와 `(μ,σ)` 출력.
4. `predicted_close, predicted_high, close_confidence, high_confidence` 산출 + z-score 역변환 정확.
5. calibration 게이트 동작 + Fail Log 적재.
6. `src/db/edge_analysis.sqlite`에 upsert, 재실행 멱등.
7. baseline(normal-only) 대비 close/high MAE·방향정확도 리포트.

---

## 10. MVP 제외 (후속)

LLM/Text 해설(인터페이스만), 섹터수익률·PER·부채비율·외국인/기관 수급(팩터는 FF5만), KR 종목,
intraday/실시간, 완전 자체학습 임베딩.

---

## 11. 확정된 설계 결정

| ID | 결정 | 확정값 |
|---|---|---|
| D1 | 예측 프레이밍 | event-study 설명형(당일 FF5 사용, 당일 close/high 미사용) |
| D2 | 컨피던스 | NN Gaussian NLL(μ,σ²) + 검증 보정; 고가는 LR 잔차 기반 |
| D3 | 학습 표본 | 전 뉴스일 pooled, 5% 임계는 게이트 전용 |
| D4 | DB | 입력=parquet(+Postgres `edge`/15432 폴백), 출력=**로컬 SQLite `src/db/`** |
| D6 | FF5 최소 관측치 | 120(부족 시 60) |
| D7 | 임베딩 | 사전학습 FinBERT + 학습형 projection |
| D8 | 코드 위치 | `src/edge_event_model/` + `common` 재사용 |
| 고가 | high arm | `high = close * exp(spread)`, spread는 선형회귀 |
| 구조 | 회귀 체인 | 선형회귀(FF5) → NN(뉴스) → 마지막 선형회귀 유지 |
| 수익률 | 단위 | 로그 수익률, 회귀 타깃 z-score 표준화 |
