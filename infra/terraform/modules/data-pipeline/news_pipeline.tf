# 뉴스(지식) 레인 별도 상태머신 (ALPHA-553, PR1)
#
# 왜 별개 SFN 인가: 시장/EOD 레인과 뉴스 레인은 자연 주기가 다르다(시장=장마감 EOD 스냅샷,
# 뉴스=하루 종일 유입). 한 SFN 에 묶으면 FeatureParallel 배리어가 두 레인 실패를 전파하고,
# 뉴스를 자체 주기로 돌릴 수 없다. 뉴스 레인이 시장 레인과 공유하는 건 instrument 마스터
# 하나(읽기 전용)뿐이라 깔끔히 분리된다 — 마스터는 시장 SFN 의 LoadInstruments 가 소유하고
# (단일 writer, ADR-0005), 뉴스 SFN 은 LoadAssertions·AssembleEvents 에서 읽기만 한다.
# 설계 SSOT: scratchpad/0039-news-pipeline-split.md (임시구조라 ADR 로는 안 만든다).
#
# **PR1 범위: 이 SFN 을 병행 세워 검증한다 — 기존 edge-dev-data-pipeline 은 미변경.** 뉴스 스텝은
# 멱등이라(태깅=버전+지문 skip, document/assertion=자연키, assemble=결정적 ID) 중복 upsert 는
# 흡수한다. 기존 SFN 에서 뉴스 스텝 제거는 PR2 다.
#
# ⚠️ **news_schedule_state 를 ENABLED 로 바꾸는 건 PR2 머지 후에만 한다.** 멱등이 중복 upsert 는
# 흡수하지만 **threading 은 prior-count·lifecycle_stage 를 쓰기 전에 읽어**, 시장 SFN 과 뉴스 SFN 이
# 같은 event 를 동시에 쓰면 두 실행이 같은 prior 상태를 보고 잘못된 novelty/snapshot 을 정상 exit 로
# 커밋한다(edge-review 지적). 그래서 PR1 은 DISABLED 로 두고 **수동 검증도 15:40 시장 런과 겹치지
# 않게** 띄운다. PR2 가 시장 SFN 에서 뉴스 스텝을 빼야 겹침 자체가 사라져 컷오버가 안전해진다
# (시장 스케줄이 DISABLED→ENABLED 컷오버를 ALPHA-489 로 한 것과 같은 순서).
#
# 운영 원장(ALPHA-591) 편입: 뉴스 스케줄은 daily 와 같이 **Planner(plan-run) 경유**다 —
# OPS_PIPELINE_TYPE=news 로 자체 레인(카탈로그 6작업·하루 2슬롯 기대, ALPHA-893)을 계획하고 뉴스 SFN 을
# 멱등 시작한다. Reconciler 가 "런 자체가 안 떴다"(PLANNER_MISSING)까지 탐지한다.

locals {
  # 뉴스 레인 잡 = 기존 잡 리스트의 부분집합 재사용 — command_expr·taskdef_key 드리프트 방지(DRY).
  # statemachine.tf 의 branches_by_phase 빌더가 이 셋으로 news_* 브랜치를 만든다.
  news_raw_jobs       = [for j in local.raw_ingest_jobs : j if contains(["CollectFmpNews", "CollectBigKindsNews"], j.state)]
  news_normalize_jobs = [for j in local.normalize_jobs : j if j.state == "NormalizeNews"]
  news_feature_jobs   = [for j in local.feature_jobs : j if contains(["TagNews", "LoadDocuments"], j.state)]

  news_raw_branches       = local.branches_by_phase["news_raw"]
  news_normalize_branches = local.branches_by_phase["news_normalize"]
  news_feature_branches   = local.branches_by_phase["news_feature"]

  # 페이즈별 전량성공 게이트 — 시장 SFN 과 같은 패턴(인덱스별 정적 생성, 결과 배열의 status 검사).
  news_raw_success_checks = [
    for index, _ in local.news_raw_jobs : {
      Variable     = "$.branch_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]
  news_normalize_success_checks = [
    for index, _ in local.news_normalize_jobs : {
      Variable     = "$.normalize_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]
  news_feature_success_checks = [
    for index, _ in local.news_feature_jobs : {
      Variable     = "$.feature_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

  # 뉴스 SFN 정의. 시장 SFN 을 트림한 형태다:
  #   NewsRawIngest(뉴스 수집) → Normalize(뉴스) → Feature[TagNews|LoadDocuments] → LoadAssertions → AssembleEvents
  # LoadInstruments·EnrichCorpCode·가격 feature·analyze 는 시장 SFN 소관이라 없다. raw 부분 실패는
  # 시장 SFN 과 같은 규약(즉시 알림 + 계속, 마지막에 FAILED 마감)을 따른다 — 정제가 빈 입력을 정상
  # 성공 처리하므로(있는 만큼 처리) BigKinds 가 죽어도 FMP 뉴스만 정제하고 진행한다.
  news_sfn_definition = jsonencode({
    StartAt        = "NewsRawIngestParallel"
    TimeoutSeconds = var.news_state_machine_timeout_seconds
    States = {
      NewsRawIngestParallel = {
        Type       = "Parallel"
        Branches   = local.news_raw_branches
        ResultPath = "$.branch_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NewsNotifyFailure" }]
        Next       = "NewsRawIngestCheckResults"
      }
      NewsRawIngestCheckResults = {
        Type = "Choice"
        Choices = [{
          And  = local.news_raw_success_checks
          Next = "NewsNormalizeParallel"
        }]
        Default = "NewsNotifyRawPartial"
      }
      # 시장 SFN 과 같은 이유로 여기서 즉시 알린다(뒤에 tag-news LLM = 무제한 소요라 최상위
      # 타임아웃으로 죽으면 Catch 를 못 탄다). 통보 뒤 계속 진행($ 보존), 최종 FAILED 는 끝의
      # NewsRawPartialCheck 가 마감한다(한 실패에 두 통 금지).
      NewsNotifyRawPartial = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "NewsNormalizeParallel"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          "Subject.$" = "States.Format('[${var.name}-news] raw 부분 실패 — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      NewsNormalizeParallel = {
        Type       = "Parallel"
        Branches   = local.news_normalize_branches
        ResultPath = "$.normalize_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NewsNotifyFailure" }]
        Next       = "NewsNormalizeCheckResults"
      }
      NewsNormalizeCheckResults = {
        Type    = "Choice"
        Choices = [{ And = local.news_normalize_success_checks, Next = "NewsFeatureParallel" }]
        Default = "NewsNotifyFailure"
      }
      NewsFeatureParallel = {
        Type       = "Parallel"
        Branches   = local.news_feature_branches
        ResultPath = "$.feature_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NewsNotifyFailure" }]
        Next       = "NewsFeatureCheckResults"
      }
      NewsFeatureCheckResults = {
        Type    = "Choice"
        Choices = [{ And = local.news_feature_success_checks, Next = "NewsLoadAssertions" }]
        Default = "NewsNotifyFailure"
      }
      # assertion 적재 — 시장 SFN 과 같은 이유로 feature(LoadDocuments 포함) 뒤 직렬이다(document
      # FK 의존). instrument 마스터를 읽어 엔티티 해소하므로 시장 SFN 의 LoadInstruments 가 선행
      # 적재해 둔 마스터가 전제다(교차 SFN 읽기 전용 공유).
      NewsLoadAssertions = merge(local.ecs_run_task_base, {
        Type  = "Task"
        Next  = "NewsLoadAssertionsCheckExitCode"
        Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NewsNotifyFailure" }]
        Parameters = merge(local.ecs_run_task_base.Parameters, {
          TaskDefinition = aws_ecs_task_definition.this["rds"].arn
          Overrides = {
            ContainerOverrides = [{
              Name        = local.container_name
              "Command.$" = "States.Array('load-assertions', '--run-id', $.run_id)"
              # 운영 원장(ALPHA-591): 직렬 state 는 페이즈 빌더 밖이라 별도 주입 — 없으면 이
              # attempt 의 sfn_state_name·실행 ARN 이 NULL 로 남아 attempt↔SFN 계보가 끊긴다.
              Environment = [
                { Name = "OPS_SFN_STATE_NAME", Value = "NewsLoadAssertions" },
                { Name = "OPS_SFN_EXECUTION_ARN", "Value.$" = "$$.Execution.Id" },
              ]
            }]
          }
        })
      })
      NewsLoadAssertionsCheckExitCode = {
        Type    = "Choice"
        Choices = [{ Variable = "$.ecs.Containers[0].ExitCode", NumericEquals = 0, Next = "NewsAssembleEvents" }]
        Default = "NewsNotifyFailure"
      }
      # 이벤트 조립 — LoadAssertions 뒤 직렬(document/assertion 자연키 브리지 수렴). 시장 SFN 의
      # analyze 가 이 스텝이 만든 event 를 기회적으로 소비한다(배리어 없음 — 준실시간 부분입력 계약).
      NewsAssembleEvents = merge(local.ecs_run_task_base, {
        Type  = "Task"
        Next  = "NewsAssembleEventsCheckExitCode"
        Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NewsNotifyFailure" }]
        Parameters = merge(local.ecs_run_task_base.Parameters, {
          TaskDefinition = aws_ecs_task_definition.this["events"].arn
          Overrides = {
            ContainerOverrides = [{
              Name = local.container_name
              # --window-days: 창을 [오늘−N, 오늘]로 겹친다(ALPHA-592). 23:50 슬롯은 체인
              # 소요(9~14분)가 자정을 넘겨 assemble 이 다음 날짜로 도는 게 기본 경로라
              # (2026-07-28 00:03 read=0 실측), 겹침 없이는 늦저녁 기사가 영영 조립되지 않는다.
              "Command.$" = "States.Array('assemble-events', '--run-id', $.run_id, '--window-days', '${var.assemble_window_days}')"
              # NewsLoadAssertions 와 같은 이유의 원장 계보 주입(ALPHA-591).
              Environment = [
                { Name = "OPS_SFN_STATE_NAME", Value = "NewsAssembleEvents" },
                { Name = "OPS_SFN_EXECUTION_ARN", "Value.$" = "$$.Execution.Id" },
              ]
            }]
          }
        })
      })
      NewsAssembleEventsCheckExitCode = {
        Type    = "Choice"
        Choices = [{ Variable = "$.ecs.Containers[0].ExitCode", NumericEquals = 0, Next = "NewsRawPartialCheck" }]
        Default = "NewsNotifyFailure"
      }
      # raw 부분 실패 런의 마감 판정(시장 SFN RawPartialCheck 와 동형) — 다운스트림을 끝까지 돌린
      # 뒤 raw 를 다시 보고 부분 실패였으면 FAILED 로 끝낸다. 알림은 이미 NewsNotifyRawPartial 이
      # 쐈으므로 여기선 SNS 를 안 탄다.
      NewsRawPartialCheck = {
        Type    = "Choice"
        Choices = [{ And = local.news_raw_success_checks, Next = "NewsSucceeded" }]
        Default = "NewsFailed"
      }
      NewsSucceeded = { Type = "Succeed" }
      NewsNotifyFailure = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "NewsFailed"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          "Subject.$" = "States.Format('[${var.name}-news] FAILED — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      NewsFailed = { Type = "Fail", Cause = "news pipeline failed" }
    }
  })
}

# 시장 SFN 과 같은 sfn 역할을 재사용한다 — 뉴스 SFN 은 같은 task-def 집합의 부분집합
# (fmp·bigkinds·deepseek·rds·events)만 runTask 하므로 권한이 이미 충분하다.
resource "aws_sfn_state_machine" "news" {
  name       = "${var.name}-news"
  role_arn   = aws_iam_role.sfn.arn
  definition = local.news_sfn_definition
}

# 시장 SFN 과 같은 이유의 타임아웃 알람 — tag-news LLM 이 무제한 소요라 최상위 TimeoutSeconds 로
# 실행이 죽으면 정의 안 NewsNotifyFailure 가 못 잡는다(States.Timeout 은 Catch 를 안 탄다).
resource "aws_cloudwatch_metric_alarm" "news_execution_timed_out" {
  alarm_name        = "${var.name}-news-execution-timed-out"
  alarm_description = "뉴스 SFN 실행이 TimeoutSeconds 초과로 죽었다 — 정의 안 NewsNotifyFailure 가 못 잡는 경로다."
  namespace         = "AWS/States"
  metric_name       = "ExecutionsTimedOut"
  dimensions        = { StateMachineArn = aws_sfn_state_machine.news.arn }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
}

# 뉴스 스케줄 2개(KST): premarket 08:10(밤새 밀린 것을 장중 수집 시작 09:00 전에 털어 분 격자를
# 1페이지로 유지) + day-close 23:50(장외·야간 뉴스로 하루치 완결, assemble 단일일 창의 오버나잇
# 갭 보전). 슬롯 정의·근거의 SSOT 는 `news_schedule_expressions` 주석이다.
#
# 옛 오후 슬롯(15:00·15:30)은 **15:40 EOD 런의 analyze 에 그날 뉴스를 공급**하려고 있었다.
# EOD 가격 설명을 하지 않기로 하면서(2026-08-09) 그 소비자가 사라져 ALPHA-893 이 내렸다 —
# 장중 신선도는 분 레인(`minute/event_assembly.py`, ALPHA-727)이 갖는다.
#
# ⚠️ 알려진 한계(임시구조 수용 — edge-review 지적, 컷오버 전 재검토):
#   ① 23:50 런은 체인이 10분+ 걸려 자정을 넘기면 assemble 이 '다음 날'만 처리해 그날 늦은 뉴스가
#      조립에서 밀린다 — today-implicit 스텝 + assemble 단일일 창의 성질이고, assemble 창 재설계는
#      정준영 질의 대기다([[assemble-window-overnight-gap]]).
#   ② **해소됨(ALPHA-874)** — 크론을 주 7일로 넓혔다. 실제 구멍은 주말 전체가 아니라 **토요일
#      하나**였다: 수집 창이 `[어제, 오늘]` 2일이라 일요일은 월요일 런이 덮었고, 토요일만 토·일
#      모두 런이 없었다(2026-08-01 raw 파티션 0 실증).
#   ③ instrument 마스터가 비었거나(최초 배포·시장 SFN 마스터 실패) 갱신 중이면 assemble 이 그 ticker
#      뉴스를 유니버스 밖으로 드롭하고 exit 0 — 마스터는 시장 SFN 단일 writer 라 steady-state 엔
#      채워져 있다(문서화된 교차 SFN 읽기전용 계약, ADR-0005).
resource "aws_scheduler_schedule" "news" {
  for_each = var.news_schedule_expressions

  name                         = "${var.name}-news-${each.key}"
  state                        = var.news_schedule_state
  schedule_expression          = each.value
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window { mode = "OFF" }

  # 운영 원장(ALPHA-591): 뉴스 스케줄도 SFN 직접 시작이 아니라 **Planner 경유**다(daily 와 동형,
  # statemachine.tf 의 aws_scheduler_schedule.daily 주석 참조). Planner 가 pipeline_run +
  # expected_task 를 먼저 커밋하고 멱등 execution_name(run_key 파생 — 콜론 없는 charset)으로
  # StartExecution 하므로, EventBridge 의 드문 중복 재전달이 같은 run_key 로 수렴해 run 1개다.
  # run_id 도 scheduled-time 리터럴이 아니라 pipeline_run_id 라 ALPHA-593 의
  # jsonencode 이스케이프 우회는 OPS_SCHEDULED_TIME env 한 곳만 남는다.
  # (재시도는 여전히 0 이다 — 아래 retry_policy 주석. ⚠️ 이유였던 슬롯 간 비중첩 불변식은
  #  ALPHA-893 에서 사실상 무의미해졌다: 최소 간격이 30분에서 8시간 20분으로 넓어졌다.
  #  "불가능"은 아니다 — RunTask 제출~컨테이너 기동은 어디에도 안 묶여 있어 앞 런이 8시간
  #  넘게 밀리면 원리상 겹칠 수 있다. 그 여지가 실무상 사라졌다는 뜻으로 읽어라.)
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler.arn
    # ⚠️ 플레이스홀더는 jsonencode **바깥**에서 주입한다(ALPHA-593) — jsonencode 가 `<`/`>` 를
    # </> 로 이스케이프해 EventBridge 가 컨텍스트 속성 패턴을 인식하지 못한다.
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
              { Name = "OPS_PIPELINE_TYPE", Value = "news" },
            ]
          }]
        }
      }),
      "SCHEDULED_TIME_TOKEN", "<aws.scheduler.scheduled-time>",
    )

    # 재시도 0 유지 — 사유가 두 번 바뀌었다(ALPHA-591 edge-review → ALPHA-893). 종전엔 멱등
    # Name 불가(콜론)가 이유였고 그건 Planner 경유(run_key 파생 execution_name)로 해소됐다.
    # 그 다음 이유는 **슬롯 간격 30분(15:00·15:30)을 재시도 창이 파고들면** 지연 시작한 실행이
    # 다음 실행과 겹쳐 서로 다른 run 의 AssembleEvents 가 동시에 돌고 threading 의 prior-count·
    # lifecycle_stage read-before-write 레이스가 되살아난다는 것이었다. ⚠️ **그 두 슬롯이
    # 사라져 지금 최소 간격은 8시간 20분(23:50→08:10)이라 겹침은 구조적으로 불가능하다** —
    # 즉 이 `retry_policy` 0 은 더는 레이스 방지책이 아니고, 아래 "잃는 게 없다"만 남는다.
    # 재시도를 포기해도 잃는 게 거의 없다: EventBridge 드문 중복 재전달은 run_key 멱등이 흡수,
    # 제출 실패로 슬롯이 누락되면 이번 티켓의 PLANNER_MISSING 탐지가 30분 뒤 이슈를 열고
    # 데이터는 다음 슬롯 창 겹침이 자가 회복한다(daily 15:40 은 하루 1회+후행 슬롯 없음이라
    # 24h·185회 재시도로 전달을 보장하는 것과 대비되는 지점).
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 0
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dlq.arn }
  }
}
