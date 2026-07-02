# data-pipeline

> 역할/아키텍처는 루트 [README](../../../README.md)·[docs/architecture](../../../docs/architecture.md)가 SSOT.
> 이 문서는 로컬 실행·설정 계약·범위 경계만 둔다.
>
> 현재 범위는 **수집 설정 관리 + 뉴스 원본저장(Step1, FMP)**까지다.
> 정규화·품질검증(Step2)과 가격 수집은 후속이다.

## 실행

Python 도구는 **uv**다(ADR-0001). Python 워크스페이스 루트는 `src/pyproject.toml`.

```bash
uv sync                                  # src/ (Python 루트)에서 의존성 설치
uv run --package data-pipeline pytest    # 테스트

# 뉴스 원본저장(Step1) — 기본은 local 스토리지(./.lake), FMP 키는 env 로
DATA_PIPELINE_NEWS__SOURCES__FMP__API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw
```

> uv가 없는 환경이면 표준 venv로 같은 일을 한다(`src/apps/data-pipeline`에서, pip ≥ 25.1):
> ```bash
> python3 -m venv .venv
> .venv/bin/pip install -e . --group dev   # dev 그룹(pytest)은 PEP 735 [dependency-groups]
> .venv/bin/pytest
> ```

## 설정 계약

수집 설정은 **TOML 베이스 파일 + 환경변수 오버라이드**로 로드한다. 진입점은 하나다:

```python
from data_pipeline import load_settings

settings = load_settings()           # 패키지 동봉 기본 설정 + env
settings.news.sources                # {이름: NewsSource}
settings.price.source                # PriceSource (가격 소스 위치)
settings.targets.symbols             # ["005930", ...]
settings.targets.keywords            # ["금리", ...]
```

- **구조/공개값** → [`src/data_pipeline/config/sources.toml`](src/data_pipeline/config/sources.toml).
  패키지에 **동봉돼 배포되는 기본 설정**이라 wheel 설치에서도 `load_settings()`가 그대로 동작한다.
  수집 대상은 `[targets]`만 바꾸면 fetcher 대상이 바뀐다 — 코드 수정 불필요.
- **비밀값(api_key 등)** → 커밋하지 말고 **환경변수**로 주입한다. 같은 경로의 env가 파일을 덮어쓴다(`env > file`):
  ```bash
  # news.sources.naver.api_key 를 주입
  export DATA_PIPELINE_NEWS__SOURCES__NAVER__API_KEY=...
  ```
  접두어 `DATA_PIPELINE_`, 중첩 구분자 `__`.
- **파일 경로**: `load_settings(path)` 인자 > `DATA_PIPELINE_CONFIG_FILE` env > 동봉 기본 설정.
  배포 환경(dev/prod)은 보통 env로 외부 설정 파일을 가리켜 동봉 기본값을 대체한다.
- **명시적 실패**: 필수값 누락·알 수 없는 키·대상 0개·공백 값·파일 없음은 조용한 기본값 대신
  `ConfigError`로 드러난다(AGENTS Rule 12). 단, `extra="forbid"`는 **TOML 파일 키에만** 적용된다 —
  `DATA_PIPELINE_*` env의 오타 키는 pydantic-settings 표준 동작상 조용히 무시된다.

## 레이크 저장 계약

수집물은 단일 레이크(`s3://stock-ai-lake/` 또는 local 스텁)에 쓴다.
경로 규약의 SSOT 는 [`lake/storage.py`](src/data_pipeline/lake/storage.py)의 빌더다.

- **raw** — `raw/source=fmp/dataset=stock_news/market=…/published_date=…/run_id=…/` 에
  run_id 별 append(재현성). 런 내 중복은 article_id 로 제거한다.
- **수집 로그** — `operations_archive/collection_logs/source=…/started_date=…/run_id=…/log.json`
- 백엔드는 `[storage]` 설정으로 고른다. 기본 `local`(루트 `./.lake`), 배포는
  `DATA_PIPELINE_STORAGE__BACKEND=s3` + `DATA_PIPELINE_STORAGE__BUCKET=…` 로 전환.

## 범위에서 의도적으로 제외한 것 (후속)

- 정규화·품질검증(Step2) — raw → canonical 병합
- 런 간(run 간) 중복 제거 — Step2 의 canonical article_id 멱등 병합이 흡수
- 실제 가격 데이터 수집(여기서는 소스 '위치'만 설정)
