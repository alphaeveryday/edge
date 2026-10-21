# 주기 실행 후 종료하는 ECS Fargate 태스크 (ADR-0063).
#
# 상시 서비스가 아니라 task 정의와 그 실행 스케줄만 만든다(aws_ecs_service 없음).
# 이미지 리비전은 배포 워크플로가 등록하고, 스케줄은 task 정의를 **패밀리 이름**으로 불러
# RunTask 의 "리비전 미지정 시 최신 ACTIVE 리비전" 규칙으로 배포된 이미지를 집는다.

data "aws_caller_identity" "current" {}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

# ── IAM ────────────────────────────────────────────────
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# 실행 역할: 이미지 pull·로그 쓰기·시크릿 읽기 (ECS 에이전트가 사용).
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secret" {
  count = length(var.secret_arns) > 0 ? 1 : 0
  name  = "${var.name}-secret"
  role  = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.secret_arns
    }]
  })
}

# 태스크 역할: 컨테이너가 AWS API 를 호출하지 않으므로 비워 둔다.
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# ── 보안그룹 ────────────────────────────────────────────
# DB 인바운드 허용은 호출부(env)가 이 SG 를 각 DB SG 인바운드로 거는 방식(순환 의존 회피).
resource "aws_security_group" "this" {
  name        = "${var.name}-task"
  description = "scheduled task ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "allow all egress (ECR image pull, databases)"
}

# ── 태스크 정의 ─────────────────────────────────────────
locals {
  container = {
    name        = var.name
    image       = var.container_image
    essential   = true
    environment = [for k, v in var.environment : { name = k, value = v }]
    secrets     = [for k, v in var.secrets : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([local.container])

  # 배포 워크플로는 이 리비전을 복제해 이미지만 바꾼 새 리비전을 따로 등록한다. TF 는 자기 리비전만
  # 추적하고 baseline 이미지가 고정 문자열(latest)이라 드리프트가 없어 ignore_changes 를 두지 않는다.
  # env·시크릿 변경은 TF 가 latest 이미지로 새 리비전을 만들어 최신 ACTIVE 가 되고, 이후 배포도
  # 그 리비전을 복제하므로 새 설정이 이어진다.
  # ⚠️ baseline 은 배포가 매번 옮기고 ECR 수명 주기가 보존하는 움직이는 태그여야 한다
  # (없는·만료된 태그면 TF 리비전이 최신이 된 뒤의 실행이 전부 pull 실패).
}

# ── 스케줄 ──────────────────────────────────────────────
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.name}-scheduler"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # 배포 워크플로가 등록하는 모든 리비전
        Effect    = "Allow"
        Action    = ["ecs:RunTask"]
        Resource  = ["${aws_ecs_task_definition.this.arn_without_revision}:*"]
        Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } }
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = [aws_sqs_queue.scheduler_dlq.arn]
      },
    ]
  })
}

resource "aws_scheduler_schedule" "this" {
  name                         = var.name
  state                        = var.schedule_enabled ? "ENABLED" : "DISABLED"
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = "Asia/Seoul"

  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      Cluster = var.cluster_arn
      # 리비전 없는 패밀리 이름 = 최신 ACTIVE 리비전(배포 워크플로가 등록한 이미지)
      TaskDefinition = aws_ecs_task_definition.this.family
      LaunchType     = "FARGATE"
      NetworkConfiguration = {
        AwsvpcConfiguration = {
          Subnets        = var.subnet_ids
          SecurityGroups = [aws_security_group.this.id]
          AssignPublicIp = "DISABLED"
        }
      }
    })

    # 스케줄러는 RunTask 제출까지만 본다. 제출 실패는 짧게 재시도하고 다음 주기 전에 포기해 DLQ 로 보낸다.
    retry_policy {
      maximum_event_age_in_seconds = var.max_event_age_seconds
      maximum_retry_attempts       = 2
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dlq.arn }
  }
}

# ── 실패 알림 ───────────────────────────────────────────
# 제출 실패(재시도 소진) — 그 주기는 아예 안 돌았다.
resource "aws_sqs_queue" "scheduler_dlq" {
  name                      = "${var.name}-scheduler-dlq"
  message_retention_seconds = 1209600 # 14일
}

# 깊이가 아니라 도착 수 — 소비자가 없는 큐라 깊이 알람은 첫 실패 뒤 래칭된다(data-pipeline ops_ledger.tf 선례).
resource "aws_cloudwatch_metric_alarm" "scheduler_dlq_arrivals" {
  alarm_name          = "${var.name}-scheduler-dlq-arrivals"
  alarm_description   = "${var.name} 스케줄의 RunTask 제출이 재시도를 소진했다 — 그 주기는 실행되지 않았다."
  namespace           = "AWS/SQS"
  metric_name         = "NumberOfMessagesSent"
  dimensions          = { QueueName = aws_sqs_queue.scheduler_dlq.name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}

# 실행 실패 — 컨테이너 종료 코드 ≠ 0, 또는 컨테이너가 뜨지 못한 경우(이미지 pull·시크릿 오류).
resource "aws_cloudwatch_event_rule" "task_failure" {
  name        = "${var.name}-task-failure"
  description = "Alert when a ${var.name} task stops with non-zero exit or fails to start"

  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Task State Change"]
    detail = {
      lastStatus = ["STOPPED"]
      taskDefinitionArn = [{
        prefix = "${aws_ecs_task_definition.this.arn_without_revision}:"
      }]
      "$or" = [
        { containers = { exitCode = [{ anything-but = 0 }] } },
        { stopCode = ["TaskFailedToStart"] },
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "task_failure" {
  rule      = aws_cloudwatch_event_rule.task_failure.name
  target_id = "alarm-sns"
  arn       = var.alarm_topic_arn
  role_arn  = aws_iam_role.task_failure_events.arn
}

data "aws_iam_policy_document" "task_failure_events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudwatch_event_rule.task_failure.arn]
    }
  }
}

resource "aws_iam_role" "task_failure_events" {
  name               = "${var.name}-task-failure-events"
  assume_role_policy = data.aws_iam_policy_document.task_failure_events_assume.json
}

resource "aws_iam_role_policy" "task_failure_events" {
  name = "${var.name}-task-failure-events"
  role = aws_iam_role.task_failure_events.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sns:Publish"
      Resource = var.alarm_topic_arn
    }]
  })
}

# 하트비트 — 원인과 관계없는 "안 돌았음". RunTask 가 200 응답의 failures 로 태스크를 못 띄우면
# DLQ(제출 성공)도 STOPPED 이벤트(태스크 없음)도 안 생긴다. 성공 완료 로그 수로 그 구멍을 덮는다.
# 꺼진 스케줄에서는 계속 울리므로 켜져 있을 때만 만든다.
resource "aws_cloudwatch_log_metric_filter" "success" {
  count          = var.schedule_enabled && var.success_log_pattern != null ? 1 : 0
  name           = "${var.name}-success"
  log_group_name = aws_cloudwatch_log_group.this.name
  pattern        = var.success_log_pattern

  metric_transformation {
    name      = "${var.name}-success"
    namespace = "Edge/ScheduledTask"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "no_success" {
  count               = var.schedule_enabled && var.success_log_pattern != null ? 1 : 0
  alarm_name          = "${var.name}-no-success"
  alarm_description   = "${var.name} 가 ${var.heartbeat_minutes}분 동안 한 번도 성공 완료하지 않았다 — 실행 실패·제출 실패·태스크 미기동 중 하나다."
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [var.alarm_topic_arn]

  # 성공할 때만 값이 생기는 희소 지표라 결측을 명시적 0 으로 채워 평가한다.
  # treat_missing_data 만으로는 CloudWatch 가 조회 범위의 이전 성공 값을 재사용해 OK 를 유지할 수 있다.
  metric_query {
    id = "success"
    metric {
      namespace   = "Edge/ScheduledTask"
      metric_name = "${var.name}-success"
      stat        = "Sum"
      period      = var.heartbeat_minutes * 60
    }
  }

  metric_query {
    id          = "filled"
    expression  = "FILL(success, 0)"
    label       = "${var.name} success count"
    return_data = true
  }

  depends_on = [aws_cloudwatch_log_metric_filter.success]
}
