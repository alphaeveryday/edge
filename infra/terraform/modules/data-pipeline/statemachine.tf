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

  # raw 성공 뒤 도는 정제 스테이지(ALPHA-355). raw 와 같은 브랜치 구조를 재사용하되 잡만 다르다.
  # normalize 는 벤더 API 키가 필요 없고(레이크만 읽고 canonical 을 쓴다) 모든 task-def 가 같은
  # task_role(레이크 RW)을 공유하므로, 시크릿 없는 bigkinds task-def 를 재사용한다 — 새 task-def·
  # IAM 불요. 전체런(`--input-run-id` 없이)이라 멱등 canonical 적재. normalize-financial 은 아직
  # canonical 스텝이 없어 제외한다(재무는 raw-only).
  normalize_jobs = [
    {
      state        = "NormalizeNews"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-news', '--run-id', $.run_id)"
    },
    {
      state        = "NormalizePrice"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-price', '--run-id', $.run_id)"
    },
    {
      state        = "NormalizeDisclosure"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-disclosure', '--run-id', $.run_id)"
    },
    {
      state        = "NormalizeDisclosureSegment"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-disclosure-segment', '--run-id', $.run_id)"
    },
  ]

  raw_ingest_success_checks = [
    for index, _ in local.raw_ingest_jobs : {
      Variable     = "$.branch_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

  normalize_success_checks = [
    for index, _ in local.normalize_jobs : {
      Variable     = "$.normalize_results[${index}].status"
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

  # raw·normalize 가 동일한 브랜치 구조라 잡 리스트만 바꿔 한 빌더로 재생성한다(ALPHA-355).
  branches_by_phase = {
    for phase, jobs in { raw = local.raw_ingest_jobs, normalize = local.normalize_jobs } :
    phase => [
      for job in jobs : {
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
  }

  raw_ingest_branches = local.branches_by_phase["raw"]
  normalize_branches  = local.branches_by_phase["normalize"]

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
          Next = "NormalizeParallel"
        }]
        Default = "NotifyFailure"
      }
      # raw 전량 성공 뒤에만 정제로 넘어간다 — raw 가 partial/실패면 NotifyFailure 로 빠져 canonical
      # 을 오염된 raw 위에 쌓지 않는다. ALPHA-351 로 흔한 절단은 이제 raw 브랜치에서 성공 처리된다.
      NormalizeParallel = {
        Type       = "Parallel"
        Branches   = local.normalize_branches
        ResultPath = "$.normalize_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NotifyFailure" }]
        Next       = "NormalizeCheckResults"
      }
      NormalizeCheckResults = {
        Type = "Choice"
        Choices = [{
          And  = local.normalize_success_checks
          Next = "PipelineSucceeded"
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
          Subject     = "[${var.name}] pipeline FAILED"
          "Message.$" = "States.JsonToString($)"
        }
      }
      PipelineFailed = { Type = "Fail", Cause = "pipeline failed" }
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
