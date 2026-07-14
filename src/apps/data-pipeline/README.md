# data-pipeline

> 역할/아키텍처는 루트 [README](../../../README.md)·[docs/architecture](../../../docs/architecture.md)가 SSOT.
> 이 문서는 로컬 실행·설정 계약·범위 경계만 둔다.
>
> 현재 범위는 **수집 설정 관리 + 원본저장(Step1)** — FMP(미국) 뉴스·가격(OHLCV 일봉)·
> 재무제표(손익·재무상태·현금흐름)·**ETF 구성종목(holdings)**, BigKinds 국내 뉴스,
> KIS(한국투자, 국내) 일봉, **KRX 국내 ETF 구성종목**(로그인 게이트 PDF), OpenDART 국내 재무·**공시(disclosure filing)**까지다. 공시는 재무제표(fnlttSinglAcnt)와
> **다른 API**(공시목록 list.json + 공시서류 원본 document.xml)로 메타 + 본문 raw 를 적재한다.
> **가격 정제(Step2)** 는 정규화(FMP·KIS 이형 → 표준 OHLCV) + 정합성 게이트 + quality_log +
> 통과 행의 `canonical/market_data/price_daily` 멱등 병합 적재까지 완료했다(`normalize-price`,
> ALPHA-133). **뉴스 정제(Step2)** 는 정규화(FMP·BigKinds 이형 → 표준 메타행) + 필수필드·발행일
> 게이트 + quality_log + 통과 행의 `canonical/news/news_articles` article_id 멱등 병합 적재까지
> 완료했다(`normalize-news`, ALPHA-131·132). **공시 정제(Step2)** 는 raw 공시 본문(euc-kr HTML)을
> 파싱해 공통 **공급계약 fact** 로 정규화 + 게이트 + quality_log + 통과 fact 의
> `canonical/disclosures/supply_contract_fact` rcept_no 멱등 병합 적재까지 완료했다
> (`normalize-disclosure`, ALPHA-345). **사업부문(segment) 정제(Step2)** 는 사업보고서 본문 표를
> 파싱해 사업부문별 매출 fact 로 정규화 + 게이트 + `canonical/disclosures/business_segment_fact`
> (rcept_no+segment_ordinal 멱등 병합)까지 완료했다(`normalize-disclosure-segment`, ALPHA-346).
> **ETF 구성종목 정제(Step2)** 는 정규화(FMP US·KRX KR 이형 → 공통 구성종목 fact) + 게이트
> (정체성 blocking·비중/주식수/평가금액은 참고필드로 범위 경고) + quality_log + 통과 행의
> `canonical/holdings/etf_holdings` (market,etf_id,constituent,as_of_date) 멱등 병합 적재까지
> 완료했다(`normalize-etf`, ALPHA-342·343). KRX 해외기초 ETF 의 대시(-) 비중은 null 로 통과시켜
> 구성종목을 보존한다.

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

# 국내 공시(disclosure) 원본저장(Step1) — OpenDART 공시목록(list.json) + 공시서류 원본
# (document.xml). 재무제표(fnlttSinglAcnt)와 다른 API·별개 잡이다. corp_code×날짜창으로
# 공시목록을 수집해 대상 유형(공급계약·사업보고서, report_nm 부분일치)만 추리고, 매칭 공시의
# 원문 본문을 rcept_no별 ZIP(euc-kr HTML)로 무변형 저장한다. 날짜창은 뉴스와 동형(미지정=증분
# 어제~오늘, 백필은 --from/--to). corp_code 는 corpCode.xml 로 런타임 매핑. 인증키는 env 주입.
DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-disclosure
# 백필 예: 2026-06 한 달
#   ... run ingest-raw-disclosure --from 2026-06-01 --to 2026-06-30

# 미국 ETF 구성종목 원본저장(Step1) — FMP ETF holdings(/stable/etf/holdings). 날짜창 없음
# (스냅샷 — 매 실행이 현재 구성종목 전량을 재요청). 수집 대상은 종목 유니버스(targets)가 아니라
# ETF 목록(etf.source.etf_map, 현재 US 대표 4종). 1 ETF→N 구성종목 fan-out 행을 ingest_date/
# run_id 파티션에 전부 append 하고, 벤더 기준일(updatedAt)은 무변형 보존(dedup·기준일 SCD 는
# 후속 canonical). ETF 는 정의상 구성종목이 있으므로 빈 holdings·에러객체는 ETF 단위 실패로 격리.
DATA_PIPELINE_ETF__SOURCE__API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-etf

# 국내 ETF 구성종목 원본저장(Step1) — KRX 정보데이터시스템 PDF(MDCSTAT05001). --source krx 로
# 벤더 선택. 로그인 계정 게이트 뒤라 KRX 계정(mbr_id/pw)을 env 로 주입해 run 당 1회 로그인,
# 승격 JSESSIONID 세션으로 getJsonData 를 호출한다. etf_map 은 our_etf_id → ISIN(krx_etf.source.
# etf_map, 현재 KR 대표 2종). 날짜창 없이 그날(trdDd) PDF 전량을 append(US ETF 와 동형). 해외기초
# ETF 는 비중·금액이 대시(-)로 와도 무변형 보존. ⚠️ 계정 파이프라인 전용(사람 동시 로그인 시 CD011).
DATA_PIPELINE_KRX_ETF__SOURCE__MBR_ID=... DATA_PIPELINE_KRX_ETF__SOURCE__PW=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-etf --source krx

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

# 공시 정제(Step2) — raw disclosures(메타 ndjson + 본문 ZIP) → 단일판매·공급계약 본문 파싱 →
# 공통 공급계약 fact. report_nm 으로 doc_type 라우팅(공급계약 '체결'만; 사업보고서·해지 등은 스킵),
# 본문은 document.xml ZIP 을 euc-kr 디코딩·파싱하고 메타 provenance(rcept_no·corp_code·ticker·
# corp_name·source_url·rcept_dt)를 조인한다. 게이트는 정체성(rcept_no)·시간축(report_date)·표현
# 불가 수치(int64 초과 금액·비유한 비율)를 blocking, 값 이상(유보 상대방·범위밖 비율·비양수 금액)을
# 경고로 data_quality_logs 에 남긴다. 통과 fact 는 canonical/disclosures/supply_contract_fact 에
# rcept_no 로 멱등 병합 적재한다(같은 rcept_no 최신 fetched_at 우선). --input-run-id 로 특정 수집
# 런만 재검증(미지정=raw disclosures 전체, 멱등; 스코프는 canonical 안 씀). 파서는 팀원(정준영)
# 검증 프로토타입 이식 — graph 투영·theme 링킹은 범위 밖(analysis-engine 소관).
uv run --package data-pipeline python -m data_pipeline.run normalize-disclosure
#   특정 런만: ... run normalize-disclosure --input-run-id 20260701T000000Z

# 공시 사업부문 정제(Step2) — raw disclosures → 사업보고서 '사업의 내용' 표 파싱 → 사업부문별
# 매출 fact. report_nm 사업보고서만 라우팅, 본문(euc-kr ZIP)은 공급계약과 같은 추출을 재사용하고
# parse_segments(4-전략 추출 + share_basis reported/rescaled/computed/unreliable 정규화, pandas)로
# 부문 rows 를 뽑아 1 문서 → N fact 로 펼친다. 행키는 (rcept_no, segment_ordinal) — segment_name 은
# 한 문서에서 유일하지 않다(제품/용역 sub-row). 게이트는 정체성·시간축·표현불가 수치 blocking,
# 값 이상(share_basis unreliable·비중 범위밖·매출 비양수) 경고. canonical/disclosures/
# business_segment_fact 에 멱등 병합. 파서는 팀원(정준영) 프로토타입(segments-v2) 이식(graph 제외).
uv run --package data-pipeline python -m data_pipeline.run normalize-disclosure-segment
#   특정 런만: ... run normalize-disclosure-segment --input-run-id 20260701T000000Z

# ETF 구성종목 정제(Step2) — raw etf_holdings(FMP US·KRX KR) → 공통 구성종목 fact 정규화 + 게이트.
# 벤더는 raw 키의 source= 로 판별한다(fmp=US·krx=KR, 수집 날짜창 없음). 정체성(market·etf_id·
# 구성종목·as_of_date)은 blocking, 비중·주식수·평가금액은 참고필드(대시(-)·결측=null, 범위 이상만
# 경고). 통과 행은 canonical/holdings/etf_holdings 에 (market,etf_id,constituent_ticker,as_of_date)
# 로 멱등 병합(같은 키 최신 fetched_at 우선). market-스코프 파티션이라 벤더 disjoint(교차충돌 없음).
# --input-run-id 로 특정 수집 런만 재검증(미지정=raw etf 전체, 멱등; 스코프는 canonical 안 씀).
uv run --package data-pipeline python -m data_pipeline.run normalize-etf
#   특정 런만: ... run normalize-etf --input-run-id 20260701T000000Z
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

Terraform 의 `modules/data-pipeline` 은 ECS task definition 과 Step Functions state machine 을
만든다. 상태머신은 아래 여덟 raw 수집을 병렬 ECS RunTask 로 실행한 뒤, **raw 전량 성공 시 정제
(normalize) 스테이지**를 이어 canonical 까지 한 실행에서 완주한다(ALPHA-355). 모든 브랜치에 같은
`--run-id` 를 넘겨 raw partition·canonical·collection_log 를 같은 실행 단위로 묶는다.

- `ingest-raw --source fmp`
- `ingest-price-raw --source fmp`
- `ingest-raw-financial --source fmp`
- `ingest-raw --source bigkinds`
- `ingest-price-raw --source kis`
- `ingest-raw-financial --source dart`
- `ingest-raw-disclosure`(공시, dart 세트) — 단일 벤더라 `--source` 없음
- `ingest-raw-etf`(미국 ETF 구성종목, fmp 세트) — SFN 은 fmp(미국) 브랜치만 실행. KRX 국내 ETF(`--source krx`, 로그인 게이트)는 코드에 있으나 SFN 편입은 후속(인프라 재정합)

정제 스테이지(raw 성공 뒤, ALPHA-355)는 아래 4잡을 병렬로 돌려 canonical 을 멱등 적재한다 —
벤더 API 키가 없어(레이크만 읽고 canonical 을 쓴다) 시크릿 없는 bigkinds task-def 를 재사용한다
(새 task-def·IAM 불요). 전체런(`--input-run-id` 없이)이라 멱등 적재다.

- `normalize-news` · `normalize-price` · `normalize-disclosure` · `normalize-disclosure-segment`

`normalize-etf`(ETF 구성종목 정제, ALPHA-342·343)는 코드에 있으나 SFN 정제 스테이지 편입은
후속(인프라 재정합) — KRX 국내 ETF 원본 수집(`--source krx`)이 SFN 미편입인 것과 같은 결이다.

재무(financial)는 canonical 스텝이 아직 없어 정제 스테이지에서 제외한다(raw-only). raw 가 partial/
실패면 정제로 넘어가지 않아 오염된 raw 위에 canonical 을 쌓지 않는다.

> ※ 공시·ETF 는 각각 dart·fmp 시크릿 세트에 env(`DATA_PIPELINE_DART_DISCLOSURE__/ETF__SOURCE__API_KEY`)를
> 편입해 상태머신 브랜치로 함께 돈다(ALPHA-347). 다만 스케줄러는 여전히 `DISABLED` 라 실제 cron 기동은
> 컷오버(시크릿 값 주입·스케줄러 ENABLED) 전까지 안 뜬다 — 새 브랜치 검증은 아래 수동 실행으로 한다.

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
settings.dart_disclosure.source      # DartDisclosureSource (OpenDART 국내 공시 — 인증키 env·KR 맵·report_nm 유형필터); 재무와 다른 API. 미설정이면 settings.dart_disclosure 은 None
settings.etf.source                  # EtfSource (FMP 미국 ETF holdings — 인증키 env·ETF 전용 맵 etf_map, 현재 US); 미설정이면 settings.etf 은 None
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
- **raw(공시)** — `raw/source=dart/dataset=disclosures/market=KR/ingest_date=…/run_id=…/` 에
  run_id 별 append. **가격·재무와 동형(bronze 통일)** — 공시목록(list.json) 행을 수집일 기준으로
  **전부 보존**한다(중복 판정 안 함). 재무제표(`fnlttSinglAcnt`, `dataset=financial_statements`)와
  **다른 API**다 — 공시는 개별 공시서류(공급계약·사업부문 등)를 다룬다. 메타 행은 `part-*.ndjson`
  에, 공시서류 원본 본문(document.xml)은 ndjson 에 못 섞는 바이너리(euc-kr HTML ZIP)라 같은 파티션
  아래 **`documents/{rcept_no}.zip` 로 받은 ZIP 을 무변형 저장**하고, 메타 행의 `document_raw_path`
  가 그 객체를 가리킨다(메타↔본문 링크). list.json 이 안 주는 `source_url` 은 rcept_no 로 구성해
  붙인다. 정체성 병합·정정 판정·corp_code↔ticker bridge 는 후속 canonical 소관.
- **raw(ETF 구성종목)** — `raw/source={fmp|krx}/dataset=etf_holdings/market={US|KR}/ingest_date=…/run_id=…/`
  에 run_id 별 append. **가격·재무와 동형(bronze 통일)** — ETF holdings 는 스냅샷이라 매 실행이 현재
  구성종목 전량을 주고, 받은 행을 수집일 기준으로 **전부 보존**한다(중복 판정 안 함). 수집 대상은
  종목 유니버스가 아니라 ETF 목록(`etf.source.etf_map`·`krx_etf.source.etf_map`)이라 **1 ETF → N
  구성종목**으로 펼쳐지고, 각 행에 벤더 기준일(FMP `updatedAt`·KRX `trd_dd`)·`our_etf_id`·`market`·
  `fetched_at` 를 부착한다. 같은 스냅샷 중복 제거·기준일 SCD·point-in-time 판정은 후속 canonical(silver)
  소관. US=FMP(ALPHA-337)·KR=KRX 로그인 게이트 PDF(ALPHA-336) — 정규화는 `normalize-etf`(342·343).
- **수집 로그** — `operations_archive/collection_logs/source=…/dataset=…/started_date=…/run_id=…/log.json`
  (`dataset=`로 갈라 같은 벤더의 뉴스·가격·재무 로그가 같은 run_id 를 공유해도 안 덮어쓴다)
- **canonical(가격, 정제 Step2)** — `canonical/market_data/price_daily/market=…/trade_date=…/part-*.parquet`
  에 게이트 통과 행을 **(market,ticker,trade_date) 키로 멱등 병합**. raw 와 달리 run_id·source_vendor
  파티션이 없다(멱등 — 같은 raw 를 몇 번 정제해도 결과 동일). market·trade_date 가 파티션, ticker 는
  파티션 내 행 키다. 같은 벤더 재적재는 최신 fetched_at 우선(정정 반영), **벤더 교차 같은 키 충돌은
  fail-loud**(둘 다 제외 + quality_log·비0 종료 — USD 를 KRW 로 태깅하는 통화 오염 방지). 통화는
  market 별 태깅만 하고 FX 환산하지 않는다.
- **canonical(뉴스, 정제 Step2)** — `canonical/news/news_articles/language={ko|en}/published_date=…/part-*.parquet`
  에 게이트 통과 행을 **article_id 키로 멱등 병합**. **정체성 `article_id = url_hash(원문 URL)`**
  (FMP `url`/BigKinds `PROVIDER_LINK_PAGE`)은 **소스 무관**이라 canonical 이 소스를 흡수한 **통합
  구조**가 된다 — `source_vendor` 는 파티션이 아니라 **컬럼**(provenance). 파티션은 **`language`
  (벤더 고정 파생: bigkinds=ko·fmp=en)→published_date 2단**(다운스트림 언어모델이 언어별로
  프루닝/분기하게 함, ALPHA-352). 같은 언어 안에선 같은 원문 URL 이면 벤더 불문 한 행으로 병합
  (통합 dedup)하되, **언어 파티션이 다르면 같은 URL 이라도 병합 안 함**(교차언어 dedup 은 다운스트림
  소관); URL 없으면 정체성은 BigKinds `NEWS_ID`→`title|date` 폴백. run_id 없음(멱등). 같은 article_id
  재적재는 최신 fetched_at 이 메타 대표를 이기되 **mentions 는 union**(종목↔기사 링크 보존). 다른
  article_id 가 같은 정규화 제목이면 **exact 병합 없이 duplicate_signal 로깅만**(URL 충돌은 곧 같은
  id 라 자동 병합). fuzzy 클러스터는 다운스트림 news_dedup_cluster 소관. mentions 는 JSON 문자열로 보존.
- **canonical(공시 공급계약, 정제 Step2)** — `canonical/disclosures/supply_contract_fact/report_date=…/part-*.parquet`
  에 게이트 통과 fact 를 **rcept_no(14자리 접수번호=문서키) 키로 멱등 병합**. raw 와 달리 run_id·
  source_vendor 파티션이 없다(멱등). 파티션은 `report_date`(rcept_dt, 공시 접수일) 하나, rcept_no 는
  파티션 내 행 키다. 같은 rcept_no 재적재(정정본 재수집)는 최신 fetched_at 우선. `source_vendor`(dart)는
  현재 KR·DART 단독이라 컬럼(provenance)이지 파티션이 아니다. 파서 출력(계약상대방·금액·매출액대비·
  계약기간·confidence)에 메타 provenance(corp_code·ticker·corp_name·source_url)를 조인한다. graph
  투영·theme 링킹·event 는 범위 밖(analysis-engine 소관).
- **canonical(공시 사업부문, 정제 Step2)** — `canonical/disclosures/business_segment_fact/report_date=…/part-*.parquet`
  에 게이트 통과 fact 를 **(rcept_no, segment_ordinal) 키로 멱등 병합**. 공급계약과 동형(멱등·report_date
  파티션·source_vendor 컬럼)이나 **1 문서 → N 부문**(fan-out)이라 행키에 파스 순서 `segment_ordinal` 을
  둔다 — `segment_name` 은 한 문서에서 유일하지 않다(제품/용역 sub-row 로 같은 부문 반복). 파서(4-전략
  추출)가 뽑은 `revenue_krw·revenue_share_pct·share_basis·period` 에 메타 provenance 를 조인한다.
- **canonical(ETF 구성종목, 정제 Step2)** — `canonical/holdings/etf_holdings/market=…/as_of_date=…/part-*.parquet`
  에 게이트 통과 행을 **(etf_id, constituent_ticker) 키로 멱등 병합**. raw 와 달리 run_id·source_vendor
  파티션이 없다(멱등). market·as_of_date 가 파티션, (etf_id, constituent_ticker)가 파티션 내 행 키다
  (1 ETF → N 구성종목 fan-out). 기준일 as_of_date 는 벤더가 준다 — FMP `updatedAt`(datetime→date)·
  KRX `trd_dd`(우리가 지정). **market-스코프 파티션이라 한 파티션엔 한 벤더만**(US=fmp·KR=krx disjoint)
  → 가격의 벤더 교차 충돌 가드가 불필요하다. 같은 키 재적재는 최신 fetched_at 우선. `weight_pct·shares·
  market_value` 는 참고 필드(KRX 해외기초는 대시(-)→null), `source_vendor`(fmp|krx)는 컬럼(provenance).
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
- 공시(disclosure) graph·eventization — 공급계약 fact(ALPHA-345)·사업부문 fact(ALPHA-346, pandas
  4-전략 파싱 → `canonical/disclosures/business_segment_fact`) 정제는 완료. graph 투영·theme 링킹·
  event 는 다운스트림(analysis-engine) 소관.
- 공시 **정정 supersession(point-in-time)** — 공급계약 canonical 은 파일링당 fact 를 rcept_no 로
  투영한다. 원본과 정정본([기재정정]…체결)은 서로 다른 rcept_no 라 각각 남고, 어느 정정본이 어느
  원본을 대체하는지의 링크는 list.json 행에 없다(정정 관련 필드·문서 파싱 필요; 원본이 정정 이전에
  수집되면 rm 마커조차 없음). 정정↔원본 collapse·이중계산 해소는 정체성 해소/SCD 문제라 후속
  트랙 소관이다(뉴스가 near-dup 를 news_dedup_cluster 로 미루는 것과 동형).
