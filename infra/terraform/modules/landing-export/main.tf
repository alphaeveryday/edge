# 랜딩 전망 내보내기(ALPHA-1267) — 매일 08:00 KST 에 그날 발행된 전망 화면을 랜딩 버킷의
# outlook/<코드>.json 으로 쓴다. etforca.com 의 종목 검색이 이 파일을 읽는다.
#
# 랜딩의 것이라 분석 모듈 밖에 둔다. 전망은 분석 v2 조회 API(IAM 인증)로만 읽으므로 VPC·DB 자격이 없다.
# 전망 배치(06:00 시작, 08:00 마감)는 08:00 전에 끝난다는 전제다 — 늦게 끝난 날의 대비는 두지 않는다.
# 함수 본문은 handler.py, 저장 규칙 테스트는 tests/test_handler.py.
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  api_id = regex("^https://([a-z0-9]+)\\.execute-api\\.", var.api_url)[0]
}

data "archive_file" "handler" {
  type        = "zip"
  source_file = "${path.module}/handler.py"
  output_path = "${path.root}/.terraform/${var.name}.zip"
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = 14
}

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy" "this" {
  role = aws_iam_role.this.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.this.arn}:*" },
    # 종목별 전망 조회 한 경로만. 분석을 시작하는 POST 는 주지 않는다.
    { Effect = "Allow", Action = ["execute-api:Invoke"], Resource = "arn:aws:execute-api:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:${local.api_id}/*/GET/v2/etfs/*/outlook" },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = "${var.bucket_arn}/outlook/*" }
  ] })
}

resource "aws_lambda_function" "this" {
  function_name    = var.name
  role             = aws_iam_role.this.arn
  runtime          = "python3.13"
  handler          = "handler.handler"
  filename         = data.archive_file.handler.output_path
  source_code_hash = data.archive_file.handler.output_base64sha256
  memory_size      = 256
  # 람다 상한. 조회 1회를 5초로 끊으므로(handler.py) 전 종목이 다 느려도 148종 × 5.25초 = 777초로 끝까지 돈다.
  # 종목 수가 170을 넘으면 이 여유가 사라진다.
  timeout = 900
  environment {
    variables = {
      ANALYSIS_API_URL = var.api_url
      LANDING_BUCKET   = var.bucket_name
      ETF_CODES        = jsonencode(var.etf_codes)
    }
  }
  depends_on = [aws_iam_role_policy.this, aws_cloudwatch_log_group.this]
}

# 스케줄러는 람다를 비동기로 부른다 — 본문이 실패해도(조회·저장 오류, 0건) 스케줄러의 실패 큐로는 오지 않는다.
# 그래서 함수 오류 지표에 알람을 건다. 하루 한 번 도는 함수라 한 번의 오류가 곧 그날 갱신 실패다.
resource "aws_cloudwatch_metric_alarm" "failed" {
  alarm_name          = "${var.name}-failed"
  alarm_description   = "랜딩 전망 내보내기가 실패했다 — etforca.com 종목 검색이 어제 전망을 보여 준다. 로그의 결과 줄(failed·missing)에 종목별 사유가 있다."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.this.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name}-scheduler"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.this.arn },
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = var.scheduler_dlq_arn }
  ] })
}

resource "aws_scheduler_schedule" "this" {
  name                         = var.name
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = "Asia/Seoul"

  flexible_time_window { mode = "OFF" }

  target {
    arn      = aws_lambda_function.this.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{}"

    retry_policy {
      maximum_event_age_in_seconds = 1800
      maximum_retry_attempts       = 3
    }
    dead_letter_config { arn = var.scheduler_dlq_arn }
  }
}
