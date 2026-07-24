# 운영 원장 MVP — Planner/Reconciler 실행 인프라 (ALPHA-530).
#
# Planner(plan-run)와 Reconciler(reconcile)는 data-pipeline 이미지를 그대로 쓰되(수집·정제와 같은
# 컨테이너), DB(원장) + SFN/ECS 권한이 필요하다. 공용 task 역할(레이크 RW)에 SFN/ECS 를 얹으면
# 수집기까지 과권한이 되므로 **전용 ops_task 역할**을 둔다(analysis_task 선례와 같은 결).
#
# ⚠️ 이 파일은 운영 배포하지 않는다(ALPHA-530 범위: 로컬 구현·검증까지). schedule state 기본
# DISABLED, 적용 시 daily 트리거가 SFN 직접 시작에서 **Planner 경유**로 컷오버된다(스펙 §5).

# ── Planner/Reconciler 전용 task 역할 ──
resource "aws_iam_role" "ops_task" {
  name               = "${var.name}-ops-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "ops_task" {
  name = "${var.name}-ops-task"
  role = aws_iam_role.ops_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Planner 가 SFN 을 시작한다(StartExecution 소유는 스케줄러가 아니라 Planner, 스펙 §5).
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = [aws_sfn_state_machine.this.arn]
      },
      {
        # Planner(ExecutionAlreadyExists 확인)·Reconciler(실행 동기화·history)가 실행을 읽는다.
        Effect = "Allow"
        Action = ["states:DescribeExecution", "states:GetExecutionHistory"]
        Resource = [
          "${replace(aws_sfn_state_machine.this.arn, ":stateMachine:", ":execution:")}:*"
        ]
      },
      {
        # Reconciler 가 ECS 실제 종료를 확인한다(증거 없이 RUNNING 을 뒤집지 않기 위해, 스펙 §7).
        Effect    = "Allow"
        Action    = ["ecs:DescribeTasks"]
        Resource  = ["*"]
        Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } }
      },
    ]
  })
}

# ── Planner/Reconciler task-def (data-pipeline 이미지 재사용) ──
# 기동 주체(execution 역할)는 공용을 쓴다 — db password 시크릿 읽기는 이미 허용(rds task-def 와
# 같은 시크릿). command 기본은 reconcile 이고, 스케줄러가 plan-run/reconcile 로 각각 덮는다.
resource "aws_ecs_task_definition" "ops" {
  family                   = "${var.name}-ops"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.ops_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name      = local.container_name
    image     = var.image
    essential = true
    command   = ["reconcile"]
    environment = [for k, v in merge(local.env, {
      DATA_PIPELINE_DB__HOST  = var.db_host
      DATA_PIPELINE_DB__PORT  = tostring(var.db_port)
      DATA_PIPELINE_DB__NAME  = var.db_name
      DATA_PIPELINE_DB__USER  = var.db_user
      # Planner 가 시작할 상태머신. 카탈로그 버전은 배포 SHA 를 CD 가 주입(없으면 unknown).
      OPS_STATE_MACHINE_ARN = aws_sfn_state_machine.this.arn
    }) : { name = k, value = v }]
    secrets = [{
      name = "DATA_PIPELINE_DB__PASSWORD", valueFrom = "${var.db_password_secret_arn}:password::"
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-stream-prefix" = "ops" })
    }
  }])
}

# ── 스케줄러 DLQ (스펙 §5: retry 는 있으나 DLQ 부재였다) ──
# 전달 실패(재시도 소진)한 스케줄 이벤트를 잃지 않고 여기 남긴다 — Planner 미기동의 증거이자
# PLANNER_MISSING 탐지의 백스톱.
resource "aws_sqs_queue" "scheduler_dlq" {
  name                      = "${var.name}-scheduler-dlq"
  message_retention_seconds = 1209600 # 14일
}

resource "aws_sqs_queue_policy" "scheduler_dlq" {
  queue_url = aws_sqs_queue.scheduler_dlq.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = ["sqs:SendMessage"]
      Resource  = aws_sqs_queue.scheduler_dlq.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_iam_role.scheduler.arn } }
    }]
  })
}

# ── Reconciler 주기 스케줄 ──
# ECS RunTask universal target — 컨테이너 command 를 reconcile 로 덮고, 스케줄 시각을 env 로 넘긴다.
resource "aws_scheduler_schedule" "reconcile" {
  name                         = "${var.name}-reconcile"
  state                        = var.reconcile_schedule_state
  schedule_expression          = var.reconcile_schedule_expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      Cluster        = var.cluster_arn
      TaskDefinition = aws_ecs_task_definition.ops.arn
      LaunchType     = "FARGATE"
      NetworkConfiguration = {
        AwsvpcConfiguration = {
          Subnets        = var.subnet_ids
          SecurityGroups = [aws_security_group.task.id]
          AssignPublicIp = "DISABLED"
        }
      }
      Overrides = {
        ContainerOverrides = [{
          Name    = local.container_name
          Command = ["reconcile"]
        }]
      }
    })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 5
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dlq.arn }
  }
}
