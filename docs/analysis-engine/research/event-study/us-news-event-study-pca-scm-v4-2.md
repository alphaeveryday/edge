---
doc_type: report
status: Accepted
owner: research
created: 2026-07-11
updated: 2026-07-11
related:
  - ../INDEX.md
---

> **모듈 스펙:** v4.2 파이프라인 전체 — `research/analytics/event_study/phase1_news_events.py` · `phase2_caar.py` · `phase3_trajectory.py` · `phase4_scm_placebo.py` · `phase5_homogeneity.py`, 공통 코어 `src/alphamale/analytics/common/`, 뉴스 적재/감정은 packaged analytics/news surfaces.
> **상태:** CURRENT — 현행 event-study lineage 정리 문서. 실행 surface 는 `uv run alphamale analytics event-study ...` 와 `research/analytics/event_study/run.py` 가 소유한다.
> **드리프트:** historical notes may still describe the earlier ad-hoc runner names; current execution surface is the packaged CLI plus `research/analytics/event_study/run.py`.

# 미국 뉴스량 기반 이벤트 스터디 & FF5 vs PCA-SCM 비교 (v4.2)

CAAR 7일 분석 + 프리이벤트 30일 궤적. 오염통제(동적 룩백) 기반. 미국 9종목.

## 1. 파일 구조 (공통모듈 승격 · 단계별 분리)

```text
src/alphamale/analytics/common/          # CAAR, event-volume, normal-return, IO helpers
src/alphamale/analytics/event_study/v42/ # registry + shared v4.2 utilities
research/analytics/event_study/          # phase drivers and dispatcher
  phase1_news_events.py
  phase2_caar.py
  phase3_trajectory.py
  phase4_scm_placebo.py
  phase5_homogeneity.py
  run.py
tests/analytics/event_study/             # focused CLI / phase / core tests
```

## 2. 데이터

- 종목 뉴스: Google News RSS 종목별 5년치 `data/raw/news/us_target_news.parquet` (201,751건, 2021-06~2026-06, 9종목).
- 감정 메타데이터: PostgreSQL `etf.news_articles.finbert_sentiment` (신규 컬럼, FinBERT `ProsusAI/finbert`)에 영속 적재 — 인메모리 캐시 제거.
- 일별 가격: `data/processed/price/us_daily_data.parquet` (FMP, 수십 년 이력 → 동적 룩백 252 충족).
- FF5 팩터: Ken French US daily. 도너 풀: 광역 대형주 109종 (yfinance 5년 캐시).
- 환경 제약(문서화 fallback): `OPENAI_API_KEY` 미설정 → 벡터 임베딩은 로컬 HashingVectorizer(키 설정 시 text-embedding-3-small); cvxpy 미설치 → 동일 제약(ΣW=1,W≥0) QP를 scipy SLSQP로 해석.

## 3. 핵심 방법론

- **Phase 1 이벤트**: 종목-일 단위 코사인 군집으로 중복 제거된 순수 뉴스량 → 과거 252거래일 μ,σ로 μ+2σ 초과일을 이벤트, 7일 쿨다운으로 중첩 이벤트 병합(미래참조 차단).
- **Phase 2 CAAR**: 이벤트 t0의 정상수익률 모델을 동적 룩백(과거 이벤트 제외 순수 비이벤트 252개)으로 적합 후 고정, [0,+7] 비정상수익률 합 = 이벤트 CAAR. 종목·모델별 1-sample t-test(H0: CAAR=0).
- **Phase 3 궤적**: FF5·PCA-SCM을 매일 동적 클린윈도우로 재적합한 OOS 잔차를 event-time [-30,+7]에 정렬·중첩(평균 |잔차| ± 95% CI). 이벤트 구간 vs 비이벤트 |잔차| 분산 F-test.
- **Phase 4 플라시보**: 도너를 차례로 가짜 타겟으로 동일 이벤트에 노출, PCA-SCM CAAR의 가짜 분포 생성 → 실제 타겟 평균 CAAR의 순위 p-value + KDE(타겟 red vs 플라시보 grey).
- **Phase 5 균질성**: 비이벤트 OOS R²와 이벤트 진입 ΔR²의 종목 간 분산을 FF5 vs SCM로 비교(F-test/Levene). SCM이 특이적 노이즈를 통제해 더 균질함을 검증.
- **데코레이터 레지스트리**: 정상수익률 모델(FF5, PCA-SCM)은 `@MODELS.register`로 등록되어 단일 인터페이스(`fit`/`predict`, 단일 설계행렬)로 호출되므로 단계마다 모델 분기 코드가 사라짐. 각 단계는 `@PHASES.register`로 자기등록하고 `run.py`가 `list`/`<phase>`/`all`로 디스패치.

## 4. 주요 결과 (실행 manifest 기준)

| Phase | 결과 | 판정 |
|---|---|---|
| 1 이벤트 | 뉴스 201,751건 → 순수 199,694; **375 이벤트**(종목당 32~53), 2021-06~2026-06 | 이벤트 정의 완료 |
| 2 CAAR | 341 이벤트; FF5·SCM 모두 유의종목 0 (뉴스량 이벤트는 방향성 약함 — 주목도≠방향) | H0 대체로 미기각(방향성 부재가 실측 결과) |
| 3 궤적 | 롤링 7,356일; 이벤트 구간 \|잔차\| 분산 급증 — FF5 F_p≈3.8e-57, SCM F_p≈2.6e-54 | **H1 채택**(이벤트 시 설명력 붕괴) |
| 4 플라시보 | 9종목 × 109 가짜타겟(≥100) 순열; **CAT만 p=0.045로 유의**, 나머지 비유의 | 부분 채택(CAT) — 뉴스량 이벤트의 약한 인과성과 일치 |
| 5 균질성 | 비이벤트 OOS R²: FF5 0.20~0.59, SCM 0.31~0.76(다수 종목 SCM 우위); 종목 간 분산 SCM 0.0174 < FF5 0.0189 | SCM이 더 균질(방향 일치, 9종목 표본으로 약한 유의) |

해석: 뉴스량 스파이크 이벤트는 **방향성(CAAR)은 약하지만 설명력 충격(분산 급증)은 강함**. PCA-SCM은 일별 초과수익 설명력에서 FF5와 대등~우위이며 종목 간 더 균질해, 특이적 노이즈 통제 측면의 강건성을 보임. (9종목 표본의 분산 검정은 검정력이 낮아 방향성 위주 해석.)

## 5. 실행

```bash
uv run alphamale help analytics event-study
uv run alphamale analytics event-study phase1-news --market us
uv run alphamale analytics event-study phase2-caar --market us
uv run alphamale analytics event-study phase3-trajectory --market us
uv run pytest tests/analytics/event_study -q
```
산출물: `data/processed/analytics/analysis_outputs/<phase>_us_<UTC>/` (parquet/csv/manifest.json), 플롯 `artifacts/analytics/analysis_plots/<phase>/`.
