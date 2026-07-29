resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

# 수집 소스가 통째로 건너뛰어진 런을 드러낸다(ALPHA-449, 검수 F-01).
#
# **왜 알람이 필요한가**: 수집기는 비활성·크리덴셜 결측을 status=skipped + **exit 0** 으로
# 처리한다. 기존 통보 2경로는 둘 다 그걸 못 잡는다 — SFN NotifyFailure 는 ExitCode != 0 에서만
# 발화하고, execution-timed-out 알람은 타임아웃 전용이다. 그래서 소스 하나가 통째로 빠져도
# 파이프라인은 정상 성공으로 끝나고 아무 메일도 안 간다. collection_log 에 사유가 남지만
# **읽는 쪽이 없어**(소비자는 테스트뿐) 사람이 S3 를 직접 열기 전엔 모른다.
#
# **이 알람이 덮지 않는 것**: 런을 실패시키지는 않는다. skip 된 소스 없이 다음 페이즈가 그대로
# 진행되는 오케스트레이션 게이트는 여전히 열려 있다(F-01 의 나머지 절반, 별도 티켓).
#
# 패턴이 두 갈래인 이유 — skip 은 로그 흔적이 다른 두 경로에서 난다:
#   - 비활성/크리덴셜 결측: logger.warning("… 수집 건너뜀") 후 조기 return 0. 5개 수집기의
#     문구가 조금씩 달라 공통 부분문자열 "수집 건너뜀" 로 잡는다.
#   - 매핑 타깃 0건: warning 이 아예 없고 종료 INFO 의 status=skipped 만 남는다.
# 벤더별로 쪼개지 않는다 — 알람은 "로그를 보라"는 신호이고, 정상 상태에서 발화가 없다(실측).
#
# ⚠️ 두 토큰 다 **부서지기 쉽다. 문구를 바꿀 땐 이 필터를 같이 고쳐라**:
#   - "수집 " 접두가 유일한 분리막이다. sources/*.py 에 "심볼 건너뜀"·"대상 건너뜀"·
#     "krx ETF 건너뜀" 같은 종목 단위 로그가 ~10종 있고 **매 정상 런마다 나온다**.
#     접두를 떼면 알람이 매일 울려 아무도 안 보게 된다.
#   - "status=skipped" 는 지금 5개 수집기의 종료 INFO 에서만 난다. tag_news.py 의
#     status=%s 는 dict 를 넣어 "status={'skipped': 3}" 로 렌더돼 안 걸리는데,
#     그 인자가 스칼라 문자열로 바뀌면 태깅이 수집 알람을 울린다.
#
# 덮지 못하는 것 하나 더: planned>0 인데 fetched==0 인 "빈 성공"은 status=success 라
# 안 걸린다(ingest_raw_financial.py 만 error 로 올린다). 같은 F-01 계열의 남은 사각이다.
resource "aws_cloudwatch_log_metric_filter" "raw_ingest_skipped" {
  name           = "${var.name}-raw-ingest-skipped"
  log_group_name = aws_cloudwatch_log_group.this.name
  pattern        = "?\"수집 건너뜀\" ?\"status=skipped\""

  # namespace 를 var.name 으로 가른다 — 메트릭 정체성은 namespace+name+dimensions 라, 고정
  # namespace 면 같은 계정·리전의 두 모듈 인스턴스(dev·prod)가 **같은 메트릭에 함께 쓴다**.
  # dev 의 skip 이 prod 알람을 울리고 그 반대도 된다. 이 모듈이 다른 리소스를 전부
  # "${var.name}-" 로 가르는 것과 같은 이유다(Codex #147 P2).
  #
  # dimensions 로 가르지 않는 이유: metric filter 의 dimension 값은 로그 이벤트에서
  # `$.field` 로 **추출**하는 것인데 우리 로그는 평문이라(run.py 의 basicConfig) 추출이
  # 안 된다. 붙이면 필터가 조용히 아무것도 안 낸다.
  metric_transformation {
    name      = "RawIngestSkipped"
    namespace = "edge/${var.name}"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "raw_ingest_skipped" {
  alarm_name        = "${var.name}-raw-ingest-skipped"
  alarm_description = "수집 소스가 건너뛰어졌다(비활성·크리덴셜 결측 또는 매핑 타깃 0건) — 런은 exit 0 으로 성공하므로 이 알람 말고는 드러나는 곳이 없다. collection_log 에서 어느 벤더인지 확인할 것."
  namespace         = "edge/${var.name}"
  metric_name       = "RawIngestSkipped"

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  # metric filter 는 매칭이 없으면 데이터포인트를 아예 안 낸다 — 평상시가 곧 결측이라
  # notBreaching 이어야 알람이 INSUFFICIENT_DATA 로 눌러앉지 않는다.
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
}

resource "aws_security_group" "task" {
  name        = "${var.name}-task"
  description = "data-pipeline raw ingest tasks ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.task.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "allow all egress (external APIs, ECR, S3)"
}

locals {
  container_name = "data-pipeline"

  # 모든 task-def 가 공유하는 평문 env. **여기에 스텝별 설정을 넣으면 안 된다** — 아래
  # env_sets 를 쓴다(이유는 거기 주석).
  env = {
    AWS_REGION_NAME                = var.region
    DATA_PIPELINE_STORAGE__BACKEND = "s3"
    DATA_PIPELINE_STORAGE__BUCKET  = var.lake_bucket_name
  }

  # task-def 별 평문 env. secret_sets 와 같은 키로 찾아 공용 env 에 덮어쓴다.
  #
  # DB 접속정보를 공용 env 에 둘 수 없어서 생긴 갈래다: `DbConfig` 는 password 가 없으면
  # **로드 시점에** ValueError 를 낸다(models.py `_require_password`). 그런데 password 는
  # rds task-def 에만 주입되므로, host 를 공용 env 에 두면 나머지 task-def 에서 db 섹션이
  # password 없이 구성돼 `load_settings()` 가 통째로 실패한다 — 수집·정제 스텝까지 전부.
  # 섹션은 있으면 완전해야 하고, 없으면 `db: DbConfig | None = None` 으로 조용히 생략된다.
  # DB 접속 env — DbConfig 는 섹션이 있으면 완전(host+password)해야 하므로 이 host-env 를 받는
  # task-def 는 아래 secret_sets 에서 password 도 함께 받아야 한다(부분 주입=load_settings 실패).
  db_env = {
    DATA_PIPELINE_DB__HOST = var.db_host
    DATA_PIPELINE_DB__PORT = tostring(var.db_port)
    DATA_PIPELINE_DB__NAME = var.db_name
    DATA_PIPELINE_DB__USER = var.db_user
  }

  # 운영 원장(ALPHA-530): 계측 대상 컨테이너가 wrapper 로 attempt/data_status 를 **직접**
  # 기록하려면 원장 DB 가 필요하다. rds·events 와 같은 DB 접속(같은 Cloud Event Store, ops_ 테이블).
  # 없으면 그 wrapper 가 no-op 이 된다(edge-review) — 원장에 PENDING 행만 남아 화면이 "대기"로
  # 굳고, 컨테이너 안에서만 도는 로그 관측(records_out·data_status)은 영영 못 올라온다.
  # ⚠️ 여기 목록은 `catalog.py` 의 `instrumented=True` 집합과 **같아야 한다** — 어긋나면
  # 그 작업이 조용히 계측 없이 돈다. test_ops_catalog 가 이 파일을 읽어 대조한다(ALPHA-596).
  # KRX ETF 수집(ALPHA-387)은 as-of 라벨을 거래일 판정으로 정한다 — 비거래일 런은 KRX 가 직전
  # 거래일 PDF 를 주므로 그 날짜로 라벨해야 한다. Planner 와 **같은** 휴장일 집합을 받아야
  # "Planner 는 비거래일로 건너뛴 날을 수집은 거래일로 라벨"하는 모순이 안 생긴다.
  env_sets = {
    rds      = local.db_env
    events   = local.db_env
    # iNAV(ALPHA-557)는 거래일·개장 이후에만 수집한다 — 응답에 날짜가 없어 거래일을 수집
    # 시각으로 붙이는데 KIS 가 휴장일에도 직전 거래일 값을 주기 때문. 그 판정이 KRX 와 **같은**
    # 휴장일 집합을 봐야 한다. 안 주면 is_trading_day 가 평일 공휴일을 거래일로 보고 가드가
    # 주말만 아는 상태로 **조용히 퇴화**한다(가드가 있는데 안 걸리는 게 제일 나쁘다).
    # KIS_TOKEN_CACHE_PARAM(ALPHA-573): kis 브랜치 4개가 액세스 토큰을 공유할 SSM 파라미터
    # 이름. 안 주면 컨테이너가 각자 발급해 분당 1회 제한에 줄을 선다(마지막 브랜치 222초 대기).
    kis = merge(local.db_env, {
      OPS_KR_HOLIDAYS       = join(",", var.kr_holidays)
      KIS_TOKEN_CACHE_PARAM = local.kis_token_param_name
    })
    bigkinds = local.db_env
    rds_dart = local.db_env
    krx      = merge(local.db_env, { OPS_KR_HOLIDAYS = join(",", var.kr_holidays) })
    dart     = local.db_env
    # TAG_NEWS wrapper 기록용(ALPHA-610) — 위 krx·dart 와 같은 이유. 이 컨테이너는 기사별 LLM
    # 실패를 격리해 exit 0 으로 끝나므로, 원장이 봉투(failed_records)를 읽지 못하면 전건 실패도
    # 초록으로 보인다. 이 배선(#379)이 한 배포 앞서고 카탈로그 플래그 전환이 뒤따랐다 —
    # 순서를 뒤집으면 Reconciler 가 resolve 불가한 LEDGER_GAP 을 연다.
    deepseek = local.db_env
  }

  secret_sets = {
    fmp = {
      DATA_PIPELINE_NEWS__SOURCES__FMP__API_KEY = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
      DATA_PIPELINE_PRICE__SOURCE__API_KEY      = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
      DATA_PIPELINE_FINANCIAL__SOURCE__API_KEY  = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
      DATA_PIPELINE_ETF__SOURCE__API_KEY        = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
    }
    # bigkinds 는 벤더 시크릿이 없지만 원장 DB password 는 받는다(NORMALIZE_PRICE wrapper 기록용).
    bigkinds = {
      DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
    }
    # 가격(kis_price)·NAV(kis_nav)·투자자수급(kis_investor)은 같은 KIS 앱키를 쓰지만 설정 섹션이
    # 달라 env 도 따로다(같은 시크릿의 같은 필드를 세 이름으로 주입 — 새 시크릿 불요). 자격증명이
    # 없으면 소스가 enabled=false 로 조용히 skip(exit0)하므로, kis taskdef 로 도는 스텝은 모두
    # 여기 매핑돼야 한다(ALPHA-385). ⚠️ 같은 앱키라 여러 스텝이 동시에 토큰을 발급하면 분당 1회
    # 제한(403 EGW00133)에 걸린다 — kis_auth 의 대기·재시도가 흡수한다(ALPHA-458). 앱키 분리는 별건.
    kis = {
      DATA_PIPELINE_KIS_PRICE__SOURCE__APP_KEY       = "${aws_secretsmanager_secret.kis.arn}:app_key::"
      DATA_PIPELINE_KIS_PRICE__SOURCE__APP_SECRET    = "${aws_secretsmanager_secret.kis.arn}:app_secret::"
      DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY         = "${aws_secretsmanager_secret.kis.arn}:app_key::"
      DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET      = "${aws_secretsmanager_secret.kis.arn}:app_secret::"
      DATA_PIPELINE_KIS_INVESTOR__SOURCE__APP_KEY    = "${aws_secretsmanager_secret.kis.arn}:app_key::"
      DATA_PIPELINE_KIS_INVESTOR__SOURCE__APP_SECRET = "${aws_secretsmanager_secret.kis.arn}:app_secret::"
      # PRICE_COLLECTION_KIS wrapper 가 원장에 기록하려면 DB password 도 필요하다(ALPHA-530).
      DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
    }
    # DISCLOSURE_COLLECTION_DART wrapper 가 원장에 기록하려면 DB password 도 필요하다(ALPHA-596).
    # 이 task-def 로는 CollectDartFinancial 도 도는데, 그건 하류 소비자가 0이라 카탈로그 미등록이다
    # — 미등록 작업은 wrapper 가 투명 통과하므로(run.py `task_key_for` → None) 영향이 없다.
    dart = {
      DATA_PIPELINE_DART_FINANCIAL__SOURCE__API_KEY  = "${aws_secretsmanager_secret.dart.arn}:apikey::"
      DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY = "${aws_secretsmanager_secret.dart.arn}:apikey::"
      DATA_PIPELINE_DB__PASSWORD                     = "${var.db_password_secret_arn}:password::"
    }
    # ETF_HOLDINGS_COLLECTION_KRX wrapper 기록용(ALPHA-596) — 위 dart 와 같은 이유.
    krx = {
      DATA_PIPELINE_KRX_ETF__SOURCE__MBR_ID = "${aws_secretsmanager_secret.krx.arn}:mbr_id::"
      DATA_PIPELINE_KRX_ETF__SOURCE__PW     = "${aws_secretsmanager_secret.krx.arn}:pw::"
      DATA_PIPELINE_DB__PASSWORD            = "${var.db_password_secret_arn}:password::"
    }
    # tag-news 의 LLM 설정은 DATA_PIPELINE_* 네임스페이스 밖이다 — LLM 은 수집 소스가 아니라
    # load_settings() 계약에 들지 않고, 호출부(run.py)가 env 를 직접 읽는다(analysis-engine
    # analyze_daily.py 와 같은 LLM_* 관례). base_url·model 은 코드 기본값이 곧 DeepSeek 이라
    # 주입하지 않는다.
    deepseek = {
      LLM_API_KEY = "${var.deepseek_secret_arn}:api_key::"
      # TAG_NEWS wrapper 가 원장에 기록하려면 DB password 도 필요하다(ALPHA-610). host-env 는
      # 위 env_sets.deepseek — 부분 주입이면 load_settings() 가 통째로 터진다.
      DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
    }
    rds = {
      DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
    }
    # assemble-events(ALPHA-412) — 분류 LLM 과 DB 적재를 한 태스크가 다 한다(엔진 추출
    # 체인 이식이라 분리 불가). deepseek·rds 두 세트의 합집합.
    events = {
      LLM_API_KEY                = "${var.deepseek_secret_arn}:api_key::"
      DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
    }
    # enrich-corp-code(ALPHA-491·532) — DB(company_profile UPDATE)와 OpenDART corpCode.xml
    # 조회를 한 태스크가 다 한다. rds(DB password)·dart(disclosure 키) 두 세트의 합집합 —
    # 결합 세트가 없으면 rds task 에서 source.enabled=false 로 조용히 skip 된다(events 와 같은 형태).
    rds_dart = {
      DATA_PIPELINE_DB__PASSWORD                     = "${var.db_password_secret_arn}:password::"
      DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY = "${aws_secretsmanager_secret.dart.arn}:apikey::"
    }
  }

  log_options = {
    "awslogs-group"         = aws_cloudwatch_log_group.this.name
    "awslogs-region"        = var.region
    "awslogs-stream-prefix" = "raw-ingest"
  }
}

# ── analyze 페이즈 task-def (구 analysis-engine 모듈 흡수, ALPHA-408) ──
# for_each 밖에 따로 두는 이유: 이미지(alphamale)·env 네임스페이스(PG*·ALPHAMALE_*·DEEPSEEK_*)·
# 컨테이너명이 data-pipeline 계열과 전부 다르다. 시크릿 주입 메커니즘만 같다.
locals {
  analysis_container_name = "analysis-engine"

  # 결과 prefix — env(ALPHAMALE_RESULT_S3_PREFIX)와 analysis task 역할의 PutObject 스코프가
  # 어긋나지 않게 한 곳에서 고정한다.
  analysis_result_s3_prefix = "operations_archive/etf_explanations/"

  analysis_env = merge({
    AWS_REGION            = var.region
    ALPHAMALE_LAKE_BUCKET = var.lake_bucket_name
    PGHOST                = var.db_host
    PGPORT                = tostring(var.db_port)
    PGDATABASE            = var.db_name
    PGUSER                = var.db_user
    PGSCHEMA              = "public"
    DEEPSEEK_MODEL        = "deepseek-v4-pro"
    # fallback 기본값 — SFN analyze Map(ALPHA-470)이 이터레이션마다 유니버스 티커로 덮는다.
    # 직접 ecs run-task(특정일 수동 재실행) 때만 이 값이 실제로 쓰인다.
    ALPHAMALE_ETF_TICKER       = "091160"
    ALPHAMALE_RESULT_S3_PREFIX = "s3://${var.lake_bucket_name}/${local.analysis_result_s3_prefix}"
    },
    var.analysis_release_bundle_version == null ? {} : { ALPHAMALE_RELEASE_BUNDLE_VERSION = var.analysis_release_bundle_version },
  )

  analysis_secrets = {
    PGPASSWORD       = "${var.db_password_secret_arn}:password::"
    DEEPSEEK_API_KEY = "${var.deepseek_secret_arn}:api_key::"
  }
}

resource "aws_ecs_task_definition" "analysis" {
  family                   = "${var.name}-analysis"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.analysis_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  # command 미지정: 이미지 ENTRYPOINT(python -m edge_analysis)가 기본 실행 = 오늘(Asia/Seoul).
  # 특정 trade-date/request-id 재실행은 SFN 라우팅 없이 ecs run-task 로 이 task-def 를 직접
  # 띄워 Command(=CMD args: --trade-date/--request-id)만 덮는다 — 운영 수동 실행 계약.
  container_definitions = jsonencode([{
    name        = local.analysis_container_name
    image       = var.analysis_image
    essential   = true
    environment = [for k, v in local.analysis_env : { name = k, value = v }]
    secrets     = [for k, v in local.analysis_secrets : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-stream-prefix" = "analysis" })
    }
  }])
}

resource "aws_ecs_task_definition" "this" {
  for_each = local.secret_sets

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
    name        = local.container_name
    image       = var.image
    essential   = true
    command     = ["ingest-raw"]
    environment = [for k, v in merge(local.env, lookup(local.env_sets, each.key, {})) : { name = k, value = v }]
    secrets     = [for k, v in each.value : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = local.log_options
    }
  }])
}
