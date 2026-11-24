# 업무 RDS(edge-dev) 정비용 ECS 태스크(ALPHA-1500). 서비스는 없다 — 사람이 RunTask 로 띄우거나, 아래 매일 스케줄이 띄운다.
#
# 첫 용도: tool_runs 의 지난 내부 스냅샷(record_research_state·capture_news_snapshot)을 S3 에 보관한 뒤 나눠 지우고
# VACUUM 한다(2026-11-16 디스크 가득 참, ALPHA-1499). tool_runs 는 소유자(마스터 edge)만 지울 수 있고, 분석 쓰기 계정은
# SELECT·INSERT 뿐이다(V202609282100). 마스터 시크릿을 주입하는 기존 태스크 셋은 이 일에 맞지 않는다:
#   - schema-migrate: Flyway 이미지라 SQL 마이그레이션만 돈다.
#   - db-query: entryPoint 가 edge_db_query 로 고정이고 읽기 전용을 강제한다.
#   - airflow dbadmin: psql 만 있고 태스크 역할이 없어 S3 에 보관할 수 없다. 용도도 Airflow 메타DB 관리다.
# 그래서 python3·psycopg·boto3 가 든 data-pipeline 이미지에, 마스터 시크릿과 보관 접두사 하나만 쓰는 역할을 붙인다.
# 명령은 RunTask override 로 준다(entryPoint sh -c). 기본 명령은 아무것도 하지 않고 exit 2 다.
# 이 파일은 modules/rds 를 건드리지 않는다(그 모듈은 apply_immediately=true 라 머지가 곧 DB 재부팅일 수 있다).
# RDS SG 에는 인그레스 규칙 리소스 하나만 더한다(schema_migrate·db_query 와 같은 방식, SG 자체는 불변).

locals {
  db_maintenance_name   = "${local.prefix}-db-maintenance"
  db_maintenance_prefix = "operations_archive/tool_runs_snapshots"
}

resource "aws_cloudwatch_log_group" "db_maintenance" {
  name              = "/ecs/${local.db_maintenance_name}"
  retention_in_days = 30
}

data "aws_iam_policy_document" "db_maintenance_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "db_maintenance_execution" {
  name               = "${local.db_maintenance_name}-execution"
  assume_role_policy = data.aws_iam_policy_document.db_maintenance_assume.json
}

resource "aws_iam_role_policy_attachment" "db_maintenance_execution" {
  role       = aws_iam_role.db_maintenance_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "db_maintenance_execution_secret" {
  name = "${local.db_maintenance_name}-secret"
  role = aws_iam_role.db_maintenance_execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [module.rds.master_user_secret_arn] }]
  })
}

# 태스크 역할: 보관 접두사 하나에만 쓰고 읽는다(지우기·목록 권한 없음 — 보관본은 사람이 지운다) + 멈출 조건용 RDS 지표 읽기.
resource "aws_iam_role" "db_maintenance_task" {
  name               = "${local.db_maintenance_name}-task"
  assume_role_policy = data.aws_iam_policy_document.db_maintenance_assume.json
}

resource "aws_iam_role_policy" "db_maintenance_task" {
  name = local.db_maintenance_name
  role = aws_iam_role.db_maintenance_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject"],
      Resource = ["${module.pipeline.lake_bucket_arn}/${local.db_maintenance_prefix}/*"] },
      # GetMetricData 는 리소스·지표 이름으로 좁힐 수 없다(AWS 서비스 권한 참조: 리소스 유형·조건 키 없음) — "*" 뿐이다.
      # 읽기 전용이고, 스크립트는 AWS/RDS FreeStorageSpace·TransactionLogsDiskUsage 만 부른다.
      { Effect = "Allow", Action = ["cloudwatch:GetMetricData"], Resource = ["*"] },
    ]
  })
}

resource "aws_security_group" "db_maintenance" {
  name        = "${local.db_maintenance_name}-task"
  description = "db maintenance one-off task ${local.db_maintenance_name}"
  vpc_id      = module.network.vpc_id
  tags        = { Name = "${local.db_maintenance_name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "db_maintenance_all" {
  security_group_id = aws_security_group.db_maintenance.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "allow all egress (ECR, S3, CloudWatch, RDS)"
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_db_maintenance" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = aws_security_group.db_maintenance.id
  ip_protocol                  = "tcp"
  from_port                    = module.rds.port
  to_port                      = module.rds.port
  description                  = "db maintenance one-off task"
}

resource "aws_ecs_task_definition" "db_maintenance" {
  family                   = local.db_maintenance_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 2048 # 보관 파트 50행 × 최대 약 14MB + gzip
  execution_role_arn       = aws_iam_role.db_maintenance_execution.arn
  task_role_arn            = aws_iam_role.db_maintenance_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name       = "db-maintenance"
    image      = "${local.data_pipeline_ecr_repository_url}:${local.data_pipeline_image_tag}" # python3.12 + psycopg3 + boto3
    essential  = true
    entryPoint = ["sh", "-c"]
    command    = ["echo 'db-maintenance: 명령은 RunTask override 로 준다'; exit 2"]
    environment = [
      { name = "PGHOST", value = module.rds.address },
      { name = "PGPORT", value = tostring(module.rds.port) },
      { name = "PGUSER", value = module.rds.master_username },
      { name = "PGDATABASE", value = module.rds.db_name },
      { name = "PGSSLMODE", value = "require" },
      { name = "ARCHIVE_BUCKET", value = module.pipeline.lake_bucket },
      { name = "RDS_INSTANCE_ID", value = local.prefix },
    ]
    secrets = [{ name = "PGPASSWORD", valueFrom = "${module.rds.master_user_secret_arn}:password::" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.db_maintenance.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "maint"
      }
    }
  }])
}

# ── 매일: 지난 분석 내부 스냅샷 정리 ──────────────────────
# 분석마다 그 ETF 의 누적 조사 상태 전체를 tool_runs 1행으로 남겨(전망 1건 약 4MB) DB 가 하루 약 1GB 씩 불었다.
# 다음 분석은 체인별 최신 행만 읽으므로 지난 행을 매일 지운다. 규칙·디스크 보호는
# apps/engine/data-pipeline/src/data_pipeline/ops/snapshot_retention.py 가 SSOT 다.
# 04:30 KST: 전망 배치(06:00)·수집 DAG·장중 워커와 겹치지 않는 시각.
resource "aws_iam_role" "db_maintenance_scheduler" {
  name = "${local.db_maintenance_name}-scheduler"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "scheduler.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "db_maintenance_scheduler" {
  name = "${local.db_maintenance_name}-scheduler"
  role = aws_iam_role.db_maintenance_scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow", Action = ["ecs:RunTask"],
        Resource  = [aws_ecs_task_definition.db_maintenance.arn_without_revision, "${aws_ecs_task_definition.db_maintenance.arn_without_revision}:*"]
        Condition = { ArnEquals = { "ecs:cluster" = module.worker_cluster.cluster_arn } }
      },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.db_maintenance_execution.arn, aws_iam_role.db_maintenance_task.arn] },
      { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = module.data_pipeline.scheduler_dlq_arn },
    ]
  })
}

resource "aws_scheduler_schedule" "snapshot_retention" {
  name                         = "${local.db_maintenance_name}-snapshot-retention"
  schedule_expression          = "cron(30 4 * * ? *)"
  schedule_expression_timezone = "Asia/Seoul"

  flexible_time_window { mode = "OFF" }

  target {
    arn      = module.worker_cluster.cluster_arn
    role_arn = aws_iam_role.db_maintenance_scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.db_maintenance.arn_without_revision
      launch_type         = "FARGATE"
      network_configuration {
        subnets          = module.network.private_subnet_ids
        security_groups  = [aws_security_group.db_maintenance.id]
        assign_public_ip = false
      }
    }

    input = jsonencode({
      containerOverrides = [{
        name        = "db-maintenance"
        command     = ["python -m data_pipeline.ops.snapshot_retention"]
        environment = [{ name = "MODE", value = "run" }]
      }]
    })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }
    dead_letter_config { arn = module.data_pipeline.scheduler_dlq_arn }
  }
}

# 실패 알림 — 스케줄러는 RunTask 제출만 안다. 종료 코드 ≠ 0(3 = 디스크 여유 부족으로 멈춤, 그 밖은 예외)이거나
# 컨테이너가 뜨지 못하면 알린다(modules/ecs-scheduled-task 의 task_failure 와 같은 모양). 사람이 띄운 정비 실행의 실패도 함께 온다.
resource "aws_cloudwatch_event_rule" "db_maintenance_failure" {
  name        = "${local.db_maintenance_name}-task-failure"
  description = "Alert when a ${local.db_maintenance_name} task stops with non-zero exit or fails to start"
  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Task State Change"]
    detail = {
      lastStatus        = ["STOPPED"]
      taskDefinitionArn = [{ prefix = "${aws_ecs_task_definition.db_maintenance.arn_without_revision}:" }]
      "$or" = [
        { containers = { exitCode = [{ anything-but = 0 }] } },
        { stopCode = ["TaskFailedToStart"] },
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "db_maintenance_failure" {
  rule      = aws_cloudwatch_event_rule.db_maintenance_failure.name
  target_id = "alarm-sns"
  arn       = module.data_pipeline.alarm_topic_arn
  role_arn  = aws_iam_role.db_maintenance_failure_events.arn
}

resource "aws_iam_role" "db_maintenance_failure_events" {
  name = "${local.db_maintenance_name}-failure-events"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRole", Principal = { Service = "events.amazonaws.com" }
      Condition = { ArnEquals = { "AWS:SourceArn" = aws_cloudwatch_event_rule.db_maintenance_failure.arn } }
    }]
  })
}

resource "aws_iam_role_policy" "db_maintenance_failure_events" {
  name   = "${local.db_maintenance_name}-failure-events"
  role   = aws_iam_role.db_maintenance_failure_events.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sns:Publish", Resource = module.data_pipeline.alarm_topic_arn }] })
}

# 하트비트 — RunTask 가 failures 로 태스크를 못 띄우면 DLQ 도 STOPPED 이벤트도 없다. 이틀 동안 정상 완료 로그가
# 없으면 알린다(하루 한 번 실패는 위 실패 알림이 맡는다).
resource "aws_cloudwatch_log_metric_filter" "snapshot_retention_success" {
  name           = "${local.db_maintenance_name}-snapshot-retention-success"
  log_group_name = aws_cloudwatch_log_group.db_maintenance.name
  pattern        = "{ $.event = \"done\" && $.mode = \"run\" && $.stopped NOT EXISTS }"
  metric_transformation {
    name      = "${local.db_maintenance_name}-snapshot-retention-success"
    namespace = "Edge/ScheduledTask"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "snapshot_retention_no_success" {
  alarm_name          = "${local.db_maintenance_name}-snapshot-retention-no-success"
  alarm_description   = "분석 스냅샷 정리(${aws_scheduler_schedule.snapshot_retention.name})가 이틀 동안 한 번도 끝나지 않았다 — 제출 실패·태스크 미기동·실행 실패 중 하나다. /ecs/${local.db_maintenance_name} 로그를 본다."
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [module.data_pipeline.alarm_topic_arn]

  metric_query {
    id = "success"
    metric {
      namespace   = "Edge/ScheduledTask"
      metric_name = "${local.db_maintenance_name}-snapshot-retention-success"
      stat        = "Sum"
      period      = 86400
    }
  }

  metric_query {
    id          = "filled"
    expression  = "FILL(success, 0)"
    label       = "snapshot retention success count"
    return_data = true
  }

  depends_on = [aws_cloudwatch_log_metric_filter.snapshot_retention_success]
}
