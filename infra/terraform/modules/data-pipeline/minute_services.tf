# 1분 파이프라인 상주 실행체 (ALPHA-711) — 큐 4종 + 상주 서비스(가격 3종 + 뉴스 소비자 2종, ALPHA-713).
#
# SFN 단발 task 와 달리 **ECS Service** 다: Worker(수집)·Relay(outbox 발행)·
# Consumer(가격 판정)는 tick 루프 상주 프로세스고, 재기동 책임이 ECS 에 있다
# (DB 오류는 프로세스가 죽어서 드러낸다 — 각 *_cli 계약).
# 세션 결속 생산자 3종 포함 — 세션 오케스트레이션이 그 세션도 함께 계획·드레인한다:
# news-worker(ALPHA-717, news_minute/bigkinds) · disclosure-worker(ALPHA-875,
# disclosure_minute/dart) · inav-worker(ALPHA-882, etf_inav_minute/kis).
# 셋 다 공용 스케일 목록에서 빠지고 자기 목록으로 올라간다(local.session_bound_workers).
#
# ⚠️ desired_count 는 **세션 오케스트레이션이 런타임에 바꾸는 값**이다 —
# lifecycle ignore_changes 가 없으면 무관한 apply 가 장중에 워커를 내린다.
# 초기값 0: 큐·서비스 정의가 먼저 착지하고, 스케일은 오케스트레이션 소관이다.
# 그 주체는 이 파일 아래의 `aws_scheduler_schedule.minute_session` 이다(ALPHA-712).

locals {
  # 큐 어휘 — jobs.py DESTINATION_JOB_KINDS(3종) + TRIGGER_EVENT_DESTINATIONS(1종)와
  # 같은 이름이어야 한다(relay 기동 검증 KNOWN_DESTINATIONS 가 4종 전부를 요구한다).
  minute_job_destinations = ["price-analysis-realtime", "news-extraction-realtime", "news-extraction-backfill"]
  minute_all_destinations = concat(local.minute_job_destinations, ["price-explanation-realtime"])

  # universe 정본 객체 — planner·worker·consumer 가 **같은 URI** 를 봐야 세 표면의
  # universe(version·hash)가 한 곳에서 나온다. 객체가 없으면 worker/consumer 는 기동 시
  # fail-loud 다.
  # 생산자는 `build-minute-universe` 스텝이고(ALPHA-953) **이 값을 그대로 `--universe`
  # 로 받는다** — 그래서 이 변수를 옮겨도 생산자와 소비자가 갈리지 않는다. ⚠️ 다만 그
  # 스텝의 스케줄 배선은 아직 없다(후속) — 지금은 수동 실행이다.
  minute_universe_uri = (
    var.minute_universe_uri != "" ? var.minute_universe_uri
    : "s3://${var.lake_bucket_name}/config/minute/universe.json"
  )

  minute_queue_urls = {
    for name in local.minute_all_destinations : name => aws_sqs_queue.minute[name].url
  }

  # 세션 종료 게이트가 보는 큐 = realtime 큐 2종. 설명 큐(price-explanation-realtime,
  # 소비자=analysis-consumer ALPHA-719)를 넣지 않는 이유는 아래 MINUTE_SESSION_ANALYSIS_SERVICES
  # 주석 — 지연 재배달의 비가시 메시지가 게이트 깊이에 잡혀 스케일다운을 막는다.
  # backfill 큐를 넣으면 밤 backlog 하나가 상주 서비스 **전체**의 스케일다운을 막는다
  # (스케일 단위가 서비스 전체).
  # backfill 은 미루는 것이 정의상 무해하다 — 다음 세션이 집는다.
  # ⚠️ 게이트 env 와 세션 역할 IAM 이 **여기 한 곳**에서 파생된다 — 갈리면 stop 이
  # AccessDenied 를 pending 으로 읽어 영영 안 내려가거나, 게이트 없는 큐 권한이 남는다.
  minute_gate_queue_names = ["price-analysis-realtime", "news-extraction-realtime"]
}

# ── SQS — 원 큐 4종 + DLQ ──────────────────────────────────────────────
# maxReceiveCount 는 **transport 상한**이다 — 논리 재시도 예산(DB, max_attempts=5)보다
# 넉넉해야 DB 가 재시도의 권위로 남는다(v0.7 12.4 — 반대면 transport 가 먼저 포기).
resource "aws_sqs_queue" "minute_dlq" {
  for_each = toset(local.minute_all_destinations)

  name = "${var.name}-${each.key}-dlq"
  # DLQ 는 근거 보존소다 — 대사(dlq-reconcile)는 메시지를 지우지 않으므로 보존을
  # 최대(14일)로 둬 사람이 볼 시간을 확보한다.
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "minute" {
  for_each = toset(local.minute_all_destinations)

  name                       = "${var.name}-${each.key}"
  visibility_timeout_seconds = 300 # Consumer visibility 기본과 일치(ConsumerConfig)
  # 7일. 4일이었으나 소비자가 세션 결속으로 스케일되면서(ALPHA-713) 장기 연휴(추석 등 —
  # 마지막 세션 후 5일+)를 넘기는 backfill 메시지가 **조용히 만료**되는 창이 생겼다 —
  # retention 만료는 DLQ 로 가지 않아 job 은 DEAD 도 아니고 relay 는 이미 PUBLISHED 라
  # wake-up 을 다시 만들 주체가 없다(영구 고착). KR 달력의 어떤 휴장 간격보다 길게 둔다.
  # ⚠️ 14일(상한)로 두지 않는 이유: SQS 는 DLQ 이동 후에도 **최초 enqueue 시각**을
  # 보존해, DLQ 검토 창 = DLQ retention(14일) − 원큐 체류시간이다. 원큐를 상한까지 쓰면
  # 오래 머문 메시지가 DLQ 도착 직후 만료돼 대사(dlq-reconcile)·사람의 근거가 사라진다.
  # 7일이면 최악에도 검토 창이 7일 남는다.
  message_retention_seconds = 604800

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.minute_dlq[each.key].arn
    # ⚠️ receive 가 곧 실행이 아니다 — lease(600) > visibility(300) 라 교대 receive 가
    # contended 로 소비돼 실행 attempt 는 receive 의 절반꼴이다. 8 이면 DB 예산(5)이
    # 권위가 되기 전에 transport 가 먼저 포기한다(v0.7 12.4 위반).
    maxReceiveCount = 16
  })
}

# ── 토스 자격증명 그릇 — 값은 운영자가 CLI 로 주입한다(state 에 평문 금지) ──
# ⚠️ 1분 레인이 KIS 로 바뀌면서(ALPHA-735) 이 그릇은 지금 아무 태스크도 주입받지 않는다.
# **이번 배포에서 지우지 않는다** — 롤백 경로(source=toss 로 되돌리기)가 이 그릇에 기대고,
# 시크릿 삭제는 복구창(7~30일)이 붙어 되살리기가 비싸다. KIS 실증이 끝난 뒤 별도로 정리한다.
resource "aws_secretsmanager_secret" "toss" {
  name = "${var.name}-toss"
}

# ── 상주 서비스 ────────────────────────────────────────────────────
locals {
  minute_services = {
    # 가격 1분 생산자 — 벤더는 KIS 다(ALPHA-735). 토스는 초당 5회라 종목당 1콜 × 400종이
    # 60초 창을 넘었다(KIS 실측 14.8 req/s).
    price-worker = {
      command = ["price-worker", "--universe", local.minute_universe_uri]
      environment = merge(local.env, local.db_env, {
        DATA_PIPELINE_MINUTE_PRICE_WORKER__TRIGGER_SCHEMA_VERSION = var.minute_trigger_schema_version
        # source 는 세션 source_group 과 **같은 변수에서 파생**한다 — 갈리면 워커가 다른
        # session_id 를 유도해 기동 거부로 레인이 통째로 선다. 롤백(kis↔toss)은 이 변수
        # 하나로 끝난다(apply 가 아래 시크릿 쌍도 함께 전환한다).
        # ⚠️ 전환은 **세션 사이에만**(다음 세션부터). 장중에 바꾸면 ①기존 세션이 ACTIVE
        # 로 고립되고(EOD stop 은 새 source 세션만 지목) ②두 세션이 source 무관 canonical
        # key 를 다퉈 ArtifactImmutabilityError 다(states.py 키 설계 경고). 장중 불가피하면
        # 기존 세션 drain·finalize 후 start 재실행이 선행이다.
        DATA_PIPELINE_MINUTE_PRICE_WORKER__SOURCE = var.minute_session_source_group
        # 토큰 공유 캐시(ALPHA-573). **상주 워커엔 없으면 안 된다** — 매 기동 발급이
        # 분당 1회 제한에 걸리고, 배치의 kis 스텝과도 발급을 다툰다.
        KIS_TOKEN_CACHE_PARAM = local.kis_token_param_name
      })
      # 선택된 source 의 자격증명 쌍**만** 주입한다 — ECS 는 기동 시 secrets 전부를
      # 해석하므로, 미사용 벤더 쌍을 같이 걸면 그 시크릿에 값이 없는 환경(신규 환경·
      # 그릇만 있는 toss)에서 ResourceInitializationError 로 워커가 아예 못 뜬다.
      secrets = merge(
        { DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::" },
        var.minute_session_source_group == "toss" ? {
          DATA_PIPELINE_MINUTE_PRICE_WORKER__CLIENT_ID     = "${aws_secretsmanager_secret.toss.arn}:client_id::"
          DATA_PIPELINE_MINUTE_PRICE_WORKER__CLIENT_SECRET = "${aws_secretsmanager_secret.toss.arn}:client_secret::"
          } : {
          DATA_PIPELINE_MINUTE_PRICE_WORKER__APP_KEY    = "${aws_secretsmanager_secret.kis.arn}:app_key::"
          DATA_PIPELINE_MINUTE_PRICE_WORKER__APP_SECRET = "${aws_secretsmanager_secret.kis.arn}:app_secret::"
        }
      )
    }
    relay = {
      command = ["relay"]
      environment = merge(local.env, local.db_env, {
        # JSON 한 변수 — destination 이름에 하이픈이 있어 nested env 형태는 셸·로더
        # 어느 쪽도 못 받는다(MinuteRelayConfig docstring). 4종 전부 필수다.
        DATA_PIPELINE_MINUTE_RELAY__QUEUE_URLS = jsonencode(local.minute_queue_urls)
      })
      secrets = {
        DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
      }
    }
    price-consumer = {
      command = ["price-consumer", "--universe", local.minute_universe_uri]
      environment = merge(local.env, local.db_env, {
        DATA_PIPELINE_MINUTE_PRICE_CONSUMER__QUEUE_URL                = aws_sqs_queue.minute["price-analysis-realtime"].url
        DATA_PIPELINE_MINUTE_PRICE_CONSUMER__DETECTION_POLICY_VERSION = var.minute_detection_policy_version
      })
      secrets = {
        DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
      }
    }
    # 뉴스 추출 Consumer 2종(ALPHA-713) — 핸들러·스텝이 같고 큐 URL 만 다르다(커널이
    # queue_url 하나만 받으므로 다중 큐 개조 대신 서비스를 분리한다 — 커널 무수정).
    # LLM 설정은 tag-news 관례(LLM_* env, base_url·model 은 코드 기본값=DeepSeek).
    # 이 맵에 들어야 세션 오케스트레이션의 스케일 대상이 된다 — 다만 **공용 목록**
    # (MINUTE_SESSION_SERVICES)은 여기서 세션 결속 생산자를 뺀 나머지다(session_bound_workers).
    # 소비자 2종은 공용에 남는다: 생산자(news-worker, ALPHA-707)가 세션 결속이라 소비자도
    # 같은 수명으로 둔다. 장외 redrive 메시지는 retention(7일) 안에서 다음 세션이 집는다.
    news-consumer-realtime = {
      command = ["news-consumer"]
      environment = merge(local.env, local.db_env, {
        DATA_PIPELINE_MINUTE_NEWS_CONSUMER__QUEUE_URL = aws_sqs_queue.minute["news-extraction-realtime"].url
      })
      secrets = {
        DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
        LLM_API_KEY                = "${var.deepseek_secret_arn}:api_key::"
      }
    }
    news-consumer-backfill = {
      command = ["news-consumer"]
      environment = merge(local.env, local.db_env, {
        DATA_PIPELINE_MINUTE_NEWS_CONSUMER__QUEUE_URL = aws_sqs_queue.minute["news-extraction-backfill"].url
      })
      secrets = {
        DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
        LLM_API_KEY                = "${var.deepseek_secret_arn}:api_key::"
      }
    }
    # 뉴스 1분 생산자(ALPHA-707/717) — BigKinds 는 키가 없어 시크릿은 DB 뿐이다.
    # 엔드포인트·카테고리는 코드 기본값([bigkinds_news] sources.toml)이 정본.
    news-worker = {
      command     = ["news-worker"]
      environment = merge(local.env, local.db_env, {})
      secrets = {
        DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
      }
    }
    # 공시 1분 생산자(ALPHA-875) — news-worker 와 같은 자리다(유니버스 없는 소스 단위,
    # 세션 결속). 다른 점은 **한 window 가 체인 전체**라는 것: collect→normalize×2→load 를
    # 이 컨테이너가 다 돌므로 벤더 키(DART)와 DB 를 **둘 다** 싣는다(형제 워커는 하나씩).
    # 엔드포인트·유형 필터는 코드 기본값([dart_disclosure.source] sources.toml)이 정본이고,
    # pacing·예산은 [minute_disclosure_worker] 기본값을 쓴다(조일 땐 env 로 덮는다).
    disclosure-worker = {
      command     = ["disclosure-worker"]
      environment = merge(local.env, local.db_env, {})
      secrets = {
        DATA_PIPELINE_DB__PASSWORD                     = "${var.db_password_secret_arn}:password::"
        DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY = "${aws_secretsmanager_secret.dart.arn}:apikey::"
      }
    }
    # 장중 iNAV 생산자(ALPHA-851/882) — 소비자가 없다. `commit_inav_window` 가 job·outbox
    # 를 일부러 안 만들어(가격 것을 빌려 쓰면 NAV 가 price-analysis-realtime 으로 나가
    # 설명이 발화된다) 이 레인은 canonical 까지만 간다. 큐도 안 늘어난다.
    # ⚠️ 질의 심볼(`krx_etf.source.etf_map`)은 동봉 sources.toml 이 정본이라 env 가 없다 —
    # 세션 유니버스(`--universe`)와 **다른 출처**이고, 갈리면 etf_map 에 없는 unit 이 매
    # window invalid 로 드러난다(조용히 missing 으로 접지 않는다).
    inav-worker = {
      command = ["inav-worker", "--universe", local.minute_universe_uri]
      environment = merge(local.env, local.db_env, {
        # 토큰 공유 캐시(ALPHA-573) — price-worker 와 **같은 앱키를 쓴다**. 상주 워커엔
        # 없으면 안 된다: 매 기동 발급이 분당 1회 제한에 걸리고, 가격 레인·15:40 배치와
        # 발급을 다툰다.
        KIS_TOKEN_CACHE_PARAM = local.kis_token_param_name
        # 거래일 판정 — `skip_reason` 을 **여는 쪽이 이 컨테이너**다(kis_inav.py). 배치 kis
        # 브랜치(tasks.tf env_sets.kis)와 같은 집합이어야 한다. 안 주면 `is_trading_day` 가
        # 평일 공휴일을 거래일로 보고 가드가 **주말만 아는 상태로 조용히 퇴화**한다 —
        # 오케스트레이터가 안 띄우는 날에도 이 서비스가 살아 있을 수 있다(수동 확인·
        # EOD stop 타임아웃 후 잔존 desired_count=1). 그때 KIS 는 직전 거래일 값을 주고
        # 그게 오늘 파티션에 앉는다(유령 as-of, ALPHA-387 과 동형).
        OPS_KR_HOLIDAYS = join(",", var.kr_holidays)
      })
      # 일별 NAV(tasks.tf ingest-raw-nav)와 **같은 자격증명 쌍**이다 — 벤더도 TR 계열도
      # 같아서 그릇을 나눌 이유가 없다. 미주입이면 워커가 기동에서 죽는다(fail-loud).
      secrets = {
        DATA_PIPELINE_DB__PASSWORD                = "${var.db_password_secret_arn}:password::"
        DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY    = "${aws_secretsmanager_secret.kis.arn}:app_key::"
        DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET = "${aws_secretsmanager_secret.kis.arn}:app_secret::"
      }
    }
    # 장중 업종지수 45종 생산자(ALPHA-887) — iNAV 와 **같은 성질**이다: 소비자가 없어
    # canonical 까지만 가고 큐도 안 늘어난다. 다른 점은 하나, **universe 를 안 받는다** —
    # 기대 집합의 정본이 universe.json 이 아니라 동봉 config `[minute_sector_index.index_map]`
    # 이라 `--universe` 를 주면 planner 가 거부한다(그래서 command 에 없다).
    sector-index-worker = {
      command = ["sector-index-worker"]
      environment = merge(local.env, local.db_env, {
        # 토큰 공유 캐시(ALPHA-573) — price-worker·inav-worker 와 **같은 앱키**다. KIS 앱키는
        # 전역 한도라 이걸 빼면 매 기동 발급이 분당 1회 제한에 걸리고 가격 레인과 다툰다.
        KIS_TOKEN_CACHE_PARAM = local.kis_token_param_name
        # ⚠️ `OPS_KR_HOLIDAYS` 가 **없는 것이 의도다** — inav-worker 와 갈리는 자리라 적어 둔다.
        # 그 env 는 컨테이너가 여는 가드가 읽을 때만 필요한데(ALPHA-882 가 이걸 빠뜨려
        # 평일 공휴일이 거래일로 통과했다), 이 어댑터에는 `skip_reason` 축이 아예 없다
        # (`worker.py` 의 sector-index 종료 판정 주석). 휴장일은 한 층 위에서 걸린다 —
        # `start-minute-session` 이 `is_trading_day` 로 세션을 안 만들고, 세션이 없으면
        # 이 Worker 는 기동에서 죽는다(fail-loud). 잔존 desired=1 로 혼자 살아남아도
        # 조용한 오염이 아니라 재기동 루프라 드러난다 — iNAV 는 그 자리에서 KIS 가 직전
        # 거래일 값을 줘 유령 as-of 가 앉았던 것이고, 여기는 그 경로가 없다.
      })
      # 업종지수 TR(FHKUP03500200)은 iNAV 와 **같은 KIS 자격증명 쌍**을 쓴다 —
      # `sector_index_worker_cli` 가 `settings.kis_nav` 를 요구한다(미주입이면 기동에서
      # 죽는다, fail-loud). 벤더도 TR 계열도 같아 그릇을 나눌 이유가 없다.
      secrets = {
        DATA_PIPELINE_DB__PASSWORD                = "${var.db_password_secret_arn}:password::"
        DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY    = "${aws_secretsmanager_secret.kis.arn}:app_key::"
        DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET = "${aws_secretsmanager_secret.kis.arn}:app_secret::"
      }
    }
  }

}

# 세션 결속 생산자 — 공용 스케일 목록에서 빼고 각자 자기 목록으로 올린다(세션이 선 날만).
# 여기 넣는 것과 `MINUTE_SESSION_*_WORKER_SERVICES` 에 싣는 것이 **짝**이다. 소비자는
# 여기 안 넣는다: 빈 큐 폴링은 무해하고 backfill 소비는 세션 무관이다.
locals {
  session_bound_workers = ["news-worker", "disclosure-worker", "inav-worker", "sector-index-worker"]
}

resource "aws_ecs_task_definition" "minute" {
  for_each = local.minute_services

  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name      = local.container_name
    image     = var.image
    essential = true
    # SIGTERM 후 in-flight(LLM 없는 판정이라도 S3·DB 왕복)를 끝낼 시간 — 기본 30초는
    # close() 계약(끝까지 기다린다)을 강제 종료로 자를 수 있다. Fargate 상한 120.
    stopTimeout = 120
    command     = each.value.command
    environment = [for k, v in each.value.environment : { name = k, value = v }]
    secrets     = [for k, v in each.value.secrets : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = local.log_options
    }
  }])
}

resource "aws_ecs_service" "minute" {
  for_each = local.minute_services

  name            = "${var.name}-${each.key}"
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.minute[each.key].arn
  desired_count   = 0
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = false
  }

  lifecycle {
    # desired_count 만 무시한다 — 세션 오케스트레이션이 런타임에 바꾸는 값이라, 없으면
    # 무관한 apply 가 장중에 워커를 내린다(ALPHA-711 의 존재 이유).
    # ⚠️ task_definition 은 무시하지 **않는다** — ecs-service 모듈과 달리 이 서비스들의
    # CD(deploy-data-pipeline.yml)는 revision 을 등록하지 않고 mutable 태그를
    # force-new-deployment 로 재당길 뿐이라, terraform 이 task-def 의 유일한 author 다.
    # 무시하면 명령·env·시크릿 변경이 apply 돼도 서비스에 영영 반영되지 않는다.
    ignore_changes = [desired_count]
  }
}

# ── IAM — 새 큐에 대한 최소 권한 ───────────────────────────────────────
resource "aws_iam_role_policy" "minute_queues" {
  name = "minute-queues"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Relay 발행 — 원 큐 4종
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = [for q in aws_sqs_queue.minute : q.arn]
      },
      {
        # Consumer 소비(job 큐 3종 — 가격 1 + 뉴스 2, ALPHA-713) + DLQ 대사(job DLQ
        # 3종 — 조회만, 삭제는 배선 오류 정리 케이스뿐이지만 같은 API 라 함께 허용).
        # 트리거 설명 큐는 제외 — 그 소비자는 이 역할로 돌지 않는다(분석엔진 소관).
        Effect = "Allow"
        Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
        Resource = concat(
          [for name in local.minute_job_destinations : aws_sqs_queue.minute[name].arn],
          [for name in local.minute_job_destinations : aws_sqs_queue.minute_dlq[name].arn],
        )
      },
    ]
  })
}

# ── 세션 스케일 오케스트레이션 (ALPHA-712) ─────────────────────────────
# 위 서비스들의 desired_count 는 ignore_changes 로 terraform 이 손을 뗀 값이다 — 이 아래가
# 그 값을 바꾸는 **유일한 주체**다. 실행체는 EventBridge Scheduler → ECS RunTask 로,
# daily·news·reconcile 스케줄과 같은 형태다(근거는 session_ops.py 모듈 docstring).

# 전용 역할이다 — 공용 `aws_iam_role.task` 에 붙이면 **모든 수집·정제 배치 task-def**
# (`aws_ecs_task_definition.this`)가 상주 서비스를 내릴 권한을 함께 갖는다. 권한 자체는
# 상주 서비스로 좁혀도, 그것을 행사할 수 있는 실행체가 레인 밖까지 넓어진다(analysis_task 선례).
resource "aws_iam_role" "minute_session" {
  name               = "${var.name}-minute-session"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "minute_session" {
  name = "${var.name}-minute-session"
  role = aws_iam_role.minute_session.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # universe 정본·1분 canonical artifact 읽기 — plan 단계가 window 범위와
        # universe_hash 를, 롤업이 재료를 여기서 뽑는다.
        # ⚠️ **쓰기를 여기 더하지 마라** — Resource 가 버킷 전체라 레이크 **전역 쓰기**가
        # 된다. 쓰기는 아래 프리픽스 한정 문장이 따로 진다(ALPHA-955).
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [var.lake_bucket_arn, "${var.lake_bucket_arn}/*"]
      },
      {
        # 5분 파생 산출 (ALPHA-955 — `rollup-minute-session`). 이 태스크가 레이크에
        # 만드는 **유일한** 것이고, 그래서 프리픽스를 그 하나로 못박는다
        # (`aws_iam_role.analysis_task` 가 같은 이유로 쓰기만 prefix 로 가르는 선례).
        # 없으면 매일 AccessDenied 인데 **스케줄러는 RunTask 제출까지만 보므로 조용한
        # 실패**다 — 스텝의 exit≠0 을 보는 백스톱이 이 레인엔 없다.
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${var.lake_bucket_arn}/canonical/market_data/intraday_5m/*"]
      },
      {
        Effect = "Allow"
        Action = ["ecs:UpdateService"]
        # ⚠️ **스케일 대상이면 여기 있어야 한다** — 빠지면 아침 스케일업이 AccessDenied 로
        # 죽어 레인 전체가 안 뜬다(목록과 같은 축).
        # analysis_consumer 는 ALPHA-912 이후 **세션의 스케일 대상이 아니다**(desired 는
        # 오토스케일링 소유). 여기 남은 것은 잉여지 근거가 아니다 — 공용 목록 정리(PR C)와
        # 함께 뺀다. 그때까지 세션이 이 서비스로 UpdateService 를 부르는 경로는 없다.
        Resource = concat(
          [for service in aws_ecs_service.minute : service.id],
          [aws_ecs_service.analysis_consumer.id],
        )
      },
      {
        # 내리기 전 큐 깊이 확인 — 게이트 env 와 같은 파생(minute_gate_queue_names).
        Effect   = "Allow"
        Action   = ["sqs:GetQueueAttributes"]
        Resource = [for name in local.minute_gate_queue_names : aws_sqs_queue.minute[name].arn]
      },
    ]
  })
}

resource "aws_ecs_task_definition" "minute_session" {
  family                   = "${var.name}-minute-session"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.minute_session.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name      = local.container_name
    image     = var.image
    essential = true
    # stop 쪽은 원장 게이트가 빌 때까지 폴링한다 — SIGTERM 으로 중간에 끊기면 서비스가
    # 안 내려간 채 끝나므로, 그 판단이 끝날 시간을 준다(Fargate 상한 120).
    stopTimeout = 120
    # command 는 스케줄 target 이 덮는다(start/stop). 기본값은 실수로 뜬 태스크가
    # 아무것도 안 하도록 계획만 하는 쪽으로 둔다.
    command = ["start-minute-session"]
    environment = [for k, v in merge(local.env, local.db_env, {
      # 비거래일 판정 — Planner·KRX·KIS 와 **같은** 공휴일 집합이어야 "Planner 는 쉬는
      # 날로 건너뛴 날에 1분 세션만 뜬다"는 모순이 안 생긴다.
      OPS_KR_HOLIDAYS = join(",", var.kr_holidays)

      MINUTE_SESSION_CLUSTER = var.cluster_arn
      # 서비스명을 코드에서 다시 조립하지 않는다 — rename 이 조용한 no-op 스케일링이 된다.
      # ⚠️ 세션 결속 생산자(local.session_bound_workers — news-worker·disclosure-worker·inav-worker)는 공용
      # 목록에서 뺀다 — 자기 세션 계획이 **성공한 날만** 올린다(실패 날 올리면 세션 부재
      # 기동 거부로 하루 종일 재기동 루프 — 비용·알람 소음). 각자 아래 자기 목록으로 간다.
      # 뉴스 소비자 2종은 공용에 남는다: 빈 큐 폴링은 무해하고 backfill 소비는 세션 무관.
      # ⚠️ 공시도 같은 이유로 공용 목록에서 뺀다(ALPHA-875) — 제외 목록을 로컬 하나로
      # 둔다: 서비스를 늘리며 여기 한쪽만 빠뜨리면 그 Worker 가 세션 없는 날도 떠서
      # 기동 거부 루프를 돈다(뺀 축과 올리는 축이 갈리면 안 된다).
      # ⚠️ analysis-consumer 는 **여기 남긴다**(ALPHA-910) — 소유 축을 떼는 주체는 아래
      # 자기 목록을 읽는 **코드**(`session_ops._services` 가 빼낸다)다. terraform 에서
      # 빼면 이 파일과 이미지 CD 가 독립 워크플로(둘 다 push:dev)라 apply 가 늦게 착지한
      # 날 구 이미지가 소비자를 아무 목록으로도 안 올린다 — 그날 장중 설명이 통째로
      # 없다([[deploy-order-splits-the-pr]] 와 같은 함정). 실제로 빼는 것은 오토스케일링
      # 부착 PR 소관이고, 그때는 새 이미지가 이미 오래 떠 있다.
      MINUTE_SESSION_SERVICES = join(",", concat(
        [for key, service in aws_ecs_service.minute : service.name
        if !contains(local.session_bound_workers, key)],
        [aws_ecs_service.analysis_consumer.name],
      ))
      # analysis-consumer(ALPHA-719)를 공용 스케일에서 **빼는 근거**(ALPHA-910 이 세운 축,
      # ALPHA-912 로 컷오버 완료). 세션은 이 서비스를 더 이상 올리지도 내리지도 않는다 —
      # desired 는 큐 잔여 일감을 보는 오토스케일링이 소유한다(`analysis_autoscaling.tf`).
      # 공용 목록에 얹힌 채로는 오토스케일링을 붙여도 무효다: 스케일러가 올린 desired 를
      # 세션 stop 이 매일 밤 0 으로 덮어쓴다.
      # 🔴 **이 값을 지우지 마라 — 비면 세션이 죽는다**(`_analysis_services` 가 fail-loud).
      # 공용 목록(위)에서 소비자를 빼고 나면 이 env 는 "뺄 게 없는 값"처럼 보이는데,
      # 그때도 지우면 start·stop 이 둘 다 SystemExit 으로 죽어 1분 파이프라인이 통째로
      # 안 뜬다. 잉여로 보이지만 **생존 토큰**이다. 계약의 자리는 그 도크스트링이다.
      # ⚠️ 설명 큐는 stop 게이트에 넣지 않는다 — 지연 재배달로 비가시인 메시지가 게이트
      # 깊이에 잡혀 레인 전체 스케일다운을 밤새 막는다. 미소비 잔여는 retention(7일)
      # 안에서 다음 세션이 집는다.
      MINUTE_SESSION_ANALYSIS_SERVICES            = aws_ecs_service.analysis_consumer.name
      MINUTE_SESSION_NEWS_WORKER_SERVICES         = aws_ecs_service.minute["news-worker"].name
      MINUTE_SESSION_DISCLOSURE_WORKER_SERVICES   = aws_ecs_service.minute["disclosure-worker"].name
      MINUTE_SESSION_INAV_WORKER_SERVICES         = aws_ecs_service.minute["inav-worker"].name
      MINUTE_SESSION_SECTOR_INDEX_WORKER_SERVICES = aws_ecs_service.minute["sector-index-worker"].name
      # 내리기 전에 비어야 하는 큐 — 선정 근거·IAM 동기화는 minute_gate_queue_names 주석.
      MINUTE_SESSION_GATE_QUEUES = join(",", [for name in local.minute_gate_queue_names : aws_sqs_queue.minute[name].url])
      # 승객 세션 편입 — start 가 그 세션도 계획하고 stop 이 함께 드레인한다. 비우면
      # 그 레인만 미편입이고 구동 레인(가격)은 그대로 돈다.
      # ⚠️ 승객은 `--dataset` 인자가 **아니다**(그건 구동 레인 전용 — states.SCALED_DATASETS).
      # 자기 워커를 소유해도 인자로는 못 온다: `_scale` 이 dataset 을 안 보고 공용 목록을
      # 내려서, stop 을 승객 dataset 으로 부르면 살아 있는 price-worker 가 내려간다.
      MINUTE_SESSION_NEWS_SOURCE_GROUP = var.minute_session_news_source_group
      # 공시 세션 편입(ALPHA-875) — 같은 토글 축. 비우면 공시 레인은 계획도 스케일도 안 된다
      # (그 상태가 곧 컷오버 전이다 — SFN 레인이 계속 소유한다).
      MINUTE_SESSION_DISCLOSURE_SOURCE_GROUP = var.minute_session_disclosure_source_group
      # iNAV 세션 편입(ALPHA-882) — 같은 토글 축.
      MINUTE_SESSION_INAV_SOURCE_GROUP = var.minute_session_inav_source_group
      # 업종지수 세션 편입(ALPHA-887) — 같은 토글 축. 비우면 이 레인만 미편입이고
      # 구동 레인(가격)은 그대로 돈다. 소급이 불가한 소스라(과거일 질의가 일봉으로
      # degrade) **비워 둔 날은 영구 결손**이다 — 뉴스·공시처럼 나중에 주워올 수 없다.
      MINUTE_SESSION_SECTOR_INDEX_SOURCE_GROUP = var.minute_session_sector_index_source_group
    }) : { name = k, value = v }]
    secrets = [{
      name = "DATA_PIPELINE_DB__PASSWORD", valueFrom = "${var.db_password_secret_arn}:password::"
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-stream-prefix" = "minute-session" })
    }
  }])
}

locals {
  # 업종지수 롤업은 **그 레인이 편입된 날에만** 존재한다 — `..._source_group` 이 그 레인의
  # 킬 스위치인데(비우면 start 가 세션을 계획하지 않는다), 스케줄만 남으면 매일 빈
  # `--source-group` 으로 어휘 검증에 걸려 exit 2 를 낸다. 끌 거면 같이 꺼진다.
  minute_session_schedules = merge(local.minute_session_lifecycle_schedules,
    var.minute_session_sector_index_source_group == "" ? {} : {
      "rollup-sector" = local.minute_session_sector_rollup_schedule
  })

  # 업종지수 5분 파생 확정 (ALPHA-955). **가격이 아니라 업종지수만** 이 자리에 있다 —
  # 두 레인의 마감 시각이 다르기 때문이다. 업종지수 세션은 09:00~15:30(정규장 390
  # window — `EXTENDED_HOURS_DATASETS` 밖이고 universe 도 없다)이고, 가격 세션은
  # 20:00 까지 계획하므로 이 시각에 가격을 롤업하면 뒤 4시간이 빠진 부분본이 남는다.
  # 가격 EOD 확정은 ALPHA-839 소관이고 20:05 stop 뒤여야 한다.
  #
  # 왜 16:00 인가 — 하한을 정하는 것은 수집 종료가 아니라 **마지막 커밋이 언제까지
  # 들어오나**다. 워커는 20:05 스케일다운까지 살아 recovery 로 결손 window 를 계속
  # 재청구한다. 그런데 이 소스는 **소급이 불가**해서(한 콜이 늘 "지금 기준 100봉",
  # `kis_sector_index.py` 도크스트링) 마감 후 벤더 페이지는 13:50~15:30 에 얼어붙는다
  # — 그 앞 구간은 기다려도 영영 안 채워지고, 그 뒤 구간은 recovery 예산(tick 당 1,
  # tick 5초)으로 몇 분이면 소진된다. 15:30 + 30분이면 채워질 것은 다 채워져 있다.
  # ⚠️ 그 대가는 16:00 **이후** 착지하는 늦은 recovery 커밋을 놓치는 것이다(그날
  # 재실행이 없다). 벤더가 장 마감 직전 오래 죽은 날에만 성립하고, 그때도 잃는 것은
  # 13:50~15:30 구간뿐이다 — 더 미뤄서 얻는 것보다 결손을 일찍 보는 편이 낫다고 봤다.
  #
  # ⚠️ 상태(`minute_session_schedule_state`)를 start/stop 과 **공유한다**. 이 롤업만
  # 따로 끄는 손잡이는 없다 — 필요해지면 그때 가른다. 실패해도 산출이 없을 뿐 기존
  # 파일을 덮지 않아(`_rollup_day` 가드) 급히 꺼야 할 성질이 아니다.
  #
  # 🔴 **실패는 이 스케줄에서 안 보인다** — 스케줄러는 RunTask 제출까지만 보므로
  # 컨테이너 exit≠0 이 관측되지 않는다(이 레인 공통. DLQ·retry_policy 는 이 유형을 못
  # 잡는다: 제출은 성공하고 태스크가 나중에 죽는다). 백스톱은 **다음 날 실행의 구멍
  # 판정**이다 — 이 스텝이 매번 `unfilled_settled_days` 로 "원장이 멈춘 거래일인데 5분
  # 산출이 없는 날"을 함께 보고한다. 업종지수 세션도 stop 이 승객 레인까지 drain 하므로
  # (`session_ops.stop_session_cli`) settled 로 잡힌다. ⚠️ 그건 **로그**지 경보가 아니다
  # — 경보는 이 레인 전체가 함께 받아야 할 별건이다.
  minute_session_sector_rollup_schedule = {
    expression = var.minute_session_sector_rollup_expression
    # `--session-date` 를 안 준다 = 오늘(KST). 16:00 KST 는 그날이라 맞다 —
    # 스케줄러가 넘기는 시각은 UTC 지만 스텝이 KST 로 잡는다(`rollup_session_cli`).
    command = ["rollup-minute-session",
      "--dataset", "sector_index_minute",
    "--source-group", var.minute_session_sector_index_source_group]
  }

  minute_session_lifecycle_schedules = {
    start = {
      expression = var.minute_session_start_expression
      command = ["start-minute-session",
        "--dataset", var.minute_session_dataset,
        "--source-group", var.minute_session_source_group,
      "--universe", local.minute_universe_uri]
    }
    stop = {
      expression = var.minute_session_stop_expression
      command = ["stop-minute-session",
        "--dataset", var.minute_session_dataset,
      "--source-group", var.minute_session_source_group]
    }
  }
}

resource "aws_scheduler_schedule" "minute_session" {
  for_each = local.minute_session_schedules

  name                         = "${var.name}-minute-session-${each.key}"
  state                        = var.minute_session_schedule_state
  schedule_expression          = each.value.expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      Cluster        = var.cluster_arn
      TaskDefinition = aws_ecs_task_definition.minute_session.arn
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
          Name    = local.container_name
          Command = each.value.command
        }]
      }
    })

    # 재시도는 **컨테이너 기동 실패**만 덮는다 — 스케줄러는 RunTask **제출**까지만 보므로
    # 컨테이너가 뜬 뒤의 exit≠0(DB 장애로 start 가 2, 상한 초과로 stop 이 1)은 스케줄러엔
    # 성공으로 보인다. ⚠️ 그 공백을 메울 백스톱이 이 레인엔 아직 없다(daily 레인은
    # Reconciler 가 메운다) — start 가 그렇게 실패하면 **그 날은 통째로 안 돈다**.
    # 지금의 신호는 컨테이너 로그와 desired_count 뿐이다.
    # 상한을 짧게 두는 이유: start 는 개장 전에 떠야 의미가 있고, stop 의 늦은 재시도는
    # 이미 지난 세션을 상대로 돌아 게이트가 비자마자 내려 무해하다.
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 5
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dlq.arn }
  }
}

# ── 분봉 트리거 설명 소비자 (ALPHA-719) ────────────────────────────────
# analysis-engine 이미지의 상주 소비자 — price-explanation-realtime 을 폴링해
# `analyze --trigger-id` 경로를 태운다. data-pipeline 서비스 맵(minute_services)에 넣지
# 않는 이유: 이미지·컨테이너명·env 네임스페이스(PG*·DEEPSEEK_*)가 전부 다르다(tasks.tf
# analysis 단서와 동일). 세션 스케일에는 아래 env 파생으로 함께 편입된다.

# ExposureReverted 회수 자격(ALPHA-746) — 소비자가 super-admin 무효화 API 를 부를 때 쓰는
# 운영자 계정. 그릇(SSM SecureString)은 TF 밖 운영자 CLI 주입이다 — 시크릿 그릇 규약("TF 는
# 그릇만, 값은 수동")에서 한 칸 더: 여기서는 **이름만 계약**한다(kis 토큰 캐시와 같은 결).
# 미주입이면 태스크가 ResourceInitializationError 로 시작하지 않는다 — 조용한 자격 공백 대신
# fail-loud. 주입(1회):
#   aws ssm put-parameter --name /<var.name>/super-admin/operator-email --type SecureString --value '<email>'
#   aws ssm put-parameter --name /<var.name>/super-admin/operator-password --type SecureString --value '<password>'
locals {
  super_admin_email_param_arn    = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.name}/super-admin/operator-email"
  super_admin_password_param_arn = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.name}/super-admin/operator-password"
}

resource "aws_ecs_task_definition" "analysis_consumer" {
  family                   = "${var.name}-analysis-consumer"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  # analyze 와 **같은 코드 경로**(`analyze --trigger-id`)를 태우므로 같은 DuckDB 피크를
  # 받는다 — 공유 `task_memory` 로 두면 상주 소비자만 OOMKilled 로 죽는다(ALPHA-671).
  memory             = var.analysis_task_memory
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.analysis_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name      = local.analysis_container_name
    image     = var.analysis_image
    essential = true
    # 진행 중 메시지(LLM 호출 포함)를 끝낼 시간 — 상주 3종과 같은 근거. Fargate 상한 120.
    stopTimeout = 120
    # 이미지 ENTRYPOINT 가 `python -m edge_analysis` 라 command 는 서브커맨드 인자다.
    command = ["consume-triggers"]
    environment = [for k, v in merge(local.analysis_env, {
      EDGE_EXPLANATION_QUEUE_URL = aws_sqs_queue.minute["price-explanation-realtime"].url
      SUPER_ADMIN_API_URL        = var.super_admin_api_url
    }) : { name = k, value = v }]
    # 회수 자격은 이 소비자에게만 주입한다 — 배치 analyze(tasks.tf)는 무효화를 부르지 않는다.
    secrets = [for k, v in merge(local.analysis_secrets, {
      SUPER_ADMIN_EMAIL    = local.super_admin_email_param_arn
      SUPER_ADMIN_PASSWORD = local.super_admin_password_param_arn
    }) : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-stream-prefix" = "analysis-consumer" })
    }
  }])
}

resource "aws_ecs_service" "analysis_consumer" {
  name            = "${var.name}-analysis-consumer"
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.analysis_consumer.arn
  desired_count   = 0
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = false
  }

  lifecycle {
    # 상주 3종과 같은 계약 — desired 는 terraform 밖에서 정하고, task-def 는 terraform 소유.
    # ⚠️ 이 서비스만 desired 의 주인이 다르다: ALPHA-912 로 **오토스케일링이 소유한다**
    # (`analysis_autoscaling.tf`). 세션은 이 서비스를 올리지도 내리지도 않는다.
    # 그래서 이 `ignore_changes` 는 그때보다 지금 **더** 필요하다 — 없으면 apply 마다
    # 스케일러가 정한 대수를 terraform 이 0 으로 되돌린다.
    ignore_changes = [desired_count]
  }
}

resource "aws_iam_role_policy" "analysis_consumer_queue" {
  name = "analysis-consumer-queue"
  role = aws_iam_role.analysis_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      # 소비 + ReturnsNotReady 지연(ChangeMessageVisibility). 설명 큐 하나뿐이다.
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
      Resource = [aws_sqs_queue.minute["price-explanation-realtime"].arn]
    }]
  })
}
