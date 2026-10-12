resource "aws_iam_role" "consumer" {
  name               = "${var.name}-consumer"
  assume_role_policy = local.task_trust
}
resource "aws_iam_role_policy" "consumer" {
  role = aws_iam_role.consumer.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [data.aws_secretsmanager_secret.reader.arn, data.aws_secretsmanager_secret.writer.arn] },
    { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = var.price_queue_arn },
    { Effect = "Allow", Action = ["states:StartExecution"], Resource = aws_sfn_state_machine.this.arn },
    { Effect = "Allow", Action = ["states:DescribeExecution"], Resource = "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:execution:${var.name}:*" }
  ] })
}
resource "aws_ecs_task_definition" "consumer" {
  family                   = "${var.name}-consumer"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.consumer.arn
  container_definitions = jsonencode([{
    name            = "price-admission", image = var.image, essential = true,
    entryPoint      = ["python", "-m", "edge_analysis_v2.cloud.queue_runtime"],
    user            = "1001:1001", readonlyRootFilesystem = true, stopTimeout = 120,
    linuxParameters = { capabilities = { drop = ["ALL"] } },
    environment = [
      { name = "AWS_REGION", value = var.region },
      { name = "PRICE_QUEUE_URL", value = var.price_queue_url },
      { name = "STATE_MACHINE_ARN", value = aws_sfn_state_machine.this.arn }
    ],
    logConfiguration = { logDriver = "awslogs", options = {
      "awslogs-group"  = aws_cloudwatch_log_group.this.name,
      "awslogs-region" = var.region, "awslogs-stream-prefix" = "consumer"
    } }
  }])
  lifecycle { ignore_changes = [container_definitions] }
}

resource "aws_cloudwatch_metric_alarm" "single_execution_failed" {
  for_each            = toset(["ExecutionsFailed", "ExecutionsTimedOut", "ExecutionsAborted"])
  alarm_name          = "${var.name}-${each.key}"
  namespace           = "AWS/States"
  metric_name         = each.key
  dimensions          = { StateMachineArn = aws_sfn_state_machine.this.arn }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}
