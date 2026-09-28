# Application Auto Scaling, 평균 CPU target tracking. 첫 호출자는 app-api(ADR-0056).
# resource_id 는 클러스터 **이름**을 요구한다(ARN 아님, data-pipeline 모듈과 같은 함정).
locals {
  cluster_name = element(split("/", var.cluster_arn), length(split("/", var.cluster_arn)) - 1)
}

resource "aws_appautoscaling_target" "this" {
  count = var.autoscaling == null ? 0 : 1

  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${local.cluster_name}/${aws_ecs_service.this.name}"
  min_capacity       = var.autoscaling.min_capacity
  max_capacity       = var.autoscaling.max_capacity
}

resource "aws_appautoscaling_policy" "cpu" {
  count = var.autoscaling == null ? 0 : 1

  name               = "${var.name}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.this[0].service_namespace
  scalable_dimension = aws_appautoscaling_target.this[0].scalable_dimension
  resource_id        = aws_appautoscaling_target.this[0].resource_id

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = var.autoscaling.cpu_target_percent
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
