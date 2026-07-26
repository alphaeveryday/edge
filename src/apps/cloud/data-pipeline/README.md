# data-pipeline

> 역할/아키텍처는 루트 [README](../../../../README.md)·[docs/context.md](../../../../docs/context.md)가 SSOT.
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
> 구성종목을 보존한다. **뉴스 이벤트 태깅(Step3, 피처)** 은 기사(제목+리드)에서 문서가 주장하는
> 사건을 온톨로지 라벨로 뽑아(`tagging/`, ALPHA-138) `feature/news/assertions` 에 article_id 멱등
> 병합 적재까지 완료했다(`tag-news`, ALPHA-365) — `entity_id` 는 NULL 로 두고 `text` 만 남긴다
> (엔티티 해소·assertion RDB 적재는 후속, ALPHA-190).
> **종목 마스터 적재(Step4, RDB)** 는 canonical ETF 구성종목을 Cloud Event Store 의
> `entity`/`actor`/`company_profile`/`instrument`/`equity_profile` 로 멱등 적재한다
> (`load-instruments`, ALPHA-372) — **이 저장소가 Cloud Event Store 48테이블에 쓰는 첫 경로**다.
> **가격변동 트리거 적재(RDB)** 는 canonical holdings 가중치와 구성종목 일봉으로 **가중 proxy
> 수익률**(coverage 정규화 — 분석엔진 L0 와 같은 산식, 정본)을 계산해 absolute gate(3%,
> `[price_triggers]`) 통과 거래일만 `price_movement_trigger` 로 멱등 적재한다
> (`load-price-triggers`, ALPHA-406→411) — 이 테이블의 **단일 writer** 이자 분석 SFN RDS
> 영속 전제 체인의 첫 고리다.

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
# (미지정=fmp). 인증키 없음. resultList[] row 원본 필드는 그대로 저장하고, market·
# bigkinds_query·fetched_at 같은 수집 provenance 만 붙인다.
# **카테고리 주도 전체 수집**(검색어 없음, ALPHA-417) — 경제 대분류(sources.toml
# `category_codes`, 필수)의 그날 뉴스 전체를 받는다. 종목 연결(mentions)은 수집이 아니라
# 정규화의 종목명 탐지(ALPHA-416) 산출물이다.
uv run --package data-pipeline python -m data_pipeline.run ingest-raw --source bigkinds

# 가격(OHLCV 일봉) 원본저장(Step1) — FMP EOD. 날짜창 미지정 = 증분(5일 소급~오늘,
# 주말·공휴일 공백 대비). 심볼맵은 가격 전용(price.source.symbol_map) — 현재 US 만.
DATA_PIPELINE_PRICE__SOURCE__API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-price-raw
# 백필 예: 2026-06 한 달
#   ... run ingest-price-raw --from 2026-06-01 --to 2026-06-30

# 국내 가격(OHLCV 일봉) 원본저장(Step1) — KIS(한국투자) REST. --source kis 로 벤더 선택
# (미지정=fmp). 인증은 OAuth 앱키/시크릿(env 주입), 도메인은 env(prod|vps). 수집 대상은
# canonical KR holdings 최신 스냅샷의 구성종목·ETF 티커 ∪ targets(ALPHA-419 — 유니버스가
# holdings 를 따라감). KRX 6자리 코드는 KIS 코드와 항등이라 심볼맵 없이 수집되고,
# symbol_map 은 예외 오버라이드 축. 신규 상장분은 코드에 문자가 섞이므로(0093A0 등 31종 중
# 7종) 형태 판정은 '선두 숫자 + 영숫자 6자'다(ALPHA-463 — 숫자로만 거르면 7종이 샌다).
# 토큰은 run 당 1회 발급·재사용.
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
# 수집 대상은 canonical KR holdings 최신 스냅샷의 **구성종목** ∪ targets(가격과 같은 축,
# ALPHA-477). KRX 단축코드는 corpCode 의 stock_code 와 항등이라 심볼맵 없이 수집되고,
# symbol_map 은 예외 오버라이드 축. ETF 자기 티커는 출처와 무관하게 뺀다 — DART 신고자가
# 아니라 corpCode 에 없어, 남기면 매 런 미매핑으로 잡혀 원장이 영구 INCOMPLETE 가 된다.
# corpCode 에 없는 종목은 kind=unmapped 로 런을 죽이지 않되(재시도로 낫지 않음) 계측에는
# 남는다 — 커버리지 구멍은 data_status=INCOMPLETE 로 사실대로 드러난다.
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
# etf_map, 현재 KR 31종 — 국내 반도체 30종 + KODEX 200, ALPHA-454). 날짜창 없이 그날(trdDd)
# PDF 전량을 append(US ETF 와 동형). 해외기초 ETF 는 비중·금액이 대시(-)로 와도 무변형 보존
# (현 유니버스엔 없다 — 경로만 유지). ⚠️ 계정 파이프라인 전용(사람 동시 로그인 시 CD011).
DATA_PIPELINE_KRX_ETF__SOURCE__MBR_ID=... DATA_PIPELINE_KRX_ETF__SOURCE__PW=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-etf --source krx

# 국내 ETF NAV 원본저장(Step1) — KIS ETF NAV비교추이(일), tr_id FHPST02440200(ALPHA-380).
# KRX getJsonData 는 무로그인·세션 모두 LOGOUT 이라(2026-07-20 실측) 가격에서 검증된 KIS 를
# 쓴다. 수집 유니버스는 별도 맵을 두지 않고 krx_etf.source.etf_map(KR 31종)을 그대로 공유한다
# — 구성종목과 NAV 가 다른 목록을 보면 안 되기 때문. KIS 는 ISIN 이 아니라 6자리 단축코드로
# 질의하며, 신규 상장분은 코드에 문자가 섞인다(0093A0 등 31종 중 7종 — 숫자로만 거르면 샌다).
# 창(--from/--to)을 그대로 받아 1콜로 구간 거래일 NAV 를 받으므로 백필도 같은 명령이다.
# raw 는 응답 행 전량 무변형(nav 외 stck_clpr·dprt 포함) append — 필드 선별은 canonical(382).
DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY=... DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-nav --from 2026-07-14 --to 2026-07-17

# 국내 ETF 장중 iNAV 원본저장(Step1) — KIS ETF NAV비교추이(분), tr_id FHPST02440100(ALPHA-555).
# 일별 NAV 와 같은 앱키·유니버스를 쓰되 시장코드가 "E"(일별은 "J")로 갈린다. 응답은 항상 30행
# 고정이라 조회 창 = --interval-sec × 30 이고(미지정 60초 → 30분치), 날짜·시각 지정이 무시돼
# **소급 백필이 없다** — 놓친 구간은 영구 유실이다. 휴장일·개장 전에는 어댑터가 status=skipped
# 로 막는다(ALPHA-557) — 그때 오는 건 직전 거래일 값이라 오늘 것으로 라벨하면 안 되기 때문.
DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY=... DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-inav --interval-sec 60

# 가격 정제(Step2) — raw price_daily(FMP·KIS) → 표준 OHLCV 정규화 + 정합성 게이트.
# 벤더는 raw 키의 source= 로 판별한다(수집 날짜창 없음). 통과/탈락 집계·탈락 사유는
# data_quality_logs 로 남기고, 통과 행은 canonical/market_data/price_daily 에 (market,ticker,
# trade_date) 로 멱등 병합 적재한다(같은 벤더 최신 fetched_at 우선, 벤더 교차 충돌 fail-loud).
# --input-run-id 로 그 수집 런의 raw 만 읽어 적재한다(SFN 이 도는 경로, ALPHA-389).
# 미지정=raw price 전체 = 백필·복구 수단. 어느 쪽이든 적재는 멱등이다.
uv run --package data-pipeline python -m data_pipeline.run normalize-price
#   그 런만: ... run normalize-price --input-run-id 20260701T000000Z

# 뉴스 정제(Step2) — raw stock_news(FMP·BigKinds) → 표준 메타행 정규화 + 필수필드·발행일 게이트.
# 벤더는 raw 키의 source= 로 판별한다(수집 날짜창 없음). blocking 사유(제목 결측·발행시각 파싱
# 불가/범위 밖)는 canonical 제외 대상이고, url·publisher 결측은 non-blocking 경고로 data_quality_logs
# 에 남긴다 — BigKinds 는 URL 없이 NEWS_ID 로 식별하므로 가변 필드로 벤더를 대량 탈락시키지 않는다.
# 통과 행은 canonical/news/news_articles 에 article_id 로 멱등 병합 적재하고(같은 벤더 최신
# fetched_at 우선), 다른 article_id 가 같은 정규화 제목·URL 해시면 duplicate_signal 로 로깅한다.
# --input-run-id 로 그 수집 런의 raw 만 읽어 적재(SFN 경로). 미지정=전체 백필. 둘 다 멱등.
uv run --package data-pipeline python -m data_pipeline.run normalize-news
#   특정 런만: ... run normalize-news --input-run-id 20260701T000000Z

# 공시 정제(Step2) — raw disclosures(메타 ndjson + 본문 ZIP) → 단일판매·공급계약 본문 파싱 →
# 공통 공급계약 fact. report_nm 으로 doc_type 라우팅(공급계약 '체결'만; 사업보고서·해지 등은 스킵),
# 본문은 document.xml ZIP 을 euc-kr 디코딩·파싱하고 메타 provenance(rcept_no·corp_code·ticker·
# corp_name·source_url·rcept_dt)를 조인한다. 게이트는 정체성(rcept_no)·시간축(report_date)·표현
# 불가 수치(int64 초과 금액·비유한 비율)를 blocking, 값 이상(유보 상대방·범위밖 비율·비양수 금액)을
# 경고로 data_quality_logs 에 남긴다. 통과 fact 는 canonical/disclosures/supply_contract_fact 에
# rcept_no 로 멱등 병합 적재한다(같은 rcept_no 최신 fetched_at 우선). --input-run-id 로 특정 수집
# 런의 raw 만 읽어 적재(SFN 경로; 미지정=전체 백필, 둘 다 멱등). 파서는 팀원(정준영)
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
# --input-run-id 로 그 수집 런의 raw 만 읽어 적재(SFN 경로). 미지정=전체 백필. 둘 다 멱등.
uv run --package data-pipeline python -m data_pipeline.run normalize-etf
#   특정 런만: ... run normalize-etf --input-run-id 20260701T000000Z

# 뉴스 이벤트 태깅(Step3, 피처) — canonical 뉴스(language=ko)를 LLM 으로 태깅해
# feature/news/assertions 에 article_id 멱등 병합. ko 만 태깅한다(프롬프트가 한국 금융 뉴스
# 전용 — 영어 기사에 씌우면 품질이 조용히 무너진다).
#
# **이미 태깅된 기사는 건너뛴다** — LLM 이 비싸서만이 아니라, 다시 돌리면 값이 흔들려 PIT
# 재현이 깨지기 때문이다. tagger_version·ontology_version 이 바뀔 때만 재태깅한다. 단
# llm_error(호출 자체 실패)는 판정이 아니라서 다음 런이 재시도한다.
#
# --from/--to 는 여기선 **태깅 대상 published_date 파티션**을 좁히는 창이다(raw 수집 창이
# 아니고, 미지정은 증분 기본창이 아니라 전체). 일일 SFN 은 --window-days N 으로 오늘−N일 창만
# 스캔한다 — 전량 스캔(실측 17분)의 대부분은 LLM 이 아니라 canonical 스캔이라 창이 곧 속도다
# (ALPHA-540). --limit 은 이번 런에서 새로 LLM 을 부를 기사 수 상한 — mentions ≥ 1
# 게이트(유니버스 종목이 안 잡힌 기사는 태깅 안 함, ALPHA-416)·창·limit 이 곧 비용 통제다.
LLM_API_KEY=... uv run --package data-pipeline python -m data_pipeline.run tag-news --limit 50 --window-days 3
#   기간 지정(백필): ... run tag-news --from 2026-07-01 --to 2026-07-08   # 미지정=풀스캔

# 종목 마스터 적재(Step4, RDB) — canonical ETF 구성종목(market=KR)의 **최신 기준일**을 읽어
# entity/actor/company_profile/instrument/equity_profile 을 만든다. 이 저장소가 Cloud Event
# Store 48테이블에 쓰는 첫 경로다.
#
# 멱등: 자연키 (market_code, ticker) 로 찾고 없을 때만 새 ULID 를 발번한다(ADR-0027) — 재실행이
# ID 를 바꾸면 그 ID 를 참조하던 FK 가 전부 끊긴다. MIC 없는 행(원화현금)은 instrument.market_code
# 가 NOT NULL 이라 스키마 자신의 규칙으로 빠진다.
#
# DB 설정은 DATA_PIPELINE_DB__* (스토리지와 같은 인프라 네임스페이스). 비밀번호는 env 주입만.
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-instruments

# corp_code enrichment(RDB, ALPHA-491) — load-instruments 가 NULL 로 둔 company_profile.
# dart_corp_code 를 OpenDART corpCode.xml 매칭으로 채운다. 공시 로더 issuer 해소(9→309)와
# 회사 자연키(우선주 dedup)의 공통 선행이라 별도 스텝(로더에 DART API 를 섞지 않는다).
# 유니버스=DB 술어(dart_corp_code IS NULL AND actor.country_code='KR'), ticker(6자리)=corpCode
# stock_code 매칭. 멱등: UPDATE … WHERE dart_corp_code IS NULL(시드 9종·재실행 불가침).
# 오염(비8자리·중복 corp_code)은 선검증해 거절, corpCode 미존재는 정상 miss 로 계수(Rule 12).
# OpenDART 키는 ingest-raw-disclosure 와 같은 DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY.
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run enrich-corp-code

# 가격변동 트리거 적재(RDB, ALPHA-411) — canonical holdings 가중치 × 구성종목 일봉 수익률의
# coverage 정규화 proxy(분석엔진 L0 산식 정본)가 absolute gate(abs_threshold=3%)를 넘는
# 거래일만 price_movement_trigger 로. holdings 는 거래일 이하 최신 스냅샷, 없으면 가장 이른
# 미래 스냅샷 폴백(엔진과 같은 선택, ALPHA-418 — 사용 횟수·as_of 는 quality_log 로 드러남).
# 게이트 미통과 일자는 행이 없는 게 정상이고 그 수는
# data_quality_logs 로 남는다. 구정책 행은 observation 참조가 없으면 자동 교체된다.
# 판정에 쓴 가격 coverage 는 두 곳에 나뉘어 남는다(ALPHA-452 — 1% 비중 종목 하나로 판정된
# 트리거를 사후에 구분하기 위함): 아직 트리거가 없는 (ETF,거래일) 셀은 quality_log
# (coverage_by_etf_date·coverage_min), 트리거가 난 셀은 그 행의 detection_reason 끝
# |coverage=… 다. 멱등 skip 때문에 갈리므로 분포를 볼 땐 둘을 합쳐야 한다.
# 하한으로 막지는 않는다(ALPHA-453).
# --from/--to 는 대상 trade_date 파티션을 좁히는 창(미지정=전체 스캔, (etf,date) 멱등 skip).
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-price-triggers

# 문서 마스터 적재(RDB, ALPHA-374) — canonical 뉴스(ko·en)를 document(document_type='NEWS')로.
# document_assertion.document_id FK 의 선행. 멱등: 자연키 uq_document_source(source_vendor,
# article_id)로 있으면 skip, 없을 때만 발번한다. ID 는 그 자연키에서 **결정적으로** 파생하는
# doc_<해시>(db.stable_domain_id, ALPHA-456) — assemble-events 가 같은 값을 계산해야 하고,
# 이 ID 가 assertion_id·source_event_id 의 재료라 랜덤이면 계보 전체가 랜덤을 상속한다.
# ADR-0027 의 ULID 형식과 달라 시간 정렬은 안 된다(그 축은 available_at). --from/--to 는 published_date
# 파티션을 좁히는 창(미지정=전체 스캔). SFN feature 페이즈에 편입됨(ALPHA-410) — 아래는 수동 백필용.
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-documents

# 공시 적재(RDB, ALPHA-476) — canonical 공시(supply_contract_fact·business_segment_fact)를
# document(document_type='DISCLOSURE')·disclosure_document·disclosure_fact·타입별 child 로.
# 설명 엔진이 explanation_run_disclosure_fact 로 직접 소비하는 fact 경로다(threading 미경유).
# issuer 는 corp_code 를 company_profile.dart_corp_code 로 해소, 미해소(마스터 미시드)면
# FK RESTRICT 회피 위해 skip+계측(커버리지 9→309 는 ALPHA-491). DB CHECK 는 파이썬 선검증해
# 위반 fact 만 뺀다(한 건이 배치 롤백 안 되게). 멱등: document 자연키·fact_id=결정적 파생
# ON CONFLICT. --from/--to 는 report_date 창(미지정=전체 스캔).
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-disclosure

# assertion 적재(RDB, ALPHA-375·376) — feature 뉴스 assertion(ko)을 document_assertion·
# assertion_argument 로. argument text 는 엔티티 마스터 완전일치(티커·정식명·종목명)로
# instrument 에 해소하고, 미해소·충돌은 quality log 에 사유별 수치로 남긴다(해소율 실측).
# 멱등: uq_document_assertion_natural(document_id, event_type, predicate) ON CONFLICT.
# 전무 해소 주장은 넣지 않는다. modality_code 는 어휘 확정 전까지 비운다(ALPHA-361).
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-assertions

# 이벤트 조립(RDB+LLM, ALPHA-412·ALPHA-545) — canonical 뉴스 제목을 v4 2콜(게이트/타입판별
# → 타입별 추출)로 정규화해 source_event 계보·참여자(event_argument)·측정값(event_measure)·
# event_thread 를 만든다(결정적 ID 산식 동일, stage 는 lifecycle 메뉴 밖이면 NULL). LLM 은
# tag-news 와 같은 LLM_* env. 창 미지정 = 오늘(KST) 하루(LLM 비용이 기사 수 비례), 과거는 창으로 백필.
LLM_API_KEY=... DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run assemble-events
```

> **thread 재계산(ALPHA-457 등 thread_key 산식 변경 시)** — `thread_id = f(thread_key)` 라
> thread_key 산식을 바꾸면 기존 `thread_id`·`thread_key` 가 전부 갈린다. 그런데 재실행은
> **미연결(event_thread_link 없는) 이벤트만** threading 하므로(`fetch_unthreaded_events`),
> 그냥 다시 돌리면 옛 키의 링크가 남아 재계산되지 않는다. 세 계보 테이블을 비우고 창으로
> 재실행한다(dev 는 누적 행이 적어 전량 재계산이 싸다 — source_event/assertion 은 결정적
> 멱등이라 보존, thread 층만 재생성). **TRUNCATE 는 못 쓴다** — `event_thread_link.thread_id`
> FK 가 `ON DELETE RESTRICT` 라 링크를 먼저 지워야 하고, `explanation_result.primary_thread_id`
> FK(`ON DELETE SET NULL`)가 참조해 TRUNCATE 는 거부된다. 순서 있는 DELETE 로 지운다:
> ```sql
> DELETE FROM event_thread_link;          -- RESTRICT FK: 링크를 먼저 지워야 event_thread 삭제 가능
> DELETE FROM thread_discovery_snapshot;
> DELETE FROM event_thread;               -- explanation_result.primary_thread_id 는 SET NULL 로 자동 정리
> ```
> ```bash
> ... run assemble-events --from <first-date> --to <last-date>   # 과거→현재 순(novelty 단조)
> ```
> 재실행은 thread 층만 되살린다. `explanation_result.primary_thread_id` 는 NULL 로 남았다가
> **설명 스텝(analysis-engine)이 다시 돌 때** 새 thread_id 로 재설정된다 — 설명까지 정합하려면
> 그 스텝도 이어서 돌린다.

> **dev RDS 는 private 서브넷이라 로컬에서 직접 못 닿는다.** 로컬 검증은 임시 베스천 + SSM
> 포트포워딩으로 터널을 뚫는다(선례: `analysis-engine/upload_ff5_rds.py` — "through the bastion
> tunnel"). 비밀번호는 RDS 관리형 시크릿(`rds!db-…`)에서 꺼내 env 로 넣는다.
> ```bash
> aws ssm start-session --target <bastion-instance-id> \
>   --document-name AWS-StartPortForwardingSessionToRemoteHost \
>   --parameters '{"host":["<rds-endpoint>"],"portNumber":["5432"],"localPortNumber":["15432"]}'
> ```
> 배포 실행은 베스천이 필요 없다 — ECS 태스크가 VPC 안에서 돌고 `edge-dev-pipeline-task` SG 가
> 이미 RDS 5432 를 허용한다.

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

dev 배포 이미지는 `src/apps/cloud/data-pipeline/Dockerfile` 로 빌드해 기존 `edge/pipeline`
ECR repository 에 `:${git_sha}` 와 `:data-pipeline-latest` 태그로 push 한다(`deploy-data-pipeline.yml`).

Terraform 의 `modules/data-pipeline` 은 ECS task definition 과 Step Functions state machine 을
만든다. 상태머신(`edge-dev-data-pipeline`)은 **raw → normalize → feature → analyze 4페이즈**를
한 실행에서 완주한다(ALPHA-355·386·408, [ADR-0028](../../../../docs/adr/0028-unified-pipeline-sfn.md)) —
각 페이즈는 잡을 병렬 ECS RunTask 로 돌리고, **앞 페이즈가 전량 성공해야** 다음으로 넘어간다 —
단 **raw 는 예외**다(ALPHA-460): 소스 하나가 실패해도 무관한 소스의 정제·분석은 계속 돈다.
정제가 빈 입력을 정상 성공으로 처리하므로 있는 만큼 처리하면 되기 때문이다. 대신 실패 직후
SNS 알림이 나가고, 그 런은 끝에서 FAILED 로 마감된다(막지 않되 조용하지도 않게).
모든 브랜치에 같은 `--run-id` 를 넘겨 raw partition·canonical·collection_log 를 같은 실행 단위로
묶는다. 앞 3페이즈는 같은 브랜치 빌더가 잡 목록만 바꿔 찍어내고(구조 동일), analyze 는 단일
태스크(analysis-engine 이미지)라 빌더 밖이다.

뉴스(지식) 레인은 별도 상태머신 `edge-dev-data-pipeline-news`(ALPHA-553)으로 분리 중이다 — 시장 레인과
자연 주기가 달라(시장=장마감 EOD, 뉴스=종일 유입) 자체 주기(평일 15:00·15:30·23:50 KST, 기본
DISABLED)로 `news raw → NormalizeNews → [TagNews·LoadDocuments] → LoadAssertions → AssembleEvents`
를 돌린다. 같은 브랜치 빌더를 재사용하고(news_* 페이즈), `instrument` 마스터는 시장 SFN 이 단일
writer 로 쓰고 뉴스 SFN 은 읽기 전용 공유한다. PR1 은 병행 세우기(위 시장 SFN 미변경), 시장 SFN
에서 뉴스 스텝 제거는 PR2 다(설계 근거는 코드 주석·ALPHA-553).

**raw 수집(12잡)** — 벤더 API 키가 필요해 각자의 시크릿 세트를 쓴다.

- `ingest-raw --source fmp`
- `ingest-price-raw --source fmp`
- `ingest-raw-financial --source fmp`
- `ingest-raw --source bigkinds`
- `ingest-price-raw --source kis`
- `ingest-raw-financial --source dart`
- `ingest-raw-disclosure`(공시, dart 세트) — 단일 벤더라 `--source` 없음
- `ingest-raw-etf`(미국 ETF 구성종목, fmp 세트)
- `ingest-raw-etf --source krx`(국내 ETF 구성종목, **krx 세트** — 로그인 게이트)
- `ingest-raw-nav`(국내 ETF NAV, **kis 세트** — 단일 벤더라 `--source` 없음)
  - ⚠️ KIS 토큰 발급은 앱키당 분당 1회라, 같은 앱키를 쓰는 `ingest-price-raw --source kis` 와
    **동시 실행하면 한쪽이 403**(EGW00133) 이다. SFN 에는 이미 나란히 편입돼 있고(ALPHA-458),
    `kis_auth` 가 이 코드를 만나면 61초 + 지터(0~20초) 대기 후 **최대 2회 재시도**(총 3회 시도)해
    흡수한다 — dev 실런으로 실증됨. 유량 제한이 아닌 4xx 는 기다려도 안 풀리므로 즉시 올린다.
  - **기준일(as-of) 규약**(ALPHA-387): 스케줄이 KST 15:40(장 마감 후, ALPHA-414)이라 거래일
    런은 그날 PDF 를 받는다(dev 실측: 07-22·23·24 스냅샷 내용 상이). 비거래일 런은 빈 응답이
    아니라 **직전 거래일 PDF** 가 온다(토 07-18 응답 = 금 07-17 바이트 동일) — 그래서 어댑터가
    `_as_of` 로 "거래일이면 오늘, 아니면 직전 거래일"을 라벨한다. 안 그러면 존재하지 않는
    거래일의 스냅샷이 canonical 에 as-of 로 남는다. 휴장일 집합은 Planner 와 같은
    `OPS_KR_HOLIDAYS`(terraform `kr_holidays`)를 krx task-def 에도 주입해 공유한다.
  - ⚠️ 잔여(ALPHA-387): **trdDd 백필 수단 부재** — `ingest-raw-etf` 는 `--from/--to` 를 안 받아
    실패한 날의 스냅샷을 다음 런이 못 줍는다(영구 결손, 별도 티켓). 빈 응답은 계속 fail-loud
    이고, ALPHA-460 이후 그 실패가 뒤 페이즈를 막지는 않는다(알림 + 런 FAILED 마감).
- `ingest-raw-etf-profile`(국내 ETF 프로필 = ETF 마스터 표시명 출처, **kis 세트**, ALPHA-462)
- `ingest-raw-investor`(종목별 투자자 수급, **kis 세트**, ALPHA-482) — 유니버스는 canonical KR
  holdings 파생(가격과 같은 축). `NormalizeInvestor → LoadEtfFlow` 체인의 raw 선행이다.
  - **EOD 서빙 블랙아웃 규약**(ALPHA-518·562): 확정 수급이 서빙되기 전에 질의하면 rt_cd=2
    `msg_cd=OPSQ2001 msg1="TIME LIMIT 00:00 ~ 15:40"` 이 온다. 이건 데이터 결손이 아니라
    **"지금이 서빙 개시 전"이라는 상시 조건**이라, 아무 때나 기다린다고 풀리지 않는다.
    그래서 **거래일이고 남은 재시도 예산(5×15초) 안에 15:41(KST)을 넘길 수 있을 때만**
    백오프로 대기하고, 아니면 대기 없이 그 심볼을 격리한다. 해소 시각이 msg1 의 상한 15:40 이
    아니라 **15:41** 인 것은 실측이다(15:40:53~59 실패, 15:41:00 이후 성공). 거래일 조건을
    빼면 비거래일 런이 심볼당 75초를 태워 유니버스 전체가 ~10시간이 된다(2026-07-26 실측:
    28분에 22종목). 휴장일 집합은 Planner·KRX·iNAV 와 같은 `OPS_KR_HOLIDAYS` 를 공유한다.

**수집 — 상태머신 밖(수동 전용)**

- `ingest-raw-inav`(국내 ETF **장중** iNAV, **kis 세트** — 일별 NAV 와 같은 앱키·유니버스)
  — **SFN 에 편입돼 있지 않다.** 위 raw 페이즈 잡 목록에 없고 `statemachine.tf` 에도 없다.
  스케줄 편입은 ALPHA-556 소관이라, 그전까지는 **손으로 돌릴 때만** 수집된다(자동 수집 없음).
  잘못된 시각에 돌리는 것 자체는 아래 가드가 막는다.
  - 일별(`FHPST02440200`)과 **시장코드가 갈린다**: iNAV 는 `FID_COND_MRKT_DIV_CODE="E"`, 일별은 `"J"`.
    `"J"` 로 보내면 전건 `rt_cd=2` 로 튕긴다(실측).
  - ⚠️ **소급 백필이 없다.** 날짜·시각 지정이 무시돼 항상 "지금 기준 최근 30행"만 온다 —
    놓친 구간은 영구 유실이다. 일별 NAV 처럼 창을 주고 나중에 주워올 수 없다.
    그래서 `--from/--to` 를 주면 **실행을 거부**한다(무시하고 돌면 갭을 못 메운 채 exit 0 이 된다).
  - **기준일 가드**(ALPHA-557): 응답에 날짜 필드가 없어(`bsop_hour` 만 옴) 거래일을 수집 시각으로
    붙여야 하는데, KIS 는 오늘 데이터가 없어도 **직전 거래일 데이터를 반복**한다(위 ALPHA-387 과
    같은 함정). 그래서 **거래일이고 09:00(KST) 이후**일 때만 수집하고, 아니면 `status=skipped`
    + 사유로 남기고 raw 를 쓰지 않는다. 장 마감 후(15:30~)는 막지 않는다 — 그때 오는 건 오늘
    종가 구간이라 라벨이 맞다. 휴장일 집합은 Planner·KRX 와 같은 `OPS_KR_HOLIDAYS` 를 공유한다
    (`kis` task-def 에도 주입). 이 skip 은 **정상 상태**라 raw-ingest-skipped 알람 토큰을 쓰지
    않는다 — 드러남은 collection_log 가 맡는다.

**정제(normalize, 6잡)** — 레이크만 읽고 canonical 을 쓰므로 벤더 키가 불요라, 시크릿 없는
bigkinds task-def 를 재사용한다(새 task-def·IAM 불요). **`--input-run-id $.run_id` 로 이 실행이
수집한 raw 만 정제한다**(ALPHA-389) — 정제 비용이 여태 쌓인 raw 전체가 아니라 이번 런에
비례한다. 적재는 여전히 멱등이다(병합이 기존 행을 읽어 합친다).

- `normalize-news` · `normalize-price` · `normalize-disclosure` · `normalize-disclosure-segment`
- `normalize-etf`(ETF 구성종목, ALPHA-342·343)

**feature(구 derive, 병렬 잡 + 직렬 선행 2스텝: load-instruments → enrich-corp-code)** — canonical 을
소비해 분석이 읽을 feature/factor 산출물을 만든다. 정제 뒤라야 하고(전부 canonical 을 읽는다) 병렬 잡들은 서로 독립이다.
시크릿이 다른 잡은 task-def 도 따로다. 최종 범위는 뉴스/공시 assertion·event·event_thread
추출 + 가격이벤트 생성까지(ALPHA-408) — 추출 스텝들은 alphamale 로직 이관 합의 후 편입한다.

- `tag-news`(→ 레이크 feature 존, **deepseek 세트**) — SFN 은 `--limit`(기본 10000)·
  `--window-days`(기본 3, 오늘−N일 창)를 넘겨 한 실행의 LLM 호출 수와 스캔 범위를 묶는다
  (창 미지정은 풀스캔이라 스캔이 O(전체 코퍼스), ALPHA-540). 상한에 걸린 잔여는 다음 실행이 이어받는다(mentions 있는 미태깅
  기사만 고른다 — 유니버스 무관 기사는 `skipped_no_mention` 으로 계측하며 태깅하지 않는다).
  LLM 호출은 기사별로 병렬 실행한다(ALPHA-519, `LLM_CONCURRENCY` env·기본 32·상한 100) —
  카운터·격리·병합은 취합 후 메인스레드라 순차 실행과 결과가 같다
- `load-instruments`(→ Cloud Event Store RDB, **rds 세트**) — DB 접속정보는 이 task-def 에만 주입한다.
  공용 env 에 두면 `DbConfig` 가 password 없이 구성돼 로드 시점에 죽어 **수집·정제 스텝까지 전멸**한다
- `enrich-corp-code`(**직렬**, load-instruments 뒤 → FeatureParallel 앞, ALPHA-491·532, **rds_dart 세트**
  =DB+DART) — company_profile 의 NULL dart_corp_code 를 corpCode.xml 매칭으로 채운다. LoadDisclosure 의
  issuer 해소(9→309)가 그 값에 의존하므로 병렬 앞 직렬이다. DB·DART 를 둘 다 부르므로 rds·dart 결합
  시크릿 task-def 를 쓴다(결합 없으면 rds 로 돌 때 source.enabled=false 로 skip). NULL 가드 멱등
- `load-price-triggers`(→ Cloud Event Store RDB, **rds 세트** 재사용) — 구성종목 가중 proxy
  3% 게이트(엔진 L0 정본, ALPHA-411). 창 미지정 = canonical 전체 스캔 + (etf, trade_date)
  멱등 skip 이라, 놓친 거래일을 다음 실행이 자연 회복한다(ALPHA-406)
- `load-documents`(→ Cloud Event Store RDB, **rds 세트** 재사용, ALPHA-374·410) — canonical 뉴스 →
  document. 자연키 멱등, LoadAssertions 의 FK 선행
- `load-disclosure`(→ Cloud Event Store RDB, **rds 세트** 재사용, ALPHA-476·532) — canonical 공시 →
  document(DISCLOSURE)·disclosure_document·disclosure_fact. issuer 는 앞 직렬 enrich-corp-code 가 채운
  dart_corp_code 로 해소(DART API 불요라 rds 세트). 자연키 멱등·정정 DO UPDATE
- `load-assertions`(**직렬**, 페이즈 전량 성공 뒤 → analyze 앞, ALPHA-376·410) — feature assertion →
  document_assertion·assertion_argument. document FK 의존이 병렬이면 레이스라 직렬로 둔다.
  엔티티 해소·해소율은 quality log 로 남는다
- `assemble-events`(**직렬**, LoadAssertions 뒤 → analyze 앞, ALPHA-412, **events 세트**=LLM+DB) —
  분석엔진 추출 체인의 이식: canonical 뉴스 제목 분류(LLM) → document/assertion/source_event
  계보 조립 → event_thread threading. 결정적 ID 산식·프롬프트는 엔진과 동일(정본), 창 미지정 =
  오늘(KST) 하루. analyze 는 이 스텝이 만든 event 를 소비한다(ADR-0028). 제목 분류 LLM 콜은
  배치별 병렬 실행한다(ALPHA-520, tag-news 와 같은 `LLM_CONCURRENCY` env) — 단 threading 은
  novelty 가 available_at 순서·prior 카운트에 의존해 **직렬** 유지다

재무(financial)는 canonical 스텝이 아직 없어 정제 페이즈에서 제외한다(raw-only). 앞 페이즈가
partial/실패면 다음으로 넘어가지 않아 오염된 raw 위에 canonical 을 쌓지 않는다.

**analyze(1태스크)** — 구 analysis-engine SFN 의 흡수(ALPHA-408). analysis-engine 이미지의
ENTRYPOINT 가 그대로 돌며(command 미지정 = 오늘 Asia/Seoul), **feature 산출물만 읽는다**
(canonical/feature 존 + Cloud Event Store 의 price_movement_trigger·instrument)가 페이즈 경계
계약이다 — 나중에 수집 빈도가 줄면 이 페이즈만 가격이벤트 기반 비동기 실행으로 떼어낸다.
특정일(trade_date) 수동 재실행은 SFN 이 아니라 `terraform output data_pipeline_analysis_task_family`
의 task-def 를 `aws ecs run-task` 로 직접 띄워 Command 를
`["--trade-date","YYYY-MM-DD","--request-id","manual-..."]` 로 덮는다.

> ※ task-def 는 시크릿 세트 단위로 만든다(`tasks.tf` 의 `secret_sets` 맵에 키를 넣으면 자동 생성) —
> 현재 9개: `fmp`·`bigkinds`·`kis`·`dart`·`krx`·`deepseek`·`rds`·`events`(LLM+DB)·`rds_dart`(DB+DART).
> 전부 같은 이미지를
> 쓰고 command override 로 스텝을 고른다. 스케줄러는 여전히 `DISABLED` 라 실제 cron 기동은
> 컷오버(스케줄러 ENABLED) 전까지 안 뜬다 — 브랜치 검증은 아래 수동 실행으로 한다.

Scheduler 는 최초 `DISABLED` 로 생성한다. 수동 검증은 `terraform output data_pipeline_state_machine_arn`
값으로 `aws stepfunctions start-execution --input '{"run_id":"manual-YYYYMMDDTHHMMSSZ"}'` 를 실행한다.

## 설정 계약

수집 설정은 **TOML 베이스 파일 + 환경변수 오버라이드**로 로드한다. 진입점은 하나다:

```python
from data_pipeline import load_settings

settings = load_settings()           # 패키지 동봉 기본 설정 + env
settings.news.sources                # {이름: NewsSource}
settings.bigkinds_news               # BigKindsNewsSource (국내 뉴스 — 키 없음·카테고리 주도 전체 수집, category_codes 필수); 미설정이면 None
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
  dedup 없음). `CONTENT` 도 BigKinds 응답 원본 필드 그대로 저장한다. **전량 보존은 받아온
  것을 안 버린다는 뜻이지 전부 받는다는 뜻이 아니다** — 무엇을 받을지는 카테고리 필터가
  정하고(경제 대분류 전체·검색어 없음, ALPHA-417 — 종목 매핑은 정규화 탐지 소관), 받은
  뒤로는 무변형 보존이다.
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
- **raw(ETF iNAV)** — `raw/source=kis/dataset=etf_inav/market=KR/ingest_date=…/run_id=…/` 에
  run_id 별 append(ALPHA-555). 일별 NAV(`dataset=etf_nav`)와 **다른 축**이라 dataset 을 나눈다 —
  저건 거래일 grain 종가 확정 NAV, 이건 장중 시각 grain 추정 NAV 다. 응답이 **항상 30행 고정**이라
  조회 창 = `--interval-sec` × 30 이고, **소급 조회가 불가능**하다(`FID_INPUT_HOUR_1` 무시·`tr_cont`
  없음 — 실측). 그래서 폴링 창을 겹치게 잡아 같은 시각이 여러 run 에 중복 수집되는 것이 **정상**이며,
  겹침이 유일한 갭 방어 수단이라 raw 는 전부 보존하고 중복 제거는 canonical 소관이다.
  각 행에 `interval_sec`·`our_etf_id`·`market`·`kis_symbol`·`fetched_at` 를 부착한다.
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
  **종목 매핑은 정규화의 일이다(ALPHA-416)**: BigKinds 행의 mentions 는 canonical ETF holdings
  최신 스냅샷(KR)의 종목명 인덱스로 제목+리드에서 substring 탐지해 합성한다(구 raw 의
  `our_ticker` provenance 와 union — 이행기 호환). 이름 비교는 **NFKC 정규화 후 substring**
  (인덱스·기사 텍스트 양쪽 — 저장소 관례). **동명이(같은 이름, 다른 ticker)는 어느 쪽도 고르지
  않고 인덱스에서 뺀다**(ALPHA-448) — 이름을 키로 덮어쓰면 parquet 나열 순서가 승자를 정해
  mention 이 비결정적으로 틀린다. 유니버스가 바뀌면 전체 백필 재정규화로 과거 기사에
  소급되고, 탐지 계측(`detected_name_counts`)·제외된 동명이(`mention_index_ambiguous_names`)·
  인덱스 상태는 quality_log 에 남는다.
  FMP 는 ingest 병합 mentions[] 그대로(영문 기사라 한글 이름 탐지 무의미).
  `lead_text` 는 벤더 리드(BigKinds `CONTENT` 200~256자 스니펫·FMP `text`)를 자르지 않고 통과시킨
  것으로, 태깅 입력이다(결측은 NULL — 게이트 대상 아님). 본문 전문 크롤은 범위 밖이다.
- **feature(뉴스 assertion, 태깅 Step3)** — `feature/news/assertions/language=ko/published_date=…/part-*.parquet`
  에 태깅 결과를 **article_id 키로 멱등 병합**(입력 canonical 과 같은 파티션 축이라 한 canonical
  파티션이 한 feature 파티션에 대응 — 날짜창 프루닝이 곧 비용 통제). **canonical 이 아니라 feature
  인 이유**: 여기 값은 벤더 원본의 결정론적 정규화가 아니라 **LLM 추론 결과**라 재실행이 값을 바꿀
  수 있고 호출마다 돈이 든다 — raw 에서 언제든 무료로 재생성되는 canonical 과 라이프사이클이 다르다.
  그래서 **한 번 만든 건 다시 만들지 않는다**(`tagger_version`·`ontology_version` 이 바뀔 때만 재태깅;
  단 `llm_error` 는 '물어보지도 못했다'는 뜻이라 다음 런이 재시도한다 — 일시 장애가 기사를 영구히
  누락시키지 않게). **행은 기사 1건 = 1행**이다(assertion 1건=1행이 아니다) — 사건 0건인 기사(시황·
  논평 등 다수)가 행을 잃으면 '태깅했는데 사건이 없었다'와 '태깅한 적 없다'가 구분되지 않는다.
  `assertions`·`reasons` 는 JSON 문자열(canonical 뉴스 mentions 와 같은 관례), `status` 는 기사별로
  무슨 일이 있었는지(ok·no_title·llm_error·llm_unparseable·bad_doc_class). `entity_id` 는 NULL —
  엔티티 해소는 entity 마스터(RDB)를 읽어야 해 적재(ALPHA-190)와 같은 소관이고 `text` 가 그 입력이다.
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

## 운영 원장 — expected_task·Planner·Reconciler (ALPHA-530)

SFN/ECS 실행을 **사후 복구 가능하게 관측**하는 Postgres projection(`ops_*` 5테이블,
`migrations-cloud`). 실행을 **제어하지 않는다**(관측만 — ADR-0030). 답하는 질문: *원래 실행돼야
했지만 아예 시작되지 않은 작업은 무엇인가.* 코드: `src/data_pipeline/ops/`.

- **상태 4축을 섞지 않는다**: plan_status(DUE·SKIPPED) / task_outcome(PENDING·FULFILLED·FAILED·
  BLOCKED·MISSED) / attempt.execution_status(RUNNING·SUCCEEDED·FAILED·TIMED_OUT) /
  data_status(UNKNOWN·VALID·VALID_EMPTY·INCOMPLETE·INVALID). STALLED 는 저장 상태가 아니라
  RUNNING+시간초과로 파생하는 health(이슈로만 남김).
- **Task Catalog**(`ops/catalog.py`) — 논리 작업의 안정적 ID·정적 의존 SSOT. **등록 25작업**
  (ECS Task state 33개 중, ALPHA-181). 제외 8개는 `fmp`·`dart`·`krx` task-def 에 DB
  env 가 없어서(부분 주입 불가 — 벤더 API 컨테이너에 RDS 접속을 주는 신뢰경계 결정이 선행)와
  `AnalyzeOne`(다른 이미지·Map 팬아웃 31종이 한 state 로 뭉쳐 거짓 초록). `TagNews` 는 DB env 가
  없지만 **feature 게이트 멤버**라 등록하되 `instrumented=False`(Reconciler 의 SFN 증거 backfill 에
  맡기고 LEDGER_GAP 을 안 연다) — 빼면 그것만 죽은 런에서 LoadAssertions 가 BLOCKED 대신
  MISSED 로 찍힌다. 제외 8개 중 대부분이
  **수집**이라 커버리지의 모양이 숫자보다 중요하다 — 정제·적재는 다 덮이고 수집은 12개 중 5개다.
  근거 표는 `ops/catalog.py` docstring, CI 는 `test_ops_catalog` 가 양방향으로 잠근다.
  MVP 3작업(ALPHA-530)이었던 것:
  `PRICE_COLLECTION_KIS`·`NORMALIZE_PRICE`·`LOAD_PRICE_DAILY`(정제→feature 게이트 직후 첫 price
  canonical consumer). 종목 반복은 작업이 아니라 completeness/manifest, 개별 규칙은 quality_check.
- **`ops` 로그 봉투**(ALPHA-181) — 모든 스텝이 자기 로그(collection_log·quality_log)에
  `"ops": {"records_out": N, "failed_records": M}` 를 남긴다. 관측(`ops/entry.py:_observe_from_log`)은
  **이 봉투만** 읽으므로 task_key 별 분기가 없다 — 새 작업을 카탈로그에 등록해도 리더를 안 고친다.
  봉투가 스텝 안에 사는 이유: 어느 카운터가 유실인지는 스텝만 안다(적재의
  `skipped_unknown_instrument` 는 유실, `skipped_self`·`gated_out`·`already_tagged` 는 정상 동작).
  ⚠️ **스코프 규칙** — 산출과 유실은 *이 런이 재판정한 범위*에서 함께 온다. 재판정 없이 건너뛴
  항목은 산출로도 유실로도 세지 않는다(세면 옛 실패가 산출로 뒤집힌다). 그래서 매 런 입력을 다시
  읽고 다시 거르는 스텝(수집·정제·적재)은 기존 행도 산출로 세지만, 처리분을 건너뛰는 스텝
  (`tag-news`·`assemble-events`·`enrich-corp-code`·`load-price-triggers`)은 no-op 재실행이 0건 →
  `UNKNOWN` 이다. 상태 기반 완전성("지금 이 데이터셋이 온전한가")은 completeness 축 소관(ALPHA-490).
  봉투가 없거나 두 키 중 하나라도 결측이면 리더는 낙관값으로 메우지 않고 warning + `UNKNOWN`(Rule 12).

### 실행 흐름 (스펙 §5)

```
EventBridge(daily) → Planner(plan-run) : DB 트랜잭션(pipeline_run+expected_task+snapshot) → commit
                                       → 결정적 execution_name → SFN StartExecution
각 ECS 태스크(25작업) → wrapper instrument : attempt 시작/종료·data_status 관측(원장 장애 시 통과)
EventBridge(reconcile) → Reconciler : SFN/ECS 증거로 예정↔실제 대조(MISSED/BLOCKED/STALLED/…)
```

Planner 는 StartExecution **전에** 원장을 남긴다 — SFN 이 안 떠도 "실행 자체가 안 됐다"를 잡기
위함(ECS 안에서 자기 expected_task 를 만들면 불가능). `ExecutionAlreadyExists` 는 즉시 LAUNCHED
로 보지 않고 DescribeExecution 으로 입력을 비교한다(동일=LAUNCHED, 상이=LAUNCH_CONFLICT).

### 실행 (로컬/수동)

```bash
# Planner — 원장 기록 + SFN 시작. OPS_STATE_MACHINE_ARN·DATA_PIPELINE_DB__* 필수.
OPS_STATE_MACHINE_ARN=arn:aws:states:…:stateMachine:edge-dev-data-pipeline \
  python -m data_pipeline.run plan-run
# Reconciler — 예정↔실제 대조(advisory lock 으로 중복 실행 방지).
python -m data_pipeline.run reconcile
```

배포는 `aws_ecs_task_definition.ops`(data-pipeline 이미지 재사용) + 스케줄러 2개(daily=plan-run·
reconcile) + DLQ. daily 스케줄이 SFN 직접 시작에서 **Planner 경유**로 컷오버된다(스케줄 state 기본
DISABLED). 원장 DB 는 canonical 과 같은 Cloud Event Store(public 스키마, `ops_` 접두사).

### 복구 절차

- **MISSED**(미실행): Reconciler 가 증거(SFN history·ECS)로 판정. "attempt 행 없음"만으로 단정하지
  않는다 — 원장 누락은 `LEDGER_GAP` 으로 backfill, ECS 생성 확인 불가는 `EVIDENCE_LOST`.
  **실행이 RUNNING 인 동안은 작업별 deadline 만으로 MISSED 를 찍지 않는다**(ALPHA-181) — deadline
  오프셋은 스테이지별 SLA 가 없어 잠정값이라 정상 실행 중에도 뒤 스테이지에서 자주 지난다.
  "아직 차례가 아니다"와 "아예 시작되지 않았다"는 다르고, `missed_at` 은 `COALESCE` 라 한 번
  찍히면 지워지지 않는다. 런 전체 hard deadline(6h)은 실행 중이어도 존중한다 — 그게 안전망이다.
- **미승격 raw 재처리**: 실패 런 raw 는 `normalize-<step> --input-run-id <실패 run_id>` 로 수동
  재처리(ADR-0030). 원장의 `ops_task_attempt`·`ops_reconciliation_issue` 가 어느 run 인지 알려준다.
- **비래치 MISSED**: 늦게 성공하면 `MISSED → FULFILLED`(missed_at 보존, MISSED 이슈 RESOLVED).

### 게이트 경계 — 이번 범위 밖 (ALPHA-452/453)

`data_status` 는 future gate 의 **정본이 아니다**(관측값). 완전성 결손은 `INCOMPLETE` 로 **기록만**
하고 downstream 을 차단하지 않는다(ADR-0030 — "관측만"). "데이터 없음 vs 움직임 없음"을 가르는
coverage 계측(**ALPHA-452**)·게이트 정책·UNEVALUABLE(**ALPHA-453·490**)이 gate 의 정본을 소유하며,
원장은 그 assessment 를 **참조/projection** 할 뿐이다. 이번 MVP 에 `gate_decision` 물리 컬럼을 두지
않은 이유다.

### 알려진 한계 (후속)

edge-review 4라운드로 실질 결함은 수렴했고, 아래는 **의도적으로 남긴** 경계다:

- **dep 완료 판정의 ECS fallback 미적용** — 선행 작업 완료를 SFN history 의 exit code 로만 본다.
  드물게 exit code 가 ECS 에만 있으면(SFN output 잘림) 선행을 미완으로 봐 downstream 을 MISSED
  대신 **BLOCKED** 로 마감한다 — 방향이 안전(BLOCKED 가 "선행 때문"을 더 정확히)하고, 매 dep 마다
  ECS 콜을 더하는 대가가 이 사소한 불일치보다 커서 두었다(Rule 2).
- **SFN 통합 실패(TaskFailed) 를 실패로 인정** — exit code 를 못 얻고 ECS 도 미확정일 때 SFN
  TaskFailed 를 FAILED 로 본다. runTask.sync 의 TaskFailed 는 컨테이너 exit≠0 이 아니라 **작업
  자체가 실패**한 신호라 이게 맞다(exit code 는 우선 조회한다).
- **완전성(VALID)의 스냅샷 wiring** — data_status 는 기대집합(expectation_snapshot)+수신집합이
  있어야 VALID 를 낸다. 아직 plan-run 이 holdings 파생 universe 를, observer 가 received_count 를
  공급하지 않아 프로덕션 data_status 는 UNKNOWN/INCOMPLETE/VALID_EMPTY 다(false-VALID 를 내느니
  UNKNOWN — 스펙 §6). 유니버스 밖 결측 종목 탐지를 켜려면 스냅샷 wiring 이 후속이다.

## 범위에서 의도적으로 제외한 것 (후속)

- 뉴스 근접중복 클러스터링(fuzzy)·교차벤더 dedup — canonical 은 exact article_id 병합 + 제목/URL
  충돌 로깅까지다. dedup_cluster·엔티티/컨셉 링크는 후속. **이벤트 태깅은 이 모듈 소관으로
  들어왔다**(ALPHA-138, `tagging/` 참조) — 피처 추출까지가 data-pipeline 경계이고, 그 피처를
  소비하는 분석(event 조립·스레드·가격 설명)이 analysis-engine 소관이다.
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
