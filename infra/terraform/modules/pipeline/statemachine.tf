# ── SNS (실패 알림) ─────────────────────────────────────
resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == null ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ── Step Functions 상태머신 ─────────────────────────────
locals {
  # 순서대로 실행할 스텝. 마지막 analyze_daily 만 inference 태스크 정의 사용.
  sfn_steps = [
    { key = "Step-collect", cmd = "collect", taskdef = aws_ecs_task_definition.pipeline.arn },
    { key = "Step-alias_map", cmd = "alias_map", taskdef = aws_ecs_task_definition.pipeline.arn },
    { key = "Step-persist", cmd = "persist", taskdef = aws_ecs_task_definition.pipeline.arn },
    { key = "Step-fmp_price_collect", cmd = "fmp_price_collect", taskdef = aws_ecs_task_definition.pipeline.arn },
    { key = "Step-fmp_financial_collect", cmd = "fmp_financial_collect", taskdef = aws_ecs_task_definition.pipeline.arn },
    { key = "Step-us_news_ingest", cmd = "us_news_ingest", taskdef = aws_ecs_task_definition.pipeline.arn },
    { key = "Step-compute_ff5", cmd = "compute_ff5", taskdef = aws_ecs_task_definition.pipeline.arn },
    { key = "Step-analyze_daily", cmd = "analyze_daily", taskdef = aws_ecs_task_definition.inference.arn },
  ]

  sfn_task_states = {
    for i, s in local.sfn_steps : s.key => {
      Type       = "Task"
      Resource   = "arn:aws:states:::ecs:runTask.sync"
      ResultPath = null
      Retry      = [{ ErrorEquals = ["States.ALL"], IntervalSeconds = 1, MaxAttempts = 3, BackoffRate = 2 }]
      Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NotifyFailure" }]
      Next       = i + 1 < length(local.sfn_steps) ? local.sfn_steps[i + 1].key : "PipelineSucceeded"
      Parameters = {
        Cluster         = var.cluster_arn
        TaskDefinition  = s.taskdef
        LaunchType      = "FARGATE"
        PlatformVersion = "LATEST"
        NetworkConfiguration = {
          AwsvpcConfiguration = {
            Subnets        = var.subnet_ids
            SecurityGroups = [aws_security_group.task.id]
            AssignPublicIp = "DISABLED" # private 서브넷 + NAT
          }
        }
        Overrides = {
          ContainerOverrides = [{
            Name    = "pipeline"
            Command = [s.cmd]
            Environment = [
              { Name = "RUN_MODE", "Value.$" = "$.mode" },
              { Name = "RUN_ID", "Value.$" = "$.run_id" },
            ]
          }]
        }
      }
    }
  }

  sfn_definition = jsonencode({
    StartAt        = "Step-collect"
    TimeoutSeconds = 86400
    States = merge(local.sfn_task_states, {
      PipelineSucceeded = { Type = "Succeed" }
      NotifyFailure = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "PipelineFailed"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          Subject     = "[${var.name}] pipeline FAILED"
          "Message.$" = "$"
        }
      }
      PipelineFailed = { Type = "Fail", Cause = "pipeline step failed" }
    })
  })
}

resource "aws_sfn_state_machine" "this" {
  name       = "${var.name}-pipeline"
  role_arn   = aws_iam_role.sfn.arn
  definition = local.sfn_definition
}

# ── EventBridge Scheduler (일일 트리거) ─────────────────
# 검증 동안 DISABLED. 컷오버 시 schedule_state=ENABLED 로 켜고 CDK 스케줄을 끈다.
resource "aws_scheduler_schedule" "daily" {
  name                         = "${var.name}-daily"
  state                        = var.schedule_state
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.this.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ mode = "incremental", run_id = "<aws.scheduler.scheduled-time>" })

    retry_policy {
      maximum_event_age_in_seconds = 86400
      maximum_retry_attempts       = 185
    }
  }
}
