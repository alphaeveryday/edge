# data-pipeline

> 역할/아키텍처는 루트 [README](../../../README.md)·[docs/architecture](../../../docs/architecture.md)가 SSOT.
> 이 문서는 로컬 실행·설정 계약·범위 경계만 둔다.
>
> 현재 범위는 **수집 설정 관리(ALPHA-102)** 까지다. 실제 뉴스 수집/적재는 ALPHA-103.

## 실행

Python 도구는 **uv**다(ADR-0001). Python 워크스페이스 루트는 `src/pyproject.toml`.

```bash
uv sync                                  # src/ (Python 루트)에서 의존성 설치
uv run --package data-pipeline pytest    # 테스트
```

> uv가 없는 환경이면 표준 venv로 같은 일을 한다(`src/apps/data-pipeline`에서, pip ≥ 25.1):
> ```bash
> python3 -m venv .venv
> .venv/bin/pip install -e . --group dev   # dev 그룹(pytest)은 PEP 735 [dependency-groups]
> .venv/bin/pytest
> ```

## 설정 계약 (ALPHA-102)

수집 설정은 **TOML 베이스 파일 + 환경변수 오버라이드**로 로드한다. 진입점은 하나다:

```python
from data_pipeline import load_settings

settings = load_settings()           # config/sources.toml + env
settings.news.sources                # {이름: NewsSource}
settings.price.source                # PriceSource (가격 소스 위치)
settings.targets.symbols             # ["005930", ...]
settings.targets.keywords            # ["금리", ...]
```

- **구조/공개값** → [`config/sources.toml`](config/sources.toml) (커밋됨). 수집 대상은 `[targets]`만
  바꾸면 fetcher 대상이 바뀐다 — 코드 수정 불필요.
- **비밀값(api_key 등)** → 커밋하지 말고 **환경변수**로 주입한다. 같은 경로의 env가 파일을 덮어쓴다(`env > file`):
  ```bash
  # news.sources.naver.api_key 를 주입
  export DATA_PIPELINE_NEWS__SOURCES__NAVER__API_KEY=...
  ```
  접두어 `DATA_PIPELINE_`, 중첩 구분자 `__`.
- **파일 경로**: `load_settings(path)` 인자 > `DATA_PIPELINE_CONFIG_FILE` env > 기본값 `config/sources.toml`.
  이 한 줄로 환경(dev/prod)별 로딩을 구분한다.
- **명시적 실패**: 필수값 누락·알 수 없는 키·대상 0개·파일 없음은 조용한 기본값 대신 `ConfigError`로
  드러난다(AGENTS Rule 12).

## 범위에서 의도적으로 제외한 것 (후속 티켓)

| 제외 대상 | 후속 |
|---|---|
| 등록 소스에서 신규 뉴스 수집·중복 없이 적재 | **ALPHA-103 (S002)** |
| 실제 가격 데이터 수집(여기서는 소스 '위치'만 설정) | 후속 |
