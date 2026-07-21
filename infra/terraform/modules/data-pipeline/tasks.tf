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
  env_sets = {
    rds = {
      DATA_PIPELINE_DB__HOST = var.db_host
      DATA_PIPELINE_DB__PORT = tostring(var.db_port)
      DATA_PIPELINE_DB__NAME = var.db_name
      DATA_PIPELINE_DB__USER = var.db_user
    }
    events = {
      DATA_PIPELINE_DB__HOST = var.db_host
      DATA_PIPELINE_DB__PORT = tostring(var.db_port)
      DATA_PIPELINE_DB__NAME = var.db_name
      DATA_PIPELINE_DB__USER = var.db_user
    }
  }

  secret_sets = {
    fmp = {
      DATA_PIPELINE_NEWS__SOURCES__FMP__API_KEY = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
      DATA_PIPELINE_PRICE__SOURCE__API_KEY      = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
      DATA_PIPELINE_FINANCIAL__SOURCE__API_KEY  = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
      DATA_PIPELINE_ETF__SOURCE__API_KEY        = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
    }
    bigkinds = {}
    # 가격(kis_price)과 NAV(kis_nav)는 같은 KIS 앱키를 쓰지만 설정 섹션이 달라 env 도 따로다
    # (같은 시크릿의 같은 필드를 두 이름으로 주입 — 새 시크릿 불요). ⚠️ 같은 앱키라 두 스텝이
    # 동시에 토큰을 발급하면 분당 1회 제한(403 EGW00133)에 걸린다 — kis_auth 의 대기·재시도가
    # 그걸 흡수한다(ALPHA-458). 앱키 분리는 별건.
    kis = {
      DATA_PIPELINE_KIS_PRICE__SOURCE__APP_KEY    = "${aws_secretsmanager_secret.kis.arn}:app_key::"
      DATA_PIPELINE_KIS_PRICE__SOURCE__APP_SECRET = "${aws_secretsmanager_secret.kis.arn}:app_secret::"
      DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY      = "${aws_secretsmanager_secret.kis.arn}:app_key::"
      DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET   = "${aws_secretsmanager_secret.kis.arn}:app_secret::"
    }
    dart = {
      DATA_PIPELINE_DART_FINANCIAL__SOURCE__API_KEY  = "${aws_secretsmanager_secret.dart.arn}:apikey::"
      DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY = "${aws_secretsmanager_secret.dart.arn}:apikey::"
    }
    krx = {
      DATA_PIPELINE_KRX_ETF__SOURCE__MBR_ID = "${aws_secretsmanager_secret.krx.arn}:mbr_id::"
      DATA_PIPELINE_KRX_ETF__SOURCE__PW     = "${aws_secretsmanager_secret.krx.arn}:pw::"
    }
    # tag-news 의 LLM 설정은 DATA_PIPELINE_* 네임스페이스 밖이다 — LLM 은 수집 소스가 아니라
    # load_settings() 계약에 들지 않고, 호출부(run.py)가 env 를 직접 읽는다(analysis-engine
    # analyze_daily.py 와 같은 LLM_* 관례). base_url·model 은 코드 기본값이 곧 DeepSeek 이라
    # 주입하지 않는다.
    deepseek = {
      LLM_API_KEY = "${var.deepseek_secret_arn}:api_key::"
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
  # 어긋나지 않게 한 곳에서 고정한다. dev 는 KODEX 반도체(091160) 단일 ETF 상수.
  analysis_result_s3_prefix = "operations_archive/etf_explanations/"

  analysis_env = merge({
    AWS_REGION                 = var.region
    ALPHAMALE_LAKE_BUCKET      = var.lake_bucket_name
    PGHOST                     = var.db_host
    PGPORT                     = tostring(var.db_port)
    PGDATABASE                 = var.db_name
    PGUSER                     = var.db_user
    PGSCHEMA                   = "public"
    DEEPSEEK_MODEL             = "deepseek-v4-pro"
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
