# 쿼터백 통화 데모 API(ALPHA-1487) — quarterback.etforca.com 의 통화 화면이 부르는 함수 하나.
# 전사 임시 토큰 발급(OpenAI)과 상담 답변 스트리밍(DeepSeek)만 한다. 본문은 handler.mjs, 테스트는 tests/.
#
# 키 값은 Terraform 상태에 두지 않는다 — 시크릿은 콘솔·CLI 로 만든 것을 이름으로 읽기만 한다.
# 함수 URL 은 공개(NONE)다. 브라우저가 CloudFront(/api/*)를 거쳐 부르고, 함수가 Origin 으로 사이트 밖 호출을 거른다.
# 시연용이라 동시 실행을 낮게 묶어 남용 시 비용 상한을 둔다.
data "aws_secretsmanager_secret" "openai" { name = var.openai_secret_name }
data "aws_secretsmanager_secret" "deepseek" { name = var.deepseek_secret_name }

data "archive_file" "handler" {
  type        = "zip"
  output_path = "${path.root}/.terraform/${var.name}.zip"
  source {
    content  = file("${path.module}/handler.mjs")
    filename = "handler.mjs"
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
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [data.aws_secretsmanager_secret.openai.arn, data.aws_secretsmanager_secret.deepseek.arn] }
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
  reserved_concurrent_executions = 5
  environment {
    variables = {
      OPENAI_SECRET_ID   = data.aws_secretsmanager_secret.openai.arn
      DEEPSEEK_SECRET_ID = data.aws_secretsmanager_secret.deepseek.arn
      ALLOWED_ORIGINS    = join(",", var.allowed_origins)
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
# InvokeFunction 을 함수 URL 경유로만 좁히는 조건(InvokedViaFunctionUrl)은 지금 provider(5.x)에 없다 —
# 직접 호출되더라도 경로가 없어 404 로 끝난다(handler.mjs route).
resource "aws_lambda_permission" "url" {
  statement_id           = "FunctionURLAllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.this.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "invoke" {
  statement_id  = "FunctionURLAllowInvokeAction"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "*"
}
