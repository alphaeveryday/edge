# daily_prediction 스키마 (데이터 사전)

### daily_prediction

| column | type | 설명 |
|---|---|---|
| `trade_date` | TEXT | 예측 기준일 |
| `market` | TEXT | 시장 (US/KR) |
| `asset_code` | TEXT | 종목코드(티커) |
| `sector` | TEXT | 섹터 |
| `status` | TEXT | 처리상태: ok / skipped / failed |
| `split` | TEXT | 데이터 분할: train / validation / test (라이브는 NULL) |
| `news_count` | INTEGER | 당일 사용 뉴스 수 |
| `news_ids` | TEXT | 사용된 뉴스 article_id 목록 |
| `input_factor_values` | TEXT | 당일 FF5 팩터값 벡터 (factor_vector) |
| `prev_close_price` | REAL | 전일 종가 |
| `layer1_ff5_normal_return` | REAL | [1층 FF5] 정상수익률 예측 |
| `layer1_ff5_alpha` | REAL | [1층 FF5] 절편 alpha |
| `layer1_ff5_importance_betas` | TEXT | [1층 FF5] 중요도 베타 벡터 (importance_vector) |
| `layer1_ff5_r2` | REAL | [1층 FF5] 설명력 R^2 |
| `layer1_ff5_residual_std` | REAL | [1층 FF5] 잔차 표준편차 |
| `layer2_news_score` | REAL | [2층 NN] 뉴스 스코어 |
| `layer2_news_abnormal_return` | REAL | [2층 NN] 비정상수익률 추정 |
| `layer2_news_uncertainty` | REAL | [2층 NN] 예측 불확실성 sigma |
| `layer3_final_abnormal_return` | REAL | [3층 최종회귀] 예측 비정상수익률 |
| `predicted_return` | REAL | 최종 예측 수익률 (정상+비정상) |
| `predicted_close_price` | REAL | 예측 종가 |
| `predicted_high_price` | REAL | 예측 고가 |
| `predicted_direction` | INTEGER | 예측 방향 (+1 상승 / -1 하락) |
| `close_confidence` | REAL | 종가 컨피던스 [0,1] |
| `high_confidence` | REAL | 고가 컨피던스 [0,1] |
| `actual_return` | REAL | 실제 수익률 (정답) |
| `actual_close_price` | REAL | 실제 종가 (정답) |
| `actual_high_price` | REAL | 실제 고가 (정답) |
| `actual_abnormal_return` | REAL | 실현 비정상수익률 (정답) |
| `return_error` | REAL | 오차: 예측수익률 - 실제수익률 |
| `close_price_error` | REAL | 오차: 예측종가 - 실제종가 |
| `is_event` | INTEGER | 이벤트 후보 (|비정상수익률| >= 5%) |
| `calibration_pass` | INTEGER | 캘리브레이션 통과 (오차<2% & 방향일치) |
| `error_code` | TEXT | 실패/스킵 코드 (성공 시 NULL) |
| `error_message` | TEXT | 실패/스킵 사람이 읽는 메시지 |
| `debug_payload` | TEXT | 디버그용 원입력 스냅샷 |
| `model_version` | TEXT | 모델 버전 |
| `embed_model` | TEXT | 임베딩 모델 |
| `ff5_available` | INTEGER | 당일 FF5 팩터 가용 여부 |
| `created_at` | TEXT | 행 생성 시각 (UTC) |
