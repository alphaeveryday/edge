# 쿼터백 통화 데모 API(ALPHA-1487) — quarterback.etforca.com 의 통화 화면이 부르는 함수 하나.
# 전사 임시 토큰 발급(OpenAI), 상담 답변 스트리밍(DeepSeek), 휴대폰 ↔ 콘솔 방 중계(DynamoDB)를 한다. 본문은 handler.mjs, 테스트는 tests/.
#
# 키 값은 Terraform 상태에 두지 않는다 — 시크릿은 콘솔·CLI 로 만든 것을 이름으로 읽기만 한다.
# 함수 URL 은 공개(NONE)다. CloudFront(/api/*)가 오리진 헤더(origin_header_value)를 붙여 보내고, 함수는 그 헤더가
# 없는 호출(함수 URL·람다 직접 호출)을 거른다. 사이트를 거친 호출을 사람 단위로 막는 장치(요청 수 제한)는 없다 —
# 동시 실행 10은 동시에 도는 함수 수만 묶고, 발급된 전사 세션 비용은 묶지 못한다.
data "aws_secretsmanager_secret" "openai" { name = var.openai_secret_name }
data "aws_secretsmanager_secret" "deepseek" { name = var.deepseek_secret_name }

# CloudFront ↔ 함수 공유 값. 역할 고유 ID(AROA…)는 계정 밖에서 볼 수 없다 — 새 provider 없이 만들고,
# plan 역할(ReadOnly)이 시크릿 값을 읽지 않아도 된다.
locals {
  origin_header_value = sha256("${aws_iam_role.this.unique_id}/quarterback-call")
}

data "archive_file" "handler" {
  type        = "zip"
  output_path = "${path.root}/.terraform/${var.name}.zip"
  source {
    content  = file("${path.module}/handler.mjs")
    filename = "handler.mjs"
  }
}

# 휴대폰 ↔ 콘솔 방(ALPHA-1490) — 휴대폰이 받아 적은 말을 쌓고 콘솔이 가져간다. 시연 규모(통화당 수백 건)라
# 요청 과금으로 둔다. 기록은 하루 뒤 TTL 로 지워진다.
resource "aws_dynamodb_table" "rooms" {
  name         = "${var.name}-rooms"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "room"
  range_key    = "seq"

  attribute {
    name = "room"
    type = "S"
  }
  attribute {
    name = "seq"
    type = "N"
  }
  ttl {
    attribute_name = "expires"
    enabled        = true
  }
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
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [data.aws_secretsmanager_secret.openai.arn, data.aws_secretsmanager_secret.deepseek.arn] },
    { Effect = "Allow", Action = ["dynamodb:BatchWriteItem", "dynamodb:Query"], Resource = aws_dynamodb_table.rooms.arn }
  ] })
}

resource "aws_lambda_function" "this" {
  function_name    = var.name
  role             = aws_iam_role.this.arn
  runtime          = "nodejs22.x"
  handler          = "handler.handler"
  filename         = data.archive_file.handler.output_path
  source_code_hash = data.archive_file.handler.output_base64sha256
  memory_size      = 256
  # 답변 스트리밍은 몇 초면 끝난다(첫 글자 ~2초). CloudFront 오리진 응답 제한(30초)과 맞춘다.
  timeout                        = 30
  reserved_concurrent_executions = 10
  environment {
    variables = {
      OPENAI_SECRET_ID   = data.aws_secretsmanager_secret.openai.arn
      DEEPSEEK_SECRET_ID = data.aws_secretsmanager_secret.deepseek.arn
      ALLOWED_ORIGINS    = join(",", var.allowed_origins)
      ORIGIN_SECRET      = local.origin_header_value
      ROOMS_TABLE        = aws_dynamodb_table.rooms.name
    }
  }
  depends_on = [aws_iam_role_policy.this, aws_cloudwatch_log_group.this]
}

resource "aws_lambda_function_url" "this" {
  function_name      = aws_lambda_function.this.function_name
  authorization_type = "NONE"
  invoke_mode        = "RESPONSE_STREAM"
}

# 공개 함수 URL 은 InvokeFunctionUrl 과 InvokeFunction 두 권한이 다 있어야 열린다.
# InvokeFunctionUrl(FunctionURLAllowPublicAccess)은 AWS 가 NONE 함수 URL 을 만들 때 스스로 붙인다 — 같은 이름으로
# 또 만들면 409 로 apply 가 깨진다(2026-11-10 첫 apply). 그래서 InvokeFunction 만 둔다.
# InvokeFunction 을 함수 URL 경유로만 좁히는 조건(InvokedViaFunctionUrl)은 지금 provider(5.x)에 없다 —
# 직접 호출은 오리진 헤더가 없어 403 으로 끝난다(handler.mjs route).
resource "aws_lambda_permission" "invoke" {
  statement_id  = "FunctionURLAllowInvokeAction"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "*"
}
