# SFN 오케스트레이션 배치 파이프라인 (ECS Fargate one-off task 체인).
#
# 상시 서비스가 아니다 — EventBridge Scheduler → Step Functions 가 단계마다
# ecs:runTask.sync 로 Fargate task 를 띄웠다 내리는 worker 다(ecs-service 모듈과
# 별개인 이유). ASL 정의는 단계 목록(var.steps)에서 생성하므로 단계 추가/삭제는
# 호출부 변수 수정으로 끝난다.
#
# 네트워크: NAT 비용 회피를 위해 퍼블릭 서브넷 + 퍼블릭 IP 로 아웃바운드(이미지
# pull·외부 API 수집)한다. 퍼블릭 IP 는 서브넷 속성이 아니라 실행 시점 옵션이라
# 여기 SFN NetworkConfiguration 의 AssignPublicIp 로 결정된다.

data "aws_caller_identity" "current" {}

# ── 클러스터 ────────────────────────────────────────────
# 배치 전용이라 Service Connect 네임스페이스가 필요 없다(ecs-cluster 모듈을 안 쓰는 이유).
resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled"
  }
}

resource "aws_cloudwatch_log_group" "task" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/sfn/${var.name}"
  retention_in_days = var.log_retention_days
}

# ── IAM: 태스크 ─────────────────────────────────────────
data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# 실행 역할: 이미지 pull·로그 쓰기·주입 시크릿 읽기 (ECS 에이전트가 사용).
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = length(var.secret_arns) > 0 ? 1 : 0
  name  = "${var.name}-injected-secrets"
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

# 태스크 역할: 앱 자신이 호출하는 AWS API — S3 raw/curated 읽기쓰기, 런타임 시크릿 읽기.
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

resource "aws_iam_role_policy" "task_s3" {
  count = length(var.s3_bucket_arns) > 0 ? 1 : 0
  name  = "${var.name}-s3"
  role  = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = var.s3_bucket_arns
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [for arn in var.s3_bucket_arns : "${arn}/*"]
      },
    ]
  })
}

resource "aws_iam_role_policy" "task_secrets" {
  count = length(var.runtime_secret_arns) > 0 ? 1 : 0
  name  = "${var.name}-runtime-secrets"
  role  = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.runtime_secret_arns
    }]
  })
}

# ── 보안그룹 ────────────────────────────────────────────
# 인바운드 없음(배치 태스크는 리스닝하지 않음). RDS 5432 인바운드 허용은 호출부(env)가
# 이 SG 를 RDS 인바운드로 거는 방식(순환 의존 회피, rds 모듈 주석 참고).
resource "aws_security_group" "task" {
  name        = "${var.name}-task"
  description = "batch pipeline tasks ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.task.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "allow all egress (ECR pull, external APIs, RDS)"
}

# ── 태스크 정의 2종 (같은 이미지, 리소스·환경만 다름) ────
# command 는 넣지 않는다 — SFN 이 단계마다 ContainerOverrides.Command 로 지정한다.
locals {
  pipeline_container = {
    name        = "pipeline" # SFN ContainerOverrides.Name 이 참조하는 고정 이름
    image       = var.container_image
    essential   = true
    environment = [for k, v in var.environment : { name = k, value = v }]
    secrets     = [for k, v in var.secrets : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.task.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "pipeline"
      }
    }
  }

  inference_container = merge(local.pipeline_container, {
    environment = [for k, v in merge(var.environment, var.inference_environment) : { name = k, value = v }]
    secrets     = [for k, v in merge(var.secrets, var.inference_secrets) : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.task.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "inference"
      }
    }
  })
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

  container_definitions = jsonencode([local.pipeline_container])
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

  container_definitions = jsonencode([local.inference_container])
}

# ── 실패 알림 SNS ───────────────────────────────────────
# 구독(이메일 등)은 확인 절차가 필요해 콘솔/수동으로 건다.
resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"
}

# ── 상태 머신 (ASL 을 steps 에서 생성) ──────────────────
locals {
  task_states = {
    for i, s in var.steps : "Step-${s.command}" => {
      Type       = "Task"
      Resource   = "arn:aws:states:::ecs:runTask.sync"
      ResultPath = null # 태스크 출력을 버리고 원본 입력($.mode·$.run_id)을 다음 단계로 유지
      Next       = i < length(var.steps) - 1 ? "Step-${var.steps[i + 1].command}" : "PipelineSucceeded"
      Retry = [{
        ErrorEquals     = ["States.ALL"]
        IntervalSeconds = 1
        MaxAttempts     = 3
        BackoffRate     = 2
      }]
      Catch = [{
        ErrorEquals = ["States.ALL"]
        ResultPath  = "$.error"
        Next        = "NotifyFailure"
      }]
      Parameters = {
        Cluster         = aws_ecs_cluster.this.arn
        TaskDefinition  = s.inference ? aws_ecs_task_definition.inference.arn : aws_ecs_task_definition.pipeline.arn
        LaunchType      = "FARGATE"
        PlatformVersion = "LATEST"
        NetworkConfiguration = {
          AwsvpcConfiguration = {
            Subnets        = var.subnet_ids
            SecurityGroups = [aws_security_group.task.id]
            AssignPublicIp = var.assign_public_ip ? "ENABLED" : "DISABLED"
          }
        }
        Overrides = {
          ContainerOverrides = [{
            Name    = "pipeline"
            Command = [s.command]
            Environment = [
              { Name = "RUN_MODE", "Value.$" = "$.mode" },
              { Name = "RUN_ID", "Value.$" = "$.run_id" },
            ]
          }]
        }
      }
    }
  }

  definition = jsonencode({
    StartAt        = "Step-${var.steps[0].command}"
    TimeoutSeconds = var.sfn_timeout_seconds
    States = merge(local.task_states, {
      PipelineSucceeded = { Type = "Succeed" }
      NotifyFailure = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "PipelineFailed"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          "Message.$" = "$"
          Subject     = "[${var.name}] pipeline FAILED"
        }
      }
      PipelineFailed = { Type = "Fail", Cause = "pipeline step failed" }
    })
  })
}

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.name}-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

resource "aws_iam_role_policy" "sfn" {
  name = "${var.name}-sfn"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ecs:RunTask"]
        Resource = [
          aws_ecs_task_definition.pipeline.arn,
          aws_ecs_task_definition.inference.arn,
        ]
      },
      {
        # .sync 가 실행 중 태스크를 추적/중단할 때 사용. 태스크 ARN 은 실행 시점에 정해져 와일드카드.
        Effect   = "Allow"
        Action   = ["ecs:StopTask", "ecs:DescribeTasks"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
      {
        # .sync 의 태스크 종료 감지는 SFN 이 관리하는 EventBridge 규칙을 통한다.
        Effect   = "Allow"
        Action   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
        Resource = "arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.alarms.arn
      },
      {
        # logging_configuration 용 로그 전달 관리 권한 — CreateLogDelivery 류는 리소스 스코프 불가.
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_sfn_state_machine" "this" {
  name       = var.name
  role_arn   = aws_iam_role.sfn.arn
  definition = local.definition

  logging_configuration {
    level                  = "ERROR"
    include_execution_data = false
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
  }
}

# ── 스케줄 ──────────────────────────────────────────────
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
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
    Statement = [{
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = aws_sfn_state_machine.this.arn
    }]
  })
}

resource "aws_scheduler_schedule" "daily" {
  name  = "${var.name}-daily"
  state = var.schedule_enabled ? "ENABLED" : "DISABLED"

  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.this.arn
    role_arn = aws_iam_role.scheduler.arn
    # run_id 에는 Scheduler 컨텍스트 속성(예약 실행 시각)이 치환돼 들어간다.
    # jsonencode 는 부등호를 유니코드 시퀀스(backslash-u003c/u003e)로 이스케이프하는데,
    # Scheduler 의 토큰 치환은 JSON 파싱 전 리터럴 텍스트 매칭이라 못 찾는다 → 원문 복원.
    input = replace(replace(jsonencode({
      mode   = var.run_mode
      run_id = "<aws.scheduler.scheduled-time>"
    }), "\\u003c", "<"), "\\u003e", ">")
  }
}
