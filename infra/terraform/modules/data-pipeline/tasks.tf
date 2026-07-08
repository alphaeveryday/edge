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

  env = {
    AWS_REGION_NAME                = var.region
    DATA_PIPELINE_STORAGE__BACKEND = "s3"
    DATA_PIPELINE_STORAGE__BUCKET  = var.lake_bucket_name
  }

  secret_sets = {
    fmp = {
      DATA_PIPELINE_NEWS__SOURCES__FMP__API_KEY = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
      DATA_PIPELINE_PRICE__SOURCE__API_KEY      = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
      DATA_PIPELINE_FINANCIAL__SOURCE__API_KEY  = "${aws_secretsmanager_secret.fmp.arn}:apikey::"
    }
    bigkinds = {}
    kis = {
      DATA_PIPELINE_KIS_PRICE__SOURCE__APP_KEY    = "${aws_secretsmanager_secret.kis.arn}:app_key::"
      DATA_PIPELINE_KIS_PRICE__SOURCE__APP_SECRET = "${aws_secretsmanager_secret.kis.arn}:app_secret::"
    }
    dart = {
      DATA_PIPELINE_DART_FINANCIAL__SOURCE__API_KEY = "${aws_secretsmanager_secret.dart.arn}:apikey::"
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
    environment = [for k, v in local.env : { name = k, value = v }]
    secrets     = [for k, v in each.value : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = local.log_options
    }
  }])
}
