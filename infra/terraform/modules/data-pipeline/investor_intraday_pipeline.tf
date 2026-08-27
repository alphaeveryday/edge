# 장중 수급 레인 별도 상태머신 (ALPHA-769)
#
# 왜 별개 SFN 인가: 장중 투자자 추정은 **하루 5번만 갱신되고 소급 조회가 없다**(KIS
# HHPTJ04160200 에 날짜 파라미터가 없다). EOD 레인은 마감 후 1회 스냅샷이라 자연 주기가 다르고,
# 이 데이터셋은 그 주기로는 존재 자체가 사라진다 — 15:40 에 한 번 물으면 그날 슬롯 5개 중
# 마지막 것만 남는 게 아니라, 애초에 다른 데이터셋(가집계 추정)이다.
#
# **공시·뉴스 레인과 성격이 다르다: 이건 컷오버가 아니라 신설이다.**
# 저 둘은 시장 SFN 이 돌던 스텝의 소유 레인을 옮긴 것이라 "두 레인이 같은 스텝을 동시에 소유하는"
# 겹침 창을 막으려고 DISABLED 로 세운 뒤 별도 apply 로 켰다. 이 레인의 3스텝은 ALPHA-767·768 이
# 층만 만들고 **배선을 한 번도 붙이지 않은** 것들이라(시장 SFN 에 들어간 적이 없다) 그 겹침 창이
# 존재하지 않는다. 그래서 스케줄을 처음부터 ENABLED 로 세운다.
#
# 배포 순서(이미지 CD ↔ terraform-apply 는 같은 push 에서 독립 실행이라 순서 보장이 없다):
# terraform 이 먼저 뜨면 Planner 가 아직 `investor-intraday` 레인을 모르는 이미지 위에서 돌아
# **fail-loud 로 죽는다**(entry.py `_LANE_STATE_MACHINE_ARN_ENV` 미등록 = SystemExit). 시끄럽지만
# 원장을 오염시키지 않고, 그 사이 놓친 슬롯의 데이터는 **다음 슬롯이 회수한다** — 이 API 의 응답이
# 누적이기 때문이다(2026-08-06 dev 실측: 한 콜이 그날 슬롯 1~5 전부를 준다). 반대 순서(이미지가
# 먼저)는 스케줄이 아직 없어 아무 일도 일어나지 않는다.

locals {
  # 장중 수급 레인 잡 = 기존 잡 리스트의 부분집합 재사용 — command_expr·taskdef_key 드리프트
  # 방지(DRY, 뉴스·공시 레인과 같은 방식). statemachine.tf 의 branches_by_phase 빌더가 이 셋으로
  # 브랜치를 만들고, 새 state **정의**는 하나도 늘지 않는다.
  investor_intraday_raw_jobs       = [for j in local.raw_ingest_jobs : j if j.state == "CollectKisInvestorEstimate"]
  investor_intraday_normalize_jobs = [for j in local.normalize_jobs : j if j.state == "NormalizeInvestorEstimate"]
  investor_intraday_feature_jobs   = [for j in local.feature_jobs : j if j.state == "LoadInvestorIntraday"]

  investor_intraday_raw_branches       = local.branches_by_phase["investor_intraday_raw"]
  investor_intraday_normalize_branches = local.branches_by_phase["investor_intraday_normalize"]
  investor_intraday_feature_branches   = local.branches_by_phase["investor_intraday_feature"]

  # 페이즈별 전량성공 게이트 — 시장·뉴스·공시 SFN 과 같은 패턴(인덱스별 정적 생성).
  investor_intraday_raw_success_checks = [
    for index, _ in local.investor_intraday_raw_jobs : {
      Variable     = "$.branch_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]
  investor_intraday_normalize_success_checks = [
    for index, _ in local.investor_intraday_normalize_jobs : {
      Variable     = "$.normalize_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]
  # 정제 exit 2는 성공 winner를 canonical+manifest에 commit한 입력 부분 실패다. exit 1이나
  # ECS 자체 실패와 달리 manifest consumer를 계속할 수 있다.
  investor_intraday_normalize_continue_checks = [
    for index, _ in local.investor_intraday_normalize_jobs : {
      Or = [
        {
          Variable     = "$.normalize_results[${index}].status"
          StringEquals = "succeeded"
        },
        {
          And = [
            {
              Variable  = "$.normalize_results[${index}].exit_code"
              IsPresent = true
            },
            {
              Variable      = "$.normalize_results[${index}].exit_code"
              NumericEquals = 2
            },
          ]
        },
      ]
    }
  ]
  investor_intraday_feature_success_checks = [
    for index, _ in local.investor_intraday_feature_jobs : {
      Variable     = "$.feature_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

  # 장중 수급 SFN 정의. 공시 SFN 과 같은 형태다:
  #   RawIngest(장중 추정 수집) → Normalize(슬롯 병합) → Feature[LoadInvestorIntraday]
  # 직렬 꼬리가 없다 — 뉴스의 LoadAssertions·AssembleEvents 는 threading 축이고 이 레인은
  # 수치 원장 경로라 그 체인을 타지 않는다.
  #
  # raw 부분 실패 규약은 세 선례와 같다(ADR-0030): 즉시 알리고 계속 가되, 마지막에 raw 를 다시
  # 보고 부분 실패였으면 FAILED 로 마감한다(한 실패에 두 통 금지).
  #
  # ⚠️ **이 레인에서는 그 규약이 자주 발화한다 — 알고 택했다.** 수집이 유니버스 325종목을
  # 종목당 1콜로 도는데, 심볼 하나만 실패해도 스텝이 `status=partial`·exit 1 이라(격리≠은폐,
  # ingest_raw_investor) 브랜치가 failed → 런이 FAILED 로 마감되고 SNS 가 나간다.
  # 2026-08-06 dev 실측에서 650콜 중 1건이 일시적 네트워크 오류(`Remote end closed
  # connection`)로 빠졌다 — 콜당 0.15% 면 슬롯당(325콜) 약 39%, 하루 5슬롯이면 **~2회**다.
  # 표본이 2런이라 참값 폭은 넓지만 자릿수는 이렇다.
  #
  # 그런데도 규약을 그대로 두는 이유: ① 같은 스텝을 EOD 레인(CollectKisInvestor)이 이미 같은
  # 유니버스로 쓰고 있어 새 종류의 동작이 아니다 — 이 레인은 빈도를 5배로 곱할 뿐이다
  # ② 허용 임계값을 두는 것은 **레인 전체 정책**이라 여기서 혼자 정하면 규약이 갈린다
  # ③ 데이터는 멀쩡하다 — 응답이 누적이라 다음 슬롯이 회수하고, 손실은 원장의
  # `failed_records` 로 남는다(FAILED 는 알람 축이지 데이터 축이 아니다).
  # 🔴 남는 대가는 **알람 피로**다: 정상 상태가 자주 빨강이면 진짜 고장이 그 사이에 묻힌다.
  # ponytail: 허용 임계(고립 실패 N건 이하는 exit 0) 도입은 **ALPHA-798** — 공유 스텝을
  # 고치는 일이라 EOD 레인과 함께 정해야 한다.
  #
  # ⚠️ 휴장일·개장 전에는 raw 가 **실패가 아니라 skip(exit 0)** 이다(ALPHA-769 가 어댑터를
  # `skip_reason` 규약으로 옮겼다 — iNAV 선례). 평일 cron 이라 공휴일에도 이 SFN 이 도는데,
  # 종전처럼 raise 로 죽으면 **공휴일마다 런이 FAILED** 라 예정된 무산출과 진짜 고장이 구분되지
  # 않는다. 그날의 정제·적재는 빈 입력을 정상 성공으로 처리해 SFN 이 Succeeded 로 끝난다.
  investor_intraday_sfn_definition = jsonencode({
    StartAt        = "InvestorIntradayRawIngestParallel"
    TimeoutSeconds = var.investor_intraday_state_machine_timeout_seconds
    States = {
      InvestorIntradayRawIngestParallel = {
        Type       = "Parallel"
        Branches   = local.investor_intraday_raw_branches
        ResultPath = "$.branch_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "InvestorIntradayNotifyFailure" }]
        Next       = "InvestorIntradayRawIngestCheckResults"
      }
      InvestorIntradayRawIngestCheckResults = {
        Type    = "Choice"
        Choices = [{ And = local.investor_intraday_raw_success_checks, Next = "InvestorIntradayNormalizeParallel" }]
        Default = "InvestorIntradayNotifyRawPartial"
      }
      # 세 선례와 같은 이유로 여기서 즉시 알린다 — 뒤에서 최상위 TimeoutSeconds 로 실행이 죽으면
      # States.Timeout 이 Catch 를 타지 않아 run_id 가 박힌 알림이 영영 안 나간다.
      # 통보 뒤 계속 진행($ 보존), 최종 FAILED 는 끝의 RawPartialCheck 가 마감한다.
      InvestorIntradayNotifyRawPartial = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "InvestorIntradayNormalizeParallel"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          "Subject.$" = "States.Format('[${var.name}-investor-intraday] raw 부분 실패 — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      InvestorIntradayNormalizeParallel = {
        Type       = "Parallel"
        Branches   = local.investor_intraday_normalize_branches
        ResultPath = "$.normalize_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "InvestorIntradayNotifyFailure" }]
        Next       = "InvestorIntradayNormalizeCheckResults"
      }
      InvestorIntradayNormalizeCheckResults = {
        Type = "Choice"
        Choices = [
          { And = local.investor_intraday_normalize_success_checks, Next = "InvestorIntradayFeatureParallel" },
          { And = local.investor_intraday_normalize_continue_checks, Next = "InvestorIntradayNotifyNormalizePartial" },
        ]
        Default = "InvestorIntradayNotifyFailure"
      }
      InvestorIntradayNotifyNormalizePartial = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "InvestorIntradayFeatureParallel"
        # 알림 장애가 이미 commit된 winner의 적재를 막지 않는다. 오류는 실행 입력에 보존되고,
        # normalize exit 2 때문에 마지막 상태는 어차피 Failed로 닫힌다.
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.normalize_partial_notification_error"
          Next        = "InvestorIntradayFeatureParallel"
        }]
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          "Subject.$" = "States.Format('[${var.name}-investor-intraday] normalize 부분 실패 — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      InvestorIntradayFeatureParallel = {
        Type       = "Parallel"
        Branches   = local.investor_intraday_feature_branches
        ResultPath = "$.feature_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "InvestorIntradayNotifyFailure" }]
        Next       = "InvestorIntradayFeatureCheckResults"
      }
      InvestorIntradayFeatureCheckResults = {
        Type    = "Choice"
        Choices = [{ And = local.investor_intraday_feature_success_checks, Next = "InvestorIntradayRawPartialCheck" }]
        Default = "InvestorIntradayNotifyFailure"
      }
      # raw/normalize 부분 실패 런의 마감 판정 — 성공 winner 적재 뒤에도 전체 런은 FAILED다.
      InvestorIntradayRawPartialCheck = {
        Type = "Choice"
        Choices = [{
          And = concat(
            local.investor_intraday_raw_success_checks,
            local.investor_intraday_normalize_success_checks,
          )
          Next = "InvestorIntradaySucceeded"
        }]
        Default = "InvestorIntradayFailed"
      }
      InvestorIntradaySucceeded = { Type = "Succeed" }
      InvestorIntradayNotifyFailure = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "InvestorIntradayFailed"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          "Subject.$" = "States.Format('[${var.name}-investor-intraday] FAILED — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      InvestorIntradayFailed = { Type = "Fail", Cause = "investor intraday pipeline failed" }
    }
  })
}

# 세 선례와 같은 sfn 역할을 재사용한다 — 이 SFN 은 같은 task-def 집합의 부분집합
# (kis·bigkinds·rds)만 runTask 하므로 권한이 이미 충분하다.
resource "aws_sfn_state_machine" "investor_intraday" {
  name       = "${var.name}-investor-intraday"
  role_arn   = aws_iam_role.sfn.arn
  definition = local.investor_intraday_sfn_definition
}

# 세 선례와 같은 이유의 타임아웃 알람 — 최상위 TimeoutSeconds 로 실행이 죽으면 States.Timeout 이
# Catch 를 안 타 정의 안 InvestorIntradayNotifyFailure 가 못 잡는다.
resource "aws_cloudwatch_metric_alarm" "investor_intraday_execution_timed_out" {
  alarm_name        = "${var.name}-investor-intraday-execution-timed-out"
  alarm_description = "장중 수급 SFN 실행이 TimeoutSeconds 초과로 죽었다 — 정의 안 InvestorIntradayNotifyFailure 가 못 잡는 경로다."
  namespace         = "AWS/States"
  metric_name       = "ExecutionsTimedOut"
  dimensions        = { StateMachineArn = aws_sfn_state_machine.investor_intraday.arn }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
}

# 장중 수급 스케줄(KST) — 슬롯 목록과 그 배치 근거는 `variables.tf` 의
# `investor_intraday_schedule_expressions` 주석이 SSOT 다(여기서 반복하지 않는다).
resource "aws_scheduler_schedule" "investor_intraday" {
  for_each = var.investor_intraday_schedule_expressions

  name                         = "${var.name}-investor-intraday-${each.key}"
  state                        = var.investor_intraday_schedule_state
  schedule_expression          = each.value
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window { mode = "OFF" }

  # 운영 원장(ALPHA-721): SFN 직접 시작이 아니라 **Planner 경유**다(daily·news·공시와 동형).
  # Planner 가 pipeline_run + expected_task 를 먼저 커밋하고 멱등 execution_name(run_key 파생)으로
  # StartExecution 하므로, EventBridge 의 드문 중복 재전달이 같은 run_key 로 수렴해 run 1개다.
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler.arn
    # ⚠️ 플레이스홀더는 jsonencode **바깥**에서 주입한다(ALPHA-593) — jsonencode 가 `<`/`>` 를
    # 이스케이프해 EventBridge 가 컨텍스트 속성 패턴을 인식하지 못한다.
    input = replace(
      jsonencode({
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
            Command = ["plan-run"]
            Environment = [
              { Name = "OPS_SCHEDULED_TIME", Value = "SCHEDULED_TIME_TOKEN" },
              { Name = "OPS_PIPELINE_TYPE", Value = "investor-intraday" },
            ]
          }]
        }
      }),
      "SCHEDULED_TIME_TOKEN", "<aws.scheduler.scheduled-time>",
    )

    # 재시도 0 — 뉴스·공시 레인과 같은 형태이고, **근거는 이 레인에서 실측으로 확인됐다**
    # (2026-08-06 dev). Planner 멱등은 같은 슬롯의 중복만 막으므로 재시도 창이 슬롯 간격(최소
    # 1800s)을 파고들면 지연 시작한 실행이 다음 슬롯 실행과 겹친다.
    #
    # 포기해도 잃는 것이 거의 없다는 근거가 이 소스에선 특히 강하다: **응답이 누적이다.**
    # 14:51 한 콜이 그날 슬롯 1~5 전부를 돌려줬고(325종목·1,574행), 한 슬롯에서 종목이 빠져도
    # (2차 런에서 `050890` 이 네트워크 오류로 결손) canonical 병합이 앞 슬롯 값을 보존해
    # 적재가 `already` 로 수렴했다. 즉 슬롯 하나를 통째로 놓쳐도 **다음 슬롯이 그날 전부를
    # 다시 받아 회수한다**. 제출 실패로 슬롯이 누락되면 PLANNER_MISSING 이 열려 드러난다.
    #
    # ⚠️ **회수 논리는 마지막 슬롯(`s1435`)에는 성립하지 않는다** — 그 뒤로 그날 슬롯이 없고
    # 이 API 엔 날짜 파라미터가 없어 소급이 불가하다(run.py 가 `--from/--to` 를 거부한다).
    # 14:35 제출이 튕기면 그날 4·5번 갱신(13:20·14:30)은 자동으로는 영구 결손이다. 그래도
    # retry 를 안 두는 이유는 같다(겹침이 더 나쁘다): 대신 DLQ + PLANNER_MISSING 으로 드러나고,
    # 벤더 값은 그날 장중 내내 남아 있으므로 **수동 plan-run 으로 회수할 수 있다**. 즉 자동
    # 회수가 없는 것이지 복구 불가가 아니다.
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 0
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dlq.arn }
  }
}
