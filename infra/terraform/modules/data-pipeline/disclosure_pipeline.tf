# 공시 레인 별도 상태머신 (ALPHA-722)
#
# 왜 별개 SFN 인가: 공시는 하루 종일 유입되는데 시장 레인은 EOD 스냅샷이라 자연 주기가 다르다
# (뉴스 레인을 가른 것과 같은 이유). ALPHA-714 로 목록 질의가 종목 순회에서 창 전체
# 페이지네이션으로 바뀌며 목록 스캔이 375초(외삽) → 6.3초(실측)가 됐고, 호출 수가 유니버스와
# 무관해져 **잦은 실행이 처음으로 성립한다**. 그전엔 어떤 주기도 불가능했다.
#
# **이 PR 범위: 이 SFN 을 병행 세운다 — 기존 시장 SFN 은 미변경이고 스케줄은 DISABLED 다.**
# 즉 이 apply 는 도는 것을 아무것도 바꾸지 않는다. 컷오버(시장 SFN 에서 공시 4스텝 제거 +
# 스케줄 ENABLED)는 다음 티켓이고, 그때까지 공시는 15:40 일일 런으로 계속 돈다.
# 뉴스 레인이 밟은 순서와 같다(ALPHA-553 PR1 → PR2).
#
# ⚠️ **컷오버가 필요한 이유는 성능이 아니라 원장 정체성이다.** 작업 정체성의 정본은
# `catalog.by_cli(step, source)` 인데(ops/entry.py `task_key_for`), 두 레인의 CLI 가 글자 그대로
# 같다(`ingest-raw-disclosure`·`normalize-disclosure`…). 같은 CLI 로 엔트리를 둘 만들면
# `by_cli` 가 먼저 온 쪽을 돌려줘 장중 런의 attempt 가 시장 레인 task_key 로 기록되고, 장중
# 런은 영구 MISSED·시장 런은 **resolve 경로가 없는 LEDGER_GAP** 이 된다. 그래서 두 레인이
# 같은 스텝을 동시에 소유할 수 없다.
#
# 운영 원장(ALPHA-721) 편입: 공시 스케줄도 daily·news 와 같이 **Planner(plan-run) 경유**다 —
# OPS_PIPELINE_TYPE=disclosure 로 자체 레인을 계획하고 이 SFN 을 멱등 시작한다.

locals {
  # 공시 레인 잡 = 기존 잡 리스트의 부분집합 재사용 — command_expr·taskdef_key 드리프트 방지(DRY,
  # 뉴스 레인과 같은 방식). statemachine.tf 의 branches_by_phase 빌더가 이 셋으로 브랜치를 만들고,
  # 새 state **정의**는 하나도 늘지 않는다(test_ops_catalog 의 ASL state 수 단언이 그대로 통과).
  #
  # ⚠️ `LoadDocuments` 는 넣지 않는다. 이름 때문에 공시 문서 적재로 오해하기 쉬운데,
  # `load_documents.py` 는 canonical **뉴스**를 읽어 `document_type='NEWS'` 만 넣고 docstring 이
  # "공시는 범위 밖(별건)"이라 명시한다. 공시 문서가 `document` 에 들어가는 경로는
  # `load_disclosure.py` 안의 `INSERT … 'DISCLOSURE'` 다 — 즉 LoadDisclosure 하나면 닫힌다.
  disclosure_raw_jobs       = [for j in local.raw_ingest_jobs : j if j.state == "CollectDartDisclosure"]
  disclosure_normalize_jobs = [for j in local.normalize_jobs : j if contains(["NormalizeDisclosure", "NormalizeDisclosureSegment"], j.state)]
  disclosure_feature_jobs   = [for j in local.feature_jobs : j if j.state == "LoadDisclosure"]

  disclosure_raw_branches       = local.branches_by_phase["disclosure_raw"]
  disclosure_normalize_branches = local.branches_by_phase["disclosure_normalize"]
  disclosure_feature_branches   = local.branches_by_phase["disclosure_feature"]

  # 페이즈별 전량성공 게이트 — 시장·뉴스 SFN 과 같은 패턴(인덱스별 정적 생성).
  disclosure_raw_success_checks = [
    for index, _ in local.disclosure_raw_jobs : {
      Variable     = "$.branch_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]
  disclosure_normalize_success_checks = [
    for index, _ in local.disclosure_normalize_jobs : {
      Variable     = "$.normalize_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]
  disclosure_feature_success_checks = [
    for index, _ in local.disclosure_feature_jobs : {
      Variable     = "$.feature_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

  # 공시 SFN 정의. 시장 SFN 을 트림한 형태다:
  #   RawIngest(공시 수집) → Normalize(공급계약|사업부문) → Feature[LoadDisclosure]
  # 직렬 꼬리가 없다 — 뉴스의 LoadAssertions·AssembleEvents 는 threading 축이고 공시는 fact
  # 경로라 그 체인을 타지 않는다(load_disclosure.py 모듈 docstring).
  #
  # ⚠️ **issuer 해소는 레인 간 읽기 전용 공유다.** LoadDisclosure 는 canonical 의 corp_code 를
  # `company_profile.dart_corp_code` 로 해소하는데, 그 컬럼을 채우는 EnrichCorpCode 는 **시장 SFN**
  # 소관이다(statemachine.tf 의 LoadInstruments→EnrichCorpCode 직렬). 뉴스 SFN 이 instrument
  # 마스터를 시장 SFN 에서 빌려 읽는 것과 같은 형태·같은 근거다(ADR-0005 단일 writer).
  # steady-state 엔 채워져 있고, 유니버스에 새로 들어온 회사는 그 슬롯에서
  # `skipped_unresolved_issuer` 로 **계측된 뒤** 다음 일일런 이후 슬롯이 줍는다 — 조용한 유실이
  # 아니라 계측된 지연이다(FK RESTRICT 라 넣으면 런 전체 롤백이므로 skip 이 맞다).
  #
  # raw 부분 실패 규약은 두 선례와 같다(ADR-0030): 즉시 알리고 계속 가되, 마지막에 raw 를 다시
  # 보고 부분 실패였으면 FAILED 로 마감한다(한 실패에 두 통 금지).
  disclosure_sfn_definition = jsonencode({
    StartAt        = "DisclosureRawIngestParallel"
    TimeoutSeconds = var.disclosure_state_machine_timeout_seconds
    States = {
      DisclosureRawIngestParallel = {
        Type       = "Parallel"
        Branches   = local.disclosure_raw_branches
        ResultPath = "$.branch_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "DisclosureNotifyFailure" }]
        Next       = "DisclosureRawIngestCheckResults"
      }
      DisclosureRawIngestCheckResults = {
        Type    = "Choice"
        Choices = [{ And = local.disclosure_raw_success_checks, Next = "DisclosureNormalizeParallel" }]
        Default = "DisclosureNotifyRawPartial"
      }
      # 시장·뉴스 SFN 과 같은 이유로 여기서 즉시 알린다 — 뒤에서 최상위 TimeoutSeconds 로 실행이
      # 죽으면 States.Timeout 이 Catch 를 타지 않아 run_id 가 박힌 알림이 영영 안 나간다.
      # 통보 뒤 계속 진행($ 보존), 최종 FAILED 는 끝의 DisclosureRawPartialCheck 가 마감한다.
      DisclosureNotifyRawPartial = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "DisclosureNormalizeParallel"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          "Subject.$" = "States.Format('[${var.name}-disclosure] raw 부분 실패 — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      DisclosureNormalizeParallel = {
        Type       = "Parallel"
        Branches   = local.disclosure_normalize_branches
        ResultPath = "$.normalize_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "DisclosureNotifyFailure" }]
        Next       = "DisclosureNormalizeCheckResults"
      }
      DisclosureNormalizeCheckResults = {
        Type    = "Choice"
        Choices = [{ And = local.disclosure_normalize_success_checks, Next = "DisclosureFeatureParallel" }]
        Default = "DisclosureNotifyFailure"
      }
      DisclosureFeatureParallel = {
        Type       = "Parallel"
        Branches   = local.disclosure_feature_branches
        ResultPath = "$.feature_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "DisclosureNotifyFailure" }]
        Next       = "DisclosureFeatureCheckResults"
      }
      DisclosureFeatureCheckResults = {
        Type    = "Choice"
        Choices = [{ And = local.disclosure_feature_success_checks, Next = "DisclosureRawPartialCheck" }]
        Default = "DisclosureNotifyFailure"
      }
      # raw 부분 실패 런의 마감 판정(시장·뉴스 SFN 과 동형) — 다운스트림을 끝까지 돌린 뒤 raw 를
      # 다시 보고 부분 실패였으면 FAILED 로 끝낸다. 알림은 이미 위에서 쐈으므로 SNS 를 안 탄다.
      DisclosureRawPartialCheck = {
        Type    = "Choice"
        Choices = [{ And = local.disclosure_raw_success_checks, Next = "DisclosureSucceeded" }]
        Default = "DisclosureFailed"
      }
      DisclosureSucceeded = { Type = "Succeed" }
      DisclosureNotifyFailure = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "DisclosureFailed"
        Parameters = {
          TopicArn    = aws_sns_topic.alarms.arn
          "Subject.$" = "States.Format('[${var.name}-disclosure] FAILED — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      DisclosureFailed = { Type = "Fail", Cause = "disclosure pipeline failed" }
    }
  })
}

# 시장·뉴스 SFN 과 같은 sfn 역할을 재사용한다 — 공시 SFN 은 같은 task-def 집합의 부분집합
# (dart·bigkinds·rds)만 runTask 하므로 권한이 이미 충분하다.
resource "aws_sfn_state_machine" "disclosure" {
  name       = "${var.name}-disclosure"
  role_arn   = aws_iam_role.sfn.arn
  definition = local.disclosure_sfn_definition
}

# 시장·뉴스 SFN 과 같은 이유의 타임아웃 알람 — 최상위 TimeoutSeconds 로 실행이 죽으면
# States.Timeout 이 Catch 를 안 타 정의 안 DisclosureNotifyFailure 가 못 잡는다.
resource "aws_cloudwatch_metric_alarm" "disclosure_execution_timed_out" {
  alarm_name        = "${var.name}-disclosure-execution-timed-out"
  alarm_description = "공시 SFN 실행이 TimeoutSeconds 초과로 죽었다 — 정의 안 DisclosureNotifyFailure 가 못 잡는 경로다."
  namespace         = "AWS/States"
  metric_name       = "ExecutionsTimedOut"
  dimensions        = { StateMachineArn = aws_sfn_state_machine.disclosure.arn }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
}

# 공시 스케줄(KST) — 슬롯 목록과 그 배치 근거는 `variables.tf` 의
# `disclosure_schedule_expressions` 주석이 SSOT 다(여기서 반복하지 않는다).
resource "aws_scheduler_schedule" "disclosure" {
  for_each = var.disclosure_schedule_expressions

  name                         = "${var.name}-disclosure-${each.key}"
  state                        = var.disclosure_schedule_state
  schedule_expression          = each.value
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window { mode = "OFF" }

  # 운영 원장(ALPHA-721): SFN 직접 시작이 아니라 **Planner 경유**다(daily·news 와 동형).
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
              { Name = "OPS_PIPELINE_TYPE", Value = "disclosure" },
            ]
          }]
        }
      }),
      "SCHEDULED_TIME_TOKEN", "<aws.scheduler.scheduled-time>",
    )

    # 재시도 0 — 뉴스 레인과 같은 근거다. Planner 멱등은 **같은 슬롯**의 중복만 막으므로,
    # 재시도 창이 슬롯 간격(60분)을 파고들면 지연 시작한 실행이 다음 슬롯 실행과 겹친다.
    # 공시에서 겹침이 나쁜 이유는 둘이다: ① 두 실행이 같은 rcept_no 본문을 동시에 보고
    # seen-map(ALPHA-720)의 TOCTOU 로 같은 ZIP 을 두 번 받는다 ② 같은 canonical 파티션을
    # 동시에 병합한다. 타임아웃 2400s < 3600s 로 세운 비중첩 불변식은 시작 지연 ≈ 0 일 때만
    # 성립하므로, 그 전제를 깨는 재시도를 두지 않는다.
    # 포기해도 잃는 게 거의 없다: 드문 중복 재전달은 run_key 멱등이 흡수하고, 제출 실패로
    # 슬롯이 누락되면 PLANNER_MISSING 이 열리며 **데이터는 다음 슬롯이 같은 날짜창을 통째로
    # 다시 읽어 자가 회복한다**(증분 커서가 없어 매 슬롯이 창 전체를 재독하는 성질의 부수효과).
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 0
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dlq.arn }
  }
}
