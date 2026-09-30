# 검증 종료 장치(ALPHA-1119) — 운영자 PC(절전·터미널 종료·네트워크 단절)와 무관하게 AWS 쪽에서 검증을 끝낸다.
# 1) verify_shutdown_at: 스케줄러가 verify-ops 태스크로 `shim.py verify-shutdown <grace>` 를 띄운다 —
#    Airflow 서비스 desired 0(새 제출 중단) → grace 동안 검증 업무 태스크가 스스로 끝나길 기다림 → 남은 검증 태스크만
#    StopTask(결과 미상으로 기록) → 호스트 ASG 0 → 보고서를 검증 버킷 shutdown/ 에 남긴다.
# 2) verify_hard_stop_at: 1) 이 실패해도 서비스·호스트는 내려가게 두 API 를 직접 부른다(코드 없음).
# 태스크를 띄우는 방식은 분 세션 스케줄(modules/data-pipeline minute_services.tf)과 같다. 호스트 ASG 를 0 으로 만든 뒤
# 다음 dev 자동 apply 는 host_count 대로 되돌리므로, 검증을 마치면 host_count = 0 머지로 코드를 맞춘다.
locals {
  verify_shutdown = var.verify_enabled && var.verify_shutdown_at != ""
  # 종료 태스크 RunTask 요청 — grace 만 다른 두 스케줄(종료·강제)이 같이 쓴다.
  # 검증이 꺼지면 참조 대상이 없다 — 이 값은 count 로 켜지는 스케줄 안에서만 쓰인다.
  verify_shutdown_run = {
    Cluster        = aws_ecs_cluster.this.arn
    TaskDefinition = try(aws_ecs_task_definition.verify["ops"].arn, null)
    LaunchType     = "FARGATE"
    StartedBy      = "verify-shutdown"
    NetworkConfiguration = {
      AwsvpcConfiguration = {
        Subnets        = var.subnet_ids
        SecurityGroups = aws_security_group.verify[*].id
        AssignPublicIp = "DISABLED"
      }
    }
  }
}

resource "aws_iam_role_policy" "verify_task_shutdown" {
  count = local.verify_shutdown ? 1 : 0
  name  = "${var.name}-verify-task-shutdown"
  role  = aws_iam_role.verify_task[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecs:UpdateService"], Resource = [aws_ecs_service.airflow.id] },
      {
        Effect    = "Allow"
        Action    = ["ecs:StopTask"]
        Resource  = ["*"]
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } }
      },
      { Effect = "Allow", Action = ["autoscaling:UpdateAutoScalingGroup"], Resource = [aws_autoscaling_group.host.arn] },
      # 호스트를 내리기 전 관측 기록 전송(shim `_ship_host_obs`): ASG 인스턴스 조회 → SSM RunShellScript → 결과 확인.
      # 출력은 호스트 역할이 검증 버킷 obs/ 에 쓴다(verify.tf host_observer_upload).
      { Effect = "Allow", Action = ["autoscaling:DescribeAutoScalingGroups"], Resource = ["*"] },
      {
        Effect = "Allow"
        Action = ["ssm:SendCommand"]
        Resource = [
          "arn:aws:ssm:${var.region}::document/AWS-RunShellScript",
          "arn:aws:ec2:${var.region}:${local.account_id}:instance/*",
        ]
      },
      { Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = ["*"] },
    ]
  })
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "verify_scheduler" {
  count              = local.verify_shutdown ? 1 : 0
  name               = "${var.name}-verify-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "verify_scheduler" {
  count = local.verify_shutdown ? 1 : 0
  name  = "${var.name}-verify-scheduler"
  role  = aws_iam_role.verify_scheduler[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["ecs:RunTask"]
        Resource  = ["${aws_ecs_task_definition.verify["ops"].arn_without_revision}:*"]
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } }
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.verify_execution[0].arn, aws_iam_role.verify_task[0].arn]
      },
      { Effect = "Allow", Action = ["ecs:UpdateService"], Resource = [aws_ecs_service.airflow.id] },
      { Effect = "Allow", Action = ["autoscaling:UpdateAutoScalingGroup"], Resource = [aws_autoscaling_group.host.arn] },
    ]
  })
}

resource "aws_scheduler_schedule" "verify_shutdown" {
  count                        = local.verify_shutdown ? 1 : 0
  name                         = "${var.name}-verify-shutdown"
  schedule_expression          = "at(${var.verify_shutdown_at})"
  schedule_expression_timezone = "Asia/Seoul"
  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.verify_scheduler[0].arn
    input = jsonencode(merge(local.verify_shutdown_run, {
      Overrides = { ContainerOverrides = [{ Name = "data-pipeline", Command = ["verify-shutdown", tostring(var.verify_shutdown_grace_seconds)] }] }
    }))
    retry_policy {
      maximum_event_age_in_seconds = 900
      maximum_retry_attempts       = 3
    }
  }
}

# 강제 시각에 종료 태스크를 한 번 더(grace 0) — 첫 종료 태스크가 기동 실패·조회 실패로 검증 태스크를 못 멈췄을 때.
# 이것마저 실패해도 남는 검증 업무 태스크는 스스로 끝난다(재제출하는 Airflow 가 없고, 각 스텝은 수 분 안에 끝난다).
resource "aws_scheduler_schedule" "verify_hard_stop_tasks" {
  count                        = local.verify_shutdown ? 1 : 0
  name                         = "${var.name}-verify-hard-stop-tasks"
  schedule_expression          = "at(${var.verify_hard_stop_at})"
  schedule_expression_timezone = "Asia/Seoul"
  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.verify_scheduler[0].arn
    input = jsonencode(merge(local.verify_shutdown_run, {
      Overrides = { ContainerOverrides = [{ Name = "data-pipeline", Command = ["verify-shutdown", "0"] }] }
    }))
    retry_policy {
      maximum_event_age_in_seconds = 900
      maximum_retry_attempts       = 3
    }
  }
}

resource "aws_scheduler_schedule" "verify_hard_stop_service" {
  count                        = local.verify_shutdown ? 1 : 0
  name                         = "${var.name}-verify-hard-stop-service"
  schedule_expression          = "at(${var.verify_hard_stop_at})"
  schedule_expression_timezone = "Asia/Seoul"
  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:updateService"
    role_arn = aws_iam_role.verify_scheduler[0].arn
    input    = jsonencode({ Cluster = aws_ecs_cluster.this.arn, Service = aws_ecs_service.airflow.name, DesiredCount = 0 })
  }
}

resource "aws_scheduler_schedule" "verify_hard_stop_host" {
  count                        = local.verify_shutdown ? 1 : 0
  name                         = "${var.name}-verify-hard-stop-host"
  schedule_expression          = "at(${var.verify_hard_stop_at})"
  schedule_expression_timezone = "Asia/Seoul"
  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:autoscaling:updateAutoScalingGroup"
    role_arn = aws_iam_role.verify_scheduler[0].arn
    input = jsonencode({
      AutoScalingGroupName = aws_autoscaling_group.host.name, MinSize = 0, MaxSize = 0, DesiredCapacity = 0
    })
  }
}
