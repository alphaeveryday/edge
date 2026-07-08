# data-pipeline

> 역할/아키텍처는 루트 [README](../../../README.md)·[docs/architecture](../../../docs/architecture.md)가 SSOT.
> 이 문서는 로컬 실행·설정 계약·범위 경계만 둔다.
>
> 현재 범위는 **수집 설정 관리 + 원본저장(Step1)** — FMP(미국) 뉴스·가격(OHLCV 일봉)·
> 재무제표(손익·재무상태·현금흐름), BigKinds 국내 뉴스, KIS(한국투자, 국내) 일봉,
> OpenDART 국내 재무까지다.
> 정규화·품질검증(뉴스 Step2)과 canonical 적재는 후속이다.

## 실행

Python 도구는 **uv**다(ADR-0001). Python 워크스페이스 루트는 `src/pyproject.toml`.

```bash
uv sync                                  # src/ (Python 루트)에서 의존성 설치
uv run --package data-pipeline pytest    # 테스트

# 뉴스 원본저장(Step1) — 기본은 local 스토리지(./.lake), FMP 키는 env 로
# 날짜창 미지정 = 증분(어제~오늘, 앱이 계산). 백필은 --from/--to 로 구간 지정.
DATA_PIPELINE_NEWS__SOURCES__FMP__API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw
# 백필 예: 2026-06 한 달
#   ... run ingest-raw --from 2026-06-01 --to 2026-06-30

# 국내 뉴스 원본저장(Step1) — BigKinds search.do. --source bigkinds 로 벤더 선택
# (미지정=fmp). 인증키 없음. resultList[] row 원본 필드는 그대로 저장하고, our_ticker·
# market·bigkinds_query·fetched_at 같은 수집 provenance 만 붙인다.
uv run --package data-pipeline python -m data_pipeline.run ingest-raw --source bigkinds

# 가격(OHLCV 일봉) 원본저장(Step1) — FMP EOD. 날짜창 미지정 = 증분(5일 소급~오늘,
# 주말·공휴일 공백 대비). 심볼맵은 가격 전용(price.source.symbol_map) — 현재 US 만.
DATA_PIPELINE_PRICE__SOURCE__API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-price-raw
# 백필 예: 2026-06 한 달
#   ... run ingest-price-raw --from 2026-06-01 --to 2026-06-30

# 국내 가격(OHLCV 일봉) 원본저장(Step1) — KIS(한국투자) REST. --source kis 로 벤더 선택
# (미지정=fmp). 인증은 OAuth 앱키/시크릿(env 주입), 도메인은 env(prod|vps). 심볼맵은
# 국내 전용(kis_price.source.symbol_map, KR 6자리). 토큰은 run 당 1회 발급·재사용.
DATA_PIPELINE_KIS_PRICE__SOURCE__APP_KEY=... DATA_PIPELINE_KIS_PRICE__SOURCE__APP_SECRET=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-price-raw --source kis
# 백필 예: 2026-06 한 달
#   ... run ingest-price-raw --source kis --from 2026-06-01 --to 2026-06-30

# 재무제표(손익·재무상태·현금흐름) 원본저장(Step1) — FMP 재무 API. 날짜창 없음(매 실행이
# 최근 N기를 재요청하는 point-in-time 폴링). 가격과 동형으로 받은 행을 ingest_date/run_id 에
# 전부 append(중복 판정 안 함 — dedup·정정·point-in-time 은 후속 canonical). 심볼맵은 재무
# 전용(financial.source.symbol_map) — 현재 US 만.
DATA_PIPELINE_FINANCIAL__SOURCE__API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-financial

# 국내 재무제표 원본저장(Step1) — OpenDART 단일회사 주요계정. --source dart 로 벤더 선택
# (미지정=fmp). 인증키는 env 주입, corp_code 는 corpCode.xml 로 런타임 매핑한다. 받은 list[]
# 행은 ingest_date/run_id 파티션에 전부 append 되고, 정규화·dedup 은 후속 canonical 소관.
DATA_PIPELINE_DART_FINANCIAL__SOURCE__API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-financial --source dart
```

> **수집 날짜창** — FMP `/stable/news/stock` 은 `from`/`to`(날짜창)·`page`(페이지네이션)를
> 지원한다. 어댑터는 심볼별로 창을 페이지 끝까지 순회해 고volume 날에도 누락이 없다.
> 스케줄 실행은 날짜창을 생략하면 되고(앱이 어제~오늘 계산 — EventBridge Scheduler 는
> 정적 입력만 넣어 동적 날짜를 못 만들기 때문), 과거 적재만 `--from/--to` 로 명시한다.

> uv가 없는 환경이면 표준 venv로 같은 일을 한다(`src/apps/data-pipeline`에서, pip ≥ 25.1):
> ```bash
> python3 -m venv .venv
> .venv/bin/pip install -e . --group dev   # dev 그룹(pytest)은 PEP 735 [dependency-groups]
> .venv/bin/pytest
> ```

## 배포/스케줄 실행

dev 배포 이미지는 `src/apps/data-pipeline/Dockerfile` 로 빌드해 기존 `edge/pipeline`
ECR repository 에 `:${git_sha}` 와 `:data-pipeline-latest` 태그로 push 한다(`deploy-data-pipeline.yml`).

Terraform 의 `modules/data-pipeline` 은 raw ingest 전용 ECS task definition 과 Step Functions
state machine 을 만든다. 상태머신은 아래 여섯 raw 수집을 병렬 ECS RunTask 로 실행하며,
모든 브랜치에 같은 `--run-id` 를 넘겨 raw partition 과 collection_log 를 같은 실행 단위로 묶는다.

- `ingest-raw --source fmp`
- `ingest-price-raw --source fmp`
- `ingest-raw-financial --source fmp`
- `ingest-raw --source bigkinds`
- `ingest-price-raw --source kis`
- `ingest-raw-financial --source dart`

Scheduler 는 최초 `DISABLED` 로 생성한다. 수동 검증은 `terraform output data_pipeline_state_machine_arn`
값으로 `aws stepfunctions start-execution --input '{"run_id":"manual-YYYYMMDDTHHMMSSZ"}'` 를 실행한다.

## 설정 계약

수집 설정은 **TOML 베이스 파일 + 환경변수 오버라이드**로 로드한다. 진입점은 하나다:

```python
from data_pipeline import load_settings

settings = load_settings()           # 패키지 동봉 기본 설정 + env
settings.news.sources                # {이름: NewsSource}
settings.bigkinds_news               # BigKindsNewsSource (국내 뉴스 — 키 없음·KR 검색어 맵); 미설정이면 None
settings.price.source                # PriceSource (FMP EOD — 가격 전용 심볼맵, 현재 US)
settings.kis_price.source            # KisPriceSource (KIS 국내 일봉 — 앱키/시크릿 env·env=prod|vps); 미설정이면 settings.kis_price 은 None
settings.financial.source            # FinancialSource (FMP 재무 — 재무 전용 심볼맵, 현재 US); 미설정이면 settings.financial 은 None
settings.dart_financial.source       # DartFinancialSource (OpenDART 국내 재무 — 인증키 env·KR 6자리 맵); 미설정이면 settings.dart_financial 은 None
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

수집물은 단일 lake 버킷(예: dev `s3://edge-dev-pipeline-lake/`, 또는 local 스텁)에 쓴다.
경로 규약의 SSOT 는 [`lake/storage.py`](src/data_pipeline/lake/storage.py)의 빌더다.

- **raw(뉴스)** — `raw/source=fmp/dataset=stock_news/market=…/published_date=…/run_id=…/` 에
  run_id 별 append(재현성). FMP 뉴스는 기존 계약대로 런 내 중복을 article_id 로 제거하고
  mentions 를 병합한다. 국내 BigKinds 뉴스는 같은 dataset·규약으로 `source=bigkinds`
  (`--source bigkinds`) 아래 쌓이며, BigKinds `resultList[]` row 를 전량 보존한다(런 내
  dedup 없음). `CONTENT` 도 BigKinds 응답 원본 필드 그대로 저장한다.
- **raw(가격)** — `raw/source=fmp/dataset=price_daily/market=…/ingest_date=…/run_id=…/` 에
  run_id 별 append. 파티션 키는 뉴스(published_date)와 달리 **ingest_date(수집일)** 다 —
  EOD 응답은 한 심볼이 여러 거래일을 한 번에 주므로 원본을 수집일 기준으로 보존한다.
  raw 는 받은 행을 **전부 보존**한다(중복 판정 안 함) — (market, ticker, trade_date)
  정체성 upsert·거래일별 분해는 후속 canonical/market_data(S006/S007) 소관.
  국내 KIS 일봉은 같은 dataset·규약으로 `source=kis`(`--source kis`) 아래 쌓인다.
- **raw(재무제표)** — `raw/source=fmp/dataset=financial_statements/market=…/ingest_date=…/run_id=…/` 에
  run_id 별 append. **가격과 동형(bronze 통일)** — 받은 행을 수집일 기준으로 **전부 보존**한다
  (중복 판정 안 함). 재무는 드물게·비동기로 공시돼 매일 재폴링하면 같은 스냅샷이 날마다 쌓이지만,
  중복 제거·정정(SCD)·point-in-time 판정은 후속 canonical(silver) MERGE 소관이다. 각 행에
  statement_type·period_type·filing_date 등이 그대로 보존돼 canonical 이 정체성 추출에 쓴다.
  국내 OpenDART 재무는 같은 dataset·규약으로 `source=dart`(`--source dart`) 아래 쌓이며,
  DART `list[]` 원본 행에 `our_ticker`·`stock_code`·`corp_code`·`bsns_year`·`reprt_code` 등
  수집 provenance 만 부착한다.
- **수집 로그** — `operations_archive/collection_logs/source=…/dataset=…/started_date=…/run_id=…/log.json`
  (`dataset=`로 갈라 같은 벤더의 뉴스·가격·재무 로그가 같은 run_id 를 공유해도 안 덮어쓴다)
- 백엔드는 `[storage]` 설정으로 고른다. 기본 `local`(루트 `./.lake`), 배포는
  `DATA_PIPELINE_STORAGE__BACKEND=s3` + `DATA_PIPELINE_STORAGE__BUCKET=…` 로 전환.

## 범위에서 의도적으로 제외한 것 (후속)

- 정규화·품질검증(뉴스 Step2) — raw → canonical 병합
- 런 간(run 간) 중복 제거 — Step2 의 canonical article_id 멱등 병합이 흡수
- 가격 canonical 적재 — raw price_daily → `canonical/market_data/price_daily`
  정규화·멱등 upsert(거래일별 분해, 품질 게이트)는 후속(S006/S007)
- 재무제표 canonical 적재·지표(Factor) 계산 — raw financial_statements → 후속 Structuring/Curation
