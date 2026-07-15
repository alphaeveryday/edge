resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == null ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  # runTask.sync 공통 파라미터. 각 실행 스텝이 TaskDefinition·(옵션)Overrides 를 merge 한다.
  ecs_run_task_base = {
    Resource   = "arn:aws:states:::ecs:runTask.sync"
    ResultPath = "$.ecs"
    Parameters = {
      Cluster         = var.cluster_arn
      LaunchType      = "FARGATE"
      PlatformVersion = "LATEST"
      NetworkConfiguration = {
        AwsvpcConfiguration = {
          Subnets        = var.subnet_ids
          SecurityGroups = [aws_security_group.task.id]
          AssignPublicIp = "DISABLED"
        }
      }
    }
  }

  sfn_definition = jsonencode({
    StartAt        = "RouteRun"
    TimeoutSeconds = var.state_machine_timeout_seconds
    States = {
      # 입력에 trade_date 가 있으면 특정일 실행, 없으면(스케줄 트리거 등) 기본 = 오늘(Asia/Seoul).
      RouteRun = {
        Type = "Choice"
        Choices = [{
          Variable  = "$.trade_date"
          IsPresent = true
          Next      = "RunAnalysisForDate"
        }]
        Default = "RunAnalysisToday"
      }

      # 기본 실행: command override 없음 → ENTRYPOINT(python -m edge_analysis) 그대로 = 오늘.
      RunAnalysisToday = merge(local.ecs_run_task_base, {
        Type = "Task"
        Next = "CheckExitCode"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "NotifyFailure"
        }]
        Parameters = merge(local.ecs_run_task_base.Parameters, {
          TaskDefinition = aws_ecs_task_definition.this.arn
        })
      })

      # 특정일 실행: CMD(args)만 덮어 --trade-date/--request-id 를 ENTRYPOINT 에 넘긴다.
      # 이 분기는 입력에 trade_date·request_id 가 함께 있어야 한다(운영 수동 실행 계약).
      RunAnalysisForDate = merge(local.ecs_run_task_base, {
        Type = "Task"
        Next = "CheckExitCode"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "NotifyFailure"
        }]
        Parameters = merge(local.ecs_run_task_base.Parameters, {
          TaskDefinition = aws_ecs_task_definition.this.arn
          Overrides = {
            ContainerOverrides = [{
              Name        = local.container_name
              "Command.$" = "States.Array('--trade-date', $.trade_date, '--request-id', $.request_id)"
            }]
          }
        })
      })

      CheckExitCode = {
        Type = "Choice"
        Choices = [{
          Variable      = "$.ecs.Containers[0].ExitCode"
          NumericEquals = 0
          Next          = "PipelineSucceeded"
        }]
        Default = "NotifyFailure"
      }

      PipelineSucceeded = { Type = "Succeed" }

      NotifyFailure = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "PipelineFailed"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          Subject     = "[${var.name}] analysis FAILED"
          "Message.$" = "States.JsonToString($)"
        }
      }

      PipelineFailed = { Type = "Fail", Cause = "analysis failed" }
    }
  })
}

resource "aws_sfn_state_machine" "this" {
  name       = "${var.name}-analyze"
  role_arn   = aws_iam_role.sfn.arn
  definition = local.sfn_definition
}

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
    input    = jsonencode({}) # trade_date 없음 → RouteRun 기본분기 = 오늘 실행

    retry_policy {
      maximum_event_age_in_seconds = 86400
      maximum_retry_attempts       = 185
    }
  }
}
