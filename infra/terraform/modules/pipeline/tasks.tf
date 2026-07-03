# ── 로그 ────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

# ── 보안그룹 ────────────────────────────────────────────
# 인바운드 없음(배치 task 는 수신 없음). egress 전체 허용(NAT 로 외부 API·ECR·RDS 도달).
# RDS 5432 인바운드 허용은 호출부(env)가 이 SG 를 RDS 인바운드로 거는 방식(순환 의존 회피).
resource "aws_security_group" "task" {
  name        = "${var.name}-task"
  description = "news-pipeline batch tasks ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.task.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "allow all egress (external API, ECR pull, RDS)"
}

# ── 태스크 정의 (pipeline / inference) ──────────────────
locals {
  # 접속·버킷을 뺀 공통 env (CDK 태스크 정의 값 그대로 옮김).
  common_env = {
    PROJECT                 = "news-pipeline"
    ENV                     = "dev"
    AWS_REGION_NAME         = var.region
    RAW_BUCKET              = aws_s3_bucket.raw.bucket
    CURATED_BUCKET          = aws_s3_bucket.curated.bucket
    NEWS_SOURCES            = "google_news,fmp"
    NEWS_SOURCE             = "stockinfo7"
    NER_ENABLED             = "false"
    PREFLIGHT_TERMS_OK      = "true"
    CONTACT_EMAIL           = var.contact_email
    FMP_PRICE_LOOKBACK_DAYS = "7"
    FMP_FINANCIAL_LIMIT     = "8"
    DB_SCHEMA               = "market"
    RUN_MODE                = "incremental" # SFN 이 $.mode 로 덮음
    RUN_ID                  = "manual"      # SFN 이 $.run_id 로 덮음
    # DB (edge RDS, edge 관례: 평문 접속정보 + PGPW 시크릿)
    NEWSDB_HOST = var.db_host
    NEWSDB_PORT = tostring(var.db_port)
    NEWSDB_NAME = var.db_name
    NEWSDB_USER = var.db_user
  }

  inference_env = merge(local.common_env, {
    TRANSFORMERS_CACHE = "/tmp/hf"
    HF_HOME            = "/tmp/hf"
    ARTIFACTS_DIR      = "model_artifacts_temporal"
    LLM_BASE_URL       = "https://api.openai.com/v1"
    LLM_MODEL          = "gpt-4o-mini"
  })

  common_secrets = {
    FMP_API_KEY = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
    PGPW        = "${var.db_password_secret_arn}:password::"
  }

  inference_secrets = merge(local.common_secrets, {
    OPENAI_API_KEY = "${aws_secretsmanager_secret.openai.arn}:apikey::"
    LLM_API_KEY    = "${aws_secretsmanager_secret.openai.arn}:apikey::"
  })

  log_options = {
    "awslogs-group"  = aws_cloudwatch_log_group.pipeline.name
    "awslogs-region" = var.region
  }
}

resource "aws_ecs_task_definition" "pipeline" {
  family                   = "${var.name}-pipeline"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.pipeline_cpu
  memory                   = var.pipeline_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name        = "pipeline"
    image       = var.image
    essential   = true
    command     = ["collect"] # 기본값 — 상태머신이 스텝별 Command 로 덮음
    environment = [for k, v in local.common_env : { name = k, value = v }]
    secrets     = [for k, v in local.common_secrets : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-stream-prefix" = "pipeline" })
    }
  }])
}

resource "aws_ecs_task_definition" "inference" {
  family                   = "${var.name}-inference"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.inference_cpu
  memory                   = var.inference_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name        = "pipeline"
    image       = var.image
    essential   = true
    command     = ["analyze_daily"]
    environment = [for k, v in local.inference_env : { name = k, value = v }]
    secrets     = [for k, v in local.inference_secrets : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-stream-prefix" = "inference" })
    }
  }])
}
