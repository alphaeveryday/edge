# data-pipeline — 구성 요소와 도입 경위

> 모듈 [README](../README.md) 의 상세 문서다. 레인·단계별 동작과 도입 경위가 함께 있다.


**원본저장(Step1)** 소스는 FMP(미국 — 지금은 `us_fmp_enabled=false` 로 꺼져 있다) 뉴스·가격(OHLCV 일봉)·
재무제표(손익·재무상태·현금흐름)·**ETF 구성종목(holdings)**, BigKinds 국내 뉴스,
KIS(한국투자, 국내) 일봉, **KRX 국내 ETF 구성종목**(로그인 게이트 PDF), OpenDART 국내 재무·**공시(disclosure filing)**까지다. 공시는 재무제표(fnlttSinglAcnt)와
**다른 API**(공시목록 list.json + 공시서류 원본 document.xml)로 메타 + 본문 raw 를 적재한다.
**가격 정제(Step2)** 는 정규화(FMP·KIS 이형 → 표준 OHLCV) + 정합성 게이트 + quality_log +
통과 행의 `canonical/market_data/price_daily` 멱등 병합 적재까지 완료했다(`normalize-price`,
ALPHA-133). **뉴스 정제(Step2)** 는 정규화(FMP·BigKinds 이형 → 표준 메타행) + 필수필드·발행일
게이트 + quality_log + 통과 행의 `canonical/news/news_articles` article_id 멱등 병합 적재까지
완료했다(`normalize-news`, ALPHA-131·132). **공시 정제(Step2)** 는 raw 공시 본문(euc-kr HTML)을
파싱해 공통 **공급계약 fact** 로 정규화 + 게이트 + quality_log + 통과 fact 의
`canonical/disclosures/supply_contract_fact` rcept_no 멱등 병합 적재까지 완료했다
(`normalize-disclosure`, ALPHA-345). **사업부문(segment) 정제(Step2)** 는 사업보고서 본문 표를
파싱해 사업부문별 매출 fact 로 정규화 + 게이트 + `canonical/disclosures/business_segment_fact`
(rcept_no+segment_ordinal 멱등 병합)까지 완료했다(`normalize-disclosure-segment`, ALPHA-346).
**ETF 구성종목 정제(Step2)** 는 정규화(FMP US·KRX KR 이형 → 공통 구성종목 fact) + 게이트
(정체성 blocking·비중/주식수/평가금액은 참고필드로 범위 경고) + quality_log + 통과 행의
`canonical/holdings/etf_holdings` (market,etf_id,constituent,as_of_date) 멱등 병합 적재까지
완료했다(`normalize-etf`, ALPHA-342·343). KRX 해외기초 ETF 의 대시(-) 비중은 null 로 통과시켜
구성종목을 보존한다. **뉴스 이벤트 태깅(Step3, 피처)** 은 기사(제목+리드)에서 문서가 주장하는
사건을 온톨로지 라벨로 뽑아(`tagging/`, ALPHA-138) `feature/news/assertions` 에 article_id 멱등
병합 적재까지 완료했다(`tag-news`, ALPHA-365) — `entity_id` 는 NULL 로 두고 `text` 만 남긴다
(엔티티 해소·assertion RDB 적재는 후속, ALPHA-190).
**종목 마스터 적재(Step4, RDB)** 는 canonical **두 입력**(ETF 구성종목 + KRX 상장 전종목
`instrument_profile`)을 Cloud Event Store 의
`entity`/`actor`/`company_profile`/`instrument`/`equity_profile` 로 멱등 적재한다
(`load-instruments`, ALPHA-372·830) — **이 저장소가 Cloud Event Store 48테이블에 쓰는 첫 경로**다.
전종목 축에서는 **보통주만** 세운다(우선주는 발행사 연결이 필요해 별건) — 실측 2,872종 중
113종이 우선주 계열이다. 두 입력이 같은 티커를 다른 시장으로 말하면 만들지 않고 기록만
한다(자연키가 `(market_code, ticker)` 라 만들면 같은 종목이 두 번 선다).
⚠️ 전종목 canonical 은 **수동 수집**이라([deploy-schedule.md](deploy-schedule.md) "수집 — 상태머신 밖") 낡을 수 있다 — 낡아서 전 행이
못 쓰이면 사유를 로그에 남기되 **비0으로 끝내지는 않는다**. 이 스텝의 exit code 는 뒤따르는
FeatureParallel 전체를 좌우해서, 선택 입력의 낡음이 다섯 로더를 세우면 안 되기 때문이다.
**가격변동 트리거 적재(RDB)** 는 canonical holdings 가중치와 구성종목 일봉으로 **가중 proxy
수익률**(coverage 정규화 — 분석엔진 L0 와 같은 산식, 정본)을 계산해 absolute gate(3%,
`[price_triggers]`) 통과 거래일만 `price_movement_trigger` 로 멱등 적재한다
(`load-price-triggers`, ALPHA-406→411) — 이 테이블의 **단일 writer** 이자 분석 SFN RDS
영속 전제 체인의 첫 고리다.
**1분 가격·뉴스 파이프라인(장중)** 은 dev 에서 운영 중이다(세션 스케줄 ENABLED, 2026-08-03 #489) — 구성은 공통 계약·fixture·결정적
fake collector·virtual clock 기반층(`minute/`, ALPHA-660)과 cloud 원장 스키마 6테이블
(session·window·news item/job·price job·outbox, ALPHA-661 — 상태 어휘는
`minute/states.py` 가 SQL CHECK 와 기계 동기화)과 session/window repository
(계획·claim·lease·fencing ALPHA-662 + watermark·lane·drain ALPHA-663)과 job/outbox
repository(결정적 event ID·원자 enqueue·PG=retry 권위, ALPHA-664), artifact/manifest
경계(결정적·불변 key·조건부 put_immutable, ALPHA-665·1060), fenced commit transaction(window·
job·outbox 원자화 + orphan 검출, ALPHA-666 — 가격 분봉 canonical 은 **S3 artifact
정본**이라 트랜잭션 밖이고 DB canonical 은 뉴스만: ALPHA-701), Price Worker loop(fence·2-lane·
세대 예측·drain·SIGTERM 인계, ALPHA-667 — collector 주입식)와 **토스 분봉 adapter**
(ALPHA-682 — 2026-08-01 실호출 실측 형상 기반: `1m` 캔들, ts 는 **구간의 끝**이라
`window_start = ts − 1분`, 거래 없어도 캔들이 오므로 no_trade 는 "행 있고 거래량 0"·
행 자체가 없어야 missing. 녹화 fixture `tests/fixtures/toss/`)와 **KIS 분봉 adapter**
(ALPHA-735 — 1분 레인의 **기본 벤더**. `FHKST03010200` 당일 분봉, 종목당 1콜에 30분치,
`stck_cntg_hour` 는 구간의 **시작**(`window_end = 라벨 + 1분`, ALPHA-1127 — 끝으로
읽던 08-04~09-29 는 창 w 에 w+1 분의 **형성 중 봉**이 실렸다. 당일 TR 응답은 "요청
라벨 행 = 형성 중 봉 / 진행 분 = 0 자리표시 / 그 아래 = 확정" 세 층이라 창 시작 라벨을
**다음 분이 끝난 뒤** 읽는다: `models.WINDOW_SETTLE_SEC`). 4분류 판정은 벤더 무관부
`minute/price_collect.py` 하나를 공유하고 각 collector 는 "그 window 의 봉 하나를 어떻게
얻는가"만 갖는다.
⚠️ **TR 이 둘이다**(ALPHA-846): 세션 날짜가 지난 거래일이면 소급 TR `FHKST03010230`
(`KisHistoricalMinuteClient`)로 간다 — 당일 TR 에는 날짜 축이 없어 과거 세션에 물리면
오늘 봉이 오늘 라벨로 돌아와 전 window 가 missing 이 된다. 설정 노브가 아니라 벤더
사실이라 **날짜에서 유도**한다. 소급 TR 응답은 거래일 경계를 넘으므로 `stck_bsop_date` 로
자른다. 소급 TR 은 무거래 분 행을 주지 않는다 — 행이 없는 분은 무거래인지 누락인지
응답만으로 가를 수 없어 **결손(missing)** 으로 남긴다 — 예외 없이 벤더가 준 행만
싣는다(ALPHA-1153). 종가 단일가 접수 구간(15:20~15:29)도 행이 없으면 결손이고, 15:29 창은
15:30 단일가 봉만으로 만든다. 당일 TR 경로(실시간 레인)는 이와 무관하다 — 벤더가 무거래
분도 flat 행으로 준다),
BigKinds adaptive overlap 컨트롤러+source item 관측 원장(anchor frontier·identity
격자 승격, ALPHA-668), News Worker loop(관측 전량 원장 판정→기사별 job, anchor 이중
보존·recovery, poll 원본/판정 기록 보존, ALPHA-669 — feed 주입식, BigKinds 실호출
feed 는 ALPHA-707 `minute/bigkinds_feed.py`), Outbox Relay(destination 별 claim·SQS batch 발행·재시도,
ALPHA-670 — `run relay` 가 이 트랙의 **첫 실행 표면**이다), SQS Consumer 공통 kernel
(long polling→DB 상태 확인→멱등 claim→실행→성공/재시도/격리, visibility+DB lease
heartbeat, **DB 가 정한 시각으로 visibility 조정**, ALPHA-672 — handler 는 7B·7C 가
채운다)과 그 복구 경로(DLQ reconciler `run dlq-reconcile` + **DB-first** redrive
`run redrive`: DEAD→RETRY_WAIT·세대 증가·새 delivery event 를 한 트랜잭션에), **시간대별
기대 유니버스 분기**(ALPHA-684 — 기대 집합은 window 시각이 정한다: 정규장 09:00~15:30 은
전 종목, 그 밖은 `Universe.extended_hours_ids` 가 선언한 시간외 거래 종목만. 세션 계획도
같은 규칙에서 나온다 — 시간외 종목이 있으면 08:00~20:00 = 720 window, 없으면 390.
⚠️ **universe 가 없는 소스 단위 dataset(뉴스·공시)은 `extended_hours` 만이 범위를 정한다**
(ALPHA-875) — 기대 집합이 universe 에서 나오지 않아 "기대가 빈 window" 라는 실패 모드가
없다(window 하나 = "그 분에 소스를 한 번 폴링했다", 소스가 낸 것이 0건이면 VALID_EMPTY).
뉴스와 공시는 모두 09:00~15:30, 390 window다(ALPHA-1072). 가격만
`states.EXTENDED_HOURS_DATASETS`에 남는다. 공시 장중 관측은 `disclosure_minute/dart`
원장과 `disclosure-worker`에서 본다. 장외·지연 공시는 평일 19:30 마감 배치가 회수한다(ALPHA-1073·1074).
⚠️ 상품군 축이 **아니다**: 개별주 001527 도 15:30 이 마지막이라, 클래스는 규칙이 아니라
universe 가 선언한다. ⛔ **2026-08-02 결정: 장외는 제외한다** — 선언을 빈 채로 두면
전 종목 정규장 390 window 이고, 정규장 390분은 실측상 전 종목이 빈틈없이 채워진다.
⚠️ **KIS 가격 창은 `window_end + WINDOW_SETTLE_SEC`(70초) 에 집힌다 — 마감 창(15:29)도
같다**(`models.scheduled_at_for`, ALPHA-1127·1128. 토스 세션은 봉이 창 닫힘과 함께
최종이라 `window_end` 그대로). 종가 단일가(15:30:00 체결)는 KIS 라벨
`153000` 봉인데, 당일 TR 은 그 체결값을 15:30:03~32(랜덤엔드)에 요청 라벨 행에만 잠깐
실었다가 15:31:00 에 0 자리표시로 리셋하고 세션 stop 전까지 확정 층으로 주지 않는다
(09-30 실측). 그래서 **실시간 15:29 창은 접수 구간 봉(vol 0·단일가 전 가격) 그대로**이고,
단일가는 마감 뒤 재수집(소급 TR)이 `kis_minute.fold_closing_auction` 으로 15:29 창에
접어 정본으로 덮는다 — 그 재수집 배선이 ALPHA-1128 의 남은 일이다(소급 TR 은 접수 구간
행을 주지 않아(10-02 원문) 재수집 15:29 창은 단일가 봉만이다, ALPHA-1153). 접수 구간(15:20~
15:29) 창은 체결이 없어 `vol 0` 이 맞다. 옛 규칙(ALPHA-763·773: 열 창을 통째로 15:31 로)은
"벤더가 직전 봉을 거래량째 복제한다"(08-05 실측)를 벤더 결함으로 읽은 것이었다 — 실제는
요청 라벨 행 = 형성 중 봉이라는 응답 형상이었고, 창 끝을 라벨로 묻던 옛 수집기만 그 행을
봤다. 09-14 부터 마감 창 거래량 0 (ALPHA-1128) 은 그 행이 15:31:00 리셋 뒤 0 이 된 것이다.
창을 안 만드는 게 아니라 claim 시각만 미루므로 원장에 구멍이 없고, KIS 는 한 콜이 30분치라
콜 수도 안 는다. window INSERT 는 `DO NOTHING` 이라 **이미 계획된 세션엔 소급되지
않는다** — 당일 적용이 필요하면 그 행의 `scheduled_at` 을 직접 UPDATE 한다. ⚠️ 옛 마감 지연은 마감 봉이
접수 구간 창보다 **먼저**
처리되는 순서 역전을 만든다(realtime lane 이 최신을 먼저 집는다) — 무거래 봉이 더
최신 앵커와 대조돼 허위 발화가 나갈 수 있었다. **ALPHA-776 이 그 발화를 막았다**
(앵커가 이 window 보다 뒤에서 왔으면 발화 안 함 — 판정부 스냅샷과 쓰기 tx 두 곳).
같은 티켓에 남은 것은 무거래 봉 축(거래량 판독 실패 포함)과 정책 identity v3
승격이다),
**뉴스 추출 Consumer handler**(ALPHA-689 — kernel 위에 `tagging/extract` 를 job 단위로
부르는 배선: 기사 정본은 PG `document`+`news_document` 자연키, 결과는 feature 존 불변
artifact 이고 반환값이 그 바이트의 sha256 이다. artifact key 축은
`(job_id, redrive_generation, attempt)` — LLM 출력이 비결정적이라 시도마다 key 가
갈려야 재시도가 자기 자신을 막지 않는다. 그 job 축 키는 원장이 색인이라 배치가 못 보므로,
**같은 결과를 배치가 읽는 날짜축 feature 파티션에도 미러한다**(ALPHA-900) — 미러 없이는
배치가 같은 기사를 다시 유료로 태운다. 미러 실패는 job 을 죽이지 않는다(재시도가 새 attempt·
새 key 라 LLM 을 다시 부른다 — 미러 1건 손실보다 비싸다). 실패 분류의 terminal 은 payload↔원장 기사 축
불일치 하나뿐이고 나머지는 예산이 판정한다), **EOD 세션 QC**(ALPHA-693 — drain 이 끝난
세션의 `DUE` 잔존을 `MISSING` 으로 확정하고 `FINALIZED` 로 닫는다. `run qc-minute-session`.
확정은 **도래한 window 만**이고 계획의 양 끝·연속성이 어긋나면 확정 대신 `FAILED` 다 —
결손은 판정 결과지만 원장이 스스로와 모순이면 판정을 믿을 수 없다), **EOD 5분봉 확정**
(ALPHA-839 — 5분 파생의 생산자는 둘이고 집계는 하나다: 커밋 후크 `maybe_rollup` 이
장중 즉시성을, `run rollup-minute-session` 이 마감 후 1회 확정을 맡는다. 후크만으로는
**지나간 거래일이 영영 안 채워진다** — 발화 조건이 "방금 커밋된 window" 라 그날 마지막
버킷 뒤엔 다음 커밋이 없다. 배치는 계획·커밋을 원장에서 읽고, 커밋이 0건이면 빈 파일을
쓰지 않고 스킵한다 — 원장에 커밋이 없는 것과 그날 봉이 폐기된 것은 다른 사실이라,
빈 파일로 덮으면 다른 writer 가 채운 파티션을 지운다), **뉴스 canonical
writer**(ALPHA-691 — 7B 가 **읽던** PG `document`+`news_document` 를 실제로 **쓰는** 쪽.
commit 트랜잭션의 커서로 `(source_code, article_id)` upsert 하고, 정규화는 배치 정제
`_normalize` 를 재사용한다. ⚠️ 시각 축 규칙이 둘로 갈린다: **내용은 이번 관측 값**으로
쓰고 **`available_at` 은 GREATEST 로 앞으로만** 간다 — 시각으로 내용 쓰기를 막으면 배치가
미래 `published_at` 을 실은 행에서 정정이 유실되고, 시각을 뒤로 밀면 과거 as-of 구간에서
문서가 사라진다. **배치와의 승자 규칙은 ALPHA-696 이 `news_document.lead_observed_at`
으로 정했다** — 이 경로는 쓰기 가드 없이 쓰되 리드 상태가 움직였을 때만 그 시각을 찍고,
배치는 미주장이거나 자기 canonical `fetched_at` 이 그보다 앞서지 않을 때만 덮는다
(절이 `<=` 라 동시각은 배치가 이긴다). 비대칭이
의도이고, 계약 전문은 마이그레이션
`V202608071018__add_news_document_lead_observed_at.sql` 에 있다. ⚠️ 그 시각도
**`GREATEST` 로 앞으로만** 간다(ALPHA-858) — 두 축 다 단조다. 그래서 `lead_observed_at`
은 엄밀히는 *관측 시각의 상한*이고, 마이그레이션의 정의문("지금 저장된 리드 상태를 누가
언제 관측했는가")은 역행 관측이 낀 경우를 모른다. 적용된 마이그레이션은 수정하지 않으므로
그 예외는 여기와 `canonical_news.py` 리드 UPSERT 주석에 있다.
⚠️ **비교축도 falsy 로 접는다**(ALPHA-860) — `NULL ↔ ''` 는 움직임이 아니다. 뿌리는
`normalize_news` 가 공백뿐인 리드를 `None` 으로 접는 것이고(그래서 canonical `lead_text`
는 결코 빈 문자열이 아니다 — 하류가 기대도 되는 불변식이다), 충돌 갈래의 `COALESCE` 는
레이크·PG 에 남은 옛 `''` 행 때문에 있다. 마이그레이션 ②가 "지워지는 것도 움직임"이라고
열거한 것은 이 예외를 모른다),
**세션 계획·drain·재오픈 CLI**(ALPHA-698 — `run plan-minute-session`·
`run drain-minute-session`. 체인의 **가운데가 비어 있었다**: EOD QC 조차 세션 행을 손으로
넣어야 돌았다. 원장이 멱등·CAS 를 갖고 있어 얇은 배선이고, 판정은 여기 두지 않는다.
재실행은 성공이다 — 재계획도 이미 걸린 drain 도 exit 0 이고, 무엇이 새로 생겼는지는
exit code 가 아니라 출력(`created`·`drain_requested`)이 말한다. ⚠️ `--dataset`·
`--source-group` 은 어휘 밖이면 거부한다: 오타 값으로 세션이 서면 그것을 처리하는
Worker 배선이 없어 하루가 통째로 안 돌면서도 원장은 정상으로 보인다. ALPHA-1135 —
`run reopen-minute-session`: FINALIZED 가격 세션을 ACTIVE·창 DUE 로 되돌려 소급 재수집. 사유
필수, 지난 날짜·가격 세션·FINALIZED/FAILED 만, 지목 창 하나라도 없으면 무변경. QC 는 재오픈 뒤
못 받은 창을 MISSING 으로 접지 않고 FAILED 로 세운다), **상주 Price
Worker 엔트리포인트**(ALPHA-706 — `run price-worker`, ECS Service 명령. session 은
결정적 유도라 설정 source 오배선은 세션 부재로 기동 거부되고, destination·자격증명·
lease 조합(lease ≥ (1+budget)×75초, session_lease ≥ heartbeat 주기+최악 tick)은
기동·로드 시점에 검증한다. `WorkerConfig.lease_seconds` 기본이 60→300 으로 오른
이유이기도 하다 — 토스 tick 실측 73초+ 아래면 자기 claim 이 in-flight 중 만료된다.
collector 는 설정 `source` 가 고른다(ALPHA-735 — kis|toss, 미지 소스는 기동 거부).
News Worker 엔트리포인트는 프로덕션 feed 부재로 별도 티켓: ALPHA-707), **가격 트리거
판정 Consumer handler**(ALPHA-708 → **판정식 v2 = ALPHA-745** — kernel 위에 얹는
LLM 0 판정. 기준선은 **전일 종가**(`price_daily` 세션당 1회 조회·캐시)고, 기준선
±`revert_threshold`(1%) 안이면 발화 금지 구간이라 노출 중이던 종목은 회수
(`ExposureReverted`)하고 앵커를 기준선으로 되돌린다. 밖이면 |close/anchor−1| ≥
`abs_threshold`(3%) 에서 발화하고 앵커(`minute_trigger_anchor`) ← 발화가 —
**2h 쿨다운은 폐지**됐고(재발화 축이 시간이 아니라 가격) 멱등 축은
UNIQUE(entity, session, window)+DO NOTHING 이다. 임계를 넘어도 **앵커가 이 window
보다 뒤에서 왔으면 발화하지 않는다**(ALPHA-776 — 늦게 재판정된 과거 창이 미래
가격으로 재는 것이라 무의미하다. 판정부 스냅샷과 쓰기 tx 두 곳에서 보고, 접힌
종목은 판정 로그 `앵커역전 N` 과 결과 `skipped_stale_anchor` 에 남는다). 트리거 행은 `open_price` 에
기준선, `anchor_price` 에 판정 기준가를 남긴다. 전일 종가가 없는 종목만 세션 시가로
폴백한다 — 그때만 `minute_session_open` 원장이 **확정 후 불변**으로 걸린다(첫 window
미커밋=재시도, 커밋됐는데 레코드 없음=MISSING+사유). 트리거 행·앵커·설명 outbox
event 는 한 트랜잭션이다. 판정식·임계의 정본은 분석엔진 소관이고 이 handler 는 확정
규칙의 배선이다), **설명 큐 4번째 destination**(ALPHA-709 — `price-explanation-realtime`
이 Relay 어휘에 등록돼 **4종이 전부 필수**다: 빠진 큐는 그 레인 event 전멸이라
기동 거부. 트리거 사건의 발행 가부는 `destination_accepts` 가 정본이고, DLQ 대사
어휘는 여전히 job 큐 3종이다 — 트리거 DLQ 는 job 테이블이 없어 대사 대상이 아니다.
분석 엔진은 `analyze --trigger-id` 로 분봉 트리거를 단건 소비한다 — 대상 ETF·
trade_date 는 트리거 행이 정본, 계보는 `minute_price_trigger_id` 축)까지다.
AWS 리소스는 terraform 에 정의됐다(ALPHA-711 — SQS 원 큐 4종+DLQ, 상주 서비스 10종
price-worker·relay·price-consumer + news-consumer-realtime·-backfill(ALPHA-713) +
news-worker(ALPHA-717) + disclosure-worker(ALPHA-875·1068 — 한 window 가 체인 전체인
증분 공시 생산자) +
inav-worker(ALPHA-882 — 장중 iNAV 생산자. 소비자가 없어
큐는 안 늘어난다) + sector-index-worker(ALPHA-887 — 업종지수 45종 생산자) +
analysis-consumer(ALPHA-719 — 설명 큐 소비, analysis-engine 이미지):
`infra/terraform/modules/data-pipeline/minute_services.tf`,
desired_count 0 에 lifecycle ignore_changes — desired 를 terraform 밖에서 정하게 두고
apply 가 장중 워커를 내리지 않게 한다. 그 주체는 **9종은 세션 오케스트레이션**,
**analysis-consumer 는 오토스케일링**이다(ALPHA-912, 아래). ⚠️ CD 의 상주 서비스 롤아웃은 repo variable
`MINUTE_SERVICES_DEPLOYED=true` 일 때만 돈다 — 이미지 CD 와 apply 는 순서 보장이
없어, 권한이 서기 전 describe 가 AccessDenied 로 떨어지면 멀쩡한 이미지 배포까지
막힌다. apply 후 그 변수를 켠다). **그 desired_count 를 바꾸는 주체가 ALPHA-712 다**
— `run start-minute-session`·`run stop-minute-session` 을 EventBridge Scheduler 가
부른다(Premarket 07:45 / EOD 16:10 KST — 정규장 마감 뒤 복구 여유 40분,
`aws_scheduler_schedule.minute_session`).
같은 자원이 **업종지수 5분 파생 확정**도 부른다(평일 16:00 KST — `rollup-minute-session
--dataset sector_index_minute`, ALPHA-955). 시각이 다른 이유는 격자가 달라서다:
업종지수 세션은 늘 09:00~15:30 인데 가격은 시간외 종목이 정본에 있으면 20:00 까지
넓어질 수 있어(지금 dev 는 시간외 축이 없어 둘 다 15:30 이지만 축은 승계로 되살아난다),
가격 EOD 확정은 이 시각에 못박을 수 없다(ALPHA-839 소관).
내리는 조건은 **시각이 아니라 원장 상태**다(phase DRAINED → 큐 깊이 0 → outbox NEW 0,
연속 확인). 게이트가 풀리면 설정된 전 레인을 QC한 뒤 scale-down한다. 한 레인의 QC 실패도
나머지 레인 QC와 안전한 scale-down을 막지 않는다. 스케줄러는 RunTask **제출**까지만 보지만,
ECS Task State Change rule이 minute-session task family의 컨테이너 exit≠0을 기존 alarm
SNS topic으로 올린다.
⚠️ **analysis-consumer 는 세션이 스케일하지 않는다**(ALPHA-912 — 컷오버 완료). desired 는
큐 잔여 일감(가시+처리중)을 보는 오토스케일링이 소유하고(`analysis_autoscaling.tf`),
세션이 이 서비스에 대해 하는 일은 **공용 목록에서 이름을 빼는 것뿐**이다 —
`_services()` 가 `MINUTE_SESSION_ANALYSIS_SERVICES` 를 근거로 뺀다(ALPHA-910 이 세운 축.
**축을 가르는 주체는 terraform 이 아니라 코드다**). 그 env 가 비면 **죽는다**: 빼기가 안
돌아 공용 경로가 이 서비스를 다시 스케일하고, 그러면 매일 밤 stop 이 스케일러의 desired 를
덮어 축이 도로 둘이 된다(ALPHA-910 의 컷오버 관대함은 여기서 회수됐다).
⚠️ 그래서 **세션 stop(16:10)에 이 서비스를 내리는 주체가 없다** — 그게 의도다. 게이트는 설명 큐를
안 보므로([ops-ledger.md](ops-ledger.md) 의 게이트 설명) 예전엔 처리 중인 설명이 stop 에 잘렸는데, 스케일러는 처리 중(비가시)까지
세어 그동안 대수를 유지한다. 야간 비용은 잔여 0 에서 0대로 내려가 해결된다.
⚠️ **절단이 통째로 사라진 것은 아니다** — 버스트 중 CD 재배포의 롤링은 여전히 처리 중인
태스크를 자른다(Fargate `stopTimeout` 상한 120초 < 건당 588초). 그건 별개 축이다.
⚠️ 그리고 이제 desired 를 0 으로 내리는 주체가 **오토스케일링 하나뿐**이다 — 세션의
EOD 하드스톱(현 16:10)이 CloudWatch 를 안 보는 유일한 천장이었다.
terraform 공용 목록에 이름이 아직 남아 있으나 코드가 늘 빼내므로 잉여다 — 제거는 후속
정합성 정리(PR C) 소관이고, 남아 있어도 동작은 같다.
⚠️ universe 정본 객체(config/minute/universe.json)는 **`build-minute-universe` 스텝**이
만들고 반영한다(ALPHA-735·953 — canonical KR holdings 와 config
`[minute_universe].sector_etf_ids` 에서 파생. 쓸 자리는 소비자와 같은 `--universe` URI 를
인자로 받는다). 무변경이면 no-op 이고, 교체할 땐 직전 객체를 `.bak-<run_id>` 로 남긴다.
**거래일 07:30 KST 이후엔 스스로 거부한다** — 세션이 이미 그 유니버스로 계획됐을 수 있고,
그러면 원장의 (universe_version, universe_hash) 는 옛 값에 고정된 채 객체만 바뀌어
worker·consumer 가 매 틱 blocked 로 돈다. 이 스텝은 **장전 레인**(ALPHA-963,
`premarket_pipeline.tf`)이 평일 07:00 KST 에 부른다 — `ingest-raw-etf --source krx`
→ `normalize-etf` → `build-minute-universe` 체인이다. 그 레인은 **원장 밖**이라
(같은 CLI 를 시장 레인이 이미 소유해 `by_cli` 가 갈리지 않는다) Reconciler 백스톱이
없고, 대신 알람 셋이 실패 지점을 나눠 본다(SFN 실패·타임아웃·스케줄러 DLQ 도착).
객체 없이 스케일업하면 worker·consumer 는 기동 거부(fail-loud)다.
⚠️ **수집 축과 판정 축은 다르다**(ALPHA-842). `unit_ids`(수집) = 판정 ETF + 구성종목 +
**참조 계열**(`sector_etf_ids`)이고, 트리거 판정은 `etf_ids` 만 받는다 — 층 분해의 섹터
후보처럼 봉만 필요한 계열을 `etf_ids` 에 얹으면 발화 대상·전일 종가 대조 대상이 된다.
**처리량 제약은 벤더 교체로 풀렸다**(ALPHA-735) — 토스는 종목당 1콜 × 363종 ÷ 초당
5회 ≈ 73초라 60초 창을 못 맞췄고, KIS 는 실측 14.8 req/s(기본은 12.5)라 410 unit 이
34초에 든다(참조 계열 48 편입 후 시점의 실측, ALPHA-842). 그 뒤 ALPHA-927 이 091170 을
판정 축으로 옮겼는데 **unit 수는 안 늘었다**(410 → 409 실측 — 참조 계열에서 빠지고
판정 축엔 holdings 착지 전이라 아직 없다). 은행 구성종목은 KODEX 200 경유로 이미
유니버스에 있을 것으로 보이나 **표본 11종 확인이 잰 전부**였다 — 091170 명부가 그때는
레이크에 없어 전수 대조를 못 했다. 확실히 늘어나는 것은
etf_map 축 3작업(KRX PDF·NAV·프로필)의 각 1콜뿐이다.
(그 뒤 착지했다 — 2026-08-11 universe 빌드가 091170 포함 전건 판정으로 성립했고,
빌더는 canonical holdings 에 없는 판정 ETF 를 거부하므로 성립 자체가 착지 증거다.)
그 뒤 ALPHA-936 이 테마 4종을 올리면서 **unit 수가 처음으로 실제로 늘었다** —
**460**(2026-08-11 실측: 판정 38 + 참조 47 + 구성 375). 4종의 구성종목은 합쳐 76종인데
상당수가 기존 유니버스와 겹쳐 순증은 그보다 작다(정확한 겹침은 레이크 대조가 필요하다).
460 ÷ 12.5 req/s ≈ 37초라 60초 창은 그대로 든다. 토스 adapter 는 대체 소스로 남는다(`source=toss`). ⚠️ 뉴스 Consumer 는 실행 표면이 생겼고(ALPHA-713 —
`run news-consumer`), **생산자도 실행 표면이 생겼다**(ALPHA-707 — `run news-worker`,
BigKinds 실호출 feed. 1분 주기 성립은 ALPHA-645 스파이크 실측). 그 feed 의 차단 시그니처
(403·429·400+HTML)는 BlockedFeedError 로 갈리고 쿨다운(기본 300초) 동안 poll 이 억제된다 —
처방은 재시도가 아니라 pacing 상향·중지다. news-worker 는
**서비스·세션 오케스트레이션까지 편입됐다**(ALPHA-717). iNAV 도 **같은 모양으로**
편입됐다(ALPHA-882) — 둘 다 구동 레인(price_minute) 스케줄의 **승객**이고, 늘어나는
자리는 `session_ops._OPTIONAL_LANES` 표 하나다(공시·업종지수도 같은 표에 든다):

| 승객 | 토글 env | 워커 목록 env |
|---|---|---|
| news_minute | `MINUTE_SESSION_NEWS_SOURCE_GROUP` | `MINUTE_SESSION_NEWS_WORKER_SERVICES` |
| disclosure_minute | `MINUTE_SESSION_DISCLOSURE_SOURCE_GROUP` | `MINUTE_SESSION_DISCLOSURE_WORKER_SERVICES` |
| etf_inav_minute | `MINUTE_SESSION_INAV_SOURCE_GROUP` | `MINUTE_SESSION_INAV_WORKER_SERVICES` |
| sector_index_minute | `MINUTE_SESSION_SECTOR_INDEX_SOURCE_GROUP` | `MINUTE_SESSION_SECTOR_INDEX_WORKER_SERVICES` |

start 가 그 세션도 계획하고, 승객 생산자는 **자기 세션이 선 날만** 별도 목록으로
올라간다(계획 실패 날 올리면 세션 부재 기동 거부 루프 — 구동 레인은 그와 무관하게
진행하고, 레인끼리도 독립이라 뉴스가 실패해도 iNAV 는 올라간다). stop 은 존재하는
세션 전부를 드레인하고 매 폴링 세션 존재를 재확인한다.
⚠️ **승객이 되는 것과 `SCALED_DATASETS` 에 드는 것은 다른 축이다** — 승객은 자기
워커를 소유해도 `--dataset` 인자로는 못 온다(`_scale` 이 dataset 을 안 보고 공용
목록을 내리므로, 그러면 살아 있는 price-worker 가 내려간다).
후속 단계는 `minute/__init__.py` docstring 참조.
