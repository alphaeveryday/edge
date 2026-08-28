# data-pipeline

> 역할/아키텍처는 [docs/repo-structure.md](../../../../docs/repo-structure.md)·[docs/context.md](../../../../docs/context.md)가 SSOT.
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
> **종목 마스터 적재(Step4, RDB)** 는 canonical **두 입력**(ETF 구성종목 + KRX 상장 전종목
> `instrument_profile`)을 Cloud Event Store 의
> `entity`/`actor`/`company_profile`/`instrument`/`equity_profile` 로 멱등 적재한다
> (`load-instruments`, ALPHA-372·830) — **이 저장소가 Cloud Event Store 48테이블에 쓰는 첫 경로**다.
> 전종목 축에서는 **보통주만** 세운다(우선주는 발행사 연결이 필요해 별건) — 실측 2,872종 중
> 113종이 우선주 계열이다. 두 입력이 같은 티커를 다른 시장으로 말하면 만들지 않고 기록만
> 한다(자연키가 `(market_code, ticker)` 라 만들면 같은 종목이 두 번 선다).
> ⚠️ 전종목 canonical 은 **수동 수집**이라(아래 "상태머신 밖") 낡을 수 있다 — 낡아서 전 행이
> 못 쓰이면 사유를 로그에 남기되 **비0으로 끝내지는 않는다**. 이 스텝의 exit code 는 뒤따르는
> FeatureParallel 전체를 좌우해서, 선택 입력의 낡음이 다섯 로더를 세우면 안 되기 때문이다.
> **가격변동 트리거 적재(RDB)** 는 canonical holdings 가중치와 구성종목 일봉으로 **가중 proxy
> 수익률**(coverage 정규화 — 분석엔진 L0 와 같은 산식, 정본)을 계산해 absolute gate(3%,
> `[price_triggers]`) 통과 거래일만 `price_movement_trigger` 로 멱등 적재한다
> (`load-price-triggers`, ALPHA-406→411) — 이 테이블의 **단일 writer** 이자 분석 SFN RDS
> 영속 전제 체인의 첫 고리다.
> **1분 가격·뉴스 파이프라인(장중)** 은 구현 중이다 — 현재는 공통 계약·fixture·결정적
> fake collector·virtual clock 기반층(`minute/`, ALPHA-660)과 cloud 원장 스키마 6테이블
> (session·window·news item/job·price job·outbox, ALPHA-661 — 상태 어휘는
> `minute/states.py` 가 SQL CHECK 와 기계 동기화)과 session/window repository
> (계획·claim·lease·fencing ALPHA-662 + watermark·lane·drain ALPHA-663)과 job/outbox
> repository(결정적 event ID·원자 enqueue·PG=retry 권위, ALPHA-664), artifact/manifest
> 경계(결정적·불변 key·put_immutable, ALPHA-665), fenced commit transaction(window·
> job·outbox 원자화 + orphan 검출, ALPHA-666 — 가격 분봉 canonical 은 **S3 artifact
> 정본**이라 트랜잭션 밖이고 DB canonical 은 뉴스만: ALPHA-701), Price Worker loop(fence·2-lane·
> 세대 예측·drain·SIGTERM 인계, ALPHA-667 — collector 주입식)와 **토스 분봉 adapter**
> (ALPHA-682 — 2026-08-01 실호출 실측 형상 기반: `1m` 캔들, ts 는 **구간의 끝**이라
> `window_start = ts − 1분`, 거래 없어도 캔들이 오므로 no_trade 는 "행 있고 거래량 0"·
> 행 자체가 없어야 missing. 녹화 fixture `tests/fixtures/toss/`)와 **KIS 분봉 adapter**
> (ALPHA-735 — 1분 레인의 **기본 벤더**. `FHKST03010200` 당일 분봉, 종목당 1콜에 30분치,
> `stck_cntg_hour` 도 구간의 끝이라 축은 토스와 같다. 4분류 판정은 벤더 무관부
> `minute/price_collect.py` 하나를 공유하고 각 collector 는 "그 window 의 봉 하나를 어떻게
> 얻는가"만 갖는다.
> ⚠️ **TR 이 둘이다**(ALPHA-846): 세션 날짜가 지난 거래일이면 소급 TR `FHKST03010230`
> (`KisHistoricalMinuteClient`)로 간다 — 당일 TR 에는 날짜 축이 없어 과거 세션에 물리면
> 오늘 봉이 오늘 라벨로 돌아와 전 window 가 missing 이 된다. 설정 노브가 아니라 벤더
> 사실이라 **날짜에서 유도**한다. 소급 TR 은 무거래 분 행을 주지 않아 어댑터가 직전 종가
> flat 으로 채우고, 응답이 거래일 경계를 넘으므로 `stck_bsop_date` 로 자른다),
> BigKinds adaptive overlap 컨트롤러+source item 관측 원장(anchor frontier·identity
> 격자 승격, ALPHA-668), News Worker loop(관측 전량 원장 판정→기사별 job, anchor 이중
> 보존·recovery, poll 원본/판정 기록 보존, ALPHA-669 — feed 주입식, BigKinds HTTP
> adapter 는 운영 승인 후), Outbox Relay(destination 별 claim·SQS batch 발행·재시도,
> ALPHA-670 — `run relay` 가 이 트랙의 **첫 실행 표면**이다), SQS Consumer 공통 kernel
> (long polling→DB 상태 확인→멱등 claim→실행→성공/재시도/격리, visibility+DB lease
> heartbeat, **DB 가 정한 시각으로 visibility 조정**, ALPHA-672 — handler 는 7B·7C 가
> 채운다)과 그 복구 경로(DLQ reconciler `run dlq-reconcile` + **DB-first** redrive
> `run redrive`: DEAD→RETRY_WAIT·세대 증가·새 delivery event 를 한 트랜잭션에), **시간대별
> 기대 유니버스 분기**(ALPHA-684 — 기대 집합은 window 시각이 정한다: 정규장 09:00~15:30 은
> 전 종목, 그 밖은 `Universe.extended_hours_ids` 가 선언한 시간외 거래 종목만. 세션 계획도
> 같은 규칙에서 나온다 — 시간외 종목이 있으면 08:00~20:00 = 720 window, 없으면 390.
> ⚠️ **universe 가 없는 소스 단위 dataset(뉴스·공시)은 `extended_hours` 만이 범위를 정한다**
> (ALPHA-875) — 기대 집합이 universe 에서 나오지 않아 "기대가 빈 window" 라는 실패 모드가
> 없다(window 하나 = "그 분에 소스를 한 번 폴링했다", 소스가 낸 것이 0건이면 VALID_EMPTY).
> 그래서 뉴스는 390·공시는 720 이고, 갈리는 자리는 `states.EXTENDED_HOURS_DATASETS` 하나다
> (⚠️ 공시 720 은 격자 **모델**이다 — 987 컷오버로 공시 1분 레인은 미편입이라 지금은 계획되지
> 않고, 공시 관측은 ops 원장(18:10 배치)에서 본다. 이 격자는 롤백 시에만 되살아난다).
> ⚠️ 상품군 축이 **아니다**: 개별주 001527 도 15:30 이 마지막이라, 클래스는 규칙이 아니라
> universe 가 선언한다. ⛔ **2026-08-02 결정: 장외는 제외한다** — 선언을 빈 채로 두면
> 전 종목 정규장 390 window 이고, 정규장 390분은 실측상 전 종목이 빈틈없이 채워진다.
> ⚠️ **종가 단일가 접수 구간에 걸친 window 는 전부 15:31 에 집힌다**(`window_end` 가
> 15:21~15:30 인 10개 — ALPHA-763 이 마감 창 하나에 건 것을 ALPHA-773 이 구간 전체로
> 넓혔다. `models.scheduled_at_for`). 그 구간(15:20~15:30)은 주문만 받고 체결이 없는데
> **묻는 시각에 따라 벤더 답이 갈린다**: 즉시 물으면 마감 창은 미완성 봉(vol 0·직전가)이
> 오고(08-03 실측 34종 중 25종이 일봉 종가와 불일치), 앞선 창들은 직전 체결 봉이
> **거래량째 복제**돼 와 5분봉 롤업 거래량을 부풀린다(08-05 실측 362/362 종목, 마지막
> 버킷 중앙 1.37배·최대 60배). 마감 뒤에 물으면 각각 정확한 종가와 `vol 0`(무거래)이
> 온다. 창을 안 만드는 게 아니라 claim 시각만 미루므로 원장에 구멍이 없고, KIS 는 한
> 콜이 `window_end` 로 끝나는 30분치라 콜 수도 안 는다. window INSERT 는 `DO NOTHING`
> 이라 **이미 계획된 세션엔 소급되지 않는다** — 당일 적용이 필요하면 그 행의
> `scheduled_at` 을 직접 UPDATE 한다. ⚠️ 이 지연은 마감 봉이 접수 구간 창보다 **먼저**
> 처리되는 순서 역전을 만든다(realtime lane 이 최신을 먼저 집는다) — 무거래 봉이 더
> 최신 앵커와 대조돼 허위 발화가 나갈 수 있었다. **ALPHA-776 이 그 발화를 막았다**
> (앵커가 이 window 보다 뒤에서 왔으면 발화 안 함 — 판정부 스냅샷과 쓰기 tx 두 곳).
> 같은 티켓에 남은 것은 무거래 봉 축(거래량 판독 실패 포함)과 정책 identity v3
> 승격이다),
> **뉴스 추출 Consumer handler**(ALPHA-689 — kernel 위에 `tagging/extract` 를 job 단위로
> 부르는 배선: 기사 정본은 PG `document`+`news_document` 자연키, 결과는 feature 존 불변
> artifact 이고 반환값이 그 바이트의 sha256 이다. artifact key 축은
> `(job_id, redrive_generation, attempt)` — LLM 출력이 비결정적이라 시도마다 key 가
> 갈려야 재시도가 자기 자신을 막지 않는다. 그 job 축 키는 원장이 색인이라 배치가 못 보므로,
> **같은 결과를 배치가 읽는 날짜축 feature 파티션에도 미러한다**(ALPHA-900) — 미러 없이는
> 배치가 같은 기사를 다시 유료로 태운다. 미러 실패는 job 을 죽이지 않는다(재시도가 새 attempt·
> 새 key 라 LLM 을 다시 부른다 — 미러 1건 손실보다 비싸다). 실패 분류의 terminal 은 payload↔원장 기사 축
> 불일치 하나뿐이고 나머지는 예산이 판정한다), **EOD 세션 QC**(ALPHA-693 — drain 이 끝난
> 세션의 `DUE` 잔존을 `MISSING` 으로 확정하고 `FINALIZED` 로 닫는다. `run qc-minute-session`.
> 확정은 **도래한 window 만**이고 계획의 양 끝·연속성이 어긋나면 확정 대신 `FAILED` 다 —
> 결손은 판정 결과지만 원장이 스스로와 모순이면 판정을 믿을 수 없다), **EOD 5분봉 확정**
> (ALPHA-839 — 5분 파생의 생산자는 둘이고 집계는 하나다: 커밋 후크 `maybe_rollup` 이
> 장중 즉시성을, `run rollup-minute-session` 이 마감 후 1회 확정을 맡는다. 후크만으로는
> **지나간 거래일이 영영 안 채워진다** — 발화 조건이 "방금 커밋된 window" 라 그날 마지막
> 버킷 뒤엔 다음 커밋이 없다. 배치는 계획·커밋을 원장에서 읽고, 커밋이 0건이면 빈 파일을
> 쓰지 않고 스킵한다 — 원장에 커밋이 없는 것과 그날 봉이 폐기된 것은 다른 사실이라,
> 빈 파일로 덮으면 다른 writer 가 채운 파티션을 지운다), **뉴스 canonical
> writer**(ALPHA-691 — 7B 가 **읽던** PG `document`+`news_document` 를 실제로 **쓰는** 쪽.
> commit 트랜잭션의 커서로 `(source_code, article_id)` upsert 하고, 정규화는 배치 정제
> `_normalize` 를 재사용한다. ⚠️ 시각 축 규칙이 둘로 갈린다: **내용은 이번 관측 값**으로
> 쓰고 **`available_at` 은 GREATEST 로 앞으로만** 간다 — 시각으로 내용 쓰기를 막으면 배치가
> 미래 `published_at` 을 실은 행에서 정정이 유실되고, 시각을 뒤로 밀면 과거 as-of 구간에서
> 문서가 사라진다. **배치와의 승자 규칙은 ALPHA-696 이 `news_document.lead_observed_at`
> 으로 정했다** — 이 경로는 쓰기 가드 없이 쓰되 리드 상태가 움직였을 때만 그 시각을 찍고,
> 배치는 미주장이거나 자기 canonical `fetched_at` 이 그보다 앞서지 않을 때만 덮는다
> (절이 `<=` 라 동시각은 배치가 이긴다). 비대칭이
> 의도이고, 계약 전문은 마이그레이션
> `V202608071018__add_news_document_lead_observed_at.sql` 에 있다. ⚠️ 그 시각도
> **`GREATEST` 로 앞으로만** 간다(ALPHA-858) — 두 축 다 단조다. 그래서 `lead_observed_at`
> 은 엄밀히는 *관측 시각의 상한*이고, 마이그레이션의 정의문("지금 저장된 리드 상태를 누가
> 언제 관측했는가")은 역행 관측이 낀 경우를 모른다. 적용된 마이그레이션은 수정하지 않으므로
> 그 예외는 여기와 `canonical_news.py` 리드 UPSERT 주석에 있다.
> ⚠️ **비교축도 falsy 로 접는다**(ALPHA-860) — `NULL ↔ ''` 는 움직임이 아니다. 뿌리는
> `normalize_news` 가 공백뿐인 리드를 `None` 으로 접는 것이고(그래서 canonical `lead_text`
> 는 결코 빈 문자열이 아니다 — 하류가 기대도 되는 불변식이다), 충돌 갈래의 `COALESCE` 는
> 레이크·PG 에 남은 옛 `''` 행 때문에 있다. 마이그레이션 ②가 "지워지는 것도 움직임"이라고
> 열거한 것은 이 예외를 모른다),
> **세션 계획·drain CLI**(ALPHA-698 — `run plan-minute-session`·
> `run drain-minute-session`. 체인의 **가운데가 비어 있었다**: EOD QC 조차 세션 행을 손으로
> 넣어야 돌았다. 원장이 멱등·CAS 를 갖고 있어 얇은 배선이고, 판정은 여기 두지 않는다.
> 재실행은 성공이다 — 재계획도 이미 걸린 drain 도 exit 0 이고, 무엇이 새로 생겼는지는
> exit code 가 아니라 출력(`created`·`drain_requested`)이 말한다. ⚠️ `--dataset`·
> `--source-group` 은 어휘 밖이면 거부한다: 오타 값으로 세션이 서면 그것을 처리하는
> Worker 배선이 없어 하루가 통째로 안 돌면서도 원장은 정상으로 보인다), **상주 Price
> Worker 엔트리포인트**(ALPHA-706 — `run price-worker`, ECS Service 명령. session 은
> 결정적 유도라 설정 source 오배선은 세션 부재로 기동 거부되고, destination·자격증명·
> lease 조합(lease ≥ (1+budget)×75초, session_lease ≥ heartbeat 주기+최악 tick)은
> 기동·로드 시점에 검증한다. `WorkerConfig.lease_seconds` 기본이 60→300 으로 오른
> 이유이기도 하다 — 토스 tick 실측 73초+ 아래면 자기 claim 이 in-flight 중 만료된다.
> collector 는 설정 `source` 가 고른다(ALPHA-735 — kis|toss, 미지 소스는 기동 거부).
> News Worker 엔트리포인트는 프로덕션 feed 부재로 별도 티켓: ALPHA-707), **가격 트리거
> 판정 Consumer handler**(ALPHA-708 → **판정식 v2 = ALPHA-745** — kernel 위에 얹는
> LLM 0 판정. 기준선은 **전일 종가**(`price_daily` 세션당 1회 조회·캐시)고, 기준선
> ±`revert_threshold`(1%) 안이면 발화 금지 구간이라 노출 중이던 종목은 회수
> (`ExposureReverted`)하고 앵커를 기준선으로 되돌린다. 밖이면 |close/anchor−1| ≥
> `abs_threshold`(3%) 에서 발화하고 앵커(`minute_trigger_anchor`) ← 발화가 —
> **2h 쿨다운은 폐지**됐고(재발화 축이 시간이 아니라 가격) 멱등 축은
> UNIQUE(entity, session, window)+DO NOTHING 이다. 임계를 넘어도 **앵커가 이 window
> 보다 뒤에서 왔으면 발화하지 않는다**(ALPHA-776 — 늦게 재판정된 과거 창이 미래
> 가격으로 재는 것이라 무의미하다. 판정부 스냅샷과 쓰기 tx 두 곳에서 보고, 접힌
> 종목은 판정 로그 `앵커역전 N` 과 결과 `skipped_stale_anchor` 에 남는다). 트리거 행은 `open_price` 에
> 기준선, `anchor_price` 에 판정 기준가를 남긴다. 전일 종가가 없는 종목만 세션 시가로
> 폴백한다 — 그때만 `minute_session_open` 원장이 **확정 후 불변**으로 걸린다(첫 window
> 미커밋=재시도, 커밋됐는데 레코드 없음=MISSING+사유). 트리거 행·앵커·설명 outbox
> event 는 한 트랜잭션이다. 판정식·임계의 정본은 분석엔진 소관이고 이 handler 는 확정
> 규칙의 배선이다), **설명 큐 4번째 destination**(ALPHA-709 — `price-explanation-realtime`
> 이 Relay 어휘에 등록돼 **4종이 전부 필수**다: 빠진 큐는 그 레인 event 전멸이라
> 기동 거부. 트리거 사건의 발행 가부는 `destination_accepts` 가 정본이고, DLQ 대사
> 어휘는 여전히 job 큐 3종이다 — 트리거 DLQ 는 job 테이블이 없어 대사 대상이 아니다.
> 분석 엔진은 `analyze --trigger-id` 로 분봉 트리거를 단건 소비한다 — 대상 ETF·
> trade_date 는 트리거 행이 정본, 계보는 `minute_price_trigger_id` 축)까지다.
> AWS 리소스는 terraform 에 정의됐다(ALPHA-711 — SQS 원 큐 4종+DLQ, 상주 서비스 10종
> price-worker·relay·price-consumer + news-consumer-realtime·-backfill(ALPHA-713) +
> news-worker(ALPHA-717) + disclosure-worker(ALPHA-875 — 한 window 가 체인 전체.
> ⚠️ 987 컷오버로 **미편입** — 공시는 저녁 배치가 소유하고 이 서비스는 롤백 경로로만 남는다) +
> inav-worker(ALPHA-882 — 장중 iNAV 생산자. 소비자가 없어
> 큐는 안 늘어난다) + sector-index-worker(ALPHA-887 — 업종지수 45종 생산자) +
> analysis-consumer(ALPHA-719 — 설명 큐 소비, analysis-engine 이미지):
> `infra/terraform/modules/data-pipeline/minute_services.tf`,
> desired_count 0 에 lifecycle ignore_changes — desired 를 terraform 밖에서 정하게 두고
> apply 가 장중 워커를 내리지 않게 한다. 그 주체는 **8종은 세션 오케스트레이션**(정의 10종 중
> disclosure-worker 는 987 미편입), **analysis-consumer 는 오토스케일링**이다(ALPHA-912, 아래). ⚠️ CD 의 상주 서비스 롤아웃은 repo variable
> `MINUTE_SERVICES_DEPLOYED=true` 일 때만 돈다 — 이미지 CD 와 apply 는 순서 보장이
> 없어, 권한이 서기 전 describe 가 AccessDenied 로 떨어지면 멀쩡한 이미지 배포까지
> 막힌다. apply 후 그 변수를 켠다). **그 desired_count 를 바꾸는 주체가 ALPHA-712 다**
> — `run start-minute-session`·`run stop-minute-session` 을 EventBridge Scheduler 가
> 부른다(Premarket 07:45 / EOD 16:10 KST — 987 이 20:05 에서 당겼다(공시가 배치로 떠나
> 시간외 격자 dataset 이 가격뿐, dev 정본에 시간외 축 없음), `aws_scheduler_schedule.minute_session`).
> 같은 자원이 **업종지수 5분 파생 확정**도 부른다(평일 16:00 KST — `rollup-minute-session
> --dataset sector_index_minute`, ALPHA-955). 시각이 다른 이유는 격자가 달라서다:
> 업종지수 세션은 늘 09:00~15:30 인데 가격은 시간외 종목이 정본에 있으면 20:00 까지
> 넓어질 수 있어(지금 dev 는 시간외 축이 없어 둘 다 15:30 이지만 축은 승계로 되살아난다),
> 가격 EOD 확정은 이 시각에 못박을 수 없다(ALPHA-839 소관).
> 내리는 조건은 **시각이 아니라 원장 상태**다(phase DRAINED → 큐 깊이 0 → outbox NEW 0,
> 연속 확인). 게이트가 풀리면 설정된 전 레인을 QC한 뒤 scale-down한다. 한 레인의 QC 실패도
> 나머지 레인 QC와 안전한 scale-down을 막지 않는다. 스케줄러는 RunTask **제출**까지만 보지만,
> ECS Task State Change rule이 minute-session task family의 컨테이너 exit≠0을 기존 alarm
> SNS topic으로 올린다.
> ⚠️ **analysis-consumer 는 세션이 스케일하지 않는다**(ALPHA-912 — 컷오버 완료). desired 는
> 큐 잔여 일감(가시+처리중)을 보는 오토스케일링이 소유하고(`analysis_autoscaling.tf`),
> 세션이 이 서비스에 대해 하는 일은 **공용 목록에서 이름을 빼는 것뿐**이다 —
> `_services()` 가 `MINUTE_SESSION_ANALYSIS_SERVICES` 를 근거로 뺀다(ALPHA-910 이 세운 축.
> **축을 가르는 주체는 terraform 이 아니라 코드다**). 그 env 가 비면 **죽는다**: 빼기가 안
> 돌아 공용 경로가 이 서비스를 다시 스케일하고, 그러면 매일 밤 stop 이 스케일러의 desired 를
> 덮어 축이 도로 둘이 된다(ALPHA-910 의 컷오버 관대함은 여기서 회수됐다).
> ⚠️ 그래서 **세션 stop(16:10)에 이 서비스를 내리는 주체가 없다** — 그게 의도다. 게이트는 설명 큐를
> 안 보므로(아래) 예전엔 처리 중인 설명이 stop 에 잘렸는데, 스케일러는 처리 중(비가시)까지
> 세어 그동안 대수를 유지한다. 야간 비용은 잔여 0 에서 0대로 내려가 해결된다.
> ⚠️ **절단이 통째로 사라진 것은 아니다** — 버스트 중 CD 재배포의 롤링은 여전히 처리 중인
> 태스크를 자른다(Fargate `stopTimeout` 상한 120초 < 건당 588초). 그건 별개 축이다.
> ⚠️ 그리고 이제 desired 를 0 으로 내리는 주체가 **오토스케일링 하나뿐**이다 — 세션의
> EOD 하드스톱(현 16:10)이 CloudWatch 를 안 보는 유일한 천장이었다.
> terraform 공용 목록에 이름이 아직 남아 있으나 코드가 늘 빼내므로 잉여다 — 제거는 후속
> 정합성 정리(PR C) 소관이고, 남아 있어도 동작은 같다.
> ⚠️ universe 정본 객체(config/minute/universe.json)는 **`build-minute-universe` 스텝**이
> 만들고 반영한다(ALPHA-735·953 — canonical KR holdings 와 config
> `[minute_universe].sector_etf_ids` 에서 파생. 쓸 자리는 소비자와 같은 `--universe` URI 를
> 인자로 받는다). 무변경이면 no-op 이고, 교체할 땐 직전 객체를 `.bak-<run_id>` 로 남긴다.
> **거래일 07:30 KST 이후엔 스스로 거부한다** — 세션이 이미 그 유니버스로 계획됐을 수 있고,
> 그러면 원장의 (universe_version, universe_hash) 는 옛 값에 고정된 채 객체만 바뀌어
> worker·consumer 가 매 틱 blocked 로 돈다. 이 스텝은 **장전 레인**(ALPHA-963,
> `premarket_pipeline.tf`)이 평일 07:00 KST 에 부른다 — `ingest-raw-etf --source krx`
> → `normalize-etf` → `build-minute-universe` 체인이다. 그 레인은 **원장 밖**이라
> (같은 CLI 를 시장 레인이 이미 소유해 `by_cli` 가 갈리지 않는다) Reconciler 백스톱이
> 없고, 대신 알람 셋이 실패 지점을 나눠 본다(SFN 실패·타임아웃·스케줄러 DLQ 도착).
> 객체 없이 스케일업하면 worker·consumer 는 기동 거부(fail-loud)다.
> ⚠️ **수집 축과 판정 축은 다르다**(ALPHA-842). `unit_ids`(수집) = 판정 ETF + 구성종목 +
> **참조 계열**(`sector_etf_ids`)이고, 트리거 판정은 `etf_ids` 만 받는다 — 층 분해의 섹터
> 후보처럼 봉만 필요한 계열을 `etf_ids` 에 얹으면 발화 대상·전일 종가 대조 대상이 된다.
> **처리량 제약은 벤더 교체로 풀렸다**(ALPHA-735) — 토스는 종목당 1콜 × 363종 ÷ 초당
> 5회 ≈ 73초라 60초 창을 못 맞췄고, KIS 는 실측 14.8 req/s(기본은 12.5)라 410 unit 이
> 34초에 든다(참조 계열 48 편입 후 시점의 실측, ALPHA-842). 그 뒤 ALPHA-927 이 091170 을
> 판정 축으로 옮겼는데 **unit 수는 안 늘었다**(410 → 409 실측 — 참조 계열에서 빠지고
> 판정 축엔 holdings 착지 전이라 아직 없다). 은행 구성종목은 KODEX 200 경유로 이미
> 유니버스에 있을 것으로 보이나 **표본 11종 확인이 잰 전부**였다 — 091170 명부가 그때는
> 레이크에 없어 전수 대조를 못 했다. 확실히 늘어나는 것은
> etf_map 축 3작업(KRX PDF·NAV·프로필)의 각 1콜뿐이다.
> (그 뒤 착지했다 — 2026-08-11 universe 빌드가 091170 포함 전건 판정으로 성립했고,
> 빌더는 canonical holdings 에 없는 판정 ETF 를 거부하므로 성립 자체가 착지 증거다.)
> 그 뒤 ALPHA-936 이 테마 4종을 올리면서 **unit 수가 처음으로 실제로 늘었다** —
> **460**(2026-08-11 실측: 판정 38 + 참조 47 + 구성 375). 4종의 구성종목은 합쳐 76종인데
> 상당수가 기존 유니버스와 겹쳐 순증은 그보다 작다(정확한 겹침은 레이크 대조가 필요하다).
> 460 ÷ 12.5 req/s ≈ 37초라 60초 창은 그대로 든다. 토스 adapter 는 대체 소스로 남는다(`source=toss`). ⚠️ 뉴스 Consumer 는 실행 표면이 생겼고(ALPHA-713 —
> `run news-consumer`), **생산자도 실행 표면이 생겼다**(ALPHA-707 — `run news-worker`,
> BigKinds 실호출 feed. 1분 주기 성립은 ALPHA-645 스파이크 실측). news-worker 는
> **서비스·세션 오케스트레이션까지 편입됐다**(ALPHA-717). iNAV 도 **같은 모양으로**
> 편입됐다(ALPHA-882) — 둘 다 구동 레인(price_minute) 스케줄의 **승객**이고, 늘어나는
> 자리는 `session_ops.PASSENGER_LANES` 표 하나다:
>
> | 승객 | 토글 env | 워커 목록 env |
> |---|---|---|
> | news_minute | `MINUTE_SESSION_NEWS_SOURCE_GROUP` | `MINUTE_SESSION_NEWS_WORKER_SERVICES` |
> | etf_inav_minute | `MINUTE_SESSION_INAV_SOURCE_GROUP` | `MINUTE_SESSION_INAV_WORKER_SERVICES` |
>
> start 가 그 세션도 계획하고, 승객 생산자는 **자기 세션이 선 날만** 별도 목록으로
> 올라간다(계획 실패 날 올리면 세션 부재 기동 거부 루프 — 구동 레인은 그와 무관하게
> 진행하고, 레인끼리도 독립이라 뉴스가 실패해도 iNAV 는 올라간다). stop 은 존재하는
> 세션 전부를 드레인하고 매 폴링 세션 존재를 재확인한다.
> ⚠️ **승객이 되는 것과 `SCALED_DATASETS` 에 드는 것은 다른 축이다** — 승객은 자기
> 워커를 소유해도 `--dataset` 인자로는 못 온다(`_scale` 이 dataset 을 안 보고 공용
> 목록을 내리므로, 그러면 살아 있는 price-worker 가 내려간다). 차단 시그니처(403·429·400+HTML)는 BlockedFeedError 로 갈리고
> 쿨다운(기본 300초) 동안 poll 이 억제된다 — 처방은 재시도가 아니라 pacing 상향·중지다.
> 후속 단계는 `minute/__init__.py` docstring 참조.

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
# symbol_map 은 예외 오버라이드 축. 신규 상장분은 코드에 문자가 섞이므로(0093A0 등 38종 중
# 8종) 형태 판정은 '선두 숫자 + 영숫자 6자'다(ALPHA-463 — 숫자로만 거르면 8종이 샌다).
# 토큰은 run 당 1회 발급·재사용, 그리고 `KIS_TOKEN_CACHE_PARAM`(SSM SecureString) 이 주입되면
# 컨테이너 사이로도 공유한다(ALPHA-573 — 아래 ingest-raw-nav 항목).
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
# etf_map, 현재 KR 38종 — 국내 반도체 30종 + KODEX 200 + 섹터 2종 + 은행 + 테마 4종,
# ALPHA-454·624·927·936). 날짜창 없이
# 그날(trdDd)
# PDF 전량을 append(US ETF 와 동형). 해외기초 ETF 는 비중·금액이 대시(-)로 와도 무변형 보존
# (현 유니버스엔 없다 — 경로만 유지). ⚠️ 계정 파이프라인 전용(사람 동시 로그인 시 CD011).
# --deadline-sec N: 벽시계 상한(ALPHA-581) — 벤더 열화로 상한에 닿으면 받은 것은 저장하고
# 미시도 ETF 를 failed_etfs 로 기록하며 조기 마감(status=partial). 판정은 ETF 사이에서만
# 하므로 진행 중인 1콜만큼은 넘길 수 있다(SFN TimeoutSeconds 의 SIGKILL 대신 택한 설계).
# 미지정=무제한(기존 동작). SFN 배선은 krx_etf_deadline_sec 변수(statemachine.tf).
DATA_PIPELINE_KRX_ETF__SOURCE__MBR_ID=... DATA_PIPELINE_KRX_ETF__SOURCE__PW=... \
  uv run --package data-pipeline python -m data_pipeline.run ingest-raw-etf --source krx

# 국내 ETF NAV 원본저장(Step1) — KIS ETF NAV비교추이(일), tr_id FHPST02440200(ALPHA-380).
# KRX getJsonData 는 무로그인·세션 모두 LOGOUT 이라(2026-07-20 실측) 가격에서 검증된 KIS 를
# 쓴다. 수집 유니버스는 별도 맵을 두지 않고 krx_etf.source.etf_map(KR 38종)을 그대로 공유한다
# — 구성종목과 NAV 가 다른 목록을 보면 안 되기 때문. KIS 는 ISIN 이 아니라 6자리 단축코드로
# 질의하며, 신규 상장분은 코드에 문자가 섞인다(0093A0 등 38종 중 8종 — 숫자로만 거르면 샌다).
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
# ⚠️ 입력 선택 축이 셋이다(CLI 는 앞 둘만 노출): 전체 스캔 / `--input-run-id` 스코프 /
# **키 직접 전달**(`run(..., raw_keys=[...])` — 1분 레인 전용, ALPHA-875). 앞 둘은 `raw/` 를
# 전량 LIST 한 뒤 거르므로 하루 720 window 가 돌 수 없어, Worker 는 방금 쓴 키를 그대로 넘겨
# LIST 를 0 으로 만든다. 넘긴 키는 **걸러지지 않는다** — 규약 밖 키는 `raw_read_error` + exit 1
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
# 차단)이다. 아직 LoadDisclosure는 issuer 미해소 자동 회수를 위해 canonical 전체를 스캔하므로,
# 실패 뒤 공유 canonical에 남은 파티션을 후속 정상 런이 회수할 수 있다. durable pending ledger가
# 이 회수를 대체하면서 manifest consumer로 전환하는 작업은 ALPHA-1045 범위다.
# 정상 0건도 canonical_written=true·빈 canonical_partitions로 producer 미실행과 구분한다.

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
# 정상 실행은 NormalizeNews manifest의 직접 parquet와 현재 article_id만 읽는다. --from/--to는
# 명시적 과거 복구, --all은 명시적 전체 복구다. --limit은 이번 런의 새 LLM 호출 수 상한이다.
LLM_API_KEY=... uv run --package data-pipeline python -m data_pipeline.run tag-news --input-run-id 20260701T000000Z --limit 50
#   기간 복구: ... run tag-news --from 2026-07-01 --to 2026-07-08
#   전체 복구: ... run tag-news --all

# 종목 마스터 적재(Step4, RDB) — canonical ETF 구성종목(market=KR)의 **최신 기준일** 중
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

# 대상 ETF 는 holdings ∩ **유니버스 뿌리**(`krx_etf.source.etf_map`) ∩ instrument 마스터다 —
# 뿌리 밖(폐지분·참조 계열)을 안 빼면 "마스터 시드 누락"과 "애초에 대상 아님"이
# skipped_unknown_etf 한 카운터로 뭉개져 진짜 결손을 못 본다.
# 가격변동 트리거 적재(RDB, ALPHA-411) — canonical holdings 가중치 × 구성종목 일봉 수익률의
# coverage 정규화 proxy(분석엔진 L0 산식 정본)가 absolute gate(abs_threshold=3%)를 넘는
# 거래일만 price_movement_trigger 로. holdings 는 거래일 이하 최신 스냅샷, 없으면 가장 이른
# 미래 스냅샷 폴백(엔진과 같은 선택, ALPHA-418 — 사용 횟수·as_of 는 quality_log 로 드러남).
# 날짜 선택과 행 규칙(비중 결손·음수 제외)은 엔진과 같지만 **결손 과반 파티션 배제는
# 엔진에만** 있다(ALPHA-951) — 그런 파티션에선 트리거는 남은 실값으로 서고 설명은 이전
# 스냅샷으로 서서 둘이 갈린다.
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
# ON CONFLICT. --from/--to 는 report_date 창(미지정=전체 스캔).
# 창 인자가 하나 더 있다(ALPHA-721): --window-days N 은 오늘−N일 창을 앱이 계산해 넘긴다.
# ASL 이 날짜 산술을 못 해 --from/--to 를 만들 수 없어서다 — 721·724 시절 다슬롯 공시 SFN
# 을 위한 흔적이다(875 의 1분 워커는 CLI 가 아니라 스텝 함수에 날짜창을 직접 넘겼고, 987
# 이후 스케줄 경로(18:10 SFN)는 옵션 없이 돈다). 명시 --from/--to 가 우선하고,
# 둘 다 없으면 종전대로 풀스캔이다(백로그 회수 경로 보존 — 987 이후 스케줄 경로가 이쪽이다).
DATA_PIPELINE_DB__HOST=... DATA_PIPELINE_DB__PASSWORD=... \
  uv run --package data-pipeline python -m data_pipeline.run load-disclosure

# assertion 적재(RDB, ALPHA-375·376) — feature 뉴스 assertion(ko)을 document_assertion·
# assertion_argument 로. **해소 축은 역할이 정한다**(ALPHA-831) — 온톨로지 identity 표를
# 읽어 셋으로 갈린다: NONE=instrument 완전일치(티커·정식명·종목명) / REGISTRY=시드된 기관
# 명부 조회(못 찾아도 채번 안 함) / MINT=멘션에서 결정적 채번. 미해소·충돌은 quality log 에
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
만든다. 상태머신(`edge-dev-data-pipeline`)은 **raw → normalize → feature 3페이즈**를
한 실행에서 완주한다(ALPHA-355·386·408, [ADR-0028](../../../../docs/adr/0028-unified-pipeline-sfn.md);
analyze 페이즈는 ALPHA-806 에서 상주 소비자로 옮겨 나갔다) —
각 페이즈는 잡을 병렬 ECS RunTask 로 돌리고, **앞 페이즈가 전량 성공해야** 다음으로 넘어간다 —
단 **raw 는 예외**다(ALPHA-460): 소스 하나가 실패해도 무관한 소스의 정제·분석은 계속 돈다.
정제가 빈 입력을 정상 성공으로 처리하므로 있는 만큼 처리하면 되기 때문이다. 대신 실패 직후
SNS 알림이 나가고, 그 런은 끝에서 FAILED 로 마감된다(막지 않되 조용하지도 않게).
모든 브랜치에 같은 `--run-id` 를 넘겨 raw partition·canonical·collection_log 를 같은 실행 단위로
묶는다. 앞 3페이즈는 같은 브랜치 빌더가 잡 목록만 바꿔 찍어내고(구조 동일), analyze 는 단일
태스크(analysis-engine 이미지)라 빌더 밖이다.

뉴스(지식) 레인은 별도 상태머신 `edge-dev-data-pipeline-news`(ALPHA-553)로 **분리 완료**다 — 시장
레인과 자연 주기가 달라(시장=장마감 EOD, 뉴스=종일 유입) 자체 주기(**주 7일** 00:10·08:10
KST, dev ENABLED 컷오버 — 요일은 ALPHA-874 로 넓혔고 슬롯은 ALPHA-893 이 3개→2개로 줄였다)로 `news raw → NormalizeNews → [TagNews·LoadDocuments] → LoadAssertions →
AssembleEvents` 를 돌린다. 같은 브랜치 빌더를 재사용하고(news_* 페이즈), `instrument` 마스터는
시장 SFN 이 단일 writer 로 쓰고 뉴스 SFN 은 읽기 전용 공유한다. PR2(ALPHA-553)로 시장 SFN 에서
뉴스 스텝(수집·정제·태깅·문서 + 직렬 LoadAssertions·AssembleEvents)이 제거됐다. ⚠️ 여기 있던
"시장 analyze 가 뉴스 SFN 의 이전 런이 조립해 둔 event 를 소비한다"는 서술은 **ALPHA-806 부터
사실이 아니다** — 그 티켓이 시장 SFN 에서 analyze 페이즈를 걷어냈고 설명은 분봉 트리거 큐를
소비하는 상주 서비스만 만든다. 그래서 ALPHA-893 이 오후 슬롯을 내려도 잃는 소비자가 없다. 뉴스 레인은
운영 원장에 **자체 `pipeline_type`(`news`)·하루 2슬롯 기대로 편입돼 있다**(ALPHA-591) — 뉴스
스케줄도 daily 와 같이 Planner(plan-run, `OPS_PIPELINE_TYPE=news`) 경유로 SFN 을 시작한다
(카탈로그 절 참고).

공시 레인도 같은 형태로 분리됐다가 **한 번 더 옮겨 갔다** —
`edge-dev-data-pipeline-disclosure`(ALPHA-722)가 세워져
`CollectDartDisclosure → [NormalizeDisclosure·NormalizeDisclosureSegment] →
LoadDisclosure` 를 돌았고(부분집합 필터 재사용, 새 state 정의 0개 — 체인은 Feature 의
LoadDisclosure 에서 닫힌다. 별도 이벤트 조립 state 는 **없다**),
시장 SFN 에서 공시 체인이 빠졌다(15:40 런은 공시를 돌리지 않는다). 875 가 공시를 1분
세션으로 넘겼다가, ⚠️ **987 이 저녁 배치로 되돌린 것이 현재 상태다**: 이 SFN 스케줄이
**평일 18:10 KST 1슬롯**으로 ENABLED 이고 카탈로그 공시 엔트리도 4개다. 공시 실행과
결손은 다시 ops 원장에서 본다. 증분 창은 원장 워터마크(`disclosure_watermark.py`)가
직전 완주 런의 window_to 당일부터로 정해 늦은 노출 꼬리·런 실패를 다음 런이 회수한다.

⚠️ `LoadDisclosure` 는 **창 없이(canonical 전체 스캔)** 돈다. 한때 이 레인만 `--window-days` 를
붙였다가 되돌렸다 — 그 풀스캔이 곧 **백로그 회수 경로**이고, 컷오버로 15:40 런이 공시를 안
돌게 된 지금은 창 밖으로 밀린 canonical 을 자동으로 주워올 경로가 그것뿐이다. 특히 아래
issuer 지연 회수가 창을 넘기면 영구 누락이 된다.

**장중 수급 레인**(`edge-dev-data-pipeline-investor-intraday`, ALPHA-769)도 같은 형태다 —
`CollectKisInvestorEstimate → NormalizeInvestorEstimate → LoadInvestorIntraday` 를 평일 5슬롯
(09:35·10:05·11:25·13:25·14:35 KST)으로 돈다. **다만 컷오버가 아니라 신설이다**: 이 3스텝은
시장 SFN 이 한 번도 돈 적이 없어(ALPHA-767·768 이 층만 만들고 배선을 안 붙였다) 두 레인이 같은
스텝을 동시에 소유하는 겹침 창이 없고, 그래서 스케줄을 처음부터 ENABLED 로 세웠다. 슬롯 수는
우리가 고른 게 아니라 소스가 정한다 — 벤더 갱신이 하루 4~5회뿐이고 유형별로 시각이 갈려
합집합이 5개다(+5분은 정각 반영 지연이 미관측이라 둔 여유).

⚠️ 이 레인의 `LoadInvestorIntraday` 도 **창 없이 돈다** — 공시와 같은 이유(풀스캔이 백로그 회수
경로)이고, 그 때문에 **공휴일에도 실일을 한다**. 원장 카탈로그에서 이 작업만
`kr_trading_calendar=False` 인 근거가 그것이다(수집·정제는 비거래일에 대상 자체가 없어 True).

⚠️ **컷오버가 필요한 이유는 성능이 아니라 원장 정체성이다.** 작업 정체성의 정본은
`catalog.by_cli(step, source)` 인데 두 레인의 CLI 가 글자 그대로 같아(`ingest-raw-disclosure`
등), 같은 스텝을 두 레인이 동시에 소유하면 `by_cli` 가 먼저 온 쪽을 돌려줘 장중 런의 attempt
가 시장 레인 task_key 로 기록된다 — 장중 런은 영구 MISSED, 시장 런은 resolve 경로 없는
`LEDGER_GAP` 이다. 그래서 컷오버는 선택이 아니라 전제다.

`LoadDisclosure` 의 issuer 해소는 **레인 간 읽기 전용 공유**다 —
`company_profile.dart_corp_code` 를 채우는 `EnrichCorpCode` 는 시장 SFN 소관이라, 유니버스에
새로 들어온 회사는 그 슬롯에서 `skipped_unresolved_issuer` 로 계측된 뒤 다음 일일런 이후
슬롯이 줍는다(조용한 유실이 아니라 계측된 지연). 뉴스 SFN 이 `instrument` 마스터를 빌려 읽는
것과 같은 형태다.

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
    **동시 실행하면 한쪽이 403**(EGW00133) 이다. SFN 에는 kis 브랜치가 4개 나란히 편입돼 있다
    (price·nav·investor·etf_profile). 흡수는 두 겹이다:
    - **공유 캐시(ALPHA-573)** — `KIS_TOKEN_CACHE_PARAM` env(터라폼이 kis task-def 에 주입,
      SSM SecureString)가 있으면 발급한 토큰을 컨테이너 사이로 공유해 발급이 하루 1회로
      수렴한다(토큰은 24h 유효). 403 을 맞으면 1분을 기다리기 전에 승자의 쓰기를 짧게
      폴링(2초×5)해 그 토큰을 가져간다. **캐시가 없거나 실패하면 아래 대기·재시도로 폴백**한다
      — 최악이 캐시 없던 시절의 동작이다. env 가 없는 로컬 실행은 항상 이 폴백 경로다.
    - **대기·재시도(ALPHA-458)** — `kis_auth` 가 403 EGW00133 을 만나면 61초 + 지터(0~20초)
      대기 후 재시도한다(예산 `TOKEN_RATE_LIMIT_MAX_RETRY = 4`, 총 5회 시도 — 동시 발급자
      수보다 커야 한다). 유량 제한이 아닌 4xx 는 기다려도 안 풀리므로 즉시 올린다.
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

- `ingest-raw-instrument` / `normalize-instrument-profile`(KRX 상장 **전종목** 종목기본정보,
  ALPHA-829) — **SFN 에 편입돼 있지 않고 ops 카탈로그에도 없다.** 배선은 별도 티켓 소관이라
  그전까지는 **손으로 돌릴 때만** 수집된다. 카탈로그 등록을 함께 하지 않은 건 의도다 —
  `required=True` 로 넣으면 원장이 매 런 이 작업의 빈 칸을 만들어 놓고 미이행으로 센다.
  - 자격증명이 `krx_etf` 와 **다르다**: 저쪽은 계정 로그인(`mbr_id`/`pw` → JSESSIONID),
    이쪽은 무상태 `AUTH_KEY` 헤더다. 시크릿도 별개(`edge-dev-data-pipeline/krx/api-key`).
    ```bash
    DATA_PIPELINE_KRX_INSTRUMENT__SOURCE__AUTH_KEY=... \
      uv run --package data-pipeline python -m data_pipeline.run ingest-raw-instrument
    uv run --package data-pipeline python -m data_pipeline.run normalize-instrument-profile
    ```
  - ⚠️ **당일 조회가 막혀 있다**(`basDd < 오늘`). 기준일은 달력이 직전 거래일로 정하므로
    `--from/--to` 는 **거부**한다 — 무시하고 돌면 소급한 줄 착각한다.
  - ⚠️ 달력을 쓰므로 `OPS_KR_HOLIDAYS` 주입이 필요하다(미주입이면 공휴일을 거래일로 보고
    0행을 받아 게이트에 걸린다). 위 krx 잡과 같은 요구사항이다.
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
- `ingest-raw-investor-estimate`(종목별 **장중** 투자자 추정, **kis 세트** — EOD 투자자 수급과
  같은 앱키·같은 유니버스, ALPHA-767) — **장중 수급 레인**(`edge-dev-data-pipeline-investor-intraday`,
  평일 5슬롯 09:35·10:05·11:25·13:25·14:35 KST)의 raw 스텝이다(ALPHA-769). dataset 은 `investor_flow_intraday` 로
  EOD(`investor_flow_daily`)와 **갈라 둔다** — 값이 가집계 추정(`*_fake_*`)이고 시간축이
  거래일이 아니라 그날의 슬롯(`bsop_hour_gb`)이라, 한 데이터셋에 섞으면 소비자가 잠정과
  확정을 구분할 수 없다.
  - EOD(`FHPTJ04160001`)와 **tr_id·파라미터가 갈린다**: 장중은 `HHPTJ04160200` 이고 종목코드
    하나(`MKSC_SHRN_ISCD`)만 받는다 — 날짜 파라미터가 아예 없다.
  - **갱신은 하루 4회**(외국인 09:30·11:20·13:20·14:30 / 기관 10:00·11:20·13:20·14:30) —
    합집합 5슬롯이 레인 스케줄의 근거다.
  - ⭐ **응답이 누적이다**(2026-08-06 dev 실측): 한 콜이 그날 슬롯 **전부**를 준다(14:51 한 번에
    325종목 1,574행 = 종목당 최대 5행). 그래서 슬롯을 하나 놓쳐도 다음 슬롯이 회수하고, 레인이
    `retry 0` 을 쓰는 근거가 된다. 슬롯 필드 `bsop_hour_gb` 의 도메인도 같은 실측에서 **`"1"`~
    `"5"` 한 자리 코드**로 확인됐다(`"0930"` 같은 시각 문자열이 아니다 — 슬롯 1 은 기관값이
    284/284 전건 0이라 09:30 외국인 갱신에 대응한다). 값은 장 시작부터의 **누적 순매수**라
    슬롯 간 차분이 그 구간의 순매수이고, 거래가 없던 종목은 그 슬롯 행이 아예 없다.
  - ⚠️ **ETF 자체는 0행이다.** 거래소가 ETF 의 장중 투자자 귀속을 생산하지 않는다(KIS 장중
    투자자 4종 전수조사로 확정). 우리 유니버스는 ETF **구성종목**(개별주식)이라 적용되지만,
    holdings 유니버스에 섞여 오는 ETF 자신은 빈 응답이 정상이다.
  - ⚠️ **소급 백필이 없다**(iNAV 와 같다). 날짜 지정이 없어 오늘치만 오고, 놓친 슬롯은 그날
    안에서만 회복된다. 그래서 `--from/--to` 를 주면 **실행을 거부**한다.
  - **기준일 가드**: 응답에 날짜 필드가 없어 거래일을 수집 시각(KST)으로 붙이는데, 비거래일·
    개장 전에 KIS 가 직전 슬롯을 주면 어제 데이터가 오늘 거래일로 굳는다(위 iNAV·ALPHA-387 과
    같은 함정). 그래서 **거래일이고 첫 슬롯(09:30 KST) 이후**일 때만 수집한다.
  - **가드는 iNAV 와 같은 `skip_reason` 규약이다**(ALPHA-769 에서 통일). 못 돌 시각이면
    `status=skipped`·exit 0 으로 마감하고 사유를 collection_log 에 남긴다. 종전엔 예외를 올려
    `status=error`·exit 1 이었는데, 레인이 **평일 cron** 이라 그대로 두면 공휴일마다 런이
    FAILED 여서 예정된 무산출과 진짜 고장이 구분되지 않는다. 기제는 공유 스텝
    (`ingest_raw_investor`)에 심었고 EOD 어댑터엔 이 속성이 없어 동작이 불변이다. 어댑터의
    `fetch` 는 여전히 같은 사유로 raise 한다 — **직접 호출자**를 위한 것이고, 조건은
    `skip_reason` 하나가 정본이라 두 경로가 갈릴 수 없다.
  - **뒤 두 스텝도 같은 레인이다**(ALPHA-768·769): `normalize-investor-estimate`(raw → canonical
    `investor_flow_intraday`) → `load-investor-intraday`(→ 동명 테이블). EOD 체인
    (`normalize-investor` → `load-etf-flow`)과 스텝을 **복제하지 않고 갈랐다** — 정체성 키에
    `asof_slot` 이 붙어 병합 키·PK·창 프루닝이 전부 달라 인자로 갈아끼울 수 없다(수집 스텝은
    저장 위치만 달라 인자로 갈랐던 것과 대조). 레인이 자동으로 돌리지만 세 스텝을 손으로 이어
    돌려도 체인이 닫힌다 — 복구·검증용이다
    (`src/` 에서. 수집은 KIS 앱키, 적재는 DB 접속이 필요하다 — 위 각 절의 env 와 같다):
    ```bash
    RUN_ID=manual-investor-20260827-0935
    export RUN_ID
    # 수집 — 거래일이고 09:30(KST) 이후일 때만. 아니면 사유를 남기고 skip(exit 0)한다
    DATA_PIPELINE_KIS_INVESTOR__SOURCE__APP_KEY=... DATA_PIPELINE_KIS_INVESTOR__SOURCE__APP_SECRET=... \
      uv run --package data-pipeline python -m data_pipeline.run ingest-raw-investor-estimate \
      --run-id "$RUN_ID"
    # 정제 — 같은 수집 run만 읽고 canonical winner manifest를 확정한다
    uv run --package data-pipeline python -m data_pipeline.run normalize-investor-estimate \
      --run-id "$RUN_ID" --input-run-id "$RUN_ID"
    # 적재 — 같은 manifest의 직접 parquet와 winner만 읽는다. 선행: Flyway 적용 완료
    DATA_PIPELINE_DB__HOST=127.0.0.1 DATA_PIPELINE_DB__PASSWORD=... \
      uv run --package data-pipeline python -m data_pipeline.run load-investor-intraday \
      --run-id "$RUN_ID" --input-run-id "$RUN_ID"
    ```
    - **컬럼은 추정 수량 3개뿐**(`net_qty_foreign_est`·`net_qty_institution_est`·
      `net_qty_total_est`). 벤더가 `frgn`·`orgn`·`sum` 가집계 수량만 주고 개인·기관 세분·
      순매수 대금을 안 준다 — EOD 의 백만원→원 환산도 `currency` 태깅도 대상이 없다.
      `_est` 접미사는 표면에서 잠정임이 읽히게 하는 장치다.
    - ⚠️ `asof_slot` 은 **TEXT 로 원문 보존**한다. `bsop_hour_gb` 의 도메인이 미관측이라
      ("0930" 같은 시각인지 "1"~"5" 코드인지) 시각으로 파싱하면 정체성 키를 잘못 가정하는데,
      이 소스는 소급 재조회가 없어 사후 정정이 불가하다. 실측으로 좁힌 뒤 좁힌다.
    - 정정 정책은 **최신값 덮어쓰기**(형제 로더와 같은 모델) — 벤더가 가집계를 고치면
      canonical 이 최신 `fetched_at` 으로 수렴하고 마트는 `DO UPDATE` 로 따라간다.

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

- `tag-news`(→ 레이크 feature 존, **deepseek 세트**) — SFN은 `--input-run-id`로 NormalizeNews
  manifest의 직접 parquet와 현재 논리 ID만 읽고 `--limit`(기본 10000)으로 LLM 호출 수를 묶는다.
  KST 전일·당일 장중 미러 prefix는 직접 조회해 canonical이 아직 없는 미러도 흡수한다.
  실제 변경한 파티션·`article_id`는
  `operations_archive/feature_run_manifests/dataset=news_assertions/run_id=…/manifest.json`에
  기록하며, 모든 파티션과 quality log가 성공한 뒤에만 `feature_written=true`가 된다.
  상한에 걸린 잔여가 있으면 manifest를 완료하지 않고 같은 run 재시도가 이어받는다(mentions 있는 미태깅
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
- `load-documents`(→ Cloud Event Store RDB, **rds 세트** 재사용, ALPHA-374·410·1031) —
  NormalizeNews manifest의 직접 parquet만 GET하고 현재 실행 `article_id`를 document로 적재한다.
  결손·손상 manifest는 전체로 넓히지 않고 실패한다. 자연키 멱등, LoadAssertions의 FK 선행.
  문마다 후보 전량을 `executemany` 로
  보낸다(ALPHA-906) — 예전엔 후보마다 최대 3왕복(document·lead·publisher)이라 31.8만 행이면
  왕복이 최대 95만 번이었고, 그것이 뉴스 SFN 이 상한에 물리던 원인이었다(TIMED_OUT 전건이 이 스텝
  미완). `created` 와 로그 표본은 `RETURNING` 이 돌려준 행에서만 뽑는다 — 배치의 `rowcount`
  로는 **어느 행**이 들어갔는지를 알 수 없다
- `load-disclosure`(→ Cloud Event Store RDB, **rds 세트** 재사용, ALPHA-476·532) — canonical 공시 →
  document(DISCLOSURE)·disclosure_document·disclosure_fact. issuer 는 앞 직렬 enrich-corp-code 가 채운
  dart_corp_code 로 해소(DART API 불요라 rds 세트). 자연키 멱등·정정 DO UPDATE.
  **적재 로더 중 유일하게 `--window-days` 를 받는다**(ALPHA-721). 공시는 장중 레인이 붙으면
  canonical 스캔이 슬롯마다 곱해진다. 뉴스 `load-documents`는 ALPHA-1031에서 manifest 직접
  키·현재 논리 ID 소비로 전환됐으며, 이 공시 경로의 LIST 제거는 별도 작업이다.
  그 레인이 실제로 붙었었다(ALPHA-875 `disclosure-worker` — 987 이 저녁 배치로 되돌려
  지금은 미편입) — 1분 레인은 이 함수를 **질의 날짜창으로 좁혀** 불렀다. ⚠️ 좁혀지는 것은 parquet GET 뿐이다: `_read_facts` 가 `report_date=` 프리픽스
  **전체**를 LIST 한 뒤 날짜를 거르므로 window 당 2 LIST(supply·segment)가 남고 그 비용은
  report_date 파티션 수에 비례해 자란다. 거래일당 +1 이라 당장은 견디지만, 줄이려면 창에서
  파티션 프리픽스를 만들어 그 날짜만 LIST 해야 한다
- `load-assertions`(**직렬**, 뉴스 SFN 의 feature 페이즈 뒤 — ALPHA-376·410·553·1033) — feature assertion →
  document_assertion·assertion_argument. document FK 의존이 병렬이면 레이스라 직렬로 둔다.
  정상 경로는 TagNews manifest의 직접 part만 GET하고 현재 `article_id`만 논리 처리한다.
  결손·손상 manifest는 풀스캔으로 넓히지 않고 실패하며, 누적 part의 물리 읽기 행과 manifest
  논리 행을 quality log에서 분리한다. 과거 일부/전체 복구는 `--from/--to`/명시 `--all`이다.
  **실패한 범위는 다음 런이 이어 싣는다**(ALPHA-1052, `manifest_carry_forward`) — 성공 시
  manifest 옆에 소비 마커를 쓰고, 시작할 때 "manifest 는 있는데 내 마커가 없는" run 을 함께
  싣는다. 이게 없던 동안은 이 스텝이 죽으면 그 범위가 **영구 유실**이었다: 다음 런 manifest
  에는 그 `article_id` 가 없다(이미 태깅돼 생산자의 `changed_ids` 밖). 마커는 소비자별이라
  한 manifest 를 둘이 읽는 계보(normalize_news → tag-news·load-documents)도 서로를 안 지운다.
  마커는 **범위가 온전히 착지했을 때만** 쓴다 — `missing_document` 가 있으면 그 범위는
  미소비로 남는다(그 결손의 회수 수단이 같은 manifest 재실행이다). 회수는 **보조 작업**이라
  manifest 마다 격리한다: 옛 manifest 하나가 깨져도 이번 런은 살고, 못 실은 사유는
  `unfinished`·`stale`·`failed`·`over_limit` 로 갈라 남는다. 창(7일) 밖으로 밀린 것은
  `skipped` 마커로 닫아 탐색이 수렴하게 한다(consumed 와 이름이 다르다 — "안 싣고 닫았다"가
  "실었다"로 둔갑하면 안 된다). ⚠️ 격리는 **읽기·검증까지**다 — 적재는 한 트랜잭션이라
  회수 행이 DB 제약을 어기면 이번 런도 같이 죽는다(ALPHA-1053).
  한 파티션에 같은 기사의 판정이 둘 있으면 **`tagged_at` 최신만 싣는다**(ALPHA-900,
  `rows_superseded`) — `tag-news` 의 압축과 같은 규칙이다. 안 그러면 사건 자연키가 갈린 옛
  판정이 함께 INSERT 되고 `ON CONFLICT DO NOTHING` 이라 영영 안 덮인다.
  **파티션 사이도 같은 규칙이다**(ALPHA-1051, `rows_moved_partitions`) — `article_id` 는 원문
  URL 해시라 불변인데 `published_date` 는 벤더 재등록으로 **이동하고**(BigKinds 가 같은 URL 을
  다른 `DATE`·`NEWS_ID` 로 다시 준다) 옛 파티션 행은 아무도 지우지 않아 한 기사가 두 파티션에
  남는다. 이걸 manifest 손상으로 보고 실패하면 그 런의 범위가 통째로 유실된다(ALPHA-1052) —
  손상이 아니므로 최신 판정만 싣고 계속 간다. 소비 순서는 이 스텝이 날짜 오름차순으로
  세운다(동률이면 늦은 파티션 승 — 생산자 배열 순서에 안 걸리게). 보장 범위는 **이번 런이
  싣는 행**까지고, 앞선 런이 이미 실은 옛 판정은 자연키가 갈리면 DB 에 남는다(ALPHA-1052).
  **흡수 전 장중 미러는
  읽지 않는다**(`minute_mirrors_unabsorbed`) — 그 조각은 아직 `tag-news` 의 mentions 게이트를
  안 거쳤다.
  역할별 엔티티 해소(ALPHA-831 — 명부·채번 축 포함)와 해소율은 quality log 로 남고,
  채번 경로는 entity·concept 마스터 행도 만든다
- `assemble-events`(**직렬**, 뉴스 SFN 의 LoadAssertions 뒤 — ALPHA-412·553, **events 세트**=LLM+DB) —
  분석엔진 추출 체인의 이식: canonical 뉴스 제목 분류(LLM) → document/assertion/source_event
  계보 조립 → event_thread threading. **배치=catch-up 이다(ALPHA-730)** — event 의 실시간 정본은
  1분 단건 조립(ALPHA-727)이고, 배치는 미조립 잔여 소진 + UNKNOWN 재평가·미연결 회수만 맡는다.
  적재 직전 doc 단위 advisory lock 아래 자국을 재확인해 단건 경로가 분류 창에서 먼저 조립한
  기사를 skip 하고, 트랜잭션은 날짜별 커밋이라 threading 락 점유가 1분 소비자를 오래 막지
  않는다. 자체 분류기 폐기는 단건 경로 커버리지 실증 후 후속. 결정적 ID 산식·프롬프트는
  엔진과 동일(정본), 창 미지정 = 오늘(KST) 하루 — 뉴스 SFN 은 `--window-days 1` 로 [어제,오늘]
  겹침(ALPHA-592, 자정 crossing·overnight 갭 방지). event 의 소비자는 ADR-0028 기준 analyze 였으나
  **ALPHA-806 이 시장 SFN 에서 analyze 를 걷어낸 뒤로는 분봉 트리거 큐의 상주 소비자**다. 제목 분류 LLM 콜은 배치별 병렬 실행한다(ALPHA-520, tag-news 와 같은
  `LLM_CONCURRENCY` env) — 단 threading 은 novelty 가 available_at 순서·prior 카운트에 의존해
  **직렬** 유지다

재무(financial)는 canonical 스텝이 아직 없어 정제 페이즈에서 제외한다(raw-only). 앞 페이즈가
partial/실패면 다음으로 넘어가지 않아 오염된 raw 위에 canonical 을 쌓지 않는다.

**analyze 페이즈는 없다(ALPHA-806).** 이 SFN 의 책임은 feature 까지다. 설명은 분봉 트리거
큐를 소비하는 **상주 서비스**(`minute_services.tf` 의 `analysis-consumer`)만 만든다 — 트리거
없이 도는 일 단위 팬아웃은 확정 일봉을 기다려야 해서 장중엔 층을 못 세웠고(`layer_route=미상`),
같은 대상에 분봉 경로와 다른 답을 냈다.

수동 재실행은 **트리거 단건 재처리**다: `analysis-consumer` task-def 를 `aws ecs run-task` 로
띄워 Command 를 `["--trigger-id","<분봉 트리거 id>"]` 로 덮는다. `--trade-date` 단독 실행 경로는
없다 — 트리거 행이 대상·거래일의 정본이다.

> ※ task-def 는 시크릿 세트 단위로 만든다(`tasks.tf` 의 `secret_sets` 맵에 키를 넣으면 자동 생성) —
> 현재 9개: `fmp`·`bigkinds`·`kis`·`dart`·`krx`·`deepseek`·`rds`·`events`(LLM+DB)·`rds_dart`(DB+DART).
> 전부 같은 이미지를
> 쓰고 command override 로 스텝을 고른다. 스케줄러 현황(레인별 ENABLED 시각)은
> infra/terraform/README.md 가 정본이다 — 시장 15:40·뉴스 00:10/08:10·공시 18:10(ALPHA-987)·
> 장중 수급 5슬롯 전부 ENABLED 다.

수동 실행·백필은 `plan-run`(Planner) 경유가 계약이다 — 그 실행이 자기 슬롯으로 원장에 남아
관측된다. `aws stepfunctions start-execution` 직접 시작은 pipeline_run/expected_task 가 없는
**무원장 실행**이라 Reconciler 대조 밖이다(신규 배선의 최초 검증처럼 원장이 아직 없는 경우가
아니면 쓰지 마라).

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
  **전부 보존**한다(정정·정체성 판정 안 함). 단 한 순회 안의 **완전히 같은 행**은 소스가 접는다
  (페이지 이동 중복) — `list_rows_seen` 과 raw 행 수가 다를 수 있고 그 차이는 유실이 아니다. 재무제표(`fnlttSinglAcnt`, `dataset=financial_statements`)와
  **다른 API**다 — 공시는 개별 공시서류(공급계약·사업부문 등)를 다룬다. 메타 행은 `part-*.ndjson`
  에, 공시서류 원본 본문(document.xml)은 ndjson 에 못 섞는 바이너리(euc-kr HTML ZIP)라 같은 파티션
  아래 **`documents/{rcept_no}.zip` 로 받은 ZIP 을 무변형 저장**하고, 메타 행의 `document_raw_path`
  가 그 객체를 가리킨다(메타↔본문 링크). ⚠️ **메타는 유니버스 행 전량이지만 본문은 대상 유형만**
  이다(ALPHA-865) — 유형은 행마다 실리는 `is_target` 플래그이고, 비대상 행은 `document_raw_path`·
  `body_format` 이 명시적 None 이다(키 부재가 아니다). 예외 하나: `rcept_no` 가 결측·비문자열인
  행은 유형과 무관하게 뺀다 — 본문 객체 키도 canonical 병합 정체성도 rcept_no 라 **보존해도
  영영 못 쓰는 행**이고, 조용히 버리지 않고 `rows_dropped_malformed` 로 센다. 그래서 이 데이터셋은
  `records_saved`(보존 전량)와 `ops.records_out`(대상 건수 = `records_saved_target`)이 **의도적으로
  다른 첫 로그**다 — 유실(`failed_records`)이 대상 스코프라 산출도 같은 스코프여야 한다(아래 ops
  봉투의 스코프 규칙). ⚠️ **그 키는 자기 run_id 파티션이 아닐 수 있다**
  (ALPHA-720): 같은 수집일(UTC 기준 ±1일)에 이미 받아 둔 본문은 다시 내려받지 않고 **기존 키를
  가리킨다** — 증분 커서가 없어 매 실행이 날짜창 전체를 재독하므로, 장치가 없으면 하루 여러 번
  도는 레인이 같은 ZIP 을 슬롯 수만큼 받는다. 메타는 그래도 **전건 저장**한다(창 전체 관측이
  완전성 근거다 — 접으면 런 사이 rcept_no 집합 비교가 성립하지 않는다).
  ⚠️ 1분 레인(ALPHA-875)이 붙었던 동안은 그 "슬롯 수"가 하루 10 → **720 window** 라 이 존의
  하루 메타량이 ~70배였다(본문은 seen-map 이 막았다). **987 이 저녁 배치(하루 1런)로 되돌려
  지금은 그 증가가 멈췄고**, 8/10~8/26 에 쌓인 720-window 파티션만 과거 구간에 남아 있다.
  대가를 알고 택했던 형상이고(완전성 근거를 스스로
  없앨 수 없다) `raw_keys` 없이 부르는 배치 경로(`normalize-disclosure` 풀스캔·백필)는 그
  커진 존을 훑게 된다. 재사용 건수는
  collection_log 의 `documents_reused` 로 드러나고, 본문 fetch 가 실패한 건은 객체가 없어
  다음 실행이 자동 재시도한다. list.json 이 안 주는 `source_url` 은 rcept_no 로 구성해
  붙인다. 정체성 병합·정정 판정·corp_code↔ticker bridge 는 후속 canonical 소관.
- **raw(ETF 구성종목)** — `raw/source={fmp|krx}/dataset=etf_holdings/market={US|KR}/ingest_date=…/run_id=…/`
  에 run_id 별 append. **가격·재무와 동형(bronze 통일)** — ETF holdings 는 스냅샷이라 매 실행이 현재
  구성종목 전량을 주고, 받은 행을 수집일 기준으로 **전부 보존**한다(정정·정체성 판정 안 함). 단 한 순회 안의 **완전히 같은 행**은 소스가 접는다
  (페이지 이동 중복) — `list_rows_seen` 과 raw 행 수가 다를 수 있고 그 차이는 유실이 아니다. 수집 대상은
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
  ⚠️ 괴리율 `dprt` 는 **퍼센트**다(실측 069500·2026-07-25: `stck_prpr/nav − 1` = **0.00114115**,
  ×100 = 0.11411 → 반올림 0.11 = `dprt`. 비율 가설이면 0.00 이라 안 맞는다. 교차 근거로
  `nav_vrss_prpr` 121.24 = `stck_prpr − nav`). 분석엔진 `sql_surface` 의 `v_nav.premium` 은
  **비율**이라 단위가 갈린다 — canonical 이 같은 이름을 쓰면 두 표면을 조인하는 쪽이 100배
  틀린 괴리를 본다. 그래서 canonical 레코드는 **`premium_pct`** 로 단위를 이름에 담는다
  (ALPHA-851 — `minute/inav_collect.record_of`: `unit_id`·`ts`·`nav`·`market_price`·
  `premium_pct`, 거기에 Worker 가 `source` 를 얹는다). `fetched_at` 는 **싣지 않는다** —
  canonical artifact 의 checksum 이 곧 세대 identity 라 실행 시각이 섞이면 값이 같은
  재실행마다 checksum 이 달라져 `ArtifactImmutabilityError` 가 난다(raw 는 반대로 붙인다).
  키는 `canonical/market_data/etf_inav_minute/market=KR/session_date=…/window=HHMM/
  generation=…/inav.ndjson` 이다. 쓰는 주체는 상주 iNAV Worker 다(ALPHA-851·882 —
  `run inav-worker`, 아래 "상주 iNAV Worker" 절).
  이 스텝은 **산출물이 로그**다(raw 는 무변형 보존이라 판단 재료가 로그뿐이다). 그래서
  로그 사전이 곧 계약이다 — ETF 마다 다음이 나온다:

  | 줄 | 레벨 | 뜻 |
  |---|---|---|
  | `벤더 지연` | INFO / **장중에 창 폭 초과면 WARN** | 수신시각 − 최신 `bsop_hour`. `구간=개장전\|장중\|마감후` 를 함께 본다 — 마감 후엔 창 폭의 십수 배가 정상이다 |
  | `개장 전 라벨` | INFO | 창이 개장 이전으로 뻗었다. **전일 값인지 미실측** — 오류가 아니라 관측이다 |
  | `시각 라벨 형식 이탈` | WARN | HHMMSS 6자리를 벗어난 행. 최신 판정에서 뺐다 |
  | `라벨 범위가 창 폭을 넘는다` | WARN | 한 창의 행이 아니다. ⚠️ **혼재만 잡는다** — 전일이 통째로 반복되면 범위가 정상이라 못 문다 |
  | `응답 행 수가 계약과 다르다` | WARN | 창 수치가 계약 행수(30) 기준이라 실제와 어긋난다 |
  | `표본 간격이 요청과 다르다` | WARN | 벤더가 `FID_HOUR_CLS_CODE` 를 무시했다. raw 의 `interval_sec` 이 거짓이 된다 |
  | `괴리 단위 드리프트 의심` | WARN | `dprt` 가 퍼센트 가설을 벗어났다. **그 종목 표본 한정** |
  | `괴리 단위 표본 부족` / `대조 불가` | WARN | 대조에 쓴 행이 모자라거나 0건. 빠진 필드명을 함께 남긴다 |

  지연이 창 폭에 가까우면 이 API 로 장중 실시간이 성립하지 않는다 — 1분 레인 편입 설계가
  그 수치에 걸려 있다. 단위 가드는 어긋날 때만 경고한다(정상은 조용 — 확정된 사실을 폴링마다
  되풀이하면 진짜 신호가 묻힌다). ⚠️ 허용 오차는 **못 조인다**: `dprt` 2자리 표기가 반올림인지
  절사인지 미실측이고, 절사면 조인 순간 정상 표본의 40%가 드리프트로 잡힌다.
  ⚠️ **지연의 부호로 전일 오염을 판정하지 않는다.** 응답에 날짜가 없어 이 콜만으로는 불가능
  하고 부호는 양방향으로 틀린다 — 라벨이 구간 끝이면 최신 행이 정상적으로 미래라 음수가
  오탐이고, 15:30 이후 실행에서는 전일 잔값이 **양수**로 위장한다(하필 창 폭과 비슷해 "실시간
  불가"의 강한 증거처럼 읽힌다). 로그는 관측한 사실만 남긴다.
- **raw(투자자 수급)** — 확정과 추정을 **다른 dataset 으로 가른다**(iNAV↔NAV 와 같은 이유):
  - EOD 확정 — `raw/source=kis/dataset=investor_flow_daily/market=KR/ingest_date=…/run_id=…/`
    (ALPHA-482). 거래일 grain 확정 순매수. 자연키 날짜는 행의 `stck_bsop_date` 다.
  - 장중 추정 — `raw/source=kis/dataset=investor_flow_intraday/market=KR/ingest_date=…/run_id=…/`
    (ALPHA-767). 그날 슬롯 grain 가집계(`*_fake_*`) 추정. **응답에 날짜 필드가 없어**
    `asof_date`(수집 시각의 KST 날짜)를 provenance 로 부착하는 것이 필수다 — 없으면 canonical
    이 어느 거래일 스냅샷인지 복원할 수 없다(KRX holdings 의 `trd_dd` 와 같은 형태).
    각 행에 `our_ticker`·`market`·`kis_symbol`·`asof_date`·`fetched_at` 를 붙인다.
    슬롯 응답이 그날 것을 누적해 오므로 슬롯 간 중복은 **정상**이고 정리는 canonical 소관이다.
- **canonical(장중 투자자 추정, 정제 Step2)** — `canonical/market_data/investor_flow_intraday/
  market=…/trade_date=…/part-*.parquet` 에 게이트 통과 행을 **(market,ticker,trade_date,asof_slot)
  키로 멱등 병합**(ALPHA-768). EOD 확정(`investor_flow_daily`)과 파티션 축은 같지만 **행 키가
  한 축 많다** — 하루 4~5 슬롯이 한 종목·한 날짜에 공존하므로 ticker 단독으로 병합하면 마지막
  슬롯이 앞을 덮어 장중 추이가 사라진다. 거래일은 raw 의 `asof_date`(수집이 붙인 provenance)가
  주고, 같은 슬롯 재관측은 최신 fetched_at 이 이긴다.
  `normalize-investor-estimate`는 매 실행
  `operations_archive/canonical_run_manifests/dataset=investor_flow_intraday/run_id=…/manifest.json`
  에 직접 parquet 키·그 바이트의 SHA-256과 이번 실행에서 게이트를 통과한 모든
  `(ticker,asof_slot)` winner를 기록한다(ALPHA-1035). 값이 바뀐 행만이 아니라 같은 값
  재확정도 포함하고 논리 키는 중복 제거한다. SHA-256은 뒤 normalize가 같은 canonical 키를
  덮어써도 consumer가 앞 run_id에 뒤 run 값을 붙이지 않게 fail-closed 하는 근거다. 행
  실패는 다른 winner의 canonical·manifest 기록을 막지 않지만 quality log와 exit 2에 남으며,
  raw 목록/읽기 또는 canonical·quality·manifest 저장 실패는 exit 1과
  `canonical_written=false`로 fail-closed 한다. `load-investor-intraday` 정상 경로(ALPHA-1036)는
  같은 run의 manifest를 직접 GET해 명시된 parquet key와 winner만 적재하고 SHA-256이 producer가
  확정한 바이트와 같은지 검증한다. 누적 parquet의
  과거 행은 `physical_rows_read`, 현재 winner는 `logical_rows_read`로 분리한다. manifest가 없거나
  손상됐거나 hash가 달라졌거나 winner가 canonical에 없으면 LIST·전체 스캔으로 넓히지 않고
  exit 1로 실패한다.
  개별 DB 행 오류는 savepoint로 격리해 다른 winner를 commit하되 quality와 exit 2에 남긴다.
  정제 exit 2도 성공 winner 적재까지 진행하지만 장중 수급 SFN의 최종 상태는 Failed로 닫힌다.
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
  최신 스냅샷(KR) **중 유니버스 뿌리(`krx_etf.source.etf_map`) 안 ETF 의 구성종목** 종목명
  인덱스로 제목+리드에서 substring 탐지해 합성한다(구 raw 의
  `our_ticker` provenance 와 union — 이행기 호환). 이름 비교는 **NFKC 정규화 후 substring**
  (인덱스·기사 텍스트 양쪽 — 저장소 관례). **동명이(같은 이름, 다른 ticker)는 어느 쪽도 고르지
  않고 인덱스에서 뺀다**(ALPHA-448) — 이름을 키로 덮어쓰면 parquet 나열 순서가 승자를 정해
  mention 이 비결정적으로 틀린다. 유니버스가 바뀌면 전체 백필 재정규화로 과거 기사에
  소급되고, 탐지 계측(`detected_name_counts`)·제외된 동명이(`mention_index_ambiguous_names`)·
  인덱스 상태는 quality_log 에 남는다.
  FMP 는 ingest 병합 mentions[] 그대로(영문 기사라 한글 이름 탐지 무의미).
  `lead_text` 는 벤더 리드(BigKinds `CONTENT` 200~256자 스니펫·FMP `text`)를 자르지 않고 통과시킨
  것으로, 태깅 입력이다(결측은 NULL — 게이트 대상 아님. **공백뿐이어도 NULL 로 접는다**,
  ALPHA-860 — 그래서 canonical `lead_text` 는 결코 빈 문자열이 아니다). 본문 전문 크롤은 범위 밖이다.
  `normalize-news` 는 현재 실행이 실제로 쓴 파티션의 `language`·`published_date`·직접 parquet 키와
  그 파티션에서 이번 입력이 통과시킨 `article_id`만
  `operations_archive/canonical_run_manifests/dataset=news_articles/run_id=…/manifest.json` 에
  남긴다(ALPHA-1030). 같은 run 재시도는 먼저 `canonical_written=false`로 이전 완료 표식을
  무효화하고, canonical과 quality log가 모두 성공한 뒤에만 `true`로 교체한다. 입력 0건은 빈
  `canonical_partitions`를 가진 유효 manifest다. `LoadDocuments`는 이 직접 키와 현재 논리 ID만
  소비하며(ALPHA-1031), `TagNews`도 같은 범위를 소비한다(ALPHA-1032).
- **feature(뉴스 assertion, 태깅 Step3)** — `feature/news/assertions/language=ko/published_date=…/part-*.parquet`
  에 태깅 결과를 **article_id 키로 멱등 병합**(입력 canonical 과 같은 파티션 축이라 한 canonical
  파티션이 한 feature 파티션에 대응 — 날짜창 프루닝이 곧 비용 통제).
  **이 파티션에는 writer 가 둘이다(ALPHA-900)** — 배치 `tag-news` 가 part 파일을 통째로 되쓰고,
  1분 뉴스 Consumer 가 같은 파티션 아래 `minute/{article_id}.{input_fingerprint}.parquet` 로
  기사당 미러를 남긴다. 그래야 두 레인의 LLM 장부가 서로를 본다 — 그 전에는 배치가 이 날짜축만,
  장중이 job 축(`feature/news_extraction/job=…`)만 보고 있어 **같은 기사를 반드시 두 번 태웠다**.
  미러 구역은 **다음 배치 런까지의 임시 자리**다: `tag-news` 가 읽어 part 파일에 병합한 뒤
  지우고(`minute_mirrors_absorbed` 로 계측), **소비자(`load-assertions`)는 흡수 전 미러를 아예
  안 읽는다**(`minute_mirrors_unabsorbed` — 유실이 아니라 대기다). 흡수 전 조각을 바로 읽으면
  `tag-news` 가 거는 mentions 게이트를 통째로 우회하기 때문이다: 배치는 유니버스 무관 기사를
  일부러 태깅에서 빼는데(ALPHA-416) 1분 레인에는 아직 그 게이트가 없어(ALPHA-690) 그런 기사의
  미러가 오고, `load-assertions` 는 mentions 를 안 본다. 그래서 `tag-news` 가 흡수 시점에
  거르고(`minute_mirrors_dropped_no_mention`), 소비는 흡수된 part 파일만 본다. 지연은 없다 —
  SFN 이 `TagNews` 뒤에 `LoadAssertions` 를 돌리므로 같은 런에서 흡수분이 실린다.
  ⚠️ 그래서 **흡수는 canonical 이 없는 날짜에도 닿아야 한다** — 기사 정본은 PG 이고 canonical
  은 다음 `normalize-news` 에 오므로 장중만 본 기사의 발행일이 아직 canonical 에 없을 수 있다.
  `tag-news`는 manifest 날짜와 KST 전일·당일의 정확한 미러 prefix를 함께 조회한다.
  ⚠️ canonical 에 **아예 없는** 기사(장중만 본 기사)의 미러는 거르지 않는다 — mentions 를 판정할
  근거가 없는 것이지 무관한 게 아니다. 미러 키에 입력 지문이 들어가는
  것은 정정 때문이다 — `article_id` 만 쓰면 배치가 읽고 지우는 사이의 정정 판정이 같은 키를
  덮은 뒤 곧바로 삭제된다. 오래된 backfill 미러는 명시적 날짜 또는 `--all` 복구가 정리한다.
  **canonical 이 아니라 feature
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
  KRX `SECUGRP_ID/MKT_ID`의 실측 조합은 `constituent_asset_type`(`EQUITY|CASH|OPTION|UNKNOWN`)
  으로 보존한다. holdings·instrument 적재기는 주식만 적재하고 현금·옵션은
  `skipped_unsupported_asset`과 유형별 수로
  계측하되 유실에는 넣지 않는다. 미지 유형은 `skipped_unknown_asset_type`으로 유실에 남긴다
  (ALPHA-1017). `load-etf-holdings`는 정상 제외 합계를 `ops.unsupported_records`에도 남겨 실행
  이력에서 적재·지원 제외·유실을 분리한다(ALPHA-1020).
  파티션을 갱신할 때는 기존 직접 자식 `part-*.parquet`와 새 행을 합쳐 `part-00000.parquet`로
  수렴시킨 뒤 나머지 직접 자식 part만 지운다. 중첩 보관 객체와 raw 입력은 삭제하지 않는다.
  🔴 **이 파티션의 etf_id 집합은 분석 유니버스가 아니다** — 파티션은 지워지지 않아 config 에서 뺀
  ETF 의 옛 행이 남고, 참조 계열(명부만 필요한 ETF)도 섞여 들어온다. 읽는 쪽은 유니버스 뿌리
  (`krx_etf.source.etf_map` 키)로 한 번 거른다 — `ingest-price-raw`·`load-etf-holdings`·
  `load-price-triggers`·`normalize-news`·`load-instruments` 다섯이 같은 정본
  (`_krx_expected_etfs`)을 `expected_etfs` 인자로 받는다. 안 거르면 마스터에 없는 ETF 가 매 런
  `failed_records` 로 잡혀 원장이 **영구 INCOMPLETE** 가 된다.
  `normalize-etf` 는 실제로 갱신한 파티션을
  `operations_archive/canonical_run_manifests/dataset=etf_holdings/run_id=…/manifest.json` 에도
  남긴다. 정규 `load-etf-holdings --input-run-id <normalize-run>` 은 이 직접 키를 GET 해 그
  파티션만 읽는다 — 과거 전체 스캔은 `--all`, 과거 일부 복구는 `--input-run-id` 또는
  `--from/--to` 를 명시한 운영 경로뿐이다. 범위 없는 호출은 거부한다(ALPHA-1011).
- **품질 로그(정제 Step2)** — `operations_archive/data_quality_logs/dataset=…/checked_date=…/run_id=…/log.json`
  에 검증 실행당 1건. 몇 건 읽고/통과/탈락·canonical 적재했는지와 **탈락 사유**(OHLCV 정합성 위반·결측·
  비수치 등)·벤더 교차 충돌을 남긴다 — 잘못된 가격을 조용히 버리지 않는다(Rule 12). 뉴스(`dataset=
  news_articles`)도 같은 규약으로 남기되 blocking 탈락 사유(제목 결측·발행시각 파싱 불가/범위 밖)와
  non-blocking 경고(url·publisher 결측)를 구분하고, canonical 적재 결과·근접중복 신호(duplicate_signals)를
  함께 기록한다. canonical 은 멱등이라 run_id 가 없지만,
  '이 검증 실행이 무엇을 걸렀나'는 실행 단위 감사라 run_id 로 가른다(수집 로그와 분리).
- 백엔드는 `[storage]` 설정으로 고른다. 기본 `local`(루트 `./.lake`), 배포는
  `DATA_PIPELINE_STORAGE__BACKEND=s3` + `DATA_PIPELINE_STORAGE__BUCKET=…` 로 전환.

## 백필 — 포워드와 격리된 재구축 경로

포워드(`steps/ingest_*`)는 매일 도는 프로덕션이고, 백필은 과거를 다시 쌓는 일이다. 둘을 섞으면
**롤백이 불가능해진다** — 어느 파티션이 어느 경로에서 왔는지 사후에 가릴 수 없기 때문이다.
그래서 `backfill/` 패키지는 진입점부터 갈라져 있고(`data_pipeline.backfill.run`), 쓰기 좌표
셋으로 격리한다.

| 좌표 | 백필 | 포워드 |
|---|---|---|
| `source=` | `dartlab` | `dart` |
| `run_id=` | `backfill-dartlab-financial-<YYYYMMDD>` | `<job>-<stamp>` |
| 접두사 | `draft/`(`--draft`) — 승격 전 기본 | 없음 |

**롤백은 `run_id` 파티션 삭제**이고, 승격은 접두사 이동이다. 셋 중 하나만으로도 파티션이
겹치지 않지만 셋을 다 쓴다 — 격리 실패의 대가가 크고, 좌표 하나는 설정 실수로 뚫린다.

```bash
py -m data_pipeline.backfill.run financial --bucket edge-dev-pipeline-lake --draft --limit 20
py -m data_pipeline.backfill.run financial --bucket edge-dev-pipeline-lake --draft   # 전 종목
py -m data_pipeline.backfill.run verify   --bucket edge-dev-pipeline-lake --draft
```

**데이터가 전소해도 다시 쌓을 수 있어야 한다.** 그 조건은 외부 입력이 전부 재접근 가능하고
로컬 상태에 의존하지 않는 것이다. 이 백필의 외부 입력은 하나(HuggingFace 공개 데이터셋)이며,
종목 유니버스조차 그 데이터셋의 파일 목록에서 얻는다(로컬 종목 마스터를 읽지 않는다).
매니페스트도 레이크에 쓴다 — 로컬 디스크에 두면 그것이 전소했을 때 재개가 불가능하다.

- **raw(재무제표 백필)** — `raw/source=dartlab/dataset=financial_statements/market=KR/ingest_date=…/run_id=…/part-<ticker>.ndjson`.
  포워드(`source=dart`)가 쓰는 `fnlttSinglAcnt`(**주요계정만**)와 달리 전체 재무제표 27열을
  무변형으로 낸다 — 주요계정에는 매출액·매출원가·판관비가 없어 원가구조·영업레버리지를
  계산할 수 없다. provenance 5열(`our_ticker`·`market`·`fetched_at`·`backfill_source`·
  `backfill_oid`)만 부착한다.
- **매니페스트** — `operations_archive/backfill_manifests/source=…/dataset=…/run_id=…/manifest.json`
  에 항목별 `{key, rows, sha256, bytes}`. `verify` 가 이것으로 레이크를 재대조하므로
  **적재 후 조작·유실이 드러난다**. 재개는 이 매니페스트를 읽어 이미 받은 항목을 건너뛴다.

**이 입력은 PIT 가 아니다.** dartlab 데이터셋은 `(bsns_year, reprt_code)` 조합마다 `rcept_no`
가 하나뿐이다 — 정정공시 이력이 없고 **최종 확정치만** 있다(2016년 이후). OpenDART 재무 API
자체가 정정 전 수치를 지목할 파라미터를 주지 않으므로 벤더 문제가 아니다. 접수번호 앞 8자리로
"언제 처음 공개됐나"는 근사할 수 있지만, 사후 정정된 값을 그 시점 값으로 쓰면 조용히 미래를
본다. 진짜 PIT 는 `list.json`(정정 열거) + `document.xml`(rcept_no 원본) 파싱이 필요하고
별 `source` 로 추가할 자리다(후속).

## 운영 원장 — expected_task·Planner·Reconciler (ALPHA-530)

SFN/ECS 실행을 **사후 복구 가능하게 관측**하는 Postgres projection(`ops_*` 5테이블,
`migrations-cloud`). 실행을 **제어하지 않는다**(관측만 — ADR-0030). 답하는 질문: *원래 실행돼야
했지만 아예 시작되지 않은 작업은 무엇인가.* 코드: `src/data_pipeline/ops/`.

- **상태 4축을 섞지 않는다**: plan_status(DUE·SKIPPED) / task_outcome(PENDING·FULFILLED·FAILED·
  BLOCKED·MISSED) / attempt.execution_status(RUNNING·SUCCEEDED·FAILED·TIMED_OUT) /
  data_status(UNKNOWN·VALID·VALID_EMPTY·INCOMPLETE·INVALID). STALLED 는 저장 상태가 아니라
  RUNNING+시간초과로 파생하는 health(이슈로만 남김).
- **Task Catalog**(`ops/catalog.py`) — 논리 작업의 안정적 ID·정적 의존 SSOT. **등록 30작업 =
  시장 레인(`etf-daily`) 17 + 뉴스 레인(`news`) 6 + 공시 레인(`disclosure`) 4 + 장중 수급 레인
  (`investor-intraday`) 3**(ALPHA-724 가 공시 4작업의 소유 레인을 옮겼고 — 총계 불변 —
  ALPHA-769 가 장중 수급 3작업을 **신설**했다: 시장 SFN 이 돈 적 없는 스텝이라 이쪽은 총계가
  늘어난다. 30 → 26 은 ALPHA-875 가 그 공시 4작업을 SFN 원장 밖 1분 세션으로 보낸 몫이었고
  **26 → 30 은 ALPHA-987 이 저녁 배치로 되돌린 복원**이다)(ECS Task state 35개 중 — **정의 파일**
  기준으로 `statemachine.tf` 33 + `news_pipeline.tf` 2 다. 공시·장중 수급 .tf 는 state 를
  새로 정의하지 않고 부분집합 필터로 재사용하므로 저 33 안에 있다 — 레인별 계수는
  `pipeline_type` 축을 써라. 36→35 는 ALPHA-806 이 AnalyzeOne 을 걷어낸 몫이다.
  ALPHA-181 → 578 → 553 PR2 → 591 → 769 → 806 → 875 → 987).
  레인은 `CatalogEntry.pipeline_type` 축이고
  Planner 가 `entries(pipeline_type)` 로 자기 레인만 계획한다 — 섞으면 상대 레인 작업이 매 런
  MISSED 다. 뉴스 6작업의 직렬 2개는 state 이름이 뉴스 SFN 의 것(`NewsLoadAssertions`·
  `NewsAssembleEvents`)이고 depends_on 도 뉴스 SFN 게이트 축으로 그렸다. 제외 5개는 ① `fmp` 수집
  4개(**FMP 공용키 bandwidth 한도 소진**으로 SFN 토글 `us_fmp_enabled` 를 껐다 — 안 도는 스텝을
  등록하면 매 런 MISSED, 한도 회복·토글 on 과 함께 등록, ALPHA-558) ② `CollectDartFinancial`
  (**하류 소비자 0** — `financial_statements` 를 읽는 정제·적재·분석이 없어, 등록하면 대응할
  이유 없는 실패 경보가 된다). 공시 체인 4개(`CollectDartDisclosure`·`NormalizeDisclosure`·
  `NormalizeDisclosureSegment`·`LoadDisclosure`)는 875 가 여기 제외로 두었다가 **987 이 저녁
  배치로 되돌리며 재등록됐다**. `AnalyzeOne` 은 제외가 아니라 **state 자체가 없다**(ALPHA-806 이
  analyze 페이즈를 걷었다 — 36→35). **KRX ETF·DART 공시는 ALPHA-596 이 직접 계측으로 올렸다** — `tasks.tf` 가 두
  task-def 에 DB env 를 주면서, 컨테이너 종료 즉시 판정되고 그전엔 못 얻던 `records_out`·
  `failed_records`·`data_status` 가 함께 올라온다("벤더 컨테이너에 RDS 접속을 주는 신뢰경계
  변경"이라는 전제는 실측 결과 이미 무너져 있었다: 실행 역할·보안그룹이 task-def 전체 공유라
  IAM·네트워크는 그전에도 열려 있었고, `kis` 가 벤더 컨테이너면서 DB password 를 받는 반례).
  ⚠️ **배선이 먼저, 플래그 해제가 나중** — 이미지 CD 와 terraform apply 가 독립 워크플로라
  플래그가 먼저 뜨면 Reconciler 가 영구 거짓 LEDGER_GAP 을 연다(ALPHA-596 은 PR 을 둘로 쪼갰고,
  ALPHA-610 도 #379→후속으로 같은 순서를 밟았다 — 중간 상태는 `_WIRING_AHEAD_OF_FLAG` 유예가
  덮고, 그 유예는 플래그가 올라가는 순간 스스로 실패해 제거를 강제한다).
  **TagNews 도 ALPHA-610 이 올려 `instrumented=False` 는 이제 0개다** — 등록 30작업이 전부 자기
  원장을 직접 쓴다(장중 수급 3작업도 `kis`·`bigkinds`·`rds` task-def 를 재사용해 DB env 를 그대로 받는다). 그래서 attempt 결측은 더는 정상이 아니라 `LEDGER_GAP` 이고, 그 스텝이
  기사별 LLM 실패를 격리해 exit 0 으로 끝나도 `failed_records` 가 `data_status=INCOMPLETE` 로
  올라온다(07-27 940/940 전건 실패가 초록으로 보였던 그 경로 — ALPHA-589 는 스텝이 스스로 exit 1
  을 내는 별건이다). 수집 커버리지는 `Collect*` 13개 중 8개 — 시장 9개 중 5개 + 뉴스 2개 중
  1개(BigKinds) + 공시 1개 중 1개(DART — 987 저녁 배치 복귀) + 장중 수급 1개 중 1개다.
  근거 표는 `ops/catalog.py` docstring, CI 는 `test_ops_catalog` 가 양방향으로 잠근다 —
  `instrumented=True`↔`tasks.tf` DB env 배선 대조 포함(어긋나면 그 작업이 조용히 계측 없이 돈다).
  MVP 3작업(ALPHA-530)이었던 것:
  `PRICE_COLLECTION_KIS`·`NORMALIZE_PRICE`·`LOAD_PRICE_DAILY`(정제→feature 게이트 직후 첫 price
  canonical consumer). 종목 반복은 작업이 아니라 completeness/manifest, 개별 규칙은 quality_check.
- **`ops` 로그 봉투**(ALPHA-181) — 모든 스텝이 자기 로그(collection_log·quality_log)에
  `"ops": {"records_out": N, "failed_records": M}` 를 남긴다. ETF holdings 적재는 선택적 저장
  신호 `unsupported_records`도 낸다. 로그의 `ops_attempt_id`가 현재 원장 시도와 일치할 때만
  저장해 같은 `run_id` 재시도의 옛 로그를 최신 건수로 오인하지 않는다. 관측(`ops/entry.py:_observe_from_log`)은
  **이 봉투만** 읽으므로 task_key 별 분기가 없다 — 새 작업을 카탈로그에 등록해도 리더를 안 고친다.
  봉투가 스텝 안에 사는 이유: 어느 카운터가 유실인지는 스텝만 안다(적재의
  `skipped_unknown_etf`·`skipped_unknown_instrument` 는 유실, `skipped_self`·
  `skipped_foreign_etf`(유니버스 뿌리 밖 ETF 의 행)·`gated_out`·`already_tagged` 는 정상 동작).
  ⚠️ **스코프 규칙** — 산출과 유실은 *이 런이 재판정한 범위*에서 함께 온다. 재판정 없이 건너뛴
  항목은 산출로도 유실로도 세지 않는다(세면 옛 실패가 산출로 뒤집힌다). 그래서 매 런 입력을 다시
  읽고 다시 거르는 스텝(수집·정제·적재)은 기존 행도 산출로 세지만, 처리분을 건너뛰는 스텝
  (`tag-news`·`assemble-events`·`enrich-corp-code`·`load-price-triggers`)은 no-op 재실행이 0건 →
  `UNKNOWN` 이다. 상태 기반 완전성("지금 이 데이터셋이 온전한가")은 completeness 축 소관(ALPHA-490).
  봉투가 없거나 두 키 중 하나라도 결측이면 리더는 낙관값으로 메우지 않고 warning + `UNKNOWN`(Rule 12).
  `LOAD_ASSERTIONS`만 저장 전용 선택 pair
  `entity_resolution_arguments_total`·`entity_resolution_arguments_resolved`를 함께 낸다. 분모는
  실체 역할 argument, 분자는 ticker·명부·채번으로 실제 접지된 `resolved_any`다. observer는
  비율을 재계산하지 않고 두 원시 카운터만 그대로 전달한다. wrapper가 실행 중에 주입한
  `ops_attempt_id`가 현재 attempt와 일치할 때만 pair를 승인하므로, 같은 run의 겹친 재시도가 공유
  로그를 덮어써도 다른 시도의 값으로 오인하지 않는다.
- **ETF 수집 완전성**(ALPHA-611) — `NAV_COLLECTION_KIS`·`ETF_PROFILE_COLLECTION_KIS`·
  `ETF_HOLDINGS_COLLECTION_KRX` 세 작업은 Planner가 실행 전에
  `krx_etf.source.etf_map`의 key(our_etf_id)를 기대 snapshot으로 고정하고, 공통 수집 스텝이
  `ops.received_count`로 실제 unique ETF 수를 낸다. Wrapper는 원장의 기대값만 분모로 사용해
  `{expected, received, missing}`을 `expected_task.completeness`에 저장한다.
  따라서 현재 종목 수를 코드에 하드코딩하지 않으며, 수집기가 기대값까지 줄여 신고해 스스로
  만점 처리할 수 없다.
  이 선택 필드가 없는 나머지 작업은 기존처럼 완전성 미확인 `UNKNOWN`이다.
- **Dataset Contract / freshness 첫 슬라이스**(ADR-0043, ALPHA-654) —
  `ETF_HOLDINGS_COLLECTION_KRX`는 Catalog가 별도 typed registry의
  `ETF_HOLDINGS_KRX_EOD` 계약 key만 참조한다. Planner는 계약 version·정책·해석한
  `LATEST_KR_TRADING_DAY`를 snapshot하고, 기존 `expected_as_of_date`에 그 거래일을 저장한다.
  KRX 응답에는 요청한 `trdDd`와 독립적인 actual-as-of evidence가 없으므로 wrapper는 현재 시도의
  raw 산출물과 수집 로그가 실제로 관측됐을 때도 `actual_as_of_date=NULL`,
  `freshness_status=UNKNOWN`, reason=`ACTUAL_AS_OF_UNVERIFIED`를 기록한다. 이때
  `collected_at`만 채우고 Monitor 평가 시각인 `observed_at`은 NULL로 남긴다. 계약 연결 작업은
  **매 시도**(예외 종료 포함) freshness를 덮는다 — 산출물을 관측하지 못한 재시도는
  `collected_at=NULL`·reason=`EVIDENCE_MISSING`으로 리셋해, 같은 raw 키를 덮어쓴 재시도에 앞
  시도의 수집 증거가 남지 않게 한다(카운터와 같은 규칙). 계약 미연결 작업의
  freshness NULL은 `UNKNOWN`이 아니라 `NOT_APPLICABLE`이다.
  `NAV_COLLECTION_KIS`는 `ETF_NAV_KIS_DAILY` 계약을 참조한다. KIS 응답 원본의
  `stck_bsop_date` 집합 중 최댓값을 `actual_as_of_date`로 쓰며, 질의 종료일·실행일·
  `fetched_at`으로 대체하지 않는다. 유효 날짜가 없으면 `UNKNOWN`을 보존한다.
- **카운터 저장**(ALPHA-182·1020) — 봉투 카운터는 판정 뒤 버리지 않고 `expected_task`의
  `records_out`·`unsupported_records`·`failed_records` 컬럼에 남긴다(운영 대시보드의 건수 열,
  ALPHA-514 — 없으면
  런×작업마다 S3 로그를 뒤져야 한다). **판정 규칙은 그대로다** — 저장 전용이다. 결측·malformed
  (음수·NaN·소수·BIGINT 초과)는 0 이 아니라 **NULL** 이고, 값이 있는데 못 쓰면 경고를 남긴다
  ("신호 없음"이 "0건 처리"로 위장되지 않게, Rule 12). 스코프는 **그 작업의 마지막 시도**다 —
  매 시도가 세 컬럼을 함께 덮고, Reconciler 는 판정을 뒤집어도 건수를 몰라 다시 쓰지 않는다.
  `unsupported_records`는 정상 지원 제외라 `INCOMPLETE`나 유실 합계에 관여하지 않는다.
  그래서 `FAILED` 옆의 건수는 앞 시도의 것일 수 있다.
  `LOAD_ASSERTIONS`의 엔티티 해소 pair는 호환용 task 행과 함께 그 값을 만든 정확한 attempt 행에도
  저장한다(ALPHA-1000·ALPHA-1002). 둘은 성공 exit의 같은 시도에서만 함께 기록되고, 한쪽
  결측·malformed·`resolved > total`·비정상 exit이면 모두 **NULL**로 덮는다. 기존 행은 백필하지
  않았으므로 NULL은 0건이 아니라 계측 전/없음이다.

### 실행 흐름 (스펙 §5)

```
EventBridge(daily·news×2(00:10·08:10)·공시×1(18:10 — ALPHA-987 저녁 배치 복귀)·장중수급×5) → Planner(plan-run) : DB 트랜잭션(pipeline_run+expected_task+snapshot)
                                              → commit → 결정적 execution_name → SFN StartExecution
                                                (레인은 OPS_PIPELINE_TYPE — 자기 레인 카탈로그만 계획)
각 ECS 태스크(30작업) → wrapper instrument : attempt 시작/종료·data_status 관측(원장 장애 시 통과)
EventBridge(reconcile) → Reconciler : SFN/ECS 증거로 예정↔실제 대조(MISSED/BLOCKED/STALLED/…)
```

Planner 는 StartExecution **전에** 원장을 남긴다 — SFN 이 안 떠도 "실행 자체가 안 됐다"를 잡기
위함(ECS 안에서 자기 expected_task 를 만들면 불가능). `ExecutionAlreadyExists` 는 즉시 LAUNCHED
로 보지 않고 DescribeExecution 으로 입력을 비교한다(동일=LAUNCHED, 상이=LAUNCH_CONFLICT).

**슬롯 = 분(ALPHA-564).** 멱등키는 `run_key = <pipeline_type>:<YYYY-MM-DDTHH:MM>`(KST)이고
`pipeline_run_id`·`execution_name` 이 여기서 결정적으로 파생된다. 날짜가 아니라 **시각**인 이유는
`UNIQUE (run_key)` 가 곧 "한 슬롯 1회 계획"이라, 날짜로 두면 하루 여러 번 도는 레인(뉴스
00:10·08:10, iNAV 15분)의 2회차부터가 1회차에 흡수되고 **수동·백필 실행이 원장에 들어올
자리가 없기** 때문이다. 결과:

- **애드혹 실행도 `plan-run` 으로 돌리면 관측된다** — 실행 분이 그 실행의 슬롯이 된다.
  `start-execution` 을 직접 쓰면 원장에 안 남아 그 런은 대조 대상이 아니다.
- 같은 분 재호출은 여전히 run 1개(Planner 재기동 무해). 수동 실행이 스케줄 분에 정확히 걸리면
  그 슬롯으로 **흡수**되고 `created=False` 로 드러난다 — 새로 도는 게 없다는 뜻이니 로그를 보라.
- 키 형식의 출처는 `planner.slot_run_key` **하나**다. Reconciler 의 `_due_slots` 도 그 함수를 쓴다 —
  두 곳에서 조립하면 어긋나는 순간 없는 슬롯을 찾아 **실제 런이 영영 대조되지 않는다**. 같은
  이유로 `OPS_DAILY_SCHED_HHMM`·`OPS_NEWS_SCHED_HHMM`·`OPS_DISCLOSURE_SCHED_HHMM`·
  `OPS_INVESTOR_INTRADAY_SCHED_HHMM` 은 별도 변수가 아니라 terraform 이 각 스케줄 cron 에서
  뽑고, cron 을 KST 로 읽으므로 `schedule_timezone` 은 `Asia/Seoul` 로 강제된다. ⚠️ 공시와
  장중 수급 것만 **스케줄이 ENABLED 일 때만 주입한다**(ALPHA-722·769) — 슬롯 기준은 Reconciler
  에게 "이 시각엔 런이 있어야 한다"는 주장이라, 꺼진 채 넣으면 뜰 리 없는 슬롯을 결측으로
  판정해 **참인** PLANNER_MISSING 을 그날 지난 슬롯마다 연다(공시 1개(18:10)·장중 수급 5개).
  빈 값 = 그 레인 결측 판정 없음이 안전 기본값이다(`entry._lane_sched_hhmms`).
- **주말은 레인마다 다르다**(ALPHA-874) — 뉴스 크론만 주 7일이고 시장·공시(987 저녁 배치, 18:10)·장중 수급은 MON-FRI 다.
  그래서 `OPS_DAILY_SCHED_WEEKEND`·`OPS_NEWS_SCHED_WEEKEND`·`OPS_DISCLOSURE_SCHED_WEEKEND`·
  `OPS_INVESTOR_INTRADAY_SCHED_WEEKEND` 가 HH:MM 과 **같은 cron 의 일·요일 필드**에서 파생돼 함께
  주입된다(`"true"`/`"false"`, `entry._lane_sched_weekend`). 이게 없으면 주말 건너뛰기가 레인 무관
  상수가 되어 어느 쪽이든 틀린다 — 상수 "건너뛴다"면 주 7일 레인의 결측 탐지가 **조용히 0** 이 되고,
  상수 "안 건너뛴다"면 MON-FRI 레인이 매 토·일 거짓 PLANNER_MISSING 을 연다(뜰 런이 없어 `run_present`
  로 영영 RESOLVE 되지 않는다). ⚠️ 이 형제에는 위의 ENABLED 조건이 **없다** — HH:MM 이 빈 값이면
  `_due_slots` 가 그 레인을 먼저 건너뛰므로 플래그만 남아도 무해하다. 미주입이면 `False`(=평일 전용,
  종전 동작)이고, `"true"`/`"false"` 외의 값은 fail-loud 다.
- 주기 Reconciler 는 레인별로 "가장 최근에 슬롯이 지난 **예정일**"의 **그날 지난 스케줄 슬롯 전부**를
  대조한다(ALPHA-591 — 뉴스의 앞 슬롯이 최신 하나에 밀려 영영 미대조되지 않게). 평일 전용 레인이면
  그 예정일이 주말을 건너뛴 직전 평일이다. ⚠️ 수동 슬롯은
  여전히 `OPS_RUN_KEY` 로 지정해야 대조된다 — 지정 없이 초기에 죽은 수동 런은 조용히
  남는다(ALPHA-565).

### 실행 (로컬/수동)

```bash
# Planner — 원장 기록 + SFN 시작. OPS_STATE_MACHINE_ARN·DATA_PIPELINE_DB__* 필수.
OPS_STATE_MACHINE_ARN=arn:aws:states:…:stateMachine:edge-dev-data-pipeline \
  python -m data_pipeline.run plan-run
# Reconciler — 예정↔실제 대조(advisory lock 으로 중복 실행 방지).
python -m data_pipeline.run reconcile
# Outbox Relay(1분 파이프라인, ALPHA-670) — outbox NEW → SQS 발행. 상주(ECS Service)가
# 기본이고 --max-ticks 는 로컬 확인·일회성 배출용이다(그 모드는 **미발행 0건을 확인**해야
# exit 0 — IDLE 은 "지금 집을 게 없다"일 뿐이라 완료 판정에 못 쓴다).
# 큐 매핑은 필수: 빠지면 그 큐의 event 가 전부 DEAD 가 되므로 기동을 거부한다.
# 큐 매핑은 **JSON 한 변수**로 준다 — destination 이름에 하이픈이 있어 nested 형태
# (…__QUEUE_URLS__price-analysis-realtime=)는 셸이 변수 할당으로 파싱하지 못한다.
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_MINUTE_RELAY__QUEUE_URLS='{"price-analysis-realtime":"https://sqs…/price","news-extraction-realtime":"https://sqs…/news","news-extraction-backfill":"https://sqs…/backfill","price-explanation-realtime":"https://sqs…/explain"}' \
  python -m data_pipeline.run relay --max-ticks 5
# DLQ 대사(1분 파이프라인, ALPHA-672) — DLQ 에 도착했는데 DB job 이 non-terminal 이면
# SQS_MAX_RECEIVE 사유로 DEAD 에 CAS 한다. **주기 실행**이고 메시지는 지우지 않는다
# (근거 보존). 원 큐 매핑도 함께 요구한다 — DLQ 자리에 원 큐가 들어가면 정상 배달
# 중인 job 이 전부 DEAD 가 되므로 겹치면 기동을 거부한다. 원 큐 매핑은 relay 어휘
# **4종**(트리거 설명 큐 포함), DLQ 매핑은 **job 큐 3종**을 다 채워야 한다(빠진
# 레인은 아무도 대사하지 않는다 — 트리거 DLQ 는 job 테이블이 없어 대사 대상이
# 아니다, ALPHA-709). 끊긴 대사는 exit 1 이다.
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_MINUTE_RELAY__QUEUE_URLS='{"price-analysis-realtime":"https://sqs…/price","news-extraction-realtime":"https://sqs…/news","news-extraction-backfill":"https://sqs…/backfill","price-explanation-realtime":"https://sqs…/explain"}' \
DATA_PIPELINE_MINUTE_CONSUMER__DLQ_URLS='{"price-analysis-realtime":"https://sqs…/price-dlq","news-extraction-realtime":"https://sqs…/news-dlq","news-extraction-backfill":"https://sqs…/backfill-dlq"}' \
  python -m data_pipeline.run dlq-reconcile --max-ticks 5
# redrive(1분 파이프라인, ALPHA-672) — **막힌 것**만 되살린다(DEAD job 또는 Relay 가
# 발행 불가로 격리한 DEAD delivery event). 정상 진행 중이거나 SUCCEEDED 는 거부한다.
# --reason 은 필수다: 실행자와 함께 대체되는 delivery event 행에 남는 유일한 감사 근거다.
# 배선이 어긋난 채 커밋된 행(Relay 가 destination↔event_type 불일치로 격리)은
# --destination 으로 올바른 큐를 지정해 바로잡는다 — event_id 가 결정적이라
# producer 를 고쳐 재실행해도 그 행은 안 바뀐다(미지정=직전 event 값 복사).
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run redrive --kind news --job-id <job_id> --reason "큐 URL 오타 수정 후 재시도"
# 세션 계획(1분 파이프라인, ALPHA-698) — 하루치 session + window 를 멱등 생성한다
# (Premarket SFN 이 부를 자리). 재실행은 no-op 이고 exit 0 — 새로 생겼는지는 출력의
# `created` 가 말한다. ⚠️ 가격 세션은 `--universe` 가 **필수**다: 빠뜨리면 정규장 390 만
# 계획되고 시간외 구간이 아무 실패 신호 없이 누락된다. window 범위와 universe_hash 가
# 그 파일에서 나온다(무엇을 정본으로 볼지는 운영자가 정한다 — CLI 는 찾아 나서지 않는다).
# exit: 0=계획됨 / 1=계획하면 안 되는 상태(다른 universe 로 고정·이미 drain 이후) /
# 2=계획 자체를 못 함(설정·인자 결손·어휘 밖 dataset·source_group·DB 장애).
# ⚠️ iNAV 세션(`--dataset etf_inav_minute --source-group kis`)도 `--universe` 를 쓰지만
# 격자는 **항상 390**이다 — 어댑터 하한이 09:00 이라(`kis_inav.MARKET_OPEN`) 시간외를
# 계획하면 매 거래일 08:00~08:59 의 60 window 가 아무도 못 채운 채 DUE 로 남고, iNAV 는
# 소급이 불가라 영구 결손이다. 시간외 종목이 든 universe 를 줘도 안 넓힌다.
# ⚠️ **공시 세션(`--dataset disclosure_minute --source-group dart`)은 정확히 반대 사례다**
# (ALPHA-875 — 🔴 987 컷오버로 지금은 **계획하지 마라**: 공시는 저녁 배치(18:10)가 소유한다.
# 이 세션을 계획해 워커를 돌리면 배치와 같은 DART 창을 이중 수집한다. 아래는 롤백 시에만): `--universe` 를 **안 받는데**(주면 거부) 격자는 **720**이다. DART 당일접수가
# 07:30~18:00 이라 정규장 격자면 16·17·18시 접수분을 다음 거래일까지 못 본다. iNAV 를 막은
# 근거(어댑터 하한·소급 불가)가 공시에는 안 걸린다 — 매 tick 이 날짜창 전체를 재독하고
# `ingest_date`(UTC) 파티션을 고르는 소비자가 없다(정제 두 스텝은 raw 전량 스캔).
# 🔴 그 소득은 **날짜창을 세션 날짜(KST)에서 유도**할 때만 실현된다 — `--from/--to` 를
# 생략한 증분 기본창은 UTC 라 08:00 KST tick 이 `[D-2, D-1]` 을 질의한다(세션 날짜가 창 밖).
# ⚠️ **업종지수 세션(`--dataset sector_index_minute --source-group kis`)은 세 번째
# 형상이다**(ALPHA-887): `--universe` 를 **안 받는데**(주면 거부) 격자는 **390**이다 —
# 위 둘의 조합이 아니라 각 축이 따로 정해진다는 뜻이다. universe 를 안 쓰는 이유는 소스
# 단위여서가 아니라 **기대 집합 45종이 universe.json 에 아예 없어서**다(지수는 ETF 명부에도
# 구성종목에도 없다) — 정본은 `[minute_sector_index.index_map]` 이고, planner 가 그 표의
# 해시를 세션에 고정한다. 그래야 장중 재배포로 표가 바뀔 때 Worker 가 거부한다(안 그러면
# 한 세션 안에서 기대 집합이 조용히 갈린다). 격자가 390 인 이유는 이 TR 이 정규장 지수만
# 주기 때문이고, 소급이 불가라 못 채운 window 는 영구 결손이다.
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run plan-minute-session --dataset disclosure_minute \
    --source-group dart --session-date 2026-08-10   # universe 없음 · 720 window
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run plan-minute-session --dataset sector_index_minute \
    --source-group kis --session-date 2026-08-10    # universe 없음 · 390 window
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run plan-minute-session --dataset price_minute \
    --source-group kis --session-date 2026-08-04 --universe /path/universe.json
# universe.json 생성(1분 파이프라인, ALPHA-735) — canonical KR holdings 의 **ETF 별 최신
# 스냅샷 합집합**(ALPHA-590 규칙) **∩ 유니버스 뿌리(`krx_etf.source.etf_map`)** 에서 만든다
# — 파티션에 남은 폐지분·참조 계열은 여기 안 든다. 손으로 유지하는 목록은 ETF 편입·제외 때마다
# 조용히 어긋난다. 여기에 config `[minute_universe].sector_etf_ids`(층 분해의 섹터 후보
# ETF)를 **참조 계열 축**(`Universe.sector_etf_ids`)으로 얹는다 — 봉만 받고 트리거 판정은
# 안 받는 계열이다(`etf_ids` 는 price-consumer 의 판정 집합이라 거기 얹으면 발화 대상이 된다).
# 반영까지 하는 것은 **스텝**이다(ALPHA-953). 쓸 자리는 소비자와 같은 `--universe` URI 를
# 인자로 받는다 — 상수로 박으면 var.minute_universe_uri 가 옮겨졌을 때 생산자와 소비자가
# 둘 다 exit 0 으로 갈린다. 무변경이면 no-op(PUT 자체를 안 한다), 교체하면 직전 객체를
# `<uri>.bak-<run_id>` 로 남긴다. 평일 07:00 KST 에 장전 레인(ALPHA-963)이 이 스텝을
# 부르므로 아래는 **수동 회수·확인용**이다(예: 그 런이 실패한 날).
AWS_PROFILE=edge DATA_PIPELINE_STORAGE__BACKEND=s3 \
DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
  uv run --package data-pipeline python -m data_pipeline.run build-minute-universe \
    --universe s3://edge-dev-pipeline-lake/config/minute/universe.json
# ⚠️ **거래일 07:30 KST(REBUILD_CUTOFF_KST) 이후엔 스텝이 스스로 거부한다** — 세션이 이미
# 계획된 뒤라면 원장의 (universe_version, universe_hash)는 옛 값에 고정된 채 객체만 바뀌어,
# 재기동된 worker 가 매 틱 blocked 로 돌면서도 안 죽는다. 장전 체인은 이 시각 전에 끝나야
# 한다(그게 계약이고 크론이 그것을 지킨다). 비거래일엔 흔들 계획이 없어 시각을 안 본다.
# ⚠️ **새 축을 담은 객체는 이미지 배포 뒤에 올린다.** Universe 는 extra="forbid" 라
# 옛 이미지가 읽으면 ValidationError 이고, planner 가 exit 2 면 스케일업을 안 해 그날
# 레인이 안 뜬다(그 실패는 minute-session non-zero exit 경보로 드러난다).
# 반대 순서는 안전하다(축 기본값이 ()이라 옛 객체는 그대로 읽힌다).
#
# 파일로만 뽑아 눈으로 대조할 땐 스크립트를 쓴다(업로드하지 않는다. `--out` 없으면 stdout).
# 마감 시각을 넘겨 오늘 안에 꼭 갈아야 할 때도 이 경로다 — 그때는 ①기존 객체를 지우지 말고
# `.bak-수동` 으로 옮기고 ②백업의 extended_hours_ids 를 새 객체에 손으로 옮겨 담고
# ③원장의 universe_version 과 universe_hash 를 **둘 다** 고치고 ④소비자를 재기동한다.
AWS_PROFILE=edge DATA_PIPELINE_STORAGE__BACKEND=s3 \
DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
  uv run python apps/cloud/data-pipeline/scripts/build_minute_universe.py --out /tmp/universe.json
# 세션 drain(1분 파이프라인, ALPHA-698) — phase 를 DRAINING 으로 옮긴다(EOD SFN 이 부를
# 자리). Worker 가 ack 하면 DRAINED 가 되고 그다음이 qc-minute-session 이다.
# ⚠️ **이미 drain 이후인 것도 exit 0** 이다 — DB 커밋 뒤 출력 전에 죽은 실행의 재시도가
# 정상 운영이라, 그걸 실패로 내면 정상 재시도가 EOD 흐름을 세운다. 방금 걸었는지는
# 출력의 `drain_requested` 가 말한다. 없는 세션은 exit 2 다(지목이 틀린 것이라 재시도로
# 낫지 않는다).
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run drain-minute-session --session-id <session_id>
# EOD 세션 QC(1분 파이프라인, ALPHA-693) — drain 이 끝난(DRAINED) 세션 하나를 판정해
# 닫는다. DUE 잔존을 MISSING 으로 확정하고 FINALIZED + final_checksum 을 기록한다.
# ⚠️ 확정 대상은 **이미 도래한** window 뿐이다(scheduled_at ≤ now) — 장중에 drain 이
# 잘못 걸린 세션을 QC 해도 아직 오지 않은 분을 봉인하지 않는다. 판정 결과는 stdout JSON.
# exit: 0=확정 / 1=원장이 스스로와 모순(사람이 봐야 한다) / 2=판정 자체를 못 함(재시도 가능).
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run qc-minute-session --session-id <session_id>
# EOD 5분봉 확정(1분 파이프라인, ALPHA-839) — 그날 5분 파생을 마감 후 한 번 확정한다.
# 커밋 후크(`minute/rollup.py:maybe_rollup`)는 "방금 커밋된 window 의 버킷이 닫혔을 때"만
# 발화해 **지나간 거래일을 영영 안 채운다** — 마지막 버킷 뒤에 온 정정이나 통째로 안 돈
# 날에는 다음 커밋이 없다. 집계는 후크와 **같은 함수**(`_rollup_day`)라 같은 커밋 세대
# 집합이면 산출이 바이트까지 같다(재실행도 멱등).
# ⚠️ 계획·커밋을 **원장에서 읽는다** — `--universe` 를 받지 않는다(거부한다). 마감 후의
# universe 파일은 수동 편집 대상이라 그날 계획과 갈릴 수 있고, 갈리면 없는 분을 결손으로
# 세거나 있는 분을 계획 밖으로 버린다.
# ⚠️ **백필이 소유하는 날**의 파티션은 거부한다(`rollup.writer_owns()` — 경계
#   `WRITER_SINCE` + 경계 앞 예외 집합) — 그 앞은 fmp·토스·KIS 백필의 정본이라 과거
# --session-date 재실행 하나가 벤더 원본을 파생본으로 갈아치운다. 날짜를 여기 적지
# 않는다: 경계는 옮겨진다(ALPHA-836 — 롤업이 온전한 계열을 갖는 날로 이동했다).
# ⚠️ **롤업 소유일 안에도 소유 축이 하나 더 있다**(`rollup.OWNER_SOURCE_GROUP`,
#   ALPHA-847): 산출 키에는 source_group 이 없는데 세션은 source_group 으로 갈리므로,
#   그날 세션이 둘이면(kis·toss) `--source-group` 만 바꾼 실행이 **같은 part-0 을 다툰다**.
#   세션이 둘 이상인 거래일은 소유 source_group 실행만 쓰고 나머지는 거부한다(exit 1,
#   로그에 소유자와 거부된 실행이 함께 남는다). 세션이 하나인 날은 안 본다 — 벤더는 설정
#   축이라(`WorkerConfig.source`) 무조건 걸면 벤더를 바꾼 날부터 파생이 통째로 멎는다.
#   🔴 대가: 소유 세션 행은 있는데 커밋이 0건이고 비소유 세션만 온전한 날은 그날 파생이
#   안 나온다(둘 다 거부된다). 아래 `unfilled_settled_days` 가 그날을 결손으로 잡는다.
# 출력에 결손 판정이 함께 실린다 — 5분 파생엔 원장이 없어서 배치가 조용히 안 돌면
# 물어볼 곳이 그것뿐이다. 목록이 **둘**인 이유는 처방이 다르기 때문이다:
#   · `unfilled_settled_days` — 파티션이 비었다 → **1분 재수집**이 필요하다
#   · `contested_days` — 다른 writer 가 물고 있어 파생이 영구 정지했다 → **소유자 결정**
#     이 필요하다(우리 part-0 이 이미 있어도 잡는다: 후크가 먼저 쓴 뒤 백필이 끼어들면
#     그 시점의 **부분본**이 완성본처럼 남는데, 그게 운영에서 더 흔한 순서다)
#   · `settled_day_count` — 그 **분모**(후보 일수). 빈 목록은 "구멍 없음"과 "본 게 없음"
#     둘 다라 분모 없이는 못 가른다
# ⚠️ 판정 축 셋:
#   · 세션 phase = `FINALIZED`이고 `final_checksum`이 64자리 소문자 sha256인 값. EOD QC가
#     원장과 artifact를 대조해 checksum으로 봉인한 날만 settled다. DRAINED·QC_RUNNING·
#     FAILED와 checksum이 없거나 잘못된 FINALIZED는 후속 rollup 결손 판정의 후보가 아니다.
#   · 날짜 창은 `[rollup.scan_lower(), 오늘)` — **`--session-date` 와 무관하게 오늘**이
#     상한이고, 하한은 롤업이 소유하는 가장 이른 날이다(경계 앞 예외를 포함한다 —
#     소유하는 날은 감시해야 구멍이 조용히 남지 않는다).
#     대상 날짜로 묶으면 과거 하루를 되돌리는 실행에서 감시 창이 가장 좁아진다.
#   · 파티션은 **타 writer 파일 유무를 먼저** 보고, 없을 때만 우리 산출의 부재를 본다.
#     한 축으로만 물으면 한쪽이 샌다 — "비었나"만 보면 거부된 날이 영원히 "채워짐"이고,
#     "우리 part-0 있나"만 보면 후크가 먼저 쓴 뒤 끼어든 날의 부분본을 못 본다.
# 실측(2026-08-07 dev): `unfilled=['2026-08-04']` · `contested=[]` · 분모 3.
# ⚠️ 스캔은 rollup **뒤**에 돈다 — 창이 `[rollup.scan_lower(), 오늘)` 이라 대상 날짜가 그 안에
# 있어서, 앞서 돌면 방금 그날을 채운 실행이 같은 출력에서 그날을 결손이라 보고한다.
# exit: 0=확정(또는 비거래일 no-op) / 1=확정 안 함(세션 없음·커밋 0건·닫힌 버킷 0·다른
# writer 파일 존재·백필 소유일·**비소유 source_group** — 전부 재시도로 안 낫는다)
# / 2=판정 자체를 못 함
# (설정·인자 결손·DB/S3 장애 — 재시도하면 될 수 있다). 구멍 판정 스캔만 실패하면 rollup
# 이 성공했을 때만 2 이고, rollup 이 거부했으면 **1 이 이긴다**(두 사실은 독립이다).
# 우선순위: rollup 예외 → 2 · 정당한 거부(key 없음) → 1 · 스캔 실패만 → 2 · 그 외 0.
# `--session-date` 미지정=오늘(KST). `--dataset` 은 **봉 dataset 만** 받는다
# (price_minute·sector_index_minute — `rollup.ROLLUP_DATASETS` 가 정본). 어휘 전체로
# 열지 않는 이유: 뉴스 세션도 390 window 를 계획해서, 열어 두면 뉴스 커밋 지평으로 잘린
# 5분봉이 가격 파일을 덮는다.
# 산출은 dataset 마다 **같은 파티션의 다른 파일**이라 둘을 따로 돌려야 한다
# (행은 서로소 — 업종코드 vs 종목코드).
# ⚠️ **업종지수는 장중 후크가 없다**(`SectorIndexWorker._after_commit` 은 비어 있다 —
# 후크는 ALPHA-839 가 지울 경로다). 배치가 유일 writer 이고, 평일 16:00 KST 스케줄이
# 그것을 부른다(ALPHA-955 — `aws_scheduler_schedule.minute_session["rollup-sector"]`).
# 아래 명령은 **그 스케줄이 못 채운 날을 손으로 되돌릴 때** 쓴다.
# 🔴 **가격은 아직 스케줄이 없다** — 장중 후크가 매일 만들고 있어 당장 공백은 없지만,
# 지나간 날은 이 명령으로만 채워진다(EOD 확정 스케줄은 ALPHA-839 PR2).
#   시각이 업종지수와 다를 수밖에 없다: stop cron 16:10(ALPHA-987) + 상한 1800초 + 확인분
#   60초 = 최악 16:41 이고 stop 태스크의 Fargate 기동(59~122초)이 더 붙어 실제 최악은
#   ~16:43 이다.
#   그 전에 뜨면 늦은 recovery 커밋이 5분 파생에 영영 안 들어간다(후크와의 배타성은
#   코드가 아니라 스케줄 시각이 진다 — 이 스텝은 phase 게이트를 의도적으로 안 건다).
#   업종지수는 09:00~15:30 격자라 그 하한이 훨씬 이르다(16:00 근거는 terraform 주석).
#   `OPS_KR_HOLIDAYS` 는 `aws_ecs_task_definition.minute_session` 에 **이미 주입돼 있다**
#   — 그 task-def 를 재사용하면 자동 충족이고, 새 task-def 를 파면 필수다.
AWS_PROFILE=edge \
DATA_PIPELINE_STORAGE__BACKEND=s3 \
DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run rollup-minute-session --dataset price_minute \
    --source-group kis --session-date 2026-08-04
# 업종지수(45종 → part-sector-index.parquet). source-group 은 kis 뿐이다.
AWS_PROFILE=edge \
DATA_PIPELINE_STORAGE__BACKEND=s3 \
DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run rollup-minute-session --dataset sector_index_minute \
    --source-group kis --session-date 2026-08-10
# 상주 iNAV Worker(1분 파이프라인, ALPHA-851) — 장중 추정 NAV 를 window 단위 canonical
# artifact 로 확정한다. 세션이 먼저 계획돼 있어야 한다
# (plan-minute-session --dataset etf_inav_minute --source-group kis — `--universe` 는
# price 와 **같은 파일**을 쓴다. 세션 identity·기대 집합이 거기서 나온다).
#
# 가격 Worker 와 갈리는 곳 넷:
#  · **기대 집합은 판정 축 ETF 뿐이다**(`etf_ids`) — 기준은 NAV 가 있는가가 아니라
#    **질의 심볼 맵(`etf_map`)에 있는가**다. 맵 밖 unit 은 `_rows_for` 가 INVALID 로
#    내고 invalid 하나면 window 전체가 INVALID 다. 구성종목은 NAV 도 맵도 없고, 참조
#    계열(`sector_etf_ids`)은 NAV 는 있어도 맵 밖이다(ALPHA-903 — 빌더가 두 축의 겹침을
#    거부해 그 상태를 지키지만 영구 보장은 아니다. `_expected_units` 도크스트링에 이유가
#    있다). ⚠️ NAV 축만의 판단이다 — 참조 계열의 **봉**은 가격 레인이 그대로 수집한다.
#  · **job·outbox 를 만들지 않는다** — 하위 소비자가 없어 window 확정에서 멈춘다.
#    (가격 것을 빌려 쓰면 NAV 가 price-analysis-realtime 으로 나가 설명이 발화된다.)
#  · **복구를 하지 않는다**(`recovery_budget_per_tick = 0`, 2026-08-08 결정) — iNAV 는
#    추정값이라 분 단위 완전성 요구가 낮다. 놓친 분은 놓친 채로 두고 결손은 원장이
#    드러낸다(`/api/v1/sources/minute` 의 overdue_no_evidence).
#  · 격자는 **390**(09:00–15:30) — 어댑터 하한이 09:00 이라 시간외를 계획하지 않는다.
#
# 질의 심볼은 `[krx_etf.source.etf_map]` 에서 온다(세션 universe 와 **다른 출처**다) —
# 갈리면 그 unit 이 매 window invalid 로 드러난다(조용히 missing 으로 접지 않는다).
# 자격증명은 일별 NAV 와 같은 쌍이다(같은 벤더·같은 계정).
#
# 🔴 **`--session-date` 는 오늘만 받는다**(다른 워커와 다르다 — 그쪽은 지난 거래일을
# 받는다). 이 벤더에는 소급 질의 경로가 아예 없고 응답 행에 날짜가 없어(`bsop_hour` =
# HHMMSS), 과거 날짜로 돌리면 **지금 값이 그 날짜의 불변 artifact 로 굳는다**. 되돌릴
# 방법이 없어(재수집 불가) 기동에서 거부한다. 그래서 아래 예시는 날짜를 안 준다(=오늘).
# 🔴 **휴장일·개장 전은 수집 전에 멈춘다**(KIS 가 빈 응답이 아니라 직전 거래일 행을
# 그대로 주기 때문 — 2026-07-25 실측). **멈추는 방식이 셋으로 갈린다**(ALPHA-882):
#   휴장일 · 상주(`--max-ticks` 없음) → exit **0**  (스케줄러가 정상 통과)
#   휴장일 · bounded                  → exit **1**  (확인 게이트라 "한 window 도 못
#                                                    봤다"를 성공으로 보고하지 않는다)
#   개장 전 · 상주                    → **종료하지 않고 09:00 까지 기다린다**
#   개장 전 · bounded                 → exit **1**  (확인은 즉답이어야 한다)
# ⚠️ 개장 전 상주 실행은 **블록된다** — 07:45 에 손으로 돌리면 09:00 까지 75분을 기다린다.
# 확인용으로 돌릴 거면 `--max-ticks` 를 준다(즉답). 상주가 기다리는 이유는 그게 ECS
# 서비스의 모습이기 때문이다: 종료하면 desired 1 을 유지하는 ECS 가 재기동 루프를 돌고,
# 백오프가 첫 정상 기동을 09:00 뒤로 밀어 소급 불가한 window 를 잃는다.
# ⚠️ 위 휴장일 분기는 `OPS_KR_HOLIDAYS` 를 받아야 성립한다 — 안 주면 `is_trading_day` 가
# 주말만 아는 상태로 **조용히 퇴화**해 평일 공휴일에 가드가 안 걸린다(terraform 은
# inav-worker 서비스에 심는다. 그 배선은 test_session_ops 의 계약 검사가 지킨다).
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY=... \
DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET=... \
KIS_TOKEN_CACHE_PARAM=/edge-dev-data-pipeline/kis/access-token \
OPS_KR_HOLIDAYS=2026-08-15,2026-10-03 \
  python -m data_pipeline.run inav-worker --universe /path/universe.json --max-ticks 3
# 토큰 만료(24h) 재발급은 **붙어 있다**(ALPHA-889). 상주 전환(ALPHA-882)이 만료를 반드시
# 만나는 것으로 바꿨기 때문이다 — 만료 신호(rt_cd `EGW00121/123` 또는 4xx)를 보면 공유
# 캐시와 컬렉터의 토큰 사본을 **둘 다** 버리고 1회 재발급해 그 window 를 살린다.
# ⚠️ 여기서 안 잡으면 자가치유가 아니다: `StopFetch` 는 tick 의 `except Exception` 에
# 삼켜져 WINDOW_FAILED 가 되고 루프는 계속 돈다 — 컨테이너가 죽고 재기동하는 게 아니라
# 그날 남은 window 가 조용히 전부 실패하고, iNAV 는 소급이 불가라 영구 결손이다.
# 🔴 **재발급 뒤에도 만료면 전역 실패로 전파한다**(MISSING 으로 안 접는다) — 그건 종목
# 축이 아니라 자격증명·시계 문제라, 접으면 "벤더가 안 준다"로 읽혀 원인을 가린다.
# 로그 신호: `토큰 만료 — 캐시 폐기 후 1회 재발급`(정상 회복) vs `재발급 뒤에도 만료`(사람 확인).

# 상주 Price Worker(1분 파이프라인, ALPHA-706) — ECS Service 명령. 세션이 먼저 계획돼
# 있어야 하고(위 plan-minute-session — `--session-date`·`--universe` 를 **같은 값**으로),
# 갈리면 다른 session_id 가 유도되거나 Worker 가 처리를 거부한다. SIGTERM 은 tick
# 경계에서 멈추고 fence lease 를 즉시 반납한다(교체 무대기 인계). `--session-date`
# 미지정=오늘(KST). `--max-ticks` 는 로컬 확인용 — WINDOW_FAILED 가 있거나 한 window
# 도 못 본 채 차단만 됐으면(경쟁 fence·universe 불일치) exit 1.
# 자격증명은 **source 마다 다른 쌍**이다(ALPHA-735) — 기본 source=kis 는 APP_KEY/APP_SECRET,
# source=toss 로 되돌릴 때만 CLIENT_ID/CLIENT_SECRET. 결손은 기동에서 죽는다.
# 상주 워커는 토큰(24h)보다 오래 사므로 KIS_TOKEN_CACHE_PARAM 을 함께 준다(발급 분당 1회).
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_MINUTE_PRICE_WORKER__APP_KEY=... \
DATA_PIPELINE_MINUTE_PRICE_WORKER__APP_SECRET=... \
DATA_PIPELINE_MINUTE_PRICE_WORKER__TRIGGER_SCHEMA_VERSION=intraday-anchor-v2.1 \
KIS_TOKEN_CACHE_PARAM=/edge-dev-data-pipeline/kis/access-token \
  python -m data_pipeline.run price-worker --session-date 2026-08-04 \
    --universe /path/universe.json
# ⚠️ `--session-date` 가 **지난 거래일이면 벤더 TR 과 콜 형상이 바뀐다**(ALPHA-846) —
# 소급 TR 로 하루를 한 번에 받아 캐시하므로 첫 window 에 362종 × 4페이지 ≈ 1,450콜이
# 몰리고(그 뒤 window 는 벤더 호출 0), 시간외 universe 는 기동에서 거부된다. 그 거부는
# **KIS 한정**이다 — 소급 TR 이 정규장만 주기 때문이라, 임의 과거 구간을 받는 토스는 안 막는다.
#
# 🔴 **과거일 백필 선행조건 하나 + 알아둘 것 둘** — 1) 을 안 지키면 태스크가 크게 터진다:
#  1) 그 날짜의 1분 canonical prefix 가 **비어 있어야 한다**. artifact 키에는 벤더·세션
#     축이 없어(ALPHA-705) 다른 벤더 세션이 같은 generation 을 이미 썼으면
#     `ArtifactImmutabilityError` 로 태스크가 죽고 ECS 가 같은 window 에서 재기동한다:
#       aws s3 ls --recursive \
#         s3://<lake>/canonical/market_data/price_minute/market=KR/session_date=<날짜>/
#  2) 과거일 세션은 **outbox 이벤트를 안 낸다**(ALPHA-863) — 커밋은 window·job 만 쓴다.
#     판정은 `--session-date` 하나이고(`make_price_collector` 가 `is_backfill` 로 돌려준다)
#     벤더와 무관하다. 그러니 백필 뒤 `dataset_commit_outbox` 에 행이 없는 것이 정상이고,
#     수동 DEAD 격리도 필요 없다. 무엇을 수집했는지는 window·job 원장에 그대로 남는다.
#  3) 종가 단일가 구간(15:21~15:29)의 값이 **당일 레인과 다르다**. 당일 TR 은 그 9분을
#     마감 체결 봉의 복제로 채우고(거래량까지 반복 — 5분 마지막 두 버킷이 부풀려진다),
#     소급 경로는 체결이 없었다는 사실대로 직전 종가 flat·거래량 0 으로 채운다. 백필한
#     하루만 그 두 버킷이 다르게(더 정확하게) 나온다.
# 상주 가격 판정 Consumer(1분 파이프라인, ALPHA-711) — Price Job SQS 를 소비해 분봉
# canonical 로 판정한다(LLM 0). 임계는 price_triggers 의 abs_threshold(발화)·
# revert_threshold(회수) 재사용(섹션 필수), --universe 는 planner·worker 와 같은
# 파일/객체(s3://… 지원). --max-ticks 는 로컬 확인용 — 배선 오류 신호
# (poison·misrouted·orphan·ahead)가 있으면 exit 1.
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_MINUTE_PRICE_CONSUMER__QUEUE_URL=https://sqs.../price \
DATA_PIPELINE_MINUTE_PRICE_CONSUMER__DETECTION_POLICY_VERSION=intraday-anchor-v2.1 \
  python -m data_pipeline.run price-consumer --universe /path/universe.json --max-ticks 5
# 상주 뉴스 추출 Consumer(1분 파이프라인, ALPHA-713) — News Job SQS 를 소비해 기사
# 정본(PG document)을 읽고 tagging/extract 로 추출, feature 존에 결과를 불변 PUT 한다.
# 그 결과를 배치가 읽는 날짜축 feature 파티션에도 미러한다(ALPHA-900) — 없으면 배치
# tag-news 가 같은 기사를 다시 유료로 태운다.
# 추출 성공은 event 계보(source_event 7종 + threading)로 **즉시 단건 조립**된다
# (ALPHA-727, minute/event_assembly.py — assemble-events 와 같은 결정적 ID·스레드).
# LLM 설정은 tag-news 와 같은 LLM_* env 관례(기본 base_url·model=DeepSeek).
# realtime·backfill 은 같은 스텝을 큐 URL 만 바꿔 서비스 2개로 띄운다.
# --max-ticks 는 로컬 확인용 — 배선 오류 신호(poison·misrouted·orphan·ahead)면 exit 1.
DATA_PIPELINE_DB__PASSWORD=... \
LLM_API_KEY=... \
DATA_PIPELINE_MINUTE_NEWS_CONSUMER__QUEUE_URL=https://sqs.../news-extraction-realtime \
  python -m data_pipeline.run news-consumer --max-ticks 5
# 상주 News Worker(1분 파이프라인, ALPHA-707) — BigKinds 를 매분 폴링해 관측 전량을
# 원장 판정, 신규/정정만 job+outbox 로. 세션이 먼저 계획돼 있어야 한다
# (plan-minute-session --dataset news_minute --source-group bigkinds — universe 없음).
# 엔드포인트·카테고리 정본은 [bigkinds_news](배치와 공유), pacing 은 [minute_news_worker]
# (기본: interval 1s·timeout 45s·max_pages 4 — ALPHA-645 실측 근거). --max-ticks 는
# 로컬 확인용 — WINDOW_FAILED 가 있거나 한 window 도 못 본 채 차단만 됐으면 exit 1.
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run news-worker --session-date 2026-08-04 --max-ticks 3
# 상주 Disclosure Worker(1분 파이프라인, ALPHA-875 — 🔴 987 컷오버로 **미편입**: 공시는 저녁
# 배치가 소유하고 이 워커는 롤백 경로다. 배치 스케줄이 켜진 채 돌리면 이중 수집이다) —
# 공시를 매분 폴링한다. **수집만이 아니라
# 체인 전체**를 한 window 에서 돈다: collect → normalize(공급계약) → normalize(사업부문)
# → load → assemble. CLI 가 아니라 스텝 함수를 부르므로 `catalog.by_cli` 동시 소유 충돌이 없다.
# 세션이 먼저 계획돼 있어야 한다(plan-minute-session --dataset disclosure_minute
# --source-group dart — universe 없음. 격자는 08:00~20:00 720개 — DART 접수 07:30~18:00).
# 엔드포인트·유형 필터 정본은 [dart_disclosure.source](배치와 공유), pacing·예산은
# [minute_disclosure_worker](기본: interval 1s·timeout 10s·페이지 예산 60·본문 예산 5).
# ⚠️ 페이지 예산은 이 워커의 소스 `max_pages` 로 **주입**된다 — 벤더 섹션의 500(백필용)이
# 그대로면 lease 검증이 실제보다 짧은 tick 을 통과시킨다.
# 질의 날짜창은 **세션 날짜(KST)** 에서 나온다: 매 tick 당일, 세션 첫 tick 만 D-1 포함
# (중단 캐치업 — 일 콜을 절반으로 줄인다). --max-ticks 는 로컬 확인용 — WINDOW_FAILED 가
# 있거나 한 window 도 못 본 채 차단만 됐으면 exit 1.
# ⚠️ 한 tick 이 1분을 넘는 것은 이 레인의 정상이다(dev 실측 window 당 ~14초·tick 당 ~27초).
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY=... \
  python -m data_pipeline.run disclosure-worker --session-date 2026-08-07 --max-ticks 3
# 보유한 DART raw 전체를 재처리한다. --from/--to 를 주면 접수일 기준 포괄 범위만 재처리한다.
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run backfill-normalize-disclosure --run-id dart-backfill-20260809
# 업종지수 Worker(1분 파이프라인, ALPHA-887) — KRX 업종지수 45종 분봉을 window 단위
# canonical artifact 로 확정한다. 세션이 먼저 계획돼 있어야 한다
# (plan-minute-session --dataset sector_index_minute --source-group kis — universe 없음).
#
# iNAV Worker 와 갈리는 곳 셋:
#  · **기대 집합이 universe 가 아니라 config** 다(`[minute_sector_index.index_map]` 45줄).
#    지수는 ETF 명부에도 구성종목에도 없어 universe.json 이 이 45종을 모른다. planner 가
#    그 표의 해시를 세션에 고정하고 Worker 가 대조한다 — **장중에 이미지가 바뀌어 표가
#    달라지면 기동에서 거부한다**(안 그러면 한 세션 안에서 기대 집합이 조용히 갈린다).
#  · **`no_trade` 축이 없다** — 지수는 자기가 체결되지 않아 `cntg_vol == 0` 인데 OHLC 가
#    움직이는 봉이 정상이다(실측 3.9%). 가격의 4분류를 그대로 물리면 매 window 가 INVALID 다.
#  · **오늘이 아닌 `--session-date` 를 거부한다** — 이 TR 에 날짜 파라미터가 없어 과거일로
#    돌리면 45종 전건 missing 이 그 날짜 원장에 굳는데, 소급이 불가라 채울 방법이 없다.
#
# 자격증명은 `[kis_nav.source]` 를 그대로 쓴다(iNAV 와 같은 쌍 — 같은 KIS 계정이고 쿼터가
# 앱키 전역이라 어차피 하나다). ⚠️ `[minute_price_worker]` 가 아닌 이유는 그 섹션이
# `sources.toml` 에 없어서다 — 전부 env 라, 그걸 쓰면 업종지수와 무관한 필수 필드
# (`trigger_schema_version`·`destination`)까지 주입해야 설정이 로드된다.
# ⚠️ 상주 배선(ECS 서비스)이 **있다**(ALPHA-887 — `sector-index-worker`. iNAV 와 같이
# 세션 오케스트레이션이 desired_count 를 올리고 내린다). 아래 명령은 그 배선과 별개인
# **수동 확인 게이트**다 — 상주 태스크가 이미 fence 를 쥐고 있으면 window 를 못 잡는다.
# --max-ticks 는 확인 게이트다: WINDOW_FAILED 가 있거나 **한 window 도 못 봤으면 exit 1**.
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY=... \
DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET=... \
  python -m data_pipeline.run sector-index-worker --session-date 2026-08-10 --max-ticks 3
# 세션 스케일 오케스트레이션(1분 파이프라인, ALPHA-712·717·719·875·882·887) — 상주 서비스의 desired_count
# 를 세션 수명에 맞춰 바꾸는 **유일한 주체**다(terraform 은 그 값을 ignore_changes 로 뒀다).
# 실제 스케일 대상은 8종이다: 정의 10종 중 analysis-consumer 는 오토스케일링 소유(코드가
# 공용 목록에서 뺀다, ALPHA-912), disclosure-worker 는 987 컷오버로 미편입(source_group 빈 값).
# EventBridge Scheduler 가 부르지만 손으로도 같은 명령을 친다.
#
# ⚠️ **`--dataset` 은 구동 레인(price_minute)만 받는다.** 선택 레인 넷(news_minute·
# disclosure_minute·etf_inav_minute·sector_index_minute)은 어휘엔 있어도 인자로는
# 거부된다(**exit 1** — 실측).
#   ⚠️ 같은 부류의 오류에 `plan-minute-session` 은 2 를 낸다(어휘 밖 dataset). 이쪽은
#   `SystemExit(문자열)` 이라 1 로 떨어지는 것이고 **의도된 구분이 아니다** — 정리 대상.
#   여기서 올리고 내리는
# 서비스 목록은 dataset 별이 아니라 **공용**이고 `_scale` 은 dataset 을 아예 안 봐서,
# 승객 dataset 으로 stop 을 부르면 phase 게이트는 그 세션만 보고(claim 0 → 즉시 통과)
# 큐·outbox 게이트는 전역이라 **살아 있는 price-worker 가 내려간다**.
#   ⚠️ **자기 워커를 소유해도 이 조건은 안 풀린다**(ALPHA-882) — 소유와 구동 레인은
#   다른 축이다(`states.SCALED_DATASETS`). news_minute 이 news-worker 를, etf_inav_minute
#   이 inav-worker 를 소유하는 지금도 둘 다 인자로는 못 온다.
# **선택 레인은 이 명령에 얹혀 계획·드레인된다 — 단 토글 env 가 켜진 레인만이다**
# (`MINUTE_SESSION_{NEWS,DISCLOSURE,INAV,SECTOR_INDEX}_SOURCE_GROUP`). 현재 dev 는
# 공시 토글이 빈 값이라(987 컷오버) 실제로 얹히는 선택 레인은 셋(news·inav·sector)이다.
# ⚠️ **토글 env 가 없는(빈) 레인은 계획도 스케일도 안 된다** — 그 레인만 조용히 빠진 채
# 세션이 선다(`session_ops._OPTIONAL_LANES`). 손으로 칠 때 아래 예시에서 한 쌍을 빼면 그 결과다.
# terraform 의
# `minute_session_dataset` 기본값도 price_minute 라 실제 경로는 없지만, 손으로 치던
# 사람은 `--dataset` 에서 막힌다.
#
# start: 거래일 판정(OPS_KR_HOLIDAYS) → plan-minute-session(오늘 KST 고정) → desired 0→1.
# ⚠️ 비거래일이면 아무것도 하지 않고 exit 0. 계획이 실패하면 **올리지 않고** 그 exit 를
# 그대로 낸다 — 세션 없이 뜬 Worker 는 기동을 거부해 하루 종일 재기동 루프를 돈다.
# ⚠️ 스케일업은 항상 force-new-deployment 다(desired 0 동안 CD 재배포가 no-op 라, 빼면
# 직전 세션의 낡은 다이제스트로 뜬다).
# ⚠️ 공시 source_group 은 **빈 값**이다(987 컷오버 — 공시는 저녁 배치 소유. dart 를 넣으면
# 1분 레인이 공시 세션을 계획해 18:10 배치와 같은 창을 이중 수집한다. terraform 실물과 동일값).
DATA_PIPELINE_DB__PASSWORD=... \
OPS_KR_HOLIDAYS=2026-01-01,2026-03-02 \
MINUTE_SESSION_CLUSTER=arn:aws:ecs:ap-northeast-2:...:cluster/edge-dev-worker \
MINUTE_SESSION_SERVICES=edge-dev-data-pipeline-price-worker,edge-dev-data-pipeline-relay,edge-dev-data-pipeline-price-consumer,edge-dev-data-pipeline-news-consumer-realtime,edge-dev-data-pipeline-news-consumer-backfill,edge-dev-data-pipeline-analysis-consumer \
MINUTE_SESSION_ANALYSIS_SERVICES=edge-dev-data-pipeline-analysis-consumer \
MINUTE_SESSION_NEWS_SOURCE_GROUP=bigkinds \
MINUTE_SESSION_NEWS_WORKER_SERVICES=edge-dev-data-pipeline-news-worker \
MINUTE_SESSION_DISCLOSURE_SOURCE_GROUP= \
MINUTE_SESSION_DISCLOSURE_WORKER_SERVICES=edge-dev-data-pipeline-disclosure-worker \
MINUTE_SESSION_INAV_SOURCE_GROUP=kis \
MINUTE_SESSION_INAV_WORKER_SERVICES=edge-dev-data-pipeline-inav-worker \
MINUTE_SESSION_SECTOR_INDEX_SOURCE_GROUP=kis \
MINUTE_SESSION_SECTOR_INDEX_WORKER_SERVICES=edge-dev-data-pipeline-sector-index-worker \
  python -m data_pipeline.run start-minute-session --dataset price_minute \
    --source-group kis --universe s3://edge-dev-pipeline-lake/config/minute/universe.json
# stop: drain 요청 → **원장 게이트**가 빌 때까지 폴링 → 활성 전 레인 QC → desired 1→0.
# 한 레인 QC 실패도 뒤 레인과 scale-down을 막지 않고, 최종 exit 은 2 > 1 > 0 으로 집계한다. 게이트는 셋이고
# 순서대로 비어야 한다 — session.phase 가 DRAINED 이후(= in-flight window 0) → 게이트 큐
# 깊이 0 → 미발행 outbox NEW 0. 큐 깊이는 approximate 라 **연속 5회(≈60초)** 확인한다.
# ⚠️ 시각으로 내리지 않는 이유가 이것이다 — 15:30 이 지났다고 내리면 recovery 레인이
# 집고 있던 window 가 조용히 결손된다.
# exit: 0=QC 성공/재사용 뒤 내렸음(또는 오늘 세션이 없어 미변경) / 1=상한까지 게이트가
# 안 비어 **내리지 않았거나** QC 불변식 위반 / 2=drain 또는 QC 실행 자체를 못 함.
AWS_PROFILE=edge \
DATA_PIPELINE_STORAGE__BACKEND=s3 \
DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
DATA_PIPELINE_DB__PASSWORD=... \
MINUTE_SESSION_CLUSTER=arn:aws:ecs:ap-northeast-2:...:cluster/edge-dev-worker \
MINUTE_SESSION_SERVICES=edge-dev-data-pipeline-price-worker,edge-dev-data-pipeline-relay,edge-dev-data-pipeline-price-consumer,edge-dev-data-pipeline-news-consumer-realtime,edge-dev-data-pipeline-news-consumer-backfill,edge-dev-data-pipeline-analysis-consumer \
MINUTE_SESSION_ANALYSIS_SERVICES=edge-dev-data-pipeline-analysis-consumer \
MINUTE_SESSION_NEWS_SOURCE_GROUP=bigkinds \
MINUTE_SESSION_NEWS_WORKER_SERVICES=edge-dev-data-pipeline-news-worker \
MINUTE_SESSION_DISCLOSURE_SOURCE_GROUP= \
MINUTE_SESSION_DISCLOSURE_WORKER_SERVICES=edge-dev-data-pipeline-disclosure-worker \
MINUTE_SESSION_INAV_SOURCE_GROUP=kis \
MINUTE_SESSION_INAV_WORKER_SERVICES=edge-dev-data-pipeline-inav-worker \
MINUTE_SESSION_SECTOR_INDEX_SOURCE_GROUP=kis \
MINUTE_SESSION_SECTOR_INDEX_WORKER_SERVICES=edge-dev-data-pipeline-sector-index-worker \
MINUTE_SESSION_GATE_QUEUES=https://sqs.../edge-dev-data-pipeline-price-analysis-realtime,https://sqs.../edge-dev-data-pipeline-news-extraction-realtime \
MINUTE_SESSION_DRAIN_TIMEOUT_SEC=1800 \
  python -m data_pipeline.run stop-minute-session --dataset price_minute --source-group kis
```

배포는 `aws_ecs_task_definition.ops`(data-pipeline 이미지 재사용) + 스케줄러 **14개**(daily 1·뉴스 2·
공시 1(18:10, ALPHA-987 — dev 가 cron 맵을 1슬롯으로 override)·장중 수급 5 =plan-run, reconcile 1,
장전 유니버스 1(SFN 직접), 1분 세션 start·stop·rollup-sector 3) + DLQ. 1분 세션 3개만 `aws_ecs_task_definition.minute_session`
(전용 IAM 역할 — 레이크 읽기 + 상주 서비스 10종 `ecs:UpdateService` + 게이트 큐(realtime 2종) 조회)을 띄운다. 설명 큐는 게이트에 없다 — 지연 재배달(장중 returns 대기) 비가시 메시지가 레인 전체를 밤새 붙잡는다(잔여는 다음 세션 소비).
네 레인 스케줄 모두 SFN 직접 시작이 아니라 **Planner 경유**다
(뉴스는 ALPHA-591 에서 전환, 공시·장중 수급은 처음부터). 원장 DB 는 canonical 과 같은 Cloud Event Store(public 스키마,
`ops_` 접두사).

### 복구 절차

**증거의 출처 규칙(ALPHA-566).** occurrence 의 `ecs_task_arn`·`exit_code` 는 **그 태스크의 ECS
생애주기 이벤트**(`TaskSubmitted`·`TaskSucceeded`·`TaskFailed`·`TaskTimedOut`·`TaskStartFailed`)
에서만 읽는다. `TaskStateExited`·Choice·Pass·Parallel 의 details 는 실행 증거가 아니라 **상태
데이터 흐름**이라, 그 `output` 에 앞 페이즈의 누적 JSON(다른 스텝의 `TaskArn`·`ExitCode`)이 그대로
실려 온다. 이걸 안 가르면 남의 실행 결과를 주워 와 마지막 값으로 덮는다 — dev 실측에서 실패한
투자자 태스크 1개가 성공한 17개 작업을 전부 FAILED + `LEDGER_GAP` 으로 만들었다. **양방향**이라
순서가 반대면 성공 ARN 이 실패를 덮어 거짓 초록이 된다. 화이트리스트는 넓혀도(남의 ARN 유입)
좁혀도(ARN 결측 → 거짓 `LEDGER_GAP`) 틀리므로, 5종 전부가 테스트로 걸려 있다.

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
- **완전성(VALID)의 부분 배선** — ETF 3작업은 정적 `etf_map` snapshot과 `received_count`가
  연결됐다(ALPHA-611). 반면 가격·수급·공시처럼 런타임 holdings에서 종목 유니버스를 파생하는
  작업은 계획 시점의 독립 정본이 없어 여전히 `UNKNOWN`이다(false-VALID 를 내느니 UNKNOWN —
  스펙 §6). 그 작업들의 스냅샷 배선은 별도 범위다.

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
