# ── 실시간 축: 분봉 트리거 소비 (ALPHA-671) ─────────────────────────────────
#
# 일간 축(SFN analyze 페이즈, 평일 15:40 KST)은 장 마감 뒤 하루를 설명한다. 그런데
# 사람이 설명을 찾는 순간은 **가격이 튄 그때**다. `minute_price_trigger` 가 그 순간을
# 이미 원장에 남기고 있으므로, 분석은 그 행을 소비하면 된다.
#
# **왜 폴링인가.** 이 레포의 실행 축은 EventBridge Scheduler → ECS RunTask → SFN 하나다
# (Lambda·SQS 소비자·API GW 선례 0건). 그리고 원장 규약이 명시한다: SFN 을 직접
# start-execution 하면 원장에 안 남아 대조 대상이 아니다. 이벤트 푸시 축을 새로 세우는
# 대신 **Reconciler 와 같은 형태**(rate(N minutes))로 미분석 트리거를 소비한다 -
# 신규 인프라 0, 원장 계약 유지.
#
# **멱등은 계보가 보장한다.** '미분석' 의 정의는 상태 컬럼이 아니라
# `etf_contribution_observation.minute_price_trigger_id` 가 비어 있는 것이다. 그래서
# 같은 트리거를 두 번 집어도 두 번째는 ON CONFLICT DO NOTHING 으로 지나간다.
# 실패한 트리거는 계보가 안 붙으므로 **다음 런이 자연히 재시도**한다.
#
# 분석 task-def 를 그대로 쓰고 Command 만 덮는다 - db-query 모듈과 같은 계약이라
# 질의(=실행 형태)가 task 정의 리비전을 늘리지 않는다.

resource "aws_scheduler_schedule" "analysis_minute" {
  name                         = "${var.name}-analysis-minute"
  state                        = var.analysis_minute_schedule_state
  schedule_expression          = var.analysis_minute_schedule_expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      Cluster        = var.cluster_arn
      TaskDefinition = aws_ecs_task_definition.analysis.arn
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
          Name = local.analysis_container_name
          # 상한은 **한 런의 벽시계 보호**다. 셀 하나가 LLM 을 포함해 수분이므로
          # 트리거가 몰린 날 한 태스크가 무한히 길어지지 않게 자른다 - 남은 것은
          # 다음 런이 집는다(계보가 큐다).
          Command = ["analyze-minute", "--limit", tostring(var.analysis_minute_limit)]
        }]
      }
    })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dlq.arn }
  }
}
