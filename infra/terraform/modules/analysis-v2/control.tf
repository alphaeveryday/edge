resource "aws_cloudwatch_log_group" "control" {
  name              = "/aws/lambda/${var.name}-control"
  retention_in_days = 14
}
resource "aws_iam_role" "control" {
  name = "${var.name}-control"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy" "control" {
  role = aws_iam_role.control.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.control.arn}:*" },
    { Effect = "Allow", Action = ["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DescribeSubnets", "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses"], Resource = "*" },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = data.aws_secretsmanager_secret.writer.arn },
    { Effect = "Allow", Action = ["states:DescribeExecution", "states:GetExecutionHistory"], Resource = "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:execution:${var.name}:*" },
    { Effect = "Allow", Action = ["ecs:DescribeTasks", "ecs:StopTask"], Resource = "*", Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } } }
  ] })
}
resource "aws_lambda_function" "control" {
  function_name                  = "${var.name}-control"
  role                           = aws_iam_role.control.arn
  package_type                   = "Image"
  image_uri                      = var.api_image
  architectures                  = ["x86_64"]
  memory_size                    = 256
  timeout                        = 28
  reserved_concurrent_executions = 1
  image_config { command = ["edge_analysis_v2.cloud.control.handler"] }
  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.this.id]
  }
  environment {
    variables = {
      RDS_CA_PATH          = "/var/task/rds-ca.pem"
      CLUSTER_ARN          = var.cluster_arn
      ANALYSIS_SLOTS       = tostring(var.analysis_slots)
      EXECUTION_ARN_PREFIX = "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:execution:${var.name}:"
    }
  }
  depends_on = [aws_iam_role_policy.control, aws_cloudwatch_log_group.control]
  lifecycle { ignore_changes = [image_uri] }
}
