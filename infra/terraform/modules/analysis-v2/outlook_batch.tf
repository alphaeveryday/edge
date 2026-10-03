# 전망 배치(ALPHA-1142·1157) — 스케줄러가 배치 워크플로를 시작하고, 배치는 ETF마다 기존 단건
# 워크플로를 **analysis_slots 건씩** 중첩 호출한다. 분석 로직·요청 계약은 그대로다.
#
# 동시 수의 상한은 워커의 분석 슬롯이 진다(단건 워크플로가 ANALYSIS_SLOTS 를 넘긴다). 워커는
# 건당 writer 연결 3개를 분석 내내 쥐므로, 슬롯이 없으면 원천을 읽기 전에 기다린다. 배치·API·수동
# 시작이 모두 같은 워커를 지나 같은 상한을 받는다. 정의의 Gate(단건 워크플로의 실행 중 개수 확인)는
# 상한이 아니라 **기다릴 태스크를 미리 띄우지 않기 위한 절약**이다 — 확인과 시작 사이에 끼어든
# 실행은 워커에서 슬롯을 기다릴 뿐 실패하지 않는다. 로컬 대시보드 실행은 슬롯을 잡지 않는다
# (tests/loadtest/analysis-v2/README.md '알려진 한계').
#
# 정의는 outlook_batch.asl.json, 계약 테스트는 tests/test_outlook_batch.py.
locals {
  outlook_batch_name = "${var.name}-outlook-batch"
}

resource "aws_iam_role" "outlook_batch" {
  name               = local.outlook_batch_name
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy" "outlook_batch" {
  role = aws_iam_role.outlook_batch.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["states:StartExecution", "states:ListExecutions"], Resource = aws_sfn_state_machine.this.arn },
    # .sync 중첩 호출: 완료를 기다리고, 부모가 중단되면 자식을 멈춘다.
    { Effect = "Allow", Action = ["states:DescribeExecution", "states:StopExecution"], Resource = "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:execution:${var.name}:*" },
    { Effect = "Allow", Action = ["events:PutTargets", "events:PutRule", "events:DescribeRule"], Resource = "arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForStepFunctionsExecutionRule" },
    # 시도별 저장 상태와 발행 시각은 조회 API 로 읽는다(종료 코드보다 DB 가 정본). POST 권한은 주지 않는다.
    { Effect = "Allow", Action = ["execute-api:Invoke"], Resource = "${aws_apigatewayv2_api.analysis.execution_arn}/*/GET/v2/analyses/outlook/*" }
  ] })
}

resource "aws_sfn_state_machine" "outlook_batch" {
  name     = local.outlook_batch_name
  role_arn = aws_iam_role.outlook_batch.arn
  definition = templatefile("${path.module}/outlook_batch.asl.json", {
    analysis_state_machine_arn = aws_sfn_state_machine.this.arn
    api_endpoint               = replace(aws_apigatewayv2_api.analysis.api_endpoint, "https://", "")
    defaults_json              = jsonencode({ etf_codes = var.outlook_etf_codes, max_attempts = 2 })
    deadline_utc               = var.outlook_deadline_utc
    slots                      = var.analysis_slots
  })
  depends_on = [aws_iam_role_policy.outlook_batch]

  lifecycle {
    precondition {
      condition     = length(var.outlook_etf_codes) > 0 && alltrue([for code in var.outlook_etf_codes : can(regex("^[A-Z0-9]{6}$", code))])
      error_message = "outlook_etf_codes 는 6자리 ETF 코드가 하나 이상이어야 한다 — 빈 목록이면 배치가 아무것도 안 돌리고 성공한다."
    }
  }
}

# 항목 실패·미완료·마감 초과는 정의가 Fail 로 끝내므로(OutlookBatch.Incomplete·DeadlineExceeded)
# ExecutionsFailed 하나로 드러난다. 사유와 항목 목록은 실행의 cause 에 있다.
resource "aws_cloudwatch_metric_alarm" "outlook_batch_failed" {
  alarm_name          = "${local.outlook_batch_name}-execution-failed"
  alarm_description   = "전망 배치가 실패로 끝났다 — 일부 ETF 의 전망이 08:00 KST 전에 저장되지 않았다. 실행의 cause 에 항목별 사유가 있다."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.outlook_batch.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}

# 정의의 TimeoutSeconds(8시간)는 마감 장치가 아니라 안전망이다 — 마감은 정의가 절대 시각으로 판정한다.
# 이 알람이 울리면 대기 루프가 마감 판정 없이 돌았다는 뜻이다.
resource "aws_cloudwatch_metric_alarm" "outlook_batch_timed_out" {
  alarm_name          = "${local.outlook_batch_name}-execution-timed-out"
  alarm_description   = "전망 배치가 안전망 시간 제한으로 죽었다 — 정의의 마감 판정을 거치지 못한 경로다."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsTimedOut"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.outlook_batch.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}

resource "aws_iam_role" "outlook_scheduler" {
  name               = "${local.outlook_batch_name}-scheduler"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy" "outlook_scheduler" {
  role = aws_iam_role.outlook_scheduler.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["states:StartExecution"], Resource = aws_sfn_state_machine.outlook_batch.arn },
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = var.scheduler_dlq_arn }
  ] })
}

# 업무 기준시각(analysis_at) = 스케줄의 **예정 시각**이다. 실제 시작이 늦어지거나 재전달돼도 같은
# 값이라 같은 작업으로 합쳐진다(분석 ID 가 이 문자열에서 나온다). 마감은 그날 08:00 KST 로 정의가 계산한다.
# ⚠️ 공휴일에도 뜬다(평일 cron) — 장전 유니버스 스케줄과 같다.
resource "aws_scheduler_schedule" "outlook_batch" {
  name                         = local.outlook_batch_name
  state                        = var.outlook_schedule_state
  schedule_expression          = var.outlook_schedule_expression
  schedule_expression_timezone = "Asia/Seoul"

  flexible_time_window { mode = "OFF" }

  target {
    arn      = aws_sfn_state_machine.outlook_batch.arn
    role_arn = aws_iam_role.outlook_scheduler.arn

    # 자리표시자는 jsonencode 바깥에서 넣는다 — jsonencode 가 `<`·`>` 를 이스케이프해
    # 스케줄러가 컨텍스트 속성으로 인식하지 못한다(ALPHA-593).
    input = replace(
      jsonencode({ analysis_at = "SCHEDULED_TIME_TOKEN" }),
      "SCHEDULED_TIME_TOKEN", "<aws.scheduler.scheduled-time>",
    )

    retry_policy {
      maximum_event_age_in_seconds = 1800
      maximum_retry_attempts       = 3
    }
    dead_letter_config { arn = var.scheduler_dlq_arn }
  }
}
