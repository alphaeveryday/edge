resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_security_group" "task" {
  name        = "${var.name}-task"
  description = "analysis-engine batch tasks ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.task.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "allow all egress (DeepSeek API, ECR, S3, RDS)"
}

locals {
  container_name = "analysis-engine"

  # 평문 env. 접속정보(PG*)·버킷·앱 설정. 비밀번호·LLM 키는 secrets 로 분리 주입한다.
  # result prefix 는 env 와 PutObject IAM 스코프가 어긋나지 않게 항상 lake bucket + prefix 로 고정한다.
  env = merge({
    AWS_REGION                 = var.region
    ALPHAMALE_LAKE_BUCKET      = var.lake_bucket_name
    PGHOST                     = var.db_host
    PGPORT                     = tostring(var.db_port)
    PGDATABASE                 = var.db_name
    PGUSER                     = var.db_user
    PGSCHEMA                   = var.pg_schema
    DEEPSEEK_MODEL             = var.deepseek_model
    ALPHAMALE_ETF_TICKER       = var.etf_ticker
    ALPHAMALE_RESULT_S3_PREFIX = "s3://${var.lake_bucket_name}/${var.result_s3_prefix}"
    },
    var.release_bundle_version == null ? {} : { ALPHAMALE_RELEASE_BUNDLE_VERSION = var.release_bundle_version },
  )

  # ECS 가 task 기동 시 Secrets Manager 에서 읽어 env 로 주입한다(앱은 평문 env 로 소비).
  secrets = {
    PGPASSWORD       = "${var.db_password_secret_arn}:password::"
    DEEPSEEK_API_KEY = "${var.deepseek_secret_arn}:apikey::"
  }

  log_options = {
    "awslogs-group"         = aws_cloudwatch_log_group.this.name
    "awslogs-region"        = var.region
    "awslogs-stream-prefix" = "analysis"
  }
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
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

  # command 미지정: 이미지 ENTRYPOINT(python -m edge_analysis)가 기본 실행 = 오늘(Asia/Seoul).
  # 특정 trade-date/request-id 실행은 상태머신이 ContainerOverrides.Command(=CMD args)로만 덮는다.
  container_definitions = jsonencode([{
    name        = local.container_name
    image       = var.image
    essential   = true
    environment = [for k, v in local.env : { name = k, value = v }]
    secrets     = [for k, v in local.secrets : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = local.log_options
    }
  }])
}
