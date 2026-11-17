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
  # 잡 목록 — 시장 SFN(edge-*-data-pipeline)은 지웠다(ALPHA-1498, 시장 마감 수집은 Airflow `edge_etf_daily`).
  # 목록은 남는다: 다른 레인 SFN(뉴스·공시·장중 수급·장전)이 부분집합 필터로 재사용하고, Airflow DAG 테스트
  # (apps/engine/airflow/tests/sfn_commands.py)와 카탈로그 테스트가 명령·태스크 정의의 정본으로 읽는다.
  #
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
      command_expr = "States.Array('ingest-price-raw', '--source', 'kis', '--max-failed-symbols', '1', '--run-id', $.run_id)"
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
      command_expr = "States.Array('ingest-raw-investor', '--max-failed-symbols', '1', '--run-id', $.run_id)"
    },
    {
      # 업종지수 일봉 종가(ALPHA-1254) — KIS FHKUP03500100. 분봉 레인(sector_index_minute)은 15:30
      # 단일가 체결을 담지 못해 공식 종가를 일봉 TR 로 따로 받는다. 대상은 분봉 레인과 같은
      # `[minute_sector_index.index_map]`, 자격증명은 같은 kis 세트다.
      state        = "CollectKisSectorIndexDaily"
      taskdef_key  = "kis"
      command_expr = "States.Array('ingest-raw-sector-index-daily', '--run-id', $.run_id)"
    },
    {
      # 장중 투자자 추정(ALPHA-767) — KIS HHPTJ04160200. 위 EOD 확정치와 **다른 데이터셋**이다
      # (가집계 추정 vs 확정). 같은 kis 세트라 task-def·앱키·유니버스를 공유한다.
      #
      # ⚠️ 이 잡은 장중 수급 레인(investor_intraday_pipeline.tf)이 5슬롯으로 돌린다. 정의를 여기 두는 이유는 공시·뉴스
      # 레인과 같다: 레인 파일이 이 리스트를 부분집합 필터로 재사용해 command_expr·taskdef_key
      # 드리프트를 막는다(DRY).
      #
      # ⚠️ **날짜창 인자가 없다.** 이 API 는 날짜 파라미터 자체가 없어 오늘치 장중 추정만 준다
      # (소급 백필 불가). run.py 가 `--from/--to` 를 주면 거부한다 — 갭을 메운 줄 착각하게 두지
      # 않으려는 것이고, 그래서 EOD 수집과 달리 창을 넘길 자리가 없다.
      state        = "CollectKisInvestorEstimate"
      taskdef_key  = "kis"
      command_expr = "States.Array('ingest-raw-investor-estimate', '--max-failed-symbols', '1', '--run-id', $.run_id)"
    },
    # 기준일(ALPHA-387, dev 실측으로 확정): 스케줄이 장 마감 후(15:40 KST, ALPHA-414)라
    # **거래일 런은 그날 PDF 가 이미 게시돼 있다**(07-22·23·24 연속 스냅샷 내용 상이). 반면
    # 비거래일 런은 빈 응답이 아니라 직전 거래일 PDF 가 온다(토 07-18 응답 = 금 07-17 바이트
    # 동일). 그래서 어댑터가 as-of 를 "거래일이면 오늘, 아니면 직전 거래일"로 라벨하고
    # (krx_etf.py `_as_of`), 휴장일 집합은 Planner 와 같은 OPS_KR_HOLIDAYS 를 공유한다.
    # 과거 실패일은 수동 ECS 실행에서 같은 날짜의 --from/--to를 주어 단일 거래일 백필한다
    # (ALPHA-1063). 정규 SFN은 인자를 생략해 오늘/직전 거래일 규약을 그대로 쓴다.
    # 빈 응답은 계속 fail-loud 다. ALPHA-460 이후 그 실패가 뒤 페이즈를 막지는 않는다 —
    # 알림이 나가고 런은 FAILED 로 마감되며 그날 ETF canonical 만 빈다.
    {
      # 수집 상한(ALPHA-581): 상한에 닿으면 남은 ETF 를 미시도로 기록하고 **받은 것은 저장한 뒤**
      # 조기 마감한다. 2026-07-27 15:40 런에서 KRX 가 마감 직후 혼잡으로 느려져 브랜치가 25분을
      # 넘겼고, 사람이 SIGKILL 로 죽이는 순간 이미 받아둔 24종이 저장 전에 날아갔다.
      #
      # 값 근거(`krx_etf_deadline_sec`, 지금 660초 — 변수 주석이 정본): 처음 300초는 31종일 때 정했다. 한산할 때
      # 36초(2026-07-27 19:04 실측), 혼잡한 07-24 15:40 런이 579초였고, "정상이면 절대 안 닿고, 혼잡이면 부분이라도
      # 건지고 끝난다" 선이었다. 대상이 늘면 정상 수집도 그 선에 닿는다(요청 간격 1초 × ETF 수) — ALPHA-1250 이
      # 660초로 올렸다. ⚠️ 부분으로 건진 것은 raw 에만 남는다: 정제는 전량 성공 런만 반영하므로 그날 구성 목록은
      # 갱신되지 않는다. ⚠️ SFN `TimeoutSeconds` 로 대체하면 안 된다 — 그건 컨테이너를 SIGKILL 해서
      # 오늘과 **똑같은 유실**이 난다(실증). 앱 상한이 먼저고 SFN 타임아웃은 넉넉한 백스톱이다.
      state        = "CollectKrxEtf"
      taskdef_key  = "krx"
      command_expr = "States.Array('ingest-raw-etf', '--source', 'krx', '--run-id', $.run_id, '--deadline-sec', '${var.krx_etf_deadline_sec}')"
    },
  ]

  # raw 잡 = US(토글) + KR. 레인 파일이 이 목록에서 자기 잡을 고른다.
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
  # 실패 런 raw 재처리: 정제가 run 범위라 실패 런의 raw 는 다음 런이 자동으로 줍지 않는다. 사람이 그 run_id 로
  # 회수한다 — Airflow 레인은 conf `reprocess_slot` 재처리 run, 그 밖은
  # `normalize-<step> --run-id <새 id> --input-run-id <실패한 run_id>`. 출발점은 run 실패 통보에 실린
  # pipeline_run_id 다. 수집 창이 '오늘'인 소스(BigKinds·DART·KRX ETF)는 다음 런이 그 날짜를 다시 받지 않는다.
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
      # 업종지수 일봉 정제(ALPHA-1254) — raw sector_index_daily → canonical. NAV 정제와 같은 장치다.
      state        = "NormalizeSectorIndexDaily"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-sector-index-daily', '--run-id', $.run_id, '--input-run-id', $.run_id)"
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
      # 장중 수급 레인 소관.
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
      # 가격·holdings 적재의 DB commit 을 모두 본 뒤에만 돈다(ALPHA-1039 — 실행 순서는 DAG 가 정한다).
      state        = "LoadPriceTriggers"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-price-triggers', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      # 종목·ETF 마스터 적재(ALPHA-462·1048) — 정제 6개 뒤, 적재 5개 앞 직렬. fact 로더들이 instrument·
      # etf_profile 을 FK 로 참조해, 마스터 커밋 전에 돌면 그 ETF 를 unknown 으로 건너뛰고 성공으로 끝난다.
      # `--latest-good` 은 producer 가 확정한 last-good snapshot 만 읽는다(shared canonical 역사 재스캔 금지).
      # 시장 SFN 의 직렬 state 였다 — 명령 정본으로 여기 둔다(실행은 Airflow `edge_etf_daily`).
      state        = "LoadInstruments"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-instruments', '--run-id', $.run_id, '--latest-good')"
    },
    {
      # corp_code 채움(ALPHA-491) — LoadInstruments 가 만든 company_profile 의 NULL dart_corp_code 를
      # OpenDART corpCode.xml 로 채운다. DB 와 DART API 를 둘 다 부르므로 결합 시크릿 task-def(rds_dart).
      # NULL 가드 UPDATE 라 재실행해도 기존 값을 덮지 않는다. LoadInstruments 와 같은 이유로 여기 둔다.
      state        = "EnrichCorpCode"
      taskdef_key  = "rds_dart"
      command_expr = "States.Array('enrich-corp-code', '--run-id', $.run_id)"
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
      # 지수 일봉 적재(ALPHA-1260) — canonical sector_index_daily → market_series_daily.
      # 같은 run의 NormalizeSectorIndexDaily manifest가 지목한 direct key와 winner만 읽고,
      # market_series 에 등록된 지수(코스피·코스닥)만 싣는다.
      state        = "LoadMarketSeriesDaily"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-market-series-daily', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      # 장중 투자자 추정 적재(ALPHA-768·1036) — 현재 normalize manifest의 직접 parquet와
      # winner만 읽는다. 결손·손상 시 범위를 전량으로 넓히지 않고 실패한다.
      state        = "LoadInvestorIntraday"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-investor-intraday', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
  ]

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
  # 뉴스 SFN 브랜치를 만든다(빌더 중복 방지).
  # disclosure_* 페이즈(ALPHA-722)도 같다 — disclosure_pipeline.tf 가 고른 부분집합이다.
  branches_by_phase = {
    for phase, jobs in {
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
          # ⚠️ ECS runTask.sync 는 비0 종료를 Task 실패(States.TaskFailed)로 올린다 — 비0 종료는
          # 위 CheckExitCode→Failed 가 아니라 여기로 온다. exit_code 를 안 실으면 "exit 2 는 부분
          # 성공이라 계속" 조건(normalize_*·feature_*_continue_check)이 영영 거짓이 되어 적재가
          # 통째로 빠진다(ALPHA-1113, 09-01~09-28 계속 경로 진입 0건). cause 는 ECS 태스크 JSON
          # 이라 ExitCode·TaskArn 을 풀어 싣는다. 그 밖의 오류(타임아웃·RunTask API 예외·ExitCode
          # 없는 시작 실패)는 exit_code 없이 기존 출력 그대로 — 계속 조건이 거짓이라 fail-closed.
          # Pass 는 Catch 가 없어 StringToJson 이 비-JSON cause 에 터지면 브랜치째 죽는다(raw 페이즈는
          # 실패 브랜치를 partial 로 넘기는데 그게 Parallel 실패로 바뀐다) — JSON 모양일 때만 푼다.
          "${job.state}TaskFailed" = {
            Type = "Choice"
            Choices = [{
              And = [
                { Variable = "$.error.Error", StringEquals = "States.TaskFailed" },
                # 없는 경로를 StringMatches 로 참조하면 Choice 가 States.Runtime 으로 죽는다(test-state 실측).
                { Variable = "$.error.Cause", IsPresent = true },
                { Variable = "$.error.Cause", StringMatches = "{*" },
              ]
              Next = "${job.state}ParseFailedTask"
            }]
            Default = "${job.state}TaskFailedNoExitCode"
          }
          "${job.state}ParseFailedTask" = {
            Type       = "Pass"
            Parameters = { "cause.$" = "States.StringToJson($.error.Cause)" }
            ResultPath = "$.failed_task"
            Next       = "${job.state}HasExitCode"
          }
          "${job.state}HasExitCode" = {
            Type = "Choice"
            Choices = [{
              And = [
                { Variable = "$.failed_task.cause.Containers[0].ExitCode", IsPresent = true },
                { Variable = "$.failed_task.cause.TaskArn", IsPresent = true },
              ]
              Next = "${job.state}TaskFailedWithExitCode"
            }]
            Default = "${job.state}TaskFailedNoExitCode"
          }
          "${job.state}TaskFailedWithExitCode" = {
            Type = "Pass"
            End  = true
            Parameters = {
              job           = job.state
              status        = "failed"
              cause         = "${job.state} container exited non-zero"
              "exit_code.$" = "$.failed_task.cause.Containers[0].ExitCode"
              "task_arn.$"  = "$.failed_task.cause.TaskArn"
              "error.$"     = "$.error"
            }
          }
          "${job.state}TaskFailedNoExitCode" = {
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
}
