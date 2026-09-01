resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == null ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  # US(FMP) 수집 잡 (ALPHA-558) — `var.us_fmp_enabled` 로 raw 병렬에 넣고 뺀다(제거 아니라 토글).
  # false 면 아래 4잡이 raw_ingest_jobs 에 안 들어가 SFN 이 실행조차 안 하므로, 공용 FMP 키의
  # bandwidth 소진 중(429 "Bandwidth Limit Reach") 매 런을 FAILED 로 마감하던 노이즈가 사라지고
  # daily 런이 clean SUCCESS 로 돈다. KR 은 FMP 와 독립이라 그대로 수집된다(ADR-0030 격리와 무관 —
  # 그건 **간헐 장애**용이고 이건 **의도적 장기 다운**이라 매일 알림보다 끄는 게 맞다, 별개 축).
  # 복구 시 `us_fmp_enabled=true`. 다운 기간 공백은 소스마다 복구성이 다르다(전량 소급 아님):
  #   - 가격·뉴스: windowed 소급 O — `ingest-price-raw --source fmp --from <시작> --to <오늘>` /
  #     `ingest-raw --source fmp --from <시작> --to <오늘>` (FMP EOD·news 둘 다 날짜창 지원).
  #   - 재무: 현재 재무제표 재조회라 다음 런이 그대로 주워온다(창 소급 불요, 공백 개념 없음).
  #   - ETF holdings: **현재 스냅샷 엔드포인트라 다운 기간의 일별 holdings 는 영구 결손**(소급 불가).
  #     단 US ETF holdings 는 ALPHA-371 로 이미 보류·미사용(constituent_mic 전량 null)이라 실손실 없음.
  us_fmp_ingest_jobs = [
    {
      state        = "CollectFmpNews"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-raw', '--source', 'fmp', '--run-id', $.run_id)"
    },
    {
      state        = "CollectFmpPrice"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-price-raw', '--source', 'fmp', '--run-id', $.run_id)"
    },
    {
      state        = "CollectFmpFinancial"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-raw-financial', '--source', 'fmp', '--run-id', $.run_id)"
    },
    {
      state        = "CollectFmpEtf"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-raw-etf', '--run-id', $.run_id)"
    },
  ]

  # KR 수집 잡 — FMP 와 독립이라 US 토글과 무관하게 항상 돈다.
  kr_ingest_jobs = [
    {
      state        = "CollectBigKindsNews"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('ingest-raw', '--source', 'bigkinds', '--run-id', $.run_id)"
    },
    {
      state        = "CollectKisPrice"
      taskdef_key  = "kis"
      command_expr = "States.Array('ingest-price-raw', '--source', 'kis', '--run-id', $.run_id)"
    },
    {
      state        = "CollectDartFinancial"
      taskdef_key  = "dart"
      command_expr = "States.Array('ingest-raw-financial', '--source', 'dart', '--run-id', $.run_id)"
    },
    {
      state        = "CollectDartDisclosure"
      taskdef_key  = "dart"
      command_expr = "States.Array('ingest-raw-disclosure', '--run-id', $.run_id)"
    },
    {
      # ETF NAV(ALPHA-380·458) — KIS ETF NAV비교추이(일). 가격과 같은 kis task-def·같은
      # 앱키를 쓰므로 CollectKisPrice 와 동시에 토큰을 발급한다. KIS 는 앱키당 분당 1회만
      # 발급하므로 kis_auth 가 403(EGW00133)을 만나면 61초+지터(0~20초) 대기 후 최대 2회
      # 재시도한다 — 그게 없으면 매 런에서 두 브랜치 중 하나가 죽는다(ALPHA-458 실측 근거).
      state        = "CollectKisNav"
      taskdef_key  = "kis"
      command_expr = "States.Array('ingest-raw-nav', '--run-id', $.run_id)"
    },
    {
      # ETF 프로필(ALPHA-462) — ETF 마스터의 표시명 출처. NAV·구성종목과 같은 kis 세트다.
      state        = "CollectKisEtfProfile"
      taskdef_key  = "kis"
      command_expr = "States.Array('ingest-raw-etf-profile', '--run-id', $.run_id)"
    },
    {
      # 종목별 투자자 수급(ALPHA-482) — KIS FHPTJ04160001. 가격·NAV·ETF프로필과 같은 kis 세트다
      # (같은 앱키·task-def, kis_auth 재시도 공유). 수집 유니버스는 canonical KR holdings 파생
      # (universe_from_holdings, 가격과 같은 축). NormalizeInvestor→LoadEtfFlow 체인의 raw 선행이다.
      state        = "CollectKisInvestor"
      taskdef_key  = "kis"
      command_expr = "States.Array('ingest-raw-investor', '--run-id', $.run_id)"
    },
    {
      # 장중 투자자 추정(ALPHA-767) — KIS HHPTJ04160200. 위 EOD 확정치와 **다른 데이터셋**이다
      # (가집계 추정 vs 확정). 같은 kis 세트라 task-def·앱키·유니버스를 공유한다.
      #
      # ⚠️ 이 잡은 **시장 SFN 에서 제외**된다(아래 market_excluded_states) — 장중 수급 레인
      # (investor_intraday_pipeline.tf)이 5슬롯으로 돌린다. 정의를 여기 두는 이유는 공시·뉴스
      # 레인과 같다: 레인 파일이 이 리스트를 부분집합 필터로 재사용해 command_expr·taskdef_key
      # 드리프트를 막는다(DRY).
      #
      # ⚠️ **날짜창 인자가 없다.** 이 API 는 날짜 파라미터 자체가 없어 오늘치 장중 추정만 준다
      # (소급 백필 불가). run.py 가 `--from/--to` 를 주면 거부한다 — 갭을 메운 줄 착각하게 두지
      # 않으려는 것이고, 그래서 EOD 수집과 달리 창을 넘길 자리가 없다.
      state        = "CollectKisInvestorEstimate"
      taskdef_key  = "kis"
      command_expr = "States.Array('ingest-raw-investor-estimate', '--run-id', $.run_id)"
    },
    # 기준일(ALPHA-387, dev 실측으로 확정): 스케줄이 장 마감 후(15:40 KST, ALPHA-414)라
    # **거래일 런은 그날 PDF 가 이미 게시돼 있다**(07-22·23·24 연속 스냅샷 내용 상이). 반면
    # 비거래일 런은 빈 응답이 아니라 직전 거래일 PDF 가 온다(토 07-18 응답 = 금 07-17 바이트
    # 동일). 그래서 어댑터가 as-of 를 "거래일이면 오늘, 아니면 직전 거래일"로 라벨하고
    # (krx_etf.py `_as_of`), 휴장일 집합은 Planner 와 같은 OPS_KR_HOLIDAYS 를 공유한다.
    # 남은 잔여: **trdDd 백필 수단 부재** — 실패한 날의 스냅샷은 다음 런이 못 줍는다(별도 티켓).
    # 빈 응답은 계속 fail-loud 다. ALPHA-460 이후 그 실패가 뒤 페이즈를 막지는 않는다 —
    # 알림이 나가고 런은 FAILED 로 마감되며 그날 ETF canonical 만 빈다.
    {
      # 수집 상한(ALPHA-581): 상한에 닿으면 남은 ETF 를 미시도로 기록하고 **받은 것은 저장한 뒤**
      # 조기 마감한다. 2026-07-27 15:40 런에서 KRX 가 마감 직후 혼잡으로 느려져 브랜치가 25분을
      # 넘겼고, 사람이 SIGKILL 로 죽이는 순간 이미 받아둔 24종이 저장 전에 날아갔다.
      #
      # 값 근거: 정상 수집은 마감 후 한산할 때 31종 36초(2026-07-27 19:04 실측), 혼잡한 07-24
      # 15:40 런이 579초였다. 300초는 "정상이면 절대 안 닿고, 혼잡이면 부분이라도 건지고 끝난다"
      # 선이다. ⚠️ SFN `TimeoutSeconds` 로 대체하면 안 된다 — 그건 컨테이너를 SIGKILL 해서
      # 오늘과 **똑같은 유실**이 난다(실증). 앱 상한이 먼저고 SFN 타임아웃은 넉넉한 백스톱이다.
      state        = "CollectKrxEtf"
      taskdef_key  = "krx"
      command_expr = "States.Array('ingest-raw-etf', '--source', 'krx', '--run-id', $.run_id, '--deadline-sec', '${var.krx_etf_deadline_sec}')"
    },
  ]

  # raw 병렬 브랜치 = US(토글) + KR. 브랜치는 서로 독립이라 순서는 무관하다.
  raw_ingest_jobs = concat(var.us_fmp_enabled ? local.us_fmp_ingest_jobs : [], local.kr_ingest_jobs)

  # raw 성공 뒤 도는 정제 스테이지(ALPHA-355). raw 와 같은 브랜치 구조를 재사용하되 잡만 다르다.
  # normalize 는 벤더 API 키가 필요 없고(레이크만 읽고 canonical 을 쓴다) 모든 task-def 가 같은
  # task_role(레이크 RW)을 공유하므로, 시크릿 없는 bigkinds task-def 를 재사용한다 — 새 task-def·
  # IAM 불요. normalize-financial 은 아직 canonical 스텝이 없어 제외한다(재무는 raw-only).
  #
  # **`--input-run-id $.run_id` = 이 실행이 수집한 raw 만 정제한다**(ALPHA-389). 정제는
  # 데이터셋별 1잡이고 벤더를 합치는 자리라(한 task 가 source= 로 FMP·KIS 를 함께 읽는다),
  # 9개 raw 브랜치가 같은 run_id 를 쓰는 덕에 스코프 안에 그 런의 전 벤더가 들어온다.
  # 적재 자체는 여전히 멱등이다 — canonical 병합이 파티션의 기존 행을 읽어 합친다.
  # 실패 런 raw 재처리는 아래 NormalizeParallel 주석 참조.
  normalize_jobs = [
    {
      state        = "NormalizeNews"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-news', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizePrice"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-price', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeDisclosure"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-disclosure', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeDisclosureSegment"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-disclosure-segment', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeEtf"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-etf', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeEtfProfile"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-etf-profile', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeEtfNav"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-etf-nav', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      # 투자자 수급 정제(ALPHA-482) — raw investor_flow_daily → canonical. 다른 normalize 와
      # 같이 레이크만 읽어 시크릿 없는 bigkinds task-def 재사용. LoadEtfFlow 의 canonical 선행이다.
      state        = "NormalizeInvestor"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-investor', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      # 장중 투자자 추정 정제(ALPHA-768) — raw investor_flow_intraday → canonical. EOD 와 스텝이
      # 따로인 이유는 정체성 키에 슬롯이 붙어 병합 규칙이 다르기 때문이다(같은 거래일에 5행).
      # 장중 수급 레인 소관 — 시장 SFN 에서는 제외된다(market_excluded_states).
      state        = "NormalizeInvestorEstimate"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-investor-estimate', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
  ]

  # feature/factor 스테이지(구 derive, ALPHA-386→408 개명). canonical 을 소비해 분석이 읽을
  # 산출물을 만든다. normalize 와 갈라 둔 이유는 의존이다 — 전부 canonical 을 읽으므로 정제가
  # 끝난 뒤라야 한다. 세 잡은 서로 독립이고(뉴스 feature vs ETF 마스터 vs 가격변동 트리거)
  # 쓰는 대상이 다르다: tag-news 는 레이크 feature 존, load-instruments·load-price-triggers 는
  # Cloud Event Store(RDB, 서로 다른 테이블·같은 rds task-def). 시크릿이 다른 잡은 task-def 도 따로다.
  #
  # 이 페이즈의 최종 범위(ALPHA-408): 뉴스/공시 assertion·event·event_thread 추출 + 가격이벤트
  # 생성까지. 추출 스텝들은 alphamale 로직의 data-pipeline 이관 합의 후 여기 잡으로 편입된다.
  # 로직·정확도(정준영)와 실행·부하·적재(김진기)의 협업 경계가 이 잡 리스트다.
  feature_jobs = [
    {
      state        = "TagNews"
      taskdef_key  = "deepseek"
      command_expr = "States.Array('tag-news', '--run-id', $.run_id, '--input-run-id', $.run_id, '--limit', '${var.tag_news_limit}')"
    },
    {
      # ETF 가격변동 트리거(ALPHA-406) — canonical 일봉 → price_movement_trigger.
      # ALPHA-1039에서 시장 SFN의 feature 병렬 뒤 직렬 상태로 옮긴다. 잡 정의는 원장·CLI
      # 정본 재사용을 위해 남기고 market_feature_jobs에서만 뺀다.
      state        = "LoadPriceTriggers"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-price-triggers', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      # ETF NAV 마트 적재(ALPHA-383) — canonical etf_nav → etf_nav_daily. feature 페이즈에
      # 두는 이유는 의존이다: normalize 가 canonical 을 쓴 뒤라야 읽을 대상이 있다.
      state        = "LoadEtfNav"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-etf-nav', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      # 문서 마스터(ALPHA-374·410·1031) — NormalizeNews manifest가 지목한 직접 parquet와
      # 현재 실행 article_id만 document로 적재한다. assertion 적재의 FK 선행이다.
      state        = "LoadDocuments"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-documents', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      # 공시 fact 적재(ALPHA-476) — canonical 공시 → document(DISCLOSURE)·disclosure_document·
      # disclosure_fact. issuer 는 company_profile.dart_corp_code 로 해소하므로 **앞 직렬
      # EnrichCorpCode 가 채운 뒤**라야 9→309 로 붙는다(rds task-def, DART API 불요).
      # 현재 normalize manifest winner를 pending에 먼저 commit하고 pending만 typed 적재한다.
      state        = "LoadDisclosure"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-disclosure', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      # 가격 원장 적재(ALPHA-377·1038) — NormalizePrice manifest의 KR direct key와 현재
      # winner만 읽는다. US manifest 항목은 현재 미지원 범위라 실패로 세지 않는다.
      state        = "LoadPriceDaily"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-price-daily', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      # ETF 구성종목 적재(ALPHA-379) — canonical etf_holdings → etf_holding_snapshot.
      # LoadEtfNav·LoadPriceDaily 와 같은 슬롯(normalize 뒤 canonical 을 읽는다).
      # 같은 run의 NormalizeEtf manifest가 지목한 파티션만 읽는다(ALPHA-1011).
      state        = "LoadEtfHoldings"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-etf-holdings', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      # 투자자 수급 적재(ALPHA-385) — canonical investor_flow_daily → investor_flow_daily.
      # LoadEtfNav·LoadPriceDaily·LoadEtfHoldings 와 같은 슬롯(normalize 뒤 canonical 을 읽는다).
      # 같은 run의 NormalizeInvestor manifest가 지목한 direct key와 winner만 읽는다.
      state        = "LoadEtfFlow"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-etf-flow', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      # 장중 투자자 추정 적재(ALPHA-768·1036) — 현재 normalize manifest의 직접 parquet와
      # winner만 읽는다. 결손·손상 시 범위를 전량으로 넓히지 않고 실패한다.
      state        = "LoadInvestorIntraday"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-investor-intraday', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
  ]

  # 뉴스 레인 스텝은 뉴스 SFN(news_pipeline.tf) 소관 — 시장 SFN 페이즈에서 제외한다(ALPHA-553
  # PR2). 잡 **정의**는 위 원본 리스트(raw_ingest_jobs 등)에 남는다: news_pipeline.tf 의
  # news_* 부분집합 필터가 같은 리스트를 읽어 command_expr·taskdef_key 드리프트를 막는다(DRY).
  # LoadAssertions·AssembleEvents(페이즈 뒤 직렬 꼬리)도 뉴스 SFN 으로 이관됐다.
  # ⚠️ 이 자리에 있던 "analyze 가 뉴스 SFN 의 이전 런이 조립해 둔 event 를 소비한다"는 서술은
  # **ALPHA-806 에서 이미 사실이 아니게 됐다** — 그 티켓이 analyze 페이즈를 이 SFN 에서 걷어냈고
  # (아래 FeatureCheckResults 앞 주석) 이 SFN 의 책임은 feature 까지다. 설명은 분봉 트리거 큐를
  # 소비하는 상주 서비스만 만든다. 그래서 ALPHA-893 이 뉴스 오후 슬롯을 내려도 **이 레인이
  # 잃는 소비자는 없다** — 그 의존은 ALPHA-806 시점에 이미 끊겨 있었다.
  # 공시 4스텝도 같은 이유로 빠진다(ALPHA-724 컷오버) — 공시 SFN(disclosure_pipeline.tf)이
  # 하루 10슬롯으로 돌린다. **성능이 아니라 원장 정체성 때문이다**: 작업 정체성의 정본인
  # `catalog.by_cli` 가 CLI 로 해소하는데 두 레인의 CLI 가 같아, 한 스텝을 두 레인이 동시에
  # 소유하면 장중 런의 attempt 가 시장 레인 task_key 로 기록된다(장중 영구 MISSED + 시장
  # LEDGER_GAP). 잡 **정의**는 위 원본 리스트에 남아 공시 SFN 이 부분집합 필터로 재사용한다.
  # 장중 수급 3스텝(ALPHA-769)도 같은 이유로 빠진다 — 장중 수급 레인
  # (investor_intraday_pipeline.tf)이 평일 5슬롯으로 돌린다. 다만 공시·뉴스와 **성격이 다르다**:
  # 저 둘은 시장 SFN 이 돌던 스텝의 소유 레인 이동(컷오버)이었지만, 이 셋은 시장 SFN 이 한 번도
  # 돈 적 없는 **신설**이다(ALPHA-767·768 이 층만 만들고 배선을 안 붙였다). 그래서 "두 레인이
  # 같은 스텝을 동시에 소유하는 겹침 창"이 애초에 없고, 스케줄을 처음부터 ENABLED 로 세운다.
  market_excluded_states = [
    "CollectFmpNews", "CollectBigKindsNews", "NormalizeNews", "TagNews", "LoadDocuments",
    "CollectDartDisclosure", "NormalizeDisclosure", "NormalizeDisclosureSegment", "LoadDisclosure",
    "CollectKisInvestorEstimate", "NormalizeInvestorEstimate", "LoadInvestorIntraday",
  ]
  market_raw_jobs       = [for j in local.raw_ingest_jobs : j if !contains(local.market_excluded_states, j.state)]
  market_normalize_jobs = [for j in local.normalize_jobs : j if !contains(local.market_excluded_states, j.state)]
  # LoadPriceTriggers는 LoadPriceDaily·LoadEtfHoldings DB commit 뒤에만 실행해야 하므로 아래
  # 직렬 꼬리에서 직접 정의한다. Parallel에 남으면 어느 쪽보다 먼저 떠 stale DB를 읽는다.
  market_feature_jobs = [
    for j in local.feature_jobs : j
    if !contains(local.market_excluded_states, j.state) && j.state != "LoadPriceTriggers"
  ]

  raw_ingest_success_checks = [
    for index, _ in local.market_raw_jobs : {
      Variable     = "$.branch_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

  normalize_success_checks = [
    for index, _ in local.market_normalize_jobs : {
      Variable     = "$.normalize_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

  # NormalizePrice·NormalizeEtfNav·NormalizeInvestor exit 2는 성공 winner를 확정한 부분 실패다.
  # 둘은 feature를 실행하되 마지막 strict gate에서 전체 SFN을 FAILED로 닫는다.
  normalize_non_partial_success_checks = [
    for index, job in local.market_normalize_jobs : {
      Variable     = "$.normalize_results[${index}].status"
      StringEquals = "succeeded"
    } if !contains(["NormalizePrice", "NormalizeEtfNav", "NormalizeInvestor"], job.state)
  ]
  normalize_price_index = index(
    [for job in local.market_normalize_jobs : job.state],
    "NormalizePrice",
  )
  normalize_price_continue_check = {
    Or = [
      {
        Variable     = "$.normalize_results[${local.normalize_price_index}].status"
        StringEquals = "succeeded"
      },
      {
        And = [
          {
            Variable  = "$.normalize_results[${local.normalize_price_index}].exit_code"
            IsPresent = true
          },
          {
            Variable      = "$.normalize_results[${local.normalize_price_index}].exit_code"
            NumericEquals = 2
          },
        ]
      },
    ]
  }
  normalize_investor_index = index(
    [for job in local.market_normalize_jobs : job.state],
    "NormalizeInvestor",
  )
  normalize_investor_continue_check = {
    Or = [
      {
        Variable     = "$.normalize_results[${local.normalize_investor_index}].status"
        StringEquals = "succeeded"
      },
      {
        And = [
          {
            Variable  = "$.normalize_results[${local.normalize_investor_index}].exit_code"
            IsPresent = true
          },
          {
            Variable      = "$.normalize_results[${local.normalize_investor_index}].exit_code"
            NumericEquals = 2
          },
        ]
      },
    ]
  }
  normalize_etf_nav_index = index(
    [for job in local.market_normalize_jobs : job.state],
    "NormalizeEtfNav",
  )
  normalize_etf_nav_continue_check = {
    Or = [
      {
        Variable     = "$.normalize_results[${local.normalize_etf_nav_index}].status"
        StringEquals = "succeeded"
      },
      {
        And = [
          {
            Variable  = "$.normalize_results[${local.normalize_etf_nav_index}].exit_code"
            IsPresent = true
          },
          {
            Variable      = "$.normalize_results[${local.normalize_etf_nav_index}].exit_code"
            NumericEquals = 2
          },
        ]
      },
    ]
  }
  normalize_continue_checks = concat(
    local.normalize_non_partial_success_checks,
    [local.normalize_price_continue_check, local.normalize_etf_nav_continue_check,
    local.normalize_investor_continue_check],
  )

  feature_success_checks = [
    for index, _ in local.market_feature_jobs : {
      Variable     = "$.feature_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

  # LoadPriceDaily·LoadEtfNav·LoadEtfFlow exit 2는 성공 winner를 DB에 보존한 부분 실패다.
  # 계속 처리하되 마지막 strict gate가 전체 SFN을 FAILED로 닫는다.
  feature_non_partial_success_checks = [
    for index, job in local.market_feature_jobs : {
      Variable     = "$.feature_results[${index}].status"
      StringEquals = "succeeded"
    } if !contains(["LoadPriceDaily", "LoadEtfNav", "LoadEtfFlow"], job.state)
  ]
  feature_price_index = index(
    [for job in local.market_feature_jobs : job.state],
    "LoadPriceDaily",
  )
  feature_price_continue_check = {
    Or = [
      {
        Variable     = "$.feature_results[${local.feature_price_index}].status"
        StringEquals = "succeeded"
      },
      {
        And = [
          {
            Variable  = "$.feature_results[${local.feature_price_index}].exit_code"
            IsPresent = true
          },
          {
            Variable      = "$.feature_results[${local.feature_price_index}].exit_code"
            NumericEquals = 2
          },
        ]
      },
    ]
  }
  feature_etf_flow_index = index(
    [for job in local.market_feature_jobs : job.state],
    "LoadEtfFlow",
  )
  feature_etf_flow_continue_check = {
    Or = [
      {
        Variable     = "$.feature_results[${local.feature_etf_flow_index}].status"
        StringEquals = "succeeded"
      },
      {
        And = [
          {
            Variable  = "$.feature_results[${local.feature_etf_flow_index}].exit_code"
            IsPresent = true
          },
          {
            Variable      = "$.feature_results[${local.feature_etf_flow_index}].exit_code"
            NumericEquals = 2
          },
        ]
      },
    ]
  }
  feature_etf_nav_index = index(
    [for job in local.market_feature_jobs : job.state],
    "LoadEtfNav",
  )
  feature_etf_nav_continue_check = {
    Or = [
      {
        Variable     = "$.feature_results[${local.feature_etf_nav_index}].status"
        StringEquals = "succeeded"
      },
      {
        And = [
          {
            Variable  = "$.feature_results[${local.feature_etf_nav_index}].exit_code"
            IsPresent = true
          },
          {
            Variable      = "$.feature_results[${local.feature_etf_nav_index}].exit_code"
            NumericEquals = 2
          },
        ]
      },
    ]
  }
  feature_continue_checks = concat(
    local.feature_non_partial_success_checks,
    [local.feature_price_continue_check, local.feature_etf_nav_continue_check,
    local.feature_etf_flow_continue_check],
  )

  ecs_run_task_base = {
    Resource   = "arn:aws:states:::ecs:runTask.sync"
    ResultPath = "$.ecs"
    Parameters = {
      Cluster         = var.cluster_arn
      LaunchType      = "FARGATE"
      PlatformVersion = "LATEST"
      NetworkConfiguration = {
        AwsvpcConfiguration = {
          Subnets        = var.subnet_ids
          SecurityGroups = [aws_security_group.task.id]
          AssignPublicIp = "DISABLED"
        }
      }
    }
  }

  # 모든 페이즈가 동일한 브랜치 구조라 잡 리스트만 바꿔 한 빌더로 재생성한다(ALPHA-355·386).
  # analyze 페이즈는 예외 — 단일 태스크·다른 이미지라 빌더를 안 거치고 아래에 직접 정의한다.
  # news_* 페이즈(ALPHA-553)는 news_pipeline.tf 가 정의한 잡 부분집합이다 — 같은 빌더를 재사용해
  # 뉴스 SFN 브랜치를 만든다(빌더 중복 방지). 기존 raw/normalize/feature 출력은 불변(순수 additive).
  # disclosure_* 페이즈(ALPHA-722)도 같다 — disclosure_pipeline.tf 가 고른 부분집합이다.
  branches_by_phase = {
    for phase, jobs in {
      raw                = local.market_raw_jobs, normalize = local.market_normalize_jobs, feature = local.market_feature_jobs,
      news_raw           = local.news_raw_jobs, news_normalize = local.news_normalize_jobs, news_feature = local.news_feature_jobs,
      disclosure_raw     = local.disclosure_raw_jobs, disclosure_normalize = local.disclosure_normalize_jobs,
      disclosure_feature = local.disclosure_feature_jobs,
      # investor_intraday_* 페이즈(ALPHA-769)도 같다 — investor_intraday_pipeline.tf 가 고른 부분집합이다.
      investor_intraday_raw       = local.investor_intraday_raw_jobs,
      investor_intraday_normalize = local.investor_intraday_normalize_jobs,
      investor_intraday_feature   = local.investor_intraday_feature_jobs,
      # premarket_* 페이즈(ALPHA-963) — premarket_pipeline.tf 소관. 앞 둘은 부분집합
      # 재사용이고 `premarket_universe` 만 그 파일이 새로 정의한 잡이다.
      premarket_raw       = local.premarket_raw_jobs,
      premarket_normalize = local.premarket_normalize_jobs,
      premarket_universe  = local.premarket_universe_jobs
    } :
    phase => [
      for job in jobs : {
        StartAt = job.state
        States = {
          (job.state) = merge(local.ecs_run_task_base, {
            Type = "Task"
            Next = "${job.state}CheckExitCode"
            Catch = [{
              ErrorEquals = ["States.ALL"]
              ResultPath  = "$.error"
              Next        = "${job.state}TaskFailed"
            }]
            Parameters = merge(local.ecs_run_task_base.Parameters, {
              TaskDefinition = aws_ecs_task_definition.this[job.taskdef_key].arn
              Overrides = {
                ContainerOverrides = [{
                  Name        = local.container_name
                  "Command.$" = job.command_expr
                  # 운영 원장(ALPHA-530 #5): 계측 작업(kis 수집·price 정제·price 적재)의 wrapper 가
                  # attempt 에 SFN 실행 ARN·state 이름을 기록하도록 주입한다. 미계측 작업은 이 env 를
                  # 안 읽어 무해하다($$.Execution.Id 는 실행 ARN 이라 attempt↔SFN 계보를 잇는다).
                  Environment = [
                    { Name = "OPS_SFN_STATE_NAME", Value = job.state },
                    { Name = "OPS_SFN_EXECUTION_ARN", "Value.$" = "$$.Execution.Id" },
                  ]
                }]
              }
            })
          })
          "${job.state}CheckExitCode" = {
            Type = "Choice"
            Choices = [{
              Variable      = "$.ecs.Containers[0].ExitCode"
              NumericEquals = 0
              Next          = "${job.state}Succeeded"
            }]
            Default = "${job.state}Failed"
          }
          "${job.state}Succeeded" = {
            Type = "Pass"
            End  = true
            Parameters = {
              job           = job.state
              status        = "succeeded"
              "exit_code.$" = "$.ecs.Containers[0].ExitCode"
              "task_arn.$"  = "$.ecs.TaskArn"
            }
          }
          "${job.state}Failed" = {
            Type = "Pass"
            End  = true
            Parameters = {
              job           = job.state
              status        = "failed"
              cause         = "${job.state} container exited non-zero"
              "exit_code.$" = "$.ecs.Containers[0].ExitCode"
              "task_arn.$"  = "$.ecs.TaskArn"
            }
          }
          "${job.state}TaskFailed" = {
            Type = "Pass"
            End  = true
            Parameters = {
              job       = job.state
              status    = "failed"
              "error.$" = "$.error"
            }
          }
        }
      }
    ]
  }

  raw_ingest_branches = local.branches_by_phase["raw"]
  normalize_branches  = local.branches_by_phase["normalize"]
  feature_branches    = local.branches_by_phase["feature"]

  sfn_definition = jsonencode({
    StartAt        = "RawIngestParallel"
    TimeoutSeconds = var.state_machine_timeout_seconds
    States = {
      RawIngestParallel = {
        Type       = "Parallel"
        Branches   = local.raw_ingest_branches
        ResultPath = "$.branch_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NotifyFailure" }]
        Next       = "RawIngestCheckResults"
      }
      # raw 부분 실패는 뒤 페이즈를 **막지 않는다**(ALPHA-460) — 알리기만 하고 계속 간다.
      # 예전엔 여기가 전량성공 게이트라 소스 하나가 죽으면 무관한 소스의 정제·분석까지 통째로
      # 멈췄다. 뉴스 수집 실패가 가격 정제를 막는 건 의도가 아니고, 재무는 canonical 스텝조차
      # 없어 아무것도 공급하지 않는데도 전체를 막았다.
      #
      # 막을 필요가 없는 근거: **정제는 빈 입력을 정상 성공으로 처리한다.** raw 키가 0개면
      # 루프가 안 돌고 exit 0 이다(normalize_price.py 의 `for raw_key in raw_keys`). 그래서
      # 수집 하나가 죽어도 그 데이터셋 정제는 남은 raw 만 정제하고 성공한다 — 정제 잡별로
      # "어느 raw 가 필수인가" 의존 맵을 ASL 에 적을 이유가 없다. 있는 만큼 처리한다.
      RawIngestCheckResults = {
        Type = "Choice"
        Choices = [{
          And  = local.raw_ingest_success_checks
          Next = "NormalizeParallel"
        }]
        Default = "NotifyRawPartial"
      }
      # ⚠️ **알림은 여기서 즉시 쏜다 — 끝으로 미루면 안 된다.** 뒤에는 analyze 처럼
      # LLM 을 부르는(소요시간 상한이 없는) 페이즈가 있고, 최상위 TimeoutSeconds 로 실행이
      # 죽으면 States.Timeout 이 실행 자체를 끝내 **어떤 Catch 도 안 탄다**(아래 CloudWatch
      # 알람 주석 참조). 즉 판정을 끝에 두면 "raw 부분 실패 + 그 뒤 타임아웃" 조합에서 run_id 가
      # 박힌 알림이 영영 안 나가고, run 스코프 정제라 그 raw 는 아무도 못 줍는다.
      # 타임아웃 알람은 실행 단위라 run_id·branch_results 를 담지 못해 대체재가 못 된다.
      #
      # 통보 뒤 NormalizeParallel 로 **계속 간다**(ResultPath = null 로 $ 를 보존). 런의 최종
      # FAILED 마감은 파이프라인 끝 RawPartialCheck 가 맡는다 — 거긴 SNS 를 다시 쏘지 않는다
      # (한 실패에 두 통이 가지 않게. 아래 ExecutionsFailed 알람을 안 거는 것과 같은 이유).
      NotifyRawPartial = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "NormalizeParallel"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          "Subject.$" = "States.Format('[${var.name}] raw 부분 실패 — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      #
      # analyze 까지 부분 입력으로 도는 것도 의도다: 준실시간에선 '완전한 입력'이라는 상태가
      # 존재하지 않아 입력 완전성 게이트는 주기가 짧아질수록 '매번 불성립'으로 수렴한다.
      # 대신 트리거 결측이 '데이터 없음'이 아니라 '움직임 없음'으로 나가는 위험이 남는데,
      # 그건 게이트가 아니라 산출물이 두 상태를 구분해야 풀리는 문제다(ALPHA-452·453 소관).
      #
      # ⚠️ **정제는 이 실행의 raw 만 본다**(`--input-run-id $.run_id`, ALPHA-389). 예전엔
      # full-scan 이라 "이전 실패 실행이 저장한 raw 도 다음 성공 실행이 함께 주워간다"는
      # 자동 구제가 있었는데, **그게 없어졌다.** 대가로 정제 비용이 여태 쌓인 raw 전체가
      # 아니라 이번 런에 비례한다(옛 구조는 영구히 O(전체 raw)였다).
      #
      # 그래서 **실패한 실행의 raw 는 명시적으로 주워와야 한다** — 자동으로 안 된다:
      #   normalize-<step> --run-id <새 id> --input-run-id <실패한 run_id>   # 그 런만
      #   normalize-<step> --run-id <새 id>                                  # 전체 백필
      #
      # 이 절차의 트리거는 NotifyFailure 알림이고, 제목에 실패한 run_id 가 박혀 나온다.
      # ⚠️ **그래서 `pipeline_alarm_email` 이 반드시 설정돼 있어야 한다** — null 이면 구독
      # 리소스가 count=0 으로 안 생겨 알림이 구독자 없는 토픽으로 사라지고, 그러면 미승격
      # run 을 **아무도 모른다**(ALPHA-389 착수 시 dev 토픽 구독자가 실제로 0이었다).
      # 수집 창이 '오늘'인 소스(BigKinds·DART·KRX ETF)는 다음 런이 그 날짜를 재수집하지도
      # 않으므로, 알림을 놓치면 그 날 데이터는 raw 에만 남고 canonical 에 영영 없다.
      #
      # 자동 구제가 나아 보이지만, 옛 구조는 "언젠가 주워진다"라 **아무도 그게 언제였는지
      # 몰랐다**. 명시적 재처리는 누가 언제 무엇을 승격했는지가 남는다 — 단 그 대가로 알림이
      # 살아 있어야 한다는 조건이 붙는다.
      NormalizeParallel = {
        Type       = "Parallel"
        Branches   = local.normalize_branches
        ResultPath = "$.normalize_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NotifyFailure" }]
        Next       = "NormalizeCheckResults"
      }
      NormalizeCheckResults = {
        Type = "Choice"
        Choices = [
          { And = local.normalize_success_checks, Next = "LoadInstruments" },
          { And = local.normalize_continue_checks, Next = "NotifyNormalizePartial" },
        ]
        Default = "NotifyFailure"
      }
      NotifyNormalizePartial = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "LoadInstruments"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.normalize_partial_notification_error"
          Next        = "LoadInstruments"
        }]
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          "Subject.$" = "States.Format('[${var.name}] normalize 부분 실패 — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      # 정제 전량 성공 또는 manifest producer(price/NAV/investor) exit 2일 때 마스터 적재로 넘어간다.
      #
      # ⚠️ 위 raw→normalize 게이트와 **성격이 다르다**(ALPHA-389 이후). 거기는 정제가 이제
      # run 스코프라 실패 런의 raw 가 자동으로 안 주워진다(영구 격리 — 사람이 재처리). 반면
      # 다른 적재 잡은 아직 canonical full-scan으로 다음 런에 회복되지만 LoadPriceDaily와
      # LoadEtfNav·LoadEtfFlow는 현재 manifest만 읽는다. producer exit 2의 성공 winner는 이 실행에서
      # 반드시 적재한다.
      # 종목·ETF 마스터 적재 — feature 병렬 **앞 직렬**이다(ALPHA-462). fact 로더들
      # (LoadEtfNav·LoadPriceTriggers)이 instrument/etf_profile 을 FK 로 참조하는데, 같은
      # 병렬 페이즈에 두면 마스터 커밋 전에 fact 로더가 instrument 스냅샷을 읽어 그 ETF 를
      # unknown 으로 건너뛰고 **성공으로 끝난다** — 그 런은 조용히 데이터를 빠뜨린다.
      # LoadAssertions 가 document FK 때문에 뒤 직렬인 것과 같은 이유·같은 형태다.
      # 자연키 멱등이라 재실행 안전하고, 마스터가 없을 때만 발번한다(ADR-0027).
      LoadInstruments = merge(local.ecs_run_task_base, {
        Type = "Task"
        Next = "LoadInstrumentsCheckExitCode"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "NotifyFailure"
        }]
        Parameters = merge(local.ecs_run_task_base.Parameters, {
          TaskDefinition = aws_ecs_task_definition.this["rds"].arn
          Overrides = {
            ContainerOverrides = [{
              Name        = local.container_name
              "Command.$" = "States.Array('load-instruments', '--run-id', $.run_id)"
              # 원장 계측(ALPHA-181): 페이즈 빌더(위)와 같은 주입 — 없으면 이 직렬 작업들의
              # attempt 에 sfn_state_name·실행 ARN 이 NULL 로 남아 attempt↔SFN 계보가 끊긴다.
              Environment = [
                { Name = "OPS_SFN_STATE_NAME", Value = "LoadInstruments" },
                { Name = "OPS_SFN_EXECUTION_ARN", "Value.$" = "$$.Execution.Id" },
              ]
            }]
          }
        })
      })
      LoadInstrumentsCheckExitCode = {
        Type = "Choice"
        Choices = [{
          Variable      = "$.ecs.Containers[0].ExitCode"
          NumericEquals = 0
          Next          = "EnrichCorpCode"
        }]
        Default = "NotifyFailure"
      }
      # corp_code enrichment(ALPHA-491) — LoadInstruments 가 만든 company_profile 의 NULL
      # dart_corp_code 를 OpenDART corpCode.xml 매칭으로 채운다. **LoadInstruments 뒤·FeatureParallel
      # 앞 직렬**이다: FeatureParallel 의 LoadDisclosure 가 issuer 를 dart_corp_code 로 해소하므로
      # 그 전에 채워져야 9→309 로 붙는다(같은 형태·같은 이유로 LoadInstruments 도 직렬 선행).
      # DB(company_profile UPDATE)와 DART API 를 둘 다 부르므로 결합 시크릿 task-def(rds_dart)를 쓴다.
      # 멱등: NULL 가드 UPDATE 라 재실행이 시드 9종·기존 충전분을 덮지 않는다.
      EnrichCorpCode = merge(local.ecs_run_task_base, {
        Type = "Task"
        Next = "EnrichCorpCodeCheckExitCode"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "NotifyFailure"
        }]
        Parameters = merge(local.ecs_run_task_base.Parameters, {
          TaskDefinition = aws_ecs_task_definition.this["rds_dart"].arn
          Overrides = {
            ContainerOverrides = [{
              Name        = local.container_name
              "Command.$" = "States.Array('enrich-corp-code', '--run-id', $.run_id)"
              # 원장 계측(ALPHA-181): 페이즈 빌더(위)와 같은 주입 — 없으면 이 직렬 작업들의
              # attempt 에 sfn_state_name·실행 ARN 이 NULL 로 남아 attempt↔SFN 계보가 끊긴다.
              Environment = [
                { Name = "OPS_SFN_STATE_NAME", Value = "EnrichCorpCode" },
                { Name = "OPS_SFN_EXECUTION_ARN", "Value.$" = "$$.Execution.Id" },
              ]
            }]
          }
        })
      })
      EnrichCorpCodeCheckExitCode = {
        Type = "Choice"
        Choices = [{
          Variable      = "$.ecs.Containers[0].ExitCode"
          NumericEquals = 0
          Next          = "FeatureParallel"
        }]
        Default = "NotifyFailure"
      }
      FeatureParallel = {
        Type       = "Parallel"
        Branches   = local.feature_branches
        ResultPath = "$.feature_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NotifyFailure" }]
        Next       = "FeatureCheckResults"
      }
      # LoadAssertions·AssembleEvents 는 뉴스 SFN 의 직렬 꼬리로 이관됐다(ALPHA-553 PR2 —
      # news_pipeline.tf).
      #
      # analyze 페이즈는 여기서 끝났다(ALPHA-806). 설명은 분봉 트리거 큐를 소비하는 상주
      # 서비스(minute_services.tf `analysis_consumer`)만 만든다 — 일 단위 팬아웃은 확정
      # 일봉을 기다려야 해서 장중엔 원리적으로 층을 못 세웠고(`layer_route=미상`), 같은
      # 대상에 분봉 경로와 다른 답을 냈다. 이 SFN 의 책임은 feature 까지다.
      FeatureCheckResults = {
        Type = "Choice"
        Choices = [{
          And  = local.feature_continue_checks
          Next = "LoadPriceTriggers"
        }]
        Default = "NotifyFailure"
      }
      # 가격·holdings loader의 DB commit을 모두 본 뒤 현재 NormalizePrice manifest 범위만
      # 평가한다(ALPHA-1039). exit 2는 성공 ETF를 보존한 부분 실패라 마지막 strict gate까지
      # 진행하지만, exit 1/Task 실패는 범위를 신뢰할 수 없어 즉시 NotifyFailure다.
      LoadPriceTriggers = merge(local.ecs_run_task_base, {
        Type = "Task"
        Next = "LoadPriceTriggersCheckExitCode"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "NotifyFailure"
        }]
        Parameters = merge(local.ecs_run_task_base.Parameters, {
          TaskDefinition = aws_ecs_task_definition.this["rds"].arn
          Overrides = {
            ContainerOverrides = [{
              Name        = local.container_name
              "Command.$" = "States.Array('load-price-triggers', '--run-id', $.run_id, '--input-run-id', $.run_id)"
              Environment = [
                { Name = "OPS_SFN_STATE_NAME", Value = "LoadPriceTriggers" },
                { Name = "OPS_SFN_EXECUTION_ARN", "Value.$" = "$$.Execution.Id" },
              ]
            }]
          }
        })
      })
      LoadPriceTriggersCheckExitCode = {
        Type = "Choice"
        Choices = [
          {
            Variable      = "$.ecs.Containers[0].ExitCode"
            NumericEquals = 0
            Next          = "FeaturePartialCheck"
          },
          {
            Variable      = "$.ecs.Containers[0].ExitCode"
            NumericEquals = 2
            Next          = "FeaturePartialCheck"
          },
        ]
        Default = "NotifyFailure"
      }
      # 가격 DB loader 또는 트리거의 exit 2는 성공 범위를 보존했지만 운영상 실패다. 최종
      # strict gate만으로 FAILED 처리하면 SNS를 우회하므로, 여기서 한 번 알리고 마감한다.
      FeaturePartialCheck = {
        Type = "Choice"
        Choices = [{
          Or = [
            {
              And = [
                {
                  Variable  = "$.feature_results[${local.feature_price_index}].exit_code"
                  IsPresent = true
                },
                {
                  Variable      = "$.feature_results[${local.feature_price_index}].exit_code"
                  NumericEquals = 2
                },
              ]
            },
            {
              And = [
                {
                  Variable  = "$.feature_results[${local.feature_etf_nav_index}].exit_code"
                  IsPresent = true
                },
                {
                  Variable      = "$.feature_results[${local.feature_etf_nav_index}].exit_code"
                  NumericEquals = 2
                },
              ]
            },
            {
              And = [
                {
                  Variable  = "$.feature_results[${local.feature_etf_flow_index}].exit_code"
                  IsPresent = true
                },
                {
                  Variable      = "$.feature_results[${local.feature_etf_flow_index}].exit_code"
                  NumericEquals = 2
                },
              ]
            },
            {
              Variable      = "$.ecs.Containers[0].ExitCode"
              NumericEquals = 2
            },
          ]
          Next = "NotifyFeaturePartial"
        }]
        Default = "RawPartialCheck"
      }
      NotifyFeaturePartial = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "RawPartialCheck"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          "Subject.$" = "States.Format('[${var.name}] feature 부분 실패 — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.feature_partial_notification_error"
          Next        = "RawPartialCheck"
        }]
      }
      # raw 부분 실패 런의 **마감 판정**(ALPHA-460) — 막는 게이트가 아니다. 다운스트림을 끝까지
      # 돌린 뒤 raw 를 다시 보고, 부분 실패였으면 런을 FAILED 로 끝낸다. 알림은 이미 raw 직후
      # NotifyRawPartial 이 쐈으므로 **여기선 SNS 를 안 탄다**(한 실패에 두 통 금지) — 곧장
      # PipelineFailed 로 간다.
      #
      # 이 상태가 없으면 안 되는 이유: 알림만으로는 실행이 Succeed 로 남아 콘솔·ExecutionsFailed
      # 지표에서 정상 런과 구분되지 않는다. raw 가 불완전한 런은 상태로도 실패여야 한다.
      #
      # `$.branch_results` 는 RawIngestParallel 이 쓴 뒤 여기까지 살아 있다 — 뒤 Task 들이
      # ResultPath 를 `$.ecs` 로 쓰고 Parallel 들도 각자 다른 키를 써서 덮이지 않는다.
      RawPartialCheck = {
        Type = "Choice"
        Choices = [{
          And = concat(
            local.raw_ingest_success_checks,
            local.normalize_success_checks,
            local.feature_success_checks,
            [{
              Variable      = "$.ecs.Containers[0].ExitCode"
              NumericEquals = 0
            }],
          )
          Next = "PipelineSucceeded"
        }]
        Default = "PipelineFailed"
      }
      PipelineSucceeded = { Type = "Succeed" }
      NotifyFailure = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "PipelineFailed"
        Parameters = {
          TopicArn = aws_sns_topic.alarms.arn
          # run_id 를 제목에 박는다 — 정제가 run 스코프가 된 뒤로(ALPHA-389) 실패 런의 raw 는
          # **사람이 그 run_id 로 명시 재처리**해야 승격된다. 제목이 전부 "pipeline FAILED" 로
          # 같으면 메일함에서 어느 런을 주워와야 하는지 알 수 없어 절차가 시작되지 않는다.
          # 본문(전체 상태 JSON)에 어느 브랜치가 실패했는지가 들어 있다.
          "Subject.$" = "States.Format('[${var.name}] FAILED — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      PipelineFailed = { Type = "Fail", Cause = "pipeline failed" }
    }
  })
}

# 이름에 접미사를 두지 않는다(ALPHA-408, 구 "-raw-ingest") — raw 수집만이 아니라
# raw → normalize → feature → analyze 전체가 이 상태머신이다. 이름 변경은 destroy+recreate 지만
# SFN 은 무상태라 안전하다(실행 이력만 새 ARN 에서 다시 시작).
resource "aws_sfn_state_machine" "this" {
  name       = var.name
  role_arn   = aws_iam_role.sfn.arn
  definition = local.sfn_definition
}

# 상태머신 정의 안의 NotifyFailure 는 **정의가 살아 있을 때만** 통보한다. 최상위
# TimeoutSeconds 로 실행이 죽으면 States.Timeout 이 실행 자체를 끝내므로 어떤 Catch 도
# 타지 않고 — 즉 SNS 로 아무것도 안 나간다. LLM 을 부르는 페이즈(analyze — tag-news 는 뉴스
# SFN 이관, ALPHA-553)가 있어 이 경로가 실질 도달 가능하다(LLM 호출은 소요시간 상한이 없다).
# 알람은 정의 밖에서 도는 유일한 통보 수단이라 그 구멍을 정확히 메운다.
# ExecutionsFailed 는 안 건다 — NotifyFailure 가 이미 덮고, 겹치면 같은 실패에 두 통이 온다.
resource "aws_cloudwatch_metric_alarm" "execution_timed_out" {
  alarm_name        = "${var.name}-execution-timed-out"
  alarm_description = "SFN 실행이 TimeoutSeconds 초과로 죽었다 — 정의 안의 NotifyFailure 가 못 잡는 경로다."
  namespace         = "AWS/States"
  metric_name       = "ExecutionsTimedOut"
  dimensions        = { StateMachineArn = aws_sfn_state_machine.this.arn }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
}

resource "aws_scheduler_schedule" "daily" {
  name                         = "${var.name}-daily"
  state                        = var.schedule_state
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  # 운영 원장(ALPHA-530): daily 트리거가 SFN 을 **직접** 시작하지 않고 **Planner** 를 띄운다.
  # Planner 가 실행 전 pipeline_run+expected_task 를 원장에 남기고(관측 정본) SFN 을 시작한다 —
  # 그래야 SFN 이 아예 안 떠도 "실행 자체가 안 됐다"를 탐지할 수 있다(스펙 §5). 스케줄 시각은
  # <aws.scheduler.scheduled-time> 를 env(OPS_SCHEDULED_TIME)로 넘겨 Planner 가 슬롯을 계산한다.
  #
  # ⚠️ retry/DLQ 의미가 바뀐다(edge-review): 스케줄러는 **RunTask 제출**까지만 보므로 아래
  # retry/DLQ 는 "Planner 컨테이너가 뜨지 못한" 경우만 덮는다. Planner 가 뜬 뒤 DB·StartExecution
  # 실패로 exit≠0 이어도 스케줄러엔 성공으로 보인다 — 그 공백은 **Reconciler 가 메운다**:
  # pipeline_run 이 없으면 PLANNER_MISSING, 있는데 SFN 실행이 확인 안 되면 LAUNCH_UNCONFIRMED
  # (Planner 가 pipeline_run 을 먼저 커밋한 뒤 StartExecution 하므로 두 경우가 갈린다).
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler.arn
    # ⚠️ 플레이스홀더는 jsonencode 바깥 replace 로 주입한다(ALPHA-593, 뉴스 스케줄과 동일) —
    # jsonencode 의 </> 이스케이프 때문에 EventBridge 가 치환하지 못해 리터럴이
    # 전달됐고, Planner 의 `_scheduled_time()` 이 파싱 실패 → **now() 폴백으로 조용히**
    # 동작해 왔다(정시 실행은 같은 분이라 자가 은폐, 지연 재시도(최대 24h·185회)는 원래
    # 슬롯이 아닌 재시도 시각으로 run_key 를 만든다).
    input = replace(
      jsonencode({
        Cluster        = var.cluster_arn
        TaskDefinition = aws_ecs_task_definition.ops.arn
        LaunchType     = "FARGATE"
        NetworkConfiguration = {
          AwsvpcConfiguration = {
            Subnets        = var.subnet_ids
            SecurityGroups = [aws_security_group.task.id]
            AssignPublicIp = "DISABLED"
          }
        }
        Overrides = {
          ContainerOverrides = [{
            Name        = local.container_name
            Command     = ["plan-run"]
            Environment = [{ Name = "OPS_SCHEDULED_TIME", Value = "SCHEDULED_TIME_TOKEN" }]
          }]
        }
      }),
      "SCHEDULED_TIME_TOKEN", "<aws.scheduler.scheduled-time>",
    )

    retry_policy {
      maximum_event_age_in_seconds = 86400
      maximum_retry_attempts       = 185
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dlq.arn }
  }
}
