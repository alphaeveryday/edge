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
  raw_ingest_jobs = [
    {
      state        = "CollectFmpNews"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-raw', '--source', 'fmp', '--run-id', $.run_id)"
    },
    {
      state        = "CollectFmpPrice"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-price-raw', '--source', 'fmp', '--run-id', $.run_id)"
    },
    {
      state        = "CollectFmpFinancial"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-raw-financial', '--source', 'fmp', '--run-id', $.run_id)"
    },
    {
      state        = "CollectBigKindsNews"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('ingest-raw', '--source', 'bigkinds', '--run-id', $.run_id)"
    },
    {
      state        = "CollectKisPrice"
      taskdef_key  = "kis"
      command_expr = "States.Array('ingest-price-raw', '--source', 'kis', '--run-id', $.run_id)"
    },
    {
      state        = "CollectDartFinancial"
      taskdef_key  = "dart"
      command_expr = "States.Array('ingest-raw-financial', '--source', 'dart', '--run-id', $.run_id)"
    },
    {
      state        = "CollectDartDisclosure"
      taskdef_key  = "dart"
      command_expr = "States.Array('ingest-raw-disclosure', '--run-id', $.run_id)"
    },
    {
      state        = "CollectFmpEtf"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-raw-etf', '--run-id', $.run_id)"
    },
  ]

  raw_ingest_success_checks = [
    for index, _ in local.raw_ingest_jobs : {
      Variable     = "$.branch_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

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

  raw_ingest_branches = [
    for job in local.raw_ingest_jobs : {
      StartAt = job.state
      States = {
        (job.state) = merge(local.ecs_run_task_base, {
          Type = "Task"
          Next = "${job.state}CheckExitCode"
          Catch = [{
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "${job.state}TaskFailed"
          }]
          Parameters = merge(local.ecs_run_task_base.Parameters, {
            TaskDefinition = aws_ecs_task_definition.this[job.taskdef_key].arn
            Overrides = {
              ContainerOverrides = [{
                Name        = local.container_name
                "Command.$" = job.command_expr
              }]
            }
          })
        })
        "${job.state}CheckExitCode" = {
          Type = "Choice"
          Choices = [{
            Variable      = "$.ecs.Containers[0].ExitCode"
            NumericEquals = 0
            Next          = "${job.state}Succeeded"
          }]
          Default = "${job.state}Failed"
        }
        "${job.state}Succeeded" = {
          Type = "Pass"
          End  = true
          Parameters = {
            job           = job.state
            status        = "succeeded"
            "exit_code.$" = "$.ecs.Containers[0].ExitCode"
            "task_arn.$"  = "$.ecs.TaskArn"
          }
        }
        "${job.state}Failed" = {
          Type = "Pass"
          End  = true
          Parameters = {
            job           = job.state
            status        = "failed"
            cause         = "${job.state} container exited non-zero"
            "exit_code.$" = "$.ecs.Containers[0].ExitCode"
            "task_arn.$"  = "$.ecs.TaskArn"
          }
        }
        "${job.state}TaskFailed" = {
          Type = "Pass"
          End  = true
          Parameters = {
            job       = job.state
            status    = "failed"
            "error.$" = "$.error"
          }
        }
      }
    }
  ]

  sfn_definition = jsonencode({
    StartAt        = "RawIngestParallel"
    TimeoutSeconds = var.state_machine_timeout_seconds
    States = {
      RawIngestParallel = {
        Type       = "Parallel"
        Branches   = local.raw_ingest_branches
        ResultPath = "$.branch_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NotifyFailure" }]
        Next       = "RawIngestCheckResults"
      }
      RawIngestCheckResults = {
        Type = "Choice"
        Choices = [{
          And  = local.raw_ingest_success_checks
          Next = "RawIngestSucceeded"
        }]
        Default = "NotifyFailure"
      }
      RawIngestSucceeded = { Type = "Succeed" }
      NotifyFailure = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "RawIngestFailed"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          Subject     = "[${var.name}] raw ingest FAILED"
          "Message.$" = "States.JsonToString($)"
        }
      }
      RawIngestFailed = { Type = "Fail", Cause = "raw ingest failed" }
    }
  })
}

resource "aws_sfn_state_machine" "this" {
  name       = "${var.name}-raw-ingest"
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
    input    = jsonencode({ mode = "incremental", run_id = "<aws.scheduler.scheduled-time>" })

    retry_policy {
      maximum_event_age_in_seconds = 86400
      maximum_retry_attempts       = 185
    }
  }
}
