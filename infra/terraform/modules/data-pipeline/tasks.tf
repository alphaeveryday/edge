resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
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
  }

  secret_sets = {
    fmp = {
      DATA_PIPELINE_NEWS__SOURCES__FMP__API_KEY = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
      DATA_PIPELINE_PRICE__SOURCE__API_KEY      = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
      DATA_PIPELINE_FINANCIAL__SOURCE__API_KEY  = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
      DATA_PIPELINE_ETF__SOURCE__API_KEY        = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
    }
    bigkinds = {}
    kis = {
      DATA_PIPELINE_KIS_PRICE__SOURCE__APP_KEY    = "${aws_secretsmanager_secret.kis.arn}:app_key::"
      DATA_PIPELINE_KIS_PRICE__SOURCE__APP_SECRET = "${aws_secretsmanager_secret.kis.arn}:app_secret::"
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
    DEEPSEEK_MODEL             = "deepseek-chat"
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
