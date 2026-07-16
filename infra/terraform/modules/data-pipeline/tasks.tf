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
