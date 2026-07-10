# data-pipeline

> 역할/아키텍처는 루트 [README](../../../README.md)·[docs/architecture](../../../docs/architecture.md)가 SSOT.
> 이 문서는 로컬 실행·설정 계약·범위 경계만 둔다.
>
> 현재 범위는 **수집 설정 관리 + 원본저장(Step1)** — FMP(미국) 뉴스·가격(OHLCV 일봉)·
> 재무제표(손익·재무상태·현금흐름), BigKinds 국내 뉴스, KIS(한국투자, 국내) 일봉,
> OpenDART 국내 재무까지다.
> **가격 정제(Step2)** 는 정규화(FMP·KIS 이형 → 표준 OHLCV) + 정합성 게이트 + quality_log +
> 통과 행의 `canonical/market_data/price_daily` 멱등 병합 적재까지 완료했다(`normalize-price`,
> ALPHA-133). **뉴스 정제(Step2)** 는 정규화(FMP·BigKinds 이형 → 표준 메타행) + 필수필드·발행일
> 게이트 + quality_log + 통과 행의 `canonical/news/news_articles` article_id 멱등 병합 적재까지
> 완료했다(`normalize-news`, ALPHA-131·132).

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

# 가격 정제(Step2) — raw price_daily(FMP·KIS) → 표준 OHLCV 정규화 + 정합성 게이트.
# 벤더는 raw 키의 source= 로 판별한다(수집 날짜창 없음). 통과/탈락 집계·탈락 사유는
# data_quality_logs 로 남기고, 통과 행은 canonical/market_data/price_daily 에 (market,ticker,
# trade_date) 로 멱등 병합 적재한다(같은 벤더 최신 fetched_at 우선, 벤더 교차 충돌 fail-loud).
# --input-run-id 로 특정 수집 런만 재검증(미지정=raw price 전체, 멱등). 단, 스코프 실행은
# 재검증(quality_log)만 하고 canonical 은 안 쓴다 — 스코프는 다른 벤더의 raw 를 못 봐 벤더
# 교차 충돌을 감지 못 하므로, canonical 은 전체 raw 를 보는 멱등 전체 런이 authoritative 하게 쓴다.
uv run --package data-pipeline python -m data_pipeline.run normalize-price
#   특정 런만: ... run normalize-price --input-run-id 20260701T000000Z

# 뉴스 정제(Step2) — raw stock_news(FMP·BigKinds) → 표준 메타행 정규화 + 필수필드·발행일 게이트.
# 벤더는 raw 키의 source= 로 판별한다(수집 날짜창 없음). blocking 사유(제목 결측·발행시각 파싱
# 불가/범위 밖)는 canonical 제외 대상이고, url·publisher 결측은 non-blocking 경고로 data_quality_logs
# 에 남긴다 — BigKinds 는 URL 없이 NEWS_ID 로 식별하므로 가변 필드로 벤더를 대량 탈락시키지 않는다.
# 통과 행은 canonical/news/news_articles 에 article_id 로 멱등 병합 적재하고(같은 벤더 최신
# fetched_at 우선), 다른 article_id 가 같은 정규화 제목·URL 해시면 duplicate_signal 로 로깅한다.
# --input-run-id 로 특정 수집 런만 재검증(미지정=raw news 전체, 멱등; 스코프는 canonical 안 씀).
uv run --package data-pipeline python -m data_pipeline.run normalize-news
#   특정 런만: ... run normalize-news --input-run-id 20260701T000000Z
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
- **canonical(가격, 정제 Step2)** — `canonical/market_data/price_daily/market=…/trade_date=…/part-*.parquet`
  에 게이트 통과 행을 **(market,ticker,trade_date) 키로 멱등 병합**. raw 와 달리 run_id·source_vendor
  파티션이 없다(멱등 — 같은 raw 를 몇 번 정제해도 결과 동일). market·trade_date 가 파티션, ticker 는
  파티션 내 행 키다. 같은 벤더 재적재는 최신 fetched_at 우선(정정 반영), **벤더 교차 같은 키 충돌은
  fail-loud**(둘 다 제외 + quality_log·비0 종료 — USD 를 KRW 로 태깅하는 통화 오염 방지). 통화는
  market 별 태깅만 하고 FX 환산하지 않는다.
- **canonical(뉴스, 정제 Step2)** — `canonical/news/news_articles/published_date=…/source_vendor=…/part-*.parquet`
  에 게이트 통과 행을 **article_id 키로 멱등 병합**. run_id 는 없다(멱등). 가격과 달리 **source_vendor
  가 파티션**이다(벤더가 파티션을 갈라 교차벤더 같은 키 충돌이 구조적으로 없어 통화 오염 fail-loud 불필요).
  같은 벤더 재적재는 최신 fetched_at 우선. 다른 article_id 가 같은 정규화 제목·URL 해시를 가지면
  **exact 병합 없이 duplicate_signal 로 로깅만** 한다(별개 기사 붕괴 방지 — fuzzy·교차벤더 클러스터는
  다운스트림 news_dedup_cluster 소관). mentions(FMP 병합분/BigKinds our_ticker 합성)는 JSON 문자열로 보존.
- **품질 로그(정제 Step2)** — `operations_archive/data_quality_logs/dataset=…/checked_date=…/run_id=…/log.json`
  에 검증 실행당 1건. 몇 건 읽고/통과/탈락·canonical 적재했는지와 **탈락 사유**(OHLCV 정합성 위반·결측·
  비수치 등)·벤더 교차 충돌을 남긴다 — 잘못된 가격을 조용히 버리지 않는다(Rule 12). 뉴스(`dataset=
  news_articles`)도 같은 규약으로 남기되 blocking 탈락 사유(제목 결측·발행시각 파싱 불가/범위 밖)와
  non-blocking 경고(url·publisher 결측)를 구분하고, canonical 적재 결과·근접중복 신호(duplicate_signals)를
  함께 기록한다. canonical 은 멱등이라 run_id 가 없지만,
  '이 검증 실행이 무엇을 걸렀나'는 실행 단위 감사라 run_id 로 가른다(수집 로그와 분리).
- 백엔드는 `[storage]` 설정으로 고른다. 기본 `local`(루트 `./.lake`), 배포는
  `DATA_PIPELINE_STORAGE__BACKEND=s3` + `DATA_PIPELINE_STORAGE__BUCKET=…` 로 전환.

## 범위에서 의도적으로 제외한 것 (후속)

- 뉴스 근접중복 클러스터링(fuzzy)·교차벤더 dedup — canonical 은 exact article_id 병합 + 제목/URL
  충돌 로깅까지다. dedup_cluster·엔티티/컨셉 링크·이벤트 태깅은 다운스트림(analysis-engine) 소관.
- 가격 factor·지표 계산 — canonical price_daily 위의 수정주가 파생·거래일 캘린더 정합(휴장일)·
  섹터 태깅·수익률/지표는 후속(S006·S007 이후 Curation). 정제(정규화·정합성·멱등 적재)까지는 완료.
- 재무제표 canonical 적재·지표(Factor) 계산 — raw financial_statements → 후속 Structuring/Curation
