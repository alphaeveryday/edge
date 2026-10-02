# One workflow runs one analysis. Callers: operators, the HTTP API (api.tf) and the scheduled outlook batch (outlook_batch.tf).
data "aws_caller_identity" "current" {}
data "aws_secretsmanager_secret" "reader" { name = "edge/analysis-v2/readonly" }
data "aws_secretsmanager_secret" "writer" { name = "edge/analysis-v2/writer" }
resource "aws_secretsmanager_secret" "model" {
  name        = "edge/analysis-v2/deepseek"
  description = "v2 DeepSeek credential; secret value supplied outside Terraform"
}
locals {
  task_trust      = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }] })
  task_arn        = "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${var.name}:*"
  observation_arn = "arn:aws:s3:::${var.bucket_name}/analysis-v2/runs/*"
}
resource "aws_cloudwatch_log_group" "this" {
  name              = "/edge/analysis-v2"
  retention_in_days = 14
}
resource "aws_security_group" "this" {
  name        = var.name
  vpc_id      = var.vpc_id
  description = "Outbound-only v2 analysis tasks"
}
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.this.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_egress_rule" "postgres" {
  security_group_id            = aws_security_group.this.id
  referenced_security_group_id = var.db_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_vpc_security_group_ingress_rule" "postgres" {
  security_group_id            = var.db_security_group_id
  referenced_security_group_id = aws_security_group.this.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = local.task_trust
}
resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = local.task_trust
}
resource "aws_iam_role_policy" "task" {
  role = aws_iam_role.task.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [data.aws_secretsmanager_secret.reader.arn, data.aws_secretsmanager_secret.writer.arn, aws_secretsmanager_secret.model.arn] },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = local.observation_arn }
  ] })
}
resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name = "analysis-v2", image = var.image, essential = true,
    environment = [
      { name = "AWS_REGION", value = var.region },
      { name = "OBSERVATION_BUCKET", value = var.bucket_name },
      { name = "DEEPSEEK_SECRET_ARN", value = aws_secretsmanager_secret.model.arn }
    ],
    logConfiguration = { logDriver = "awslogs", options = {
      "awslogs-group"  = aws_cloudwatch_log_group.this.name,
      "awslogs-region" = var.region, "awslogs-stream-prefix" = "run"
    } }
  }])
  # The v2 deployment workflow owns image revisions and their scratch volume; Terraform owns permissions/network.
  lifecycle { ignore_changes = [container_definitions, volume] }
}
resource "aws_iam_role" "workflow" {
  name               = "${var.name}-workflow"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "workflow" {
  role = aws_iam_role.workflow.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecs:RunTask"], Resource = local.task_arn, Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } } },
    { Effect = "Allow", Action = ["ecs:DescribeTasks", "ecs:StopTask"], Resource = "*", Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } } },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn], Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } } },
    { Effect = "Allow", Action = ["events:PutTargets", "events:PutRule", "events:DescribeRule"], Resource = "arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForECSTaskRule" },
    { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.control.arn }
  ] })
}
resource "aws_sfn_state_machine" "this" {
  name     = var.name
  role_arn = aws_iam_role.workflow.arn
  definition = templatefile("${path.module}/single_analysis.asl.json", {
    control_arn    = aws_lambda_function.control.arn
    cluster_arn    = var.cluster_arn
    task_family    = aws_ecs_task_definition.this.family
    security_group = aws_security_group.this.id
    subnets_json   = jsonencode(var.subnet_ids)
    slots          = tostring(var.analysis_slots)
  })
}

resource "aws_iam_role_policy" "deploy" {
  name = "analysis-v2-task-registration"
  role = var.deploy_role_name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn, aws_iam_role.consumer.arn], Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } } }
  ] })
}
resource "aws_iam_role" "observer" {
  name               = "${var.name}-observer"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { AWS = var.operator_arn }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "observer" {
  role = aws_iam_role.observer.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${var.bucket_name}", Condition = { StringLike = { "s3:prefix" = ["analysis-v2/runs/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = local.observation_arn },
    { Effect = "Allow", Action = ["states:StartExecution", "states:ListExecutions"], Resource = aws_sfn_state_machine.this.arn },
    { Effect = "Allow", Action = ["states:DescribeExecution"], Resource = "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:execution:${var.name}:*" }
  ] })
}
