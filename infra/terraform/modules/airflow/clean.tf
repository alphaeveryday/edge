# 메타DB 실행 기록 정리(airflow db clean) — 매주 일요일 05:00 KST.
# DAG·태스크 실행 기록, 로그 행, XCom 은 Airflow 가 스스로 지우지 않아 슬롯마다 쌓인다. 30일보다 오래된 것은
# 쓰는 곳이 없다: 재처리는 새 run 의 conf 로 슬롯을 받고(edge_batch.reprocess_slot), Reconciler 는 Airflow 가 아니라 운영 원장을
# 본다. DAG 마다 마지막 run 은 airflow db clean 이 남기므로 스케줄 계산은 그대로다. 보관 테이블 없이 지운다.
# 마이그레이션 태스크 정의를 그대로 쓴다: 같은 이미지·메타DB 연결·서브넷·SG 이고, 배포(deploy-airflow)가 새 리비전을
# 등록하면 리비전 없는 ARN 이 그 최신 리비전을 가리킨다. 일요일 새벽은 수집 DAG 슬롯이 없는 시각이다.
resource "aws_iam_role" "clean_scheduler" {
  name = "${var.name}-db-clean-scheduler"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "scheduler.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "clean_scheduler" {
  name = "${var.name}-db-clean-scheduler"
  role = aws_iam_role.clean_scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecs:RunTask"], Resource = [aws_ecs_task_definition.migrate.arn_without_revision, "${aws_ecs_task_definition.migrate.arn_without_revision}:*"] },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn] },
    ]
  })
}

resource "aws_scheduler_schedule" "db_clean" {
  name                         = "${var.name}-db-clean"
  schedule_expression          = "cron(0 5 ? * SUN *)"
  schedule_expression_timezone = "Asia/Seoul"

  flexible_time_window { mode = "OFF" }

  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.clean_scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.migrate.arn_without_revision
      launch_type         = "FARGATE"
      network_configuration {
        subnets          = var.subnet_ids
        security_groups  = [aws_security_group.task.id]
        assign_public_ip = false
      }
    }

    # 공식 이미지 entrypoint 는 첫 인자가 bash 면 그대로 실행한다. 기준 시각은 실행 때 계산한다.
    input = jsonencode({
      containerOverrides = [{
        name    = "migrate"
        command = ["bash", "-c", "airflow db clean --clean-before-timestamp \"$(date -u -d '30 days ago' +%Y-%m-%dT%H:%M:%S+00:00)\" --skip-archive --yes"]
      }]
    })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }
  }
}
