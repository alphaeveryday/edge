# 뉴스 arm 리팩터링: 어텐션 풀링 + 비정상수익률 직접 회귀

## 1. 배경 — 왜 바꿨나

기존 뉴스 arm은 **① 하루 기사 임베딩 평균 → ② NN이 점수(score) 출력 → ③ 점수를 선형회귀로 비정상수익률에 매핑**하는 3단 구조였다. 진단 결과:

- NN의 `mu_z` std ≈ 0.12 (z스케일 1이어야 정상) → in-sample corr(0.13)에 수렴. 즉 **신호 한계까지 학습은 됐으나 신호가 약함**.
- out-of-sample corr(예측 비정상, 실제 비정상) = **0.047** (test).
- 원인 2가지가 신호를 더 죽임:
  1. **평균 풀링**: 하루 100건 헤드라인을 평균 → 임팩트 있는 한 건이 희석.
  2. **점수 → 선형회귀 중간단계**: 정보가 스칼라 1개로 압축된 뒤 다시 선형 매핑 → 표현력 손실.

→ **기사 단위 어텐션으로 중요한 헤드라인에 가중**하고, **NN이 비정상수익률을 직접 회귀**하도록 단순화한다.

## 2. 아키텍처: 이전 → 이후

```text
[이전]  기사임베딩들 → mean → NN → news_score(스칼라) → LinearReg → abnormal
[이후]  기사임베딩들 → Attention Pool → MLP → abnormal (μ, logσ²)  (직접 회귀)
```

- **스코어링 단계 제거**, **최종 선형회귀(score→abnormal) 제거**.
- FF5(1층 정상수익률)와 고가 스프레드 회귀는 그대로. 바뀐 건 **비정상수익률 경로뿐**.

## 3. 어텐션 풀링 수식

하루 `t`, 종목 `i`의 기사 임베딩 행렬 `X ∈ R^{n×768}` (FinBERT, 기사 n건):

```text
projection :  h_k = Dropout(ReLU(W_p · x_k))            # (n × 64)
attention  :  a_k = v · tanh(W_a · h_k)                 # 기사별 스칼라 점수
              α   = softmax(a)   (패딩 기사는 -inf 마스킹)
pooling    :  c   = Σ_k α_k · h_k                       # (64)  학습된 가중 평균
head       :  c → ReLU(32) → (μ, logσ²)
```

- `α_k` = 그날 기사 중 **어느 헤드라인이 비정상수익률 설명에 중요한지**의 가중치(평균 풀링의 일반화: 균일 가중 = 평균).
- 가변 길이(하루 기사 수 상이)는 **배치 내 패딩 + 마스크**로 처리.

## 4. 직접 회귀 & 학습

- **타깃**: 그날 FF5 잔차 = 비정상수익률 `ε_t` (1층 산출). 학습 시 z-score 표준화(최적화 안정), 출력 시 역변환 → **비정상수익률 단위로 직접 출력**.
- **손실**: Gaussian NLL `0.5·(logσ² + (z−μ)²/σ²)` → 점추정 `μ`와 **불확실성 σ** 동시 산출.
- **컨피던스**: `close_confidence = exp(−σ̂_return / s_close)` (s_close = 검증셋 σ 중앙값).
- 규제 완화(weight_decay 0, dropout 0.1)로 `μ`가 신호 한계까지 학습되게 함.
- 뉴스 없는 날: 비정상 0, σ = 무조건부 표준편차(낮은 컨피던스).

## 5. 데이터 흐름 / 변경 모듈

| 모듈 | 변경 |
|---|---|
| `features/news_arm.py` | `build_day_embeddings`: 일자 평균 대신 **(ticker,date)→기사 임베딩 행렬 dict**(`day_emb`) + news_count 반환. 임베딩은 news_id로 캐시(1회 계산). |
| `features/dataset.py` | 임베딩 컬럼(e0..e767) 제거 — 임베딩은 `day_emb`로 분리. news_count만 병합. 스프레드 피처에서 news_score 제거. |
| `models/news_nn.py` | `AttentionNewsModel`: 어텐션 풀링 + (μ,logσ²) 직접 회귀, 패딩/마스킹 배치 학습. |
| `models/combine.py` | 비정상 최종 LR 제거 → **스프레드(고가) 회귀 + 컨피던스 전용**(`SpreadModel`, `close_confidence`). |
| `models/predictor.py` | `abnormal, σ = news_model.predict_abnormal(df, day_emb)` 직접 사용. |
| `pipeline/run.py` | `day_emb`를 학습/예측에 관통 전달. |
| `pipeline/daily.py` | 당일 기사 임베딩 행렬 → 어텐션 직접 추론. |
| `model_io.py` | 어텐션 NN(state_dict+scaler+target_z+차원) + SpreadModel + s_close 저장/로드. |

복원식은 동일:
```text
predicted_return = layer1_ff5_normal_return + abnormal(NN)
predicted_close  = prev_close · exp(predicted_return)
predicted_high   = predicted_close · exp(max(spread, 0))
```

## 6. 결과 (2024-07-01 ~ 2026-04-30, 9종목)

| 지표 | 이전(평균+점수+LR) | 이후(어텐션+직접회귀) |
|---|--:|--:|
| corr(예측 비정상, 실제 비정상) **test** | 0.047 | **0.150** |
| corr **validation** | 0.099 | 0.091 |
| close 방향정확도 test | 0.675 | 0.671 |
| close MAE train vs baseline | 0.00892 / 0.00898 | **0.00872** / 0.00898 |

- **OOS 비정상 상관이 ~3배 개선**(0.047→0.150). 합성 데이터(실신호 존재) 검증에서는 corr 0.83까지 학습 → 구조는 충분히 표현력 있음.
- 비정상 예측의 절대 스케일은 여전히 작음(`std≈0.0016` vs 실제 `0.0167`): **당일 뉴스→당일 잔차 신호 자체가 약함**(구조 문제 아님). 추가 개선 레버: 익일(t+1) 타깃, 이벤트일 가중, 더 강한/파인튜닝 인코더.

## 7. 추론(daily)

`daily.py`는 학습된 어텐션 모델을 **로드만** 하여, 당일 `market.us_news_articles`의 종목별 기사를 임베딩→어텐션 풀링→비정상 직접 산출→`market.daily_prediction` upsert. 매일 재학습 없음.
