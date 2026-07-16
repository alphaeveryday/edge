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
    # ⚠️ 컷오버 블로커 — 이 잡은 **현재 스케줄로는 항상 실패한다**(ALPHA-387).
    # 형제 KR 소스(BigKinds·KIS·DART)는 날짜창 기반이라 빈 결과를 허용하지만, KRX ETF 는
    # `trdDd`=오늘(KST) 스냅샷이고 빈 응답을 fail-loud 한다(krx_etf.py 의 의도된 설계).
    # 그런데 이 SFN 스케줄은 미 동부 16:10 = **KST 05:10**이라 기준일이 PDF 미게시(금요일
    # 런은 아예 토요일)를 가리킨다. raw 는 전량성공 게이트라 이게 normalize·derive 를 통째로
    # 막는다. 지금 안 터지는 건 스케줄러가 DISABLED 라서다.
    # 그럼에도 게이트에 넣는 이유: 스케줄 자체가 US 마감 기준이라 KR 소스 전반과 안 맞고,
    # 그 재검토는 컷오버(schedule_state=ENABLED)의 일이지 편입의 일이 아니다. 수동
    # StartExecution(KST 주간)은 정상 동작한다. **ENABLED 로 바꾸기 전에 ALPHA-387 을 닫아라.**
    {
      state        = "CollectKrxEtf"
      taskdef_key  = "krx"
      command_expr = "States.Array('ingest-raw-etf', '--source', 'krx', '--run-id', $.run_id)"
    },
  ]

  # raw 성공 뒤 도는 정제 스테이지(ALPHA-355). raw 와 같은 브랜치 구조를 재사용하되 잡만 다르다.
  # normalize 는 벤더 API 키가 필요 없고(레이크만 읽고 canonical 을 쓴다) 모든 task-def 가 같은
  # task_role(레이크 RW)을 공유하므로, 시크릿 없는 bigkinds task-def 를 재사용한다 — 새 task-def·
  # IAM 불요. normalize-financial 은 아직 canonical 스텝이 없어 제외한다(재무는 raw-only).
  #
  # **`--input-run-id $.run_id` = 이 실행이 수집한 raw 만 정제한다**(ALPHA-389). 정제는
  # 데이터셋별 1잡이고 벤더를 합치는 자리라(한 task 가 source= 로 FMP·KIS 를 함께 읽는다),
  # 9개 raw 브랜치가 같은 run_id 를 쓰는 덕에 스코프 안에 그 런의 전 벤더가 들어온다.
  # 적재 자체는 여전히 멱등이다 — canonical 병합이 파티션의 기존 행을 읽어 합친다.
  # 실패 런 raw 재처리는 아래 NormalizeParallel 주석 참조.
  normalize_jobs = [
    {
      state        = "NormalizeNews"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-news', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizePrice"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-price', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeDisclosure"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-disclosure', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeDisclosureSegment"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-disclosure-segment', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeEtf"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-etf', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
  ]

  # canonical 을 소비해 다운스트림 산출물을 만드는 스테이지(ALPHA-386). normalize 와 갈라 둔
  # 이유는 의존이다 — 둘 다 canonical 전체를 읽으므로 정제가 끝난 뒤라야 한다. 두 잡은 서로
  # 독립이고(뉴스 feature vs ETF 마스터) 쓰는 대상도 다르다: tag-news 는 레이크 feature 존,
  # load-instruments 는 Cloud Event Store(RDB). 각자 시크릿이 달라 task-def 도 따로다.
  derive_jobs = [
    {
      state        = "TagNews"
      taskdef_key  = "deepseek"
      command_expr = "States.Array('tag-news', '--run-id', $.run_id, '--limit', '${var.tag_news_limit}')"
    },
    {
      state        = "LoadInstruments"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-instruments', '--run-id', $.run_id)"
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

  derive_success_checks = [
    for index, _ in local.derive_jobs : {
      Variable     = "$.derive_results[${index}].status"
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

  # 모든 페이즈가 동일한 브랜치 구조라 잡 리스트만 바꿔 한 빌더로 재생성한다(ALPHA-355·386).
  branches_by_phase = {
    for phase, jobs in { raw = local.raw_ingest_jobs, normalize = local.normalize_jobs, derive = local.derive_jobs } :
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
  derive_branches     = local.branches_by_phase["derive"]

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
      # raw 전량 성공일 때만 정제로 넘어간다 — 이번 실행의 raw 수집이 불완전하면 정제를
      # 헛돌리지 않는다. ALPHA-351 로 흔한 절단은 raw 브랜치에서 성공 처리되므로, 여기 걸리는
      # 건 진짜 실패다.
      #
      # ⚠️ **정제는 이 실행의 raw 만 본다**(`--input-run-id $.run_id`, ALPHA-389). 예전엔
      # full-scan 이라 "이전 실패 실행이 저장한 raw 도 다음 성공 실행이 함께 주워간다"는
      # 자동 구제가 있었는데, **그게 없어졌다.** 대가로 정제 비용이 여태 쌓인 raw 전체가
      # 아니라 이번 런에 비례한다(옛 구조는 영구히 O(전체 raw)였다).
      #
      # 그래서 **실패한 실행의 raw 는 명시적으로 주워와야 한다** — 자동으로 안 된다:
      #   normalize-<step> --run-id <새 id> --input-run-id <실패한 run_id>   # 그 런만
      #   normalize-<step> --run-id <새 id>                                  # 전체 백필
      #
      # 이 절차의 트리거는 NotifyFailure 알림이고, 제목에 실패한 run_id 가 박혀 나온다.
      # ⚠️ **그래서 `pipeline_alarm_email` 이 반드시 설정돼 있어야 한다** — null 이면 구독
      # 리소스가 count=0 으로 안 생겨 알림이 구독자 없는 토픽으로 사라지고, 그러면 미승격
      # run 을 **아무도 모른다**(ALPHA-389 착수 시 dev 토픽 구독자가 실제로 0이었다).
      # 수집 창이 '오늘'인 소스(BigKinds·DART·KRX ETF)는 다음 런이 그 날짜를 재수집하지도
      # 않으므로, 알림을 놓치면 그 날 데이터는 raw 에만 남고 canonical 에 영영 없다.
      #
      # 자동 구제가 나아 보이지만, 옛 구조는 "언젠가 주워진다"라 **아무도 그게 언제였는지
      # 몰랐다**. 명시적 재처리는 누가 언제 무엇을 승격했는지가 남는다 — 단 그 대가로 알림이
      # 살아 있어야 한다는 조건이 붙는다.
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
          Next = "DeriveParallel"
        }]
        Default = "NotifyFailure"
      }
      # 정제 전량 성공일 때만 파생으로 넘어간다 — 실행 내 순서 제어다.
      #
      # ⚠️ 위 raw→normalize 게이트와 **성격이 다르다**(ALPHA-389 이후). 거기는 정제가 이제
      # run 스코프라 실패 런의 raw 가 자동으로 안 주워진다(영구 격리 — 사람이 재처리). 반면
      # 두 파생 잡은 **canonical 을 full-scan** 하므로 여기 걸린 건 자동 회복된다: 이번 실행이
      # 멈춰도 다음 성공 실행이 밀린 canonical 을 함께 소비한다. tag-news 는 미태깅 기사만
      # 고르고(태거·온톨로지 버전 + 입력 지문으로 판정) load-instruments 는 자연키 멱등이라
      # 재실행이 중복을 만들지 않는다. 즉 derive 는 아직 옛 모델이고, 그래서 안전하다.
      DeriveParallel = {
        Type       = "Parallel"
        Branches   = local.derive_branches
        ResultPath = "$.derive_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NotifyFailure" }]
        Next       = "DeriveCheckResults"
      }
      DeriveCheckResults = {
        Type = "Choice"
        Choices = [{
          And  = local.derive_success_checks
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
          TopicArn = aws_sns_topic.alarms.arn
          # run_id 를 제목에 박는다 — 정제가 run 스코프가 된 뒤로(ALPHA-389) 실패 런의 raw 는
          # **사람이 그 run_id 로 명시 재처리**해야 승격된다. 제목이 전부 "pipeline FAILED" 로
          # 같으면 메일함에서 어느 런을 주워와야 하는지 알 수 없어 절차가 시작되지 않는다.
          # 본문(전체 상태 JSON)에 어느 브랜치가 실패했는지가 들어 있다.
          "Subject.$" = "States.Format('[${var.name}] FAILED — run {}', $.run_id)"
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

# 상태머신 정의 안의 NotifyFailure 는 **정의가 살아 있을 때만** 통보한다. 최상위
# TimeoutSeconds 로 실행이 죽으면 States.Timeout 이 실행 자체를 끝내므로 어떤 Catch 도
# 타지 않고 — 즉 SNS 로 아무것도 안 나간다. derive 페이즈가 들어오면서 이 경로가 처음으로
# 실질 도달 가능해졌다(LLM 호출은 소요시간 상한이 없다. tag_news_limit 이 1차 방어).
# 알람은 정의 밖에서 도는 유일한 통보 수단이라 그 구멍을 정확히 메운다.
# ExecutionsFailed 는 안 건다 — NotifyFailure 가 이미 덮고, 겹치면 같은 실패에 두 통이 온다.
resource "aws_cloudwatch_metric_alarm" "execution_timed_out" {
  alarm_name        = "${var.name}-execution-timed-out"
  alarm_description = "SFN 실행이 TimeoutSeconds 초과로 죽었다 — 정의 안의 NotifyFailure 가 못 잡는 경로다."
  namespace         = "AWS/States"
  metric_name       = "ExecutionsTimedOut"
  dimensions        = { StateMachineArn = aws_sfn_state_machine.this.arn }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
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
