# analysis-engine

EDGE 이벤트 기반 수익률 모델 (Python). EOD(장 마감) 확정 데이터만으로 당일 종가/고가 로그수익률을 예측한다 — 당일 종가/고가 자체는 입력으로 쓰지 않는다.

- **Stage A** — FF5 선형회귀 → `normal_return`, `abnormal_return`(잔차)
- **Stage B** — 뉴스 NN → `news_score` (+ mu, sigma 신뢰도)
- **Stage C** — 최종 선형회귀 → `abnormal_return ~ news_score`

전체 계약: [`spec/edge_event_return_model_spec.md`](spec/edge_event_return_model_spec.md).

## 실행
```
python run_model.py --start 2024-07-01 --end 2026-04-30
```
옵션: `--tickers` · `--db-path` · `--min-obs` · `--news-limit` · `--no-persist` · `--save-dir`.

## 테스트
```
pytest
```

## 리포트 · 시각화 (선택)
`pip install -e .[viz]` 후:
```
python report_results.py     # artifacts/*.png + summary.md
python viz_plotly.py         # artifacts/*.html (인터랙티브)
```

## 환경 변수
| 변수 | 용도 | 기본값 |
|---|---|---|
| `EDGE_DATA_ROOT` | 데이터 루트(`data/` 포함 디렉토리) | 상위로 walk-up 탐색 |
| `EDGE_EMBED_MODEL` | 뉴스 임베딩 모델 | `ProsusAI/finbert` |
| `FF5_FACTOR_UNIT` | FF5 단위 (`auto`/`percent`/`decimal`) | `auto` |
| `PGHOST`·`PGPORT`·`PGDATABASE`·`PGUSER`·`PGPASSWORD`·`PGSCHEMA` | 뉴스 Postgres fallback (선택) | `127.0.0.1`·`15432`·`edge`·`edge`·—·`etf` |

## 데이터 의존 (버전 미추적)
`data/`(price·news·FF5 parquet), 로컬 SQLite `db/edge_analysis.sqlite`, 학습 산출물 `model_artifacts/`, 리포트 `artifacts/`는 out-of-band로 관리하며 git에 올리지 않는다(`.gitignore`).

## 후속 작업 (별도 PR)
- **공유 DB 연동** — 현재 로컬 SQLite에 persist. edge 공유 DB + `libs/schema` SSOT 이전은 확장-수축 절차로 진행: [ADR-0005](../../../docs/adr/0005-db-as-contract.md), [schema.md](../../../docs/schema.md).
- **적재 분리** — 뉴스 적재(`upload_us_news.py`)는 성격상 `data-pipeline` 소관이라 본 앱에 미포함. analysis-engine은 적재분을 읽어 분석/`analysis_result` 산출만 담당.
