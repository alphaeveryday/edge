# Short-lived HTTP requests launch the existing worker or read committed publications.
resource "aws_secretsmanager_secret" "api_reader" {
  name        = "edge/analysis-v2/api-reader"
  description = "SELECT-only analysis result credential; value provisioned outside Terraform"
}
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.name}-api"
  retention_in_days = 14
}
resource "aws_iam_role" "api" {
  name = "${var.name}-api"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy" "api" {
  role = aws_iam_role.api.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.api.arn}:*" },
    { Effect = "Allow", Action = ["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DescribeSubnets", "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses"], Resource = "*" },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.api_reader.arn },
    { Effect = "Allow", Action = ["states:StartExecution"], Resource = aws_sfn_state_machine.this.arn },
    { Effect = "Allow", Action = ["states:DescribeExecution"], Resource = "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:execution:${var.name}:*" }
  ] })
}
resource "aws_lambda_function" "api" {
  function_name                  = "${var.name}-api"
  role                           = aws_iam_role.api.arn
  package_type                   = "Image"
  image_uri                      = var.api_image
  architectures                  = ["x86_64"]
  memory_size                    = 512
  timeout                        = 28
  reserved_concurrent_executions = 3
  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.this.id]
  }
  environment {
    variables = {
      STATE_MACHINE_ARN        = aws_sfn_state_machine.this.arn
      RESULT_READER_SECRET_ARN = aws_secretsmanager_secret.api_reader.arn
      RDS_CA_PATH              = "/var/task/rds-ca.pem"
    }
  }
  depends_on = [aws_iam_role_policy.api, aws_cloudwatch_log_group.api]
  lifecycle { ignore_changes = [image_uri] }
}
resource "aws_apigatewayv2_api" "analysis" {
  name          = "${var.name}-api"
  protocol_type = "HTTP"
}
resource "aws_apigatewayv2_integration" "analysis" {
  api_id                 = aws_apigatewayv2_api.analysis.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}
resource "aws_apigatewayv2_route" "analysis" {
  for_each = toset([
    "POST /v2/analyses",
    "GET /v2/analyses/{kind}/{analysis_id}",
    "GET /v2/analyses/{kind}/{analysis_id}/screens/{feature}",
    "GET /v2/etfs/{etf_code}/analyses/{kind}/latest/screens/{feature}"
  ])
  api_id             = aws_apigatewayv2_api.analysis.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.analysis.id}"
  authorization_type = "AWS_IAM"
}
resource "aws_apigatewayv2_stage" "analysis" {
  api_id      = aws_apigatewayv2_api.analysis.id
  name        = "$default"
  auto_deploy = true
  depends_on  = [aws_apigatewayv2_route.analysis]
  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
  route_settings {
    route_key              = "POST /v2/analyses"
    throttling_burst_limit = 2
    throttling_rate_limit  = 0.2
  }
}
resource "aws_lambda_permission" "api" {
  statement_id  = "AnalysisHttpGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.analysis.execution_arn}/*/*/v2/*"
}
resource "aws_iam_role_policy" "api_client" {
  for_each = toset(concat([aws_iam_role.observer.name], var.api_client_role_names))
  name     = "analysis-v2-http-invoke"
  role     = each.value
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow", Action = ["execute-api:Invoke"],
    Resource = ["${aws_apigatewayv2_api.analysis.execution_arn}/*/GET/v2/*", "${aws_apigatewayv2_api.analysis.execution_arn}/*/POST/v2/analyses"]
  }] })
}
resource "aws_iam_role_policy" "api_deploy" {
  name = "analysis-v2-lambda-deploy"
  role = var.deploy_role_name
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow", Action = ["lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:UpdateFunctionCode"],
    Resource = aws_lambda_function.api.arn
  }] })
}
