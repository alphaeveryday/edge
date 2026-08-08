# layer_scope — 층 분해 스코프 실증

`docs/analysis-engine/research/price-decomposition/experiments/EXP-003-layer-decomposition-scope.md` 의 재현 코드.
설계: `docs/analysis-engine/layer-scope-design.md`

## 실행

**레포 루트에서** 돈다 (중간 parquet 을 루트 `.tmp/` 에 캐시한다).

```bash
E=src/apps/cloud/analysis-engine/experiments/layer_scope
UV="uv run --python 3.12 --with duckdb --with pandas --with numpy --with scipy --with scikit-learn --with pyarrow python"
$UV $E/pull_px.py        # 1회. S3 → .tmp/kodex_px.parquet · .tmp/kodex_w.parquet
$UV $E/scope_study2.py
$UV $E/cache_study.py
```

`AWS_PROFILE=work` SSO 세션 필요.

| 스크립트 | 하는 일 |
|---|---|
| `pull_px.py` | DataGuide 수정주가(1980~, 8,712종목 wide CSV) → KODEX 200 구성 200종목 로컬 parquet |
| `scope_study2.py` | LOO nested means 3층 분해 · 항등식/직교 검산 · 커버리지 스코프 수 (K = 8·15·25) |
| `cluster_input.py` | 클러스터링 입력 비교 (raw vs 시장차감 잔차 × average/ward × K) — EXP-003 §5 |
| `cache_study.py` | 클러스터 ARI 안정성(캐시 TTL) · 군집 스코프 축소 배율 |

## 주의

- 산업 분류는 레이크에 없다. `W_SECTOR`(DataGuide universe)는 **지수·규모 태그**이지 산업이 아니다 —
  삼성전자·현대차·POSCO 가 같은 코드다. 그래서 상관 클러스터로 대체했다.
- 표본 12영업일 · ETF 1개. 파일럿이며 일반화 근거가 아니다.
