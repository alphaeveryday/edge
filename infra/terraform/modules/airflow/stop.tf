# 운영 중단 장치(ALPHA-1141) — 중단 기준을 넘으면 Airflow 서비스를 desired 0 으로 내린다. 운영자 PC 와 무관하다.
#
# 무엇을 멈추나: Airflow 서비스(scheduler·api·dag-processor)뿐이다. 새 업무 제출이 멈춘다. 이미 뜬 업무 ECS 태스크(worker
# 클러스터)는 끝까지 돈다(EdgeStep.on_kill 이 StopTask 를 부르지 않는 것과 같은 선택). 호스트도 남긴다(로그·조사용).
# 그 뒤는 사람이 한다: 종료 확인 ①~⑥ → SFN 복귀(README "롤백"). 자동으로 SFN 을 켜지 않는다 — 종료 확인 없이 두 주체가
# 같은 파티션을 쓸 수 있기 때문이다.
#
# 왜 CloudWatch 경보 + 오토스케일링인가: 검증 때의 감시 태스크(verify-watchdog)는 감시 자신이 죽는 경우를 따로 막아야
# 했다(심장박동 게이트). 경보는 AWS 가 평가하므로 그 문제가 없다. 형태는 analysis_autoscaling.tf(ALPHA-912)와 같다.
# - 경보 액션은 ALARM 에 머무는 동안 **매분 다시** 불린다. 기준을 넘는 동안은 운영자가 desired 1 로 올려도 다시 0 이 된다.
#   다시 켜려면 경보가 OK 로 돌아온 뒤 deploy-airflow start_service=true(또는 update-service)로 올린다.
# - 감시 상실: RDS 지표가 끊기면 RDS 상태를 모르는 것이라 위반으로 본다(breaching). 서비스 메모리 지표는 태스크가 없으면
#   끊기는데, 그때는 이미 멈춘 것이다 — notBreaching 으로 두어야 다시 켤 수 있다(breaching 이면 기동 중 지표가 오기 전에
#   또 0 으로 내린다). 태스크 부재 자체는 service_down 경보(main.tf)가 알린다.
locals {
  stop_enabled = var.host_count > 0
  stop_alarms = {
    # 태스크 cgroup 상한(task_memory) 대비. 실측 최대 80%(1129/1408, 10-01 A4·A5), 1분 지표 최대 76.7%.
    # OOM 은 100% 에서 순간에 나므로 이 경보는 **누적 증가**(유휴 anon +197MiB/6회, 장시간 미검증)를 잡는 장치다.
    task_memory = {
      namespace  = "AWS/ECS", metric = "MemoryUtilization", statistic = "Maximum", op = "GreaterThanThreshold"
      threshold  = 90, points = 2, periods = 3, missing = "notBreaching"
      dimensions = { ClusterName = aws_ecs_cluster.this.name, ServiceName = aws_ecs_service.airflow.name }
      what       = "Airflow 태스크 메모리 > 90%(task_memory 대비) 3분 중 2분"
    }
    # 업무 RDS 여유 메모리. 장중 평시 최저 552~591MiB(09-22~10-01), Airflow 몫 약 −30~40MiB(검증 실측).
    # README "기존 RDS 재사용 평가"의 중단 기준(400MiB)을 그대로 쓴다.
    rds_freeable = {
      namespace  = "AWS/RDS", metric = "FreeableMemory", statistic = "Minimum", op = "LessThanThreshold"
      threshold  = 400 * 1024 * 1024, points = 5, periods = 5, missing = "breaching"
      dimensions = { DBInstanceIdentifier = var.db_instance_identifier }
      what       = "업무 RDS 여유 메모리 < 400MiB 5분 연속(지표 결측도 위반)"
    }
    rds_swap = {
      namespace  = "AWS/RDS", metric = "SwapUsage", statistic = "Maximum", op = "GreaterThanThreshold"
      threshold  = 100 * 1024 * 1024, points = 5, periods = 5, missing = "notBreaching"
      dimensions = { DBInstanceIdentifier = var.db_instance_identifier }
      what       = "업무 RDS 스왑 > 100MiB 5분 연속"
    }
  }
}

resource "aws_appautoscaling_target" "airflow" {
  count              = local.stop_enabled ? 1 : 0
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.airflow.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  # 늘리는 주체는 CD(deploy-airflow)·운영자다. 이 장치는 0 으로 내리기만 한다.
  min_capacity = 0
  max_capacity = 1
}

resource "aws_appautoscaling_policy" "airflow_stop" {
  count              = local.stop_enabled ? 1 : 0
  name               = "${var.name}-stop"
  service_namespace  = aws_appautoscaling_target.airflow[0].service_namespace
  resource_id        = aws_appautoscaling_target.airflow[0].resource_id
  scalable_dimension = aws_appautoscaling_target.airflow[0].scalable_dimension
  policy_type        = "StepScaling"

  step_scaling_policy_configuration {
    adjustment_type = "ExactCapacity"
    cooldown        = 60
    # 위(Greater)·아래(Less) 방향 경보가 같은 정책을 쓴다 — 오프셋이 어느 쪽이든 0 대.
    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = 0
    }
    step_adjustment {
      metric_interval_lower_bound = 0
      scaling_adjustment          = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "stop" {
  for_each          = local.stop_enabled ? local.stop_alarms : {}
  alarm_name        = "${var.name}-stop-${replace(each.key, "_", "-")}"
  alarm_description = "중단 기준: ${each.value.what} → Airflow 서비스 desired 0(새 제출 중단, 도는 업무 태스크는 그대로). 할 일: README \"운영 전환·롤백 절차\"의 종료 확인 ①~⑥ 뒤 롤백(Airflow → SFN). 기준을 넘는 동안은 다시 켜도 0 으로 돌아간다."
  namespace         = each.value.namespace
  metric_name       = each.value.metric
  dimensions        = each.value.dimensions
  statistic         = each.value.statistic
  # 1분 지표 — period 60 이면 evaluation_periods 가 곧 "분"이다(modules/rds freeable_memory 주석과 같은 이유).
  period              = 60
  evaluation_periods  = each.value.periods
  datapoints_to_alarm = each.value.points
  threshold           = each.value.threshold
  comparison_operator = each.value.op
  treat_missing_data  = each.value.missing
  alarm_actions       = [aws_appautoscaling_policy.airflow_stop[0].arn, var.alarm_topic_arn]
  ok_actions          = [var.alarm_topic_arn]
}
