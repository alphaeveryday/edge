# data-pipeline — 실행 단계별 명령과 주의

> 모듈 [README](../README.md) 의 상세 문서다. 레인·단계별 동작과 도입 경위가 함께 있다.

## 실행

Python 도구는 **uv**다(ADR-0001). Python 워크스페이스 루트는 `src/pyproject.toml`.

```bash
uv sync --package data-pipeline --group dev                         # src/에서 의존성 설치
uv run --package data-pipeline --group dev pytest apps/cloud/data-pipeline/tests

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
# `category_codes`, 필수)의 창 안 뉴스 전체를 받는다. 종목 연결(mentions)은 수집이 아니라
# 정규화의 종목명 탐지(ALPHA-416) 산출물이다.
# 창 미지정 = **`[어제, 오늘]` 2일**(다른 증분 스텝과 같은 `default_window`, 날짜는 **KST
# 달력** — ALPHA-883). 하루가 아니다 —
# 깊이가 2배라 `max_pages` 산정이 여기 걸린다(sources.toml 주석이 근거 SSOT).
# 받아야 할 건수는 응답의 `totalCount` 가 정본이고, 못 채우면 유실 건수와 함께 절단 경고를
# 낸다(kind=truncation, exit 0). 그 경고는 `<name>-collection-truncated` 알람이 받는다
# (dev = `edge-dev-data-pipeline-collection-truncated`).
uv run --package data-pipeline python -m data_pipeline.run ingest-raw --source bigkinds

# 가격(OHLCV 일봉) 원본저장(Step1) — FMP EOD. 날짜창 미지정 = 증분(5일 소급~오늘,
# 주말·공휴일 공백 대비). 심볼맵은 가격 전용(price.source.symbol_map) — 현재 US 만.
DATA_PIPELINE_PRICE__SOURCE__API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-price-raw
# 백필 예: 2026-06 한 달
#   ... run ingest-price-raw --from 2026-06-01 --to 2026-06-30

# 국내 가격(OHLCV 일봉) 원본저장(Step1) — KIS(한국투자) REST. --source kis 로 벤더 선택
# (미지정=fmp). 인증은 OAuth 앱키/시크릿(env 주입), 도메인은 env(prod|vps). 수집 대상은
# canonical KR holdings 의 ETF 별 최신 파티션 합집합(부분 스냅샷이 유니버스를 못 줄임,
# ALPHA-590)의 구성종목·ETF 티커 ∪ targets(ALPHA-419 — 유니버스가 holdings 를 따라감). KRX 6자리 코드는 KIS 코드와 항등이라 심볼맵 없이 수집되고,
# symbol_map 은 예외 오버라이드 축. 신규 상장분은 코드에 문자가 섞이므로(0093A0 등 39종 중
# 9종) 형태 판정은 '선두 숫자 + 영숫자 6자'다(ALPHA-463 — 숫자로만 거르면 9종이 샌다).
# 토큰은 run 당 1회 발급·재사용, 그리고 `KIS_TOKEN_CACHE_PARAM`(SSM SecureString) 이 주입되면
# 컨테이너 사이로도 공유한다(ALPHA-573 — docs/deploy-schedule.md 의 ingest-raw-nav 항목).
# 시장 SFN은 `--max-failed-symbols 1`을 명시한다(ALPHA-798). 고립 실패 1개는 exit 0이지만
# collection log는 partial·failed_records=1을 보존해 원장에는 INCOMPLETE로 남는다. 2개 이상과
# 저장 0건·신규편입 스캔 미완료는 비영이다. FMP·Yahoo는 양수 임계값을 거부해 엄격 모드다.
#
# ⭐ **유니버스에 처음 들어온 종목은 이력 창으로 한 번 더 받는다**(ALPHA-989). 유니버스가
# holdings 파생이라 ETF 가 추가되면 즉시 넓어지는데 증분 창은 5일이라, 넓어진 유니버스는
# 최근 5일만 다시 긁고 그 이전 날짜에는 새 종목이 **영영** 안 채워졌다(dev 레이크에서 절벽
# 3회·결손 1,613셀). 그래서 편입 종목에만 `NEWCOMER_LOOKBACK_DAYS`(400일 ≈ 270거래일) 창을
# 붙인다. 전 종목에 그 창을 매일 물리면 수집량이 통째로 커지므로 **편입분만** 간다. 창
# 길이를 정하는 건 소비자다 — 가장 깊은 것이 analysis-engine `attribute.SIGMA_N`(60거래일
# 롤링)이고 4배 여유를 뒀다. 편입이 없는 런은 이력 수집 자체가 안 돈다. 이미 그만큼 깊은
# `--from` 백필도, 하한 없는 창(`--to` 만 준 백필)도 안 돈다.
#
# ⭐ **판정은 존재가 아니라 깊이다.** "canonical 에 있다"는 "이력이 있다"를 증명하지 못한다 —
# 티커는 얕게도 들어온다(판정 불가 런의 증분 5일치 · 이력 fetch 가 실패해도 어댑터가 모은
# 봉을 냄 · MAX_PAGES 절단). SFN 은 partial 런도 정제로 계속 보내므로 그 얕은 행이 실제로
# canonical 에 들어가고, 존재만 보면 그 티커는 '이미 있음'이 되어 이력이 영영 재시도되지
# 않는다. 그래서 최신 기준 파티션과 **`NEWCOMER_DEPTH_PARTITIONS`(60거래일) 과거 파티션**
# 둘을 보고, 하나에라도 없으면 편입이다 — 성공할 때까지 자격이 유지된다(상태 저장 없음).
# canonical 이 그만큼 깊지 않으면(부트스트랩·손상) 답할 수 없는데, 그건 '괜찮음'이 아니라
# '증명되지 않음'이라 **전 종목을 편입으로 본다**(`ok(depth_unavailable)` ·
# `ok(bootstrap_empty_canonical)`). ⚠️ 그 런은 유니버스 전체 × 400일이다(실측 앵커:
# ALPHA-989 백필 실런이 413종 × 378일에 10분 32초 — 400일이면 ~11분) —
# 다만 한 번 받으면 파티션이 깊어져 다음 런부터 이 모드가 꺼지므로 **한 런으로 끝난다.**
# ⚠️ 대가: 갓 상장한 종목은 60거래일이 찰 때까지 계속 편입으로 잡혀 하루 몇 콜을 더 쓴다
# (영구가 아니라 자연 소멸. 실측 2026-08-19 dev: 0210A0 1종이 이 상태).
#
# **편입분은 증분 창에서 뺀다** — 둘 다 받으면 이력 fetch 가 실패해도 증분 5일치가 남아
# 위의 얕은 유입이 된다. 빼 두면 실패한 종목은 행이 하나도 안 남아 다음 런이 다시 잡는다.
#
# 판정 상태는 collection_log 의 `newcomer_scan` 에 **모든 경로에서** 남는다:
#   ok · not_applicable(holdings 파생 아닌 소스) · not_reached(스캔 전 종료) ·
#   scan_failed(스캔 중 예외) · covered_by_primary_window(1차 창이 이미 깊다) ·
#   no_usable_partition(scanned=N)
# 편입 종목 수는 `symbols_newcomer`, 붙인 창 하한은 `newcomer_window_from` 이다.
# ⚠️ `no_usable_partition` 은 런을 **partial(exit 1)** 로 내린다 — 기준 파티션을 못 찾은 런에
# 편입 종목이 있었다면 그 이력은 **영구** 결손이다(다음 런은 '이미 있음'으로 본다). 조용히
# 성공으로 마감하면 아무도 모른다. 단 `ops.failed_records` 는 안 올린다(심볼 실패가 아니라
# 원장을 영구 INCOMPLETE 로 만들면 안 된다).
DATA_PIPELINE_KIS_PRICE__SOURCE__APP_KEY=... DATA_PIPELINE_KIS_PRICE__SOURCE__APP_SECRET=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-price-raw --source kis
# 백필 예: 2026-06 한 달
#   ... run ingest-price-raw --source kis --from 2026-06-01 --to 2026-06-30

# 벤치마크 지수(^KS11·^KQ11) 원본저장(Step1) — Yahoo(yfinance). **로컬 전용 실험 소스**:
# yfinance 는 local 의존그룹이라 클라우드 이미지에 없고 SFN 수집 잡에도 안 든다. 인증 없음.
# 지수는 targets/holdings 와 무관하게 항상 계획에 들고(대조축이라 symbols 로 들어올 길이
# 없다), KR 6자리 코드를 함께 넘기면 .KS 접미사로 받는다(KOSDAQ 은 symbol_map 으로 명시).
uv sync --package data-pipeline --group local   # 로컬에만 설치. 미설치로 부르면 fail-loud
uv run --package data-pipeline python -m data_pipeline.run ingest-price-raw --source yahoo \
  --from 2026-06-01 --to 2026-06-30
# 클라우드(분석엔진)는 **s3 canonical 에서만 소비한다** — yfinance 를 클라우드에서 부르지
# 않는다. 로컬 수집분을 태우려면 수집·정제 두 런을 s3 레이크(분석엔진이 읽는 버킷:
# ALPHAMALE_LAKE_BUCKET, dev=edge-dev-pipeline-lake)로 돌린다:
#   DATA_PIPELINE_STORAGE__BACKEND=s3 DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
#     ... run ingest-price-raw --source yahoo --from … --to …   # 그리고 같은 env 로 normalize-price

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
# (document.xml). 재무제표(fnlttSinglAcnt)와 다른 API·별개 잡이다. **날짜창의 시장 전체**
# 공시목록을 페이지네이션해 유니버스(stock_code) 행을 **전량** 메타로 남기고, 그중 대상 유형
# (공급계약·사업보고서, report_nm 부분일치)의 원문 본문만 rcept_no별 ZIP(euc-kr HTML)로 무변형
# 저장한다. ⚠️ **유형은 탈락 조건이 아니라 행마다 실리는 `is_target` 플래그다**(ALPHA-865) —
# 목록 질의는 유형과 무관하게 창 전체를 훑으므로 비대상 행을 버려도 콜이 하나도 안 주는데,
# 버리면 나중에 대상을 넓힐 때 그 기간을 통째로 재수집해야 한다. 비싼 것은 본문(행당 1콜)이라
# 그쪽만 제한한다. 비대상 행은 `document_raw_path`·`body_format` 이 명시적 None 이고, 감쇠는
# collection_log 의 `universe_matched`(유니버스 통과)·`type_matched`(유형까지 통과)가 갈라 센다.
# `is_target` 을 정한 기준은 같은 로그의 `report_name_filters` 에 남는다(필터를 넓힌 뒤 어느
# 런이 어느 기준이었는지 복원하려면 필요하다). 정제는 원래부터 report_nm 으로 라우팅해 와서
# 비대상 행은 `records_skipped_type` 으로 빠진다 — 정제 스텝은 손댈 것이 없다.
# 날짜창은 뉴스와 동형(미지정=증분 어제~오늘, 백필은 --from/--to). 인증키는 env 주입.
# 단 배치 증분 창은 원장 워터마크(disclosure_watermark.py, ALPHA-987)가 재결정한다 —
# 기본은 그림자(창 불변, 계산-실제 대조만 collection_log 에 기록)이고
# dart_disclosure.watermark_window=true 면 직전 완주 런의 window_to 당일부터로 넓혀
# 직전 런 실패·건너뜀을 자동 회수한다(부재·조회 실패는 기본창 폴백 + window_source 기록).
# 수집 대상은 canonical KR holdings ETF 별 최신 파티션 합집합의 **구성종목** ∪ targets
# (가격과 같은 축, ALPHA-477 — 합집합 규칙은 ALPHA-590). KRX 단축코드는 list 행의 stock_code 와 항등이라 심볼맵 없이 수집되고,
# symbol_map 은 예외 오버라이드 축. ETF 자기 티커는 출처와 무관하게 뺀다 — DART 신고자가 아니다.
# ⚠️ 유니버스는 **질의 축이 아니라 필터**다. corp_code 는 list.json 의 선택 파라미터이고,
# 종목별로 질의하면 콜 수가 유니버스에 비례해(311 종 ⇒ ~311초) 잦은 실행이 불가능하다. 창
# 전체를 훑으면 페이지 수에만 비례한다(5거래일 3,267행 = 33 콜, 실측 2026-08-03). 그래서
# 수집 경로에는 corpCode.xml 해소가 없다 — 매 런 상수로 걸리며 data_status 를 INCOMPLETE 에
# 묶던 kind=unmapped 실패도 함께 사라졌다. corpCode.xml 은 enrich-corp-code 스텝만 쓴다.
# ⚠️ 창은 30일씩 잘라 순회한다 — corp_code 없는 질의는 **검색기간 3개월** 제한을 받는다
# (4개월 창은 status=100 거절, 실측). --from 만 주면 끝일을 KST 오늘로 확정해 자르고, 실제
# 수집한 창은 collection_log 의 window_from/window_to 에 남는다(인자가 아니라 실제 값).
# ⚠️ 본문(ZIP)은 **틱 멱등**이다(ALPHA-720) — 같은 수집일(UTC ±1일)에 이미 받아 둔 rcept_no 는
# 다시 내려받지 않고 기존 객체를 가리킨다(`documents_reused` 로 계상). 같은 날 두 번 돌리면
# 2회차는 `documents_saved=0` 이고 메타(`records_saved`)는 1회차와 같다 — 메타는 매 실행이
# 창 전체 관측을 남기는 것이 완전성 근거라 접지 않는다. 2일 밖 창의 백필은 재다운로드한다.
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
# etf_map, 현재 KR 39종 — 국내 반도체 29종(488210 상장폐지 제외, ALPHA-1114) + KODEX 200 + 섹터 2종 + 은행 + 테마 6종,
# ALPHA-454·624·927·936·1171). 날짜창 미지정이면 그날(trdDd), 과거 복구는 같은 거래일을
# --from/--to 양쪽에 지정해 그날 PDF 전량을 append한다. KRX PDF는 한 날짜 snapshot이라
# 다일 범위와 한쪽만 지정한 창은 거부한다. 해외기초 ETF 는 비중·금액이 대시(-)로 와도 무변형 보존
# (현 유니버스엔 없다 — 경로만 유지). ⚠️ 계정 파이프라인 전용(사람 동시 로그인 시 CD011).
# --deadline-sec N: 벽시계 상한(ALPHA-581) — 벤더 열화로 상한에 닿으면 받은 것은 저장하고
# 미시도 ETF 를 failed_etfs 로 기록하며 조기 마감(status=partial). 판정은 ETF 사이에서만
# 하므로 진행 중인 1콜만큼은 넘길 수 있다(SFN TimeoutSeconds 의 SIGKILL 대신 택한 설계).
# 미지정=무제한(기존 동작). SFN 배선은 krx_etf_deadline_sec 변수(statemachine.tf).
DATA_PIPELINE_KRX_ETF__SOURCE__MBR_ID=... DATA_PIPELINE_KRX_ETF__SOURCE__PW=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-etf --source krx
# 단일 거래일 백필 예:
#   ... run ingest-raw-etf --source krx --from 2026-09-07 --to 2026-09-07
# 명시 백필은 대상 연도가 포함된 OPS_KR_HOLIDAYS 설정이 있어야 한다. 거래일을 추측하지 않는다.

# 국내 ETF NAV 원본저장(Step1) — KIS ETF NAV비교추이(일), tr_id FHPST02440200(ALPHA-380).
# KRX getJsonData 는 무로그인·세션 모두 LOGOUT 이라(2026-07-20 실측) 가격에서 검증된 KIS 를
# 쓴다. 수집 유니버스는 별도 맵을 두지 않고 krx_etf.source.etf_map(KR 39종)을 그대로 공유한다
# — 구성종목과 NAV 가 다른 목록을 보면 안 되기 때문. KIS 는 ISIN 이 아니라 6자리 단축코드로
# 질의하며, 신규 상장분은 코드에 문자가 섞인다(0093A0 등 39종 중 9종 — 숫자로만 거르면 샌다).
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
# trade_date) 로 멱등 병합 적재한다(같은 벤더는 KR=최근 거래일 수집분과 OHLC 가 같은 마감 후
# 수집분 중 가장 이른 것·비KR=최신 fetched_at 우선, 벤더 교차 충돌 fail-loud). OPS_KR_HOLIDAYS 가
# 휴장일 판정 근거다. --input-run-id 로 그 수집 런의 raw 만 읽어 적재한다(SFN 이 도는 경로, ALPHA-389).
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
# rcept_no 로 멱등 병합 적재한다(같은 rcept_no 최신 fetched_at 우선). --input-run-id 는 배치
# 수집이 남긴 completed raw manifest를 직접 GET해 그 exact key만 읽는다(SFN 경로). manifest가
# 없거나 불완전·손상됐으면 과거 raw로 넓히지 않고 exit 1, 미지정은 전체 백필이다. 파서는 팀원(정준영)
# 검증 프로토타입 이식 — graph 투영·theme 링킹은 범위 밖(analysis-engine 소관).
# ⚠️ 입력 선택 축이 셋이다(CLI 는 앞 둘만 노출): 전체 스캔 / `--input-run-id` 스코프 /
# **키 직접 전달**(`run(..., raw_keys=[...])` — 1분 레인 전용, ALPHA-875). 전체 백필만 `raw/` 를
# LIST한다. 배치는 manifest, Worker는 방금 쓴 키를 그대로 넘겨 LIST를 0으로 만든다. 직접 넘긴
# 키는 **걸러지지 않는다** — 규약 밖 키는 `raw_read_error` + exit 1
# 로 크게 남는다(미리 걸러내면 전건 탈락이 exit 0·0행으로 조용히 성공처럼 보인다).
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

# 두 공시 정제는 run별 canonical manifest를 각각 남긴다. supply_contract_fact는 rcept_no,
# business_segment_fact는 (rcept_no, segment_ordinal)을 winner_ids로 기록하며, 파티션의 직접
# part-00000.parquet 키와 SHA-256도 함께 고정한다. canonical·quality log가 모두 성공한 뒤에만
# canonical_written=true가 된다. 행 격리는 성공 winner를 확정한 exit 2(하류 처리 뒤 실행은
# INCOMPLETE/FAILED), 저장·무결성 실패는 incomplete manifest를 남기는 exit 1(해당 호출의 하류
# 차단)이다. 단 이 두 CLI 는 exit 2 의 실패가 **전부 확정 거부**(다시 읽어도 같은 거부 —
# `quality.disclosure.CONFIRMED_REJECT_REASONS`)이고 문서 수가 상한 이하면 exit 0 으로 닫는다
# (ALPHA-1163, 상한 `DATA_PIPELINE_DART_DISCLOSURE__MAX_CONFIRMED_REJECTS_PER_RUN` 기본 3). 본문
# 미도착·모르는 사유가 섞였거나 상한을 넘으면 그대로 exit 2 다. 0 으로 닫혀도 거부는 quality log
# `failures`·원장 failed_records(INCOMPLETE)·문서별 경고 줄에 남고, 그 줄이 쌓이면(5일 안에 3번의
# 실행) `<name>-disclosure-confirmed-reject-piling` 알람이 운다. 1분 레인은 함수를 직접 불러
# 이 판정을 타지 않는다(창은 INCOMPLETE). 정상 LoadDisclosure는 completed dual manifest의 direct key winner를
# disclosure_load_pending에 먼저 commit하고 pending만 typed 적재한다. issuer 미해소·일시 실패는
# 원장에 남아 다음 정상 실행이 재시도한다. 명시 복구(--all 또는 --from/--to)만 shared canonical을
# pending에 bootstrap하며, --pending-only는 canonical을 읽지 않고 잔여만 회수한다.
# 정상 0건도 canonical_written=true·빈 canonical_partitions로 producer 미실행과 구분한다.

# ETF 구성종목 정제(Step2) — raw etf_holdings(FMP US·KRX KR) → 공통 구성종목 fact 정규화 + 게이트.
# 벤더는 raw 키의 source= 로 판별한다(fmp=US·krx=KR, 수집 날짜창 없음). 정체성(market·etf_id·
# 구성종목·as_of_date)은 blocking, 비중·주식수·평가금액은 참고필드(대시(-)·결측=null, 범위 이상만
# 경고). 통과 행은 canonical/holdings/etf_holdings 에 (market,etf_id,constituent_ticker,as_of_date)
# 로 멱등 병합(같은 키 최신 fetched_at 우선). market-스코프 파티션이라 벤더 disjoint(교차충돌 없음).
# --input-run-id 로 그 수집 런의 raw 만 읽어 적재(SFN 경로). 미지정=전체 백필. 둘 다 멱등.
uv run --package data-pipeline python -m data_pipeline.run normalize-etf
#   특정 런만: ... run normalize-etf --input-run-id 20260701T000000Z

# ETF NAV 정제(Step2) — raw etf_nav(KIS)에서 거래일별 최신 NAV를 canonical로 병합한다.
# 정상 SFN은 --input-run-id로 현재 수집 런만 읽는다. 성공 winner는 run-scoped parquet의
# direct key·SHA-256과 함께 completed manifest에 고정되어 다음 normalize와 재시도에도 불변이다.
# 행 탈락·동시각 NAV 충돌은 성공 winner를 보존한 exit 2, 저장·무결성 실패는 exit 1이다.
uv run --package data-pipeline python -m data_pipeline.run normalize-etf-nav \
  --input-run-id 20260701T000000Z

# 뉴스 이벤트 태깅(Step3, 피처) — canonical 뉴스(language=ko)를 LLM 으로 태깅해
# feature/news/assertions 에 article_id 멱등 병합. ko 만 태깅한다(프롬프트가 한국 금융 뉴스
# 전용 — 영어 기사에 씌우면 품질이 조용히 무너진다).
#
# **이미 태깅된 기사는 건너뛴다** — LLM 이 비싸서만이 아니라, 다시 돌리면 값이 흔들려 PIT
# 재현이 깨지기 때문이다. tagger_version·ontology_version 이 바뀔 때만 재태깅한다. 단
# llm_error(호출 자체 실패)는 판정이 아니라서 다음 런이 재시도한다.
#
# 정상 실행은 NormalizeNews manifest의 직접 parquet와 현재 article_id만 읽는다. --from/--to는
# 명시적 과거 복구, --all은 명시적 전체 복구다. --limit은 이번 런의 새 LLM 호출 수 상한이다.
LLM_API_KEY=... uv run --package data-pipeline python -m data_pipeline.run tag-news --input-run-id 20260701T000000Z --limit 50
#   기간 복구: ... run tag-news --from 2026-07-01 --to 2026-07-08
#   전체 복구: ... run tag-news --all

# 종목 마스터 적재(Step4, RDB) — 세 KR latest-good pointer가 직접 지목한 불변 snapshot 중
# **유니버스 뿌리(`krx_etf.source.etf_map`) 안 ETF 만** 읽어(뿌리 밖 구성종목을 주워 담으면
# 수집하지도 분석하지도 않는 회사가 마스터에 선다 — KRX 상장 전종목 축은 이 필터와 무관)
# entity/actor/company_profile/instrument/equity_profile 을 만든다. 이 저장소가 Cloud Event
# Store 48테이블에 쓰는 첫 경로다.
#
# 멱등: 자연키 (market_code, ticker) 로 찾고 없을 때만 새 ULID 를 발번한다(ADR-0027) — 재실행이
# ID 를 바꾸면 그 ID 를 참조하던 FK 가 전부 끊긴다. 현금·옵션은 자산 유형으로 먼저 분류해
# `skipped_unsupported_asset`으로 계측하고 instrument 후보에서 제외한다.
#
# DB 설정은 DATA_PIPELINE_DB__* (스토리지와 같은 인프라 네임스페이스). 비밀번호는 env 주입만.
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-instruments --latest-good
# 명시적 재해 복구만 shared canonical 전체를 읽는다: ... run load-instruments --all
# 입력 범위를 생략한 전량 스캔을 막기 위해 --latest-good 또는 --all 중 정확히 하나가 필수다.

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

# 대상 ETF 는 holdings ∩ **유니버스 뿌리**(`krx_etf.source.etf_map`) ∩ instrument 마스터다 —
# 뿌리 밖(폐지분·참조 계열)을 안 빼면 "마스터 시드 누락"과 "애초에 대상 아님"이
# skipped_unknown_etf 한 카운터로 뭉개져 진짜 결손을 못 본다.
# 가격변동 트리거 적재(RDB, ALPHA-411) — canonical holdings 가중치 × 구성종목 일봉 수익률의
# coverage 정규화 proxy(분석엔진 L0 산식 정본)가 absolute gate(abs_threshold=3%)를 넘는
# 거래일만 price_movement_trigger 로. 정상 --input-run-id 경로의 holdings 는 거래일 이하 최신
# 정상 DB 스냅샷만 쓰며, 명시 복구 경로는 없을 때 가장 이른 미래 스냅샷으로 폴백한다
# (ALPHA-418 — 사용 횟수·as_of 는 quality_log 로 드러남).
# 날짜 선택과 행 규칙(비중 결손·음수 제외)은 엔진과 같지만 **결손 과반 파티션 배제는
# 엔진에만** 있다(ALPHA-951) — 그런 파티션에선 트리거는 남은 실값으로 서고 설명은 이전
# 스냅샷으로 서서 둘이 갈린다.
# 게이트 미통과 일자는 행이 없는 게 정상이고 그 수는
# data_quality_logs 로 남는다. 구정책 행은 observation 참조가 없으면 자동 교체된다.
# 정상 manifest 경로는 최신 KR 거래일을 operational_trade_date로 둔다. 그 날짜의 holdings·가격
# 결손만 current_missing_*와 ops.failed_records·exit 2에 반영한다. 더 과거 결손은
# historical_missing_*와 failures.scope=historical_reconciliation_debt로 계속 남기되 당일 원장
# 상태를 INCOMPLETE로 만들지 않는다. 가격이 있어도 proxy를 계산할 수 없는 holdings는
# current/historical_unavailable_proxies와 proxy_unavailable 상세로 같은 범위를 구분한다(ALPHA-1062).
# 판정에 쓴 가격 coverage 는 두 곳에 나뉘어 남는다(ALPHA-452 — 1% 비중 종목 하나로 판정된
# 트리거를 사후에 구분하기 위함): 아직 트리거가 없는 (ETF,거래일) 셀은 quality_log
# (coverage_by_etf_date·coverage_min), 트리거가 난 셀은 그 행의 detection_reason 끝
# |coverage=… 다. 멱등 skip 때문에 갈리므로 분포를 볼 땐 둘을 합쳐야 한다.
# 하한으로 막지는 않는다(ALPHA-453).
# Scheduler는 --input-run-id로 NormalizePrice manifest만 읽는다. --from/--to와 --all은
# canonical 복구 경로이며 (etf,date) 멱등 skip을 유지한다.
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-price-triggers \
    --input-run-id <normalize-price-run-id>

# ETF NAV 적재(RDB) — 정상 경로는 normalize-etf-nav의 completed manifest가 지목한 direct
# parquet와 winner만 읽어 etf_nav_daily에 적재한다. key·SHA-256·파티션 정체성이 어긋나면
# 과거 canonical 전체로 넓히지 않고 실패한다. 같은 관측시각의 다른 run은 기존 data_version을
# 덮지 않으며, 행별 DB 실패는 savepoint로 격리해 다른 winner를 commit하고 exit 2로 남긴다.
# 날짜창과 --all은 명시 복구 전용이며 정상 Scheduler에서는 --input-run-id만 사용한다.
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-etf-nav \
    --input-run-id <normalize-run-id>
# 기간 복구: ... run load-etf-nav --from 2026-07-14 --to 2026-07-17
# 전체 복구: ... run load-etf-nav --all

# 문서 마스터 적재(RDB, ALPHA-374) — canonical 뉴스(ko·en)를 document(document_type='NEWS')로.
# document_assertion.document_id FK 의 선행. 멱등: 자연키 uq_document_source(source_vendor,
# article_id)로 있으면 skip, 없을 때만 발번한다. ID 는 그 자연키에서 **결정적으로** 파생하는
# doc_<해시>(db.stable_domain_id, ALPHA-456) — assemble-events 가 같은 값을 계산해야 하고,
# 이 ID 가 assertion_id·source_event_id 의 재료라 랜덤이면 계보 전체가 랜덤을 상속한다.
# ADR-0027 의 ULID 형식과 달라 시간 정렬은 안 된다(그 축은 available_at). ⚠️ 이 계약은 **소급되지
# 않는다** — ALPHA-456 이전에 적재된 행(dev 6,674건)은 랜덤 ULID id 를 갖고 있어 계산값과 갈린다.
# 그래서 이 문서를 참조하는 행은 계산값이 아니라 **자연키로 되읽은 id** 에 붙여야 한다(ALPHA-628).
# 이 스텝이 함께 채우는 news_document.lead_text(분석엔진 프롬프트의 스니펫 축)·publisher
# (언론사, ALPHA-695)가 그 규칙을 쓴다.
# ⚠️ lead_text 는 **무조건 덮지 않는다**(ALPHA-696) — 이 표엔 1분 뉴스 레인
# (PgNewsCanonicalWriter)도 쓰기 때문이다. news_document.lead_observed_at 이 미주장(NULL)
# 이거나 이 런의 canonical fetched_at 이 그보다 앞서지 않을 때만 이긴다(`<=` — 동시각은
# 배치가 이긴다). fetched_at 이 결손이면
# 신선도를 주장하지 않고(published_at 폴백 금지) 그 노출을 로그의 lead_unclaimed_freshness
# 로 센다(결손엔 빈 문자열도 포함 — 분모는 같은 로그의 lead_attempted, ALPHA-848).
# publisher 는 별도 축이라 이 가드가 없다.
# 정상 SFN은 NormalizeNews manifest의 직접 parquet와 현재 article_id만 읽는다(ALPHA-1031).
# 아래는 같은 범위의 수동 재실행. 일부 복구는 --from/--to, 전체 복구는 명시적 --all을 쓴다.
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-documents \
    --input-run-id <normalize-run-id>

# 공시 적재(RDB, ALPHA-476) — canonical 공시(supply_contract_fact·business_segment_fact)를
# document(document_type='DISCLOSURE')·disclosure_document·disclosure_fact·타입별 child 로.
# 설명 엔진이 explanation_run_disclosure_fact 로 직접 소비하는 fact 경로다(threading 미경유).
# issuer 는 corp_code 를 company_profile.dart_corp_code 로 해소, 미해소(마스터 미시드)면
# FK RESTRICT 회피 위해 skip+계측(커버리지 9→309 는 ALPHA-491). DB CHECK 는 파이썬 선검증해
# 위반 fact 만 뺀다(한 건이 배치 롤백 안 되게). 멱등: document 자연키·fact_id=결정적 파생
# ON CONFLICT. 명시 복구는 --all(전체) 또는 --from/--to(report_date inclusive)이며 canonical을
# pending에 먼저 고정한다. --pending-only는 pending 잔여만 회수한다. 범위 없는 수동 호출은
# 거부한다(암묵 풀스캔 방지).
# 창 인자가 하나 더 있다(ALPHA-721): --window-days N 은 오늘−N일 창을 앱이 계산해 넘긴다.
# ASL 이 날짜 산술을 못 해 --from/--to 를 만들 수 없어서다 — 721·724 시절 다슬롯 공시 SFN
# 을 위한 흔적이다(875 의 1분 워커는 CLI 가 아니라 스텝 함수에 날짜창을 직접 넘겼고, 987
# 마감 보충 배치 경로(19:30 SFN)는 --input-run-id로 종전 동작을 유지한다). 명시 --from/--to가
# input-run-id 없이 오면 bootstrap 복구이고, --all도 같은 전체 bootstrap이다.
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-disclosure --all
# 실패 잔여만 재시도: ... run load-disclosure --pending-only

# assertion 적재(RDB, ALPHA-375·376) — feature 뉴스 assertion(ko)을 document_assertion·
# assertion_argument 로. **해소 축은 역할이 정한다**(ALPHA-831) — 온톨로지 identity 표를
# 읽어 셋으로 갈린다: NONE=instrument 우선(미해소인 PARTNER·PARTNER_2·INVESTOR만
# 선언된 기관 명부 폴백, 충돌/ISSUER는 제외) / REGISTRY=시드된 기관 명부 조회
# (못 찾아도 채번 안 함; AUTHORITY는 중앙은행 포함) / MINT=멘션에서 결정적 채번. 미해소·충돌은 quality log 에
# 사유별 수치로 남긴다(해소율 실측).
# ⚠️ **쓰기 표면이 넷이다**: 채번 경로가 entity(CONCEPT)·concept 마스터 행을 함께 만든다
# (FK 순서로 argument 보다 먼저). 채번 산식은 entity_resolution.mint_concept **하나**이고
# assemble-events 도 그걸 부른다 — 갈리면 같은 개념에 ID 가 둘 생긴다.
# 해소율 분모는 **실체 역할 argument 만**이다(ALPHA-802) — 실체를 가리키지 않는
# non_entity(TIME·VALUE·TEXT)를 미해소로 세면 분모가 부풀어 마스터 확대의 효과를 못 잰다.
# 분자는 **붙은 것 전부**다(resolved+registry_hit+minted) — 07-31 이전 값과 정의가 다르다.
# non_entity 는 이제 적재도 안 한다(ALPHA-831 — 예전엔 분모에서만 뺐다).
# 역할 종별 분포·어휘 밖 역할 이름도 같은 로그에 남는다. 정상 SFN은 TagNews feature
# manifest의 직접 part만 GET하고 현재 article_id만 논리 처리한다(ALPHA-1033). 누적 part의
# 과거 행은 `physical_rows_read`, manifest 범위는 `logical_rows_read`로 구분한다. 아래는 같은
# 범위의 수동 재실행이며 일부 복구는 --from/--to, 전체 복구는 명시적 --all을 쓴다.
# 멱등: uq_document_assertion_natural(document_id, event_type, predicate) ON CONFLICT.
# 전무 해소 주장은 넣지 않는다. modality_code 는 어휘 확정 전까지 비운다(ALPHA-361).
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-assertions \
    --input-run-id <tag-news-run-id>

# 이벤트 조립(RDB+LLM, ALPHA-412·ALPHA-545) — canonical 뉴스 제목을 v4 2콜(게이트/타입판별
# → 타입별 추출)로 정규화해 source_event 계보·참여자(event_argument)·측정값(event_measure)·
# event_thread 를 만든다(결정적 ID 산식 동일, stage 는 lifecycle 메뉴 밖이면 NULL). LLM 은
# tag-news 와 같은 LLM_* env. 창 미지정 = 오늘(KST) 하루(LLM 비용이 기사 수 비례), 과거는 창으로 백필.
# 뉴스 SFN 은 --window-days 1 로 [어제,오늘] 겹침(ALPHA-592) — day-close 가 00:10 이라(ALPHA-905)
# assemble 은 **언제나** 다음 날짜에 돈다. 겹침이 없으면 닫으려던 어제를 통째로 못 읽고
# read=0 으로 성공한다. 멱등이라 겹침 비용은 스캔뿐.
LLM_API_KEY=... DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run assemble-events

# 분석 v2 원천 관측(ALPHA-1130) — 매크로 5계열·DART 재무 지표·KIS 지수업종. 계약 정본은
# docs/design/etf-data-storage-plan.md §10. 세 데이터셋 모두 수집(raw + raw manifest) →
# 정제(--input-run-id = 수집 run, 실행별 artifact + canonical 현재 상태 + manifest) →
# 적재(--input-run-id = 정제 run 또는 --all = 소비 마커 없는 완료 manifest 전부)다.
# 매크로 창 미지정 = 계열별 소급일 ~ 어제(KST). 백필은 --from/--to(관측일, --to ≤ 어제). 키는 env 로(미국채 10y 는 FRED `DGS10` — FMP 는 쓰지 않는다, USD/KRW·국고채는 ECOS.
# raw·canonical manifest 의 code_version 은 GIT_SHA env 에서 온다, 없으면 unknown):
DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__FRED_API_KEY=... \
DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__ECOS_API_KEY=... \
DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__KOSIS_API_KEY=... \
DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__EIA_API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-macro --run-id run_m1 [--series usd_krw,us_10y_yield]
uv run --package data-pipeline python -m data_pipeline.run normalize-macro --run-id run_m1n --input-run-id run_m1
uv run --package data-pipeline python -m data_pipeline.run load-macro --run-id run_m1l --input-run-id run_m1n
# 재무: 창 미지정 = 접수일 오늘−14 ~ 오늘. 대상 종목은 [krx_etf.source.etf_map] ETF 전부의 canonical 구성종목
# 스냅샷에서 기간별로 파생한다([source_observations].etf_ids 를 적으면 그 ETF 로만 좁힌다). DART 키는 기존 재무 키를 쓴다.
DATA_PIPELINE_DART_FINANCIAL__SOURCE__API_KEY=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-financial-metric --run-id run_f1 --from 2025-07-01 --to 2025-12-31
# 업종: KIS 공개 마스터 ZIP(키 없음). 현재값만 준다 — --from/--to 를 거부하고, 비거래일엔 받지 않는다.
uv run --package data-pipeline python -m data_pipeline.run ingest-raw-sector --run-id run_s1
# 저장 뒤 적재 전에 멈춘 실행 회수
uv run --package data-pipeline python -m data_pipeline.run load-financial-metric --all
```

> **사건 참여자와 이력 보존** — 사건의 Equity 참여자는 저장 시 발행 Actor로 변환한다.
> 문서의 종목 매칭과 source_event ID는 유지한다. 기존 데이터는
> `V202610030010__event_participants_reference_issuers.sql`로 참여 대상과 thread_key만
> 바꾸며, 참여·사건·thread ID와 이력 참조는 보존한다. 아래 삭제 절차는 이 전환에 적용하지 않는다.
> threading은 기존 키의 thread ID를 조회해 재사용하고, 처음 보는 키에만 ID를 생성한다.
> 전환 시 모든 사건 writer와 재가동 스케줄을 멈추고 백업·충돌 검사를 마친 뒤,
> 호환 코드와 마이그레이션을 모두 적용하고 쓰기를 재개한다. 두 배포 워크플로의 실행 순서는
> 자동으로 보장되지 않는다. 전환 후 Equity 참여자를 쓰는 구버전 writer는 DB가 거부한다.
> 이번 전환의 분석 조회 지원 범위는 v2다. 종목 ID를 사건 참여 ID와 직접 비교하는
> 구형 v1 조회는 호환되지 않으므로 배포 후 검증·재가동 대상으로 사용하지 않는다.

> **thread 전체 재계산(ALPHA-457 등 이력을 재생성하기로 한 경우에만)** — 새 thread의
> ID는 키로 생성하지만, 이미 존재하는 thread는 키를 변경해도 ID를 보존할 수 있다.
> 단순 재실행은
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
> 포트포워딩으로 터널을 뚫는다. 비밀번호는 RDS 관리형 시크릿(`rds!db-…`)에서 꺼내 env 로 넣는다.
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
> ⚠️ **그 날짜가 어느 달력인지는 벤더가 정한다**(ALPHA-883, `run.window_calendar_tz`) — 우리가
> 만든 날짜 문자열이 그대로 벤더 질의에 실리기 때문이다. 기준은 벤더 국적이 아니라 **그
> 데이터가 어느 시장의 날짜인가**다: BigKinds·DART·KIS 는 KST, **yahoo 도 KST**(미국 서비스지만
> `index_map` 이 `^KS11`·`^KQ11` 뿐이다), **FMP 만 미국 달력(UTC)**. 프로세스 시계(UTC)로 뽑으면
> KST 벤더는 09:00 KST 이전에 도는 슬롯에서 하루가 밀린다 — ALPHA-883 당시엔 모든 슬롯이 09:00
> 이후라 안 드러났고(공시 09:00 슬롯이 그 경계에 정확히 서 있었다), **ALPHA-893 의 뉴스 08:10
> 슬롯이 그 경계를 실제로 넘은 첫 슬롯이다.** 이제 이 표는 잠복 대비가 아니라 매일 도는 런을
> 지킨다.
> 창을 쓰는 스텝이 늘면 달력을 표에 **선언해야** 한다(미선언은 fail-loud) — 기본값을 두면 새
> 스텝이 조용히 한쪽으로 떨어지고 그 창은 하루가 밀린 채 성공한다.

> uv가 없는 환경이면 표준 venv로 같은 일을 한다(`src/apps/cloud/data-pipeline`에서, pip ≥ 25.1):
> ```bash
> python3 -m venv .venv
> .venv/bin/pip install -e . --group dev   # dev 그룹(pytest)은 PEP 735 [dependency-groups]
> .venv/bin/pytest
> ```
