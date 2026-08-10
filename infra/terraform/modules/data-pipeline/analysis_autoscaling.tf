# 설명 소비자 오토스케일링 (ALPHA-912) — 큐 깊이로 analysis-consumer 대수를 정한다.
#
# **왜**: 트리거는 개장 전후 한 번의 **버스트**로 오는데(실측 일 최대 대기 42~65) 건당 처리가
# p50 588초라, 1대로는 배출에 6시간 넘게 걸려 **장이 끝나도 설명이 안 나온다**. 대수를 늘리면
# 그대로 나눠진다(직렬 처리라 선형) — 40건 기준 1대 6.5시간 → 4대 1.6시간.
#
# ⚠️ **상한은 성능이 원하는 수가 아니라 공유 DB 가 견디는 수가 정한다.** 2026-08-10 에
# 다른 세션이 수동으로 12대를 올렸다가 db.t4g.micro 가 메모리로 죽었다(당일 2회). 그래서
# `max_capacity` 는 보수적으로 시작하고 실측으로 올린다 — 지금 값은 ALPHA-924 로 상향된
# db.t4g.small(2GB, micro 의 2배) 기준의 **잠정치**다. 소비자는 처리 건당 DB 커넥션 1개를
# 쓴다(`EventStore.connect` 하나 — `connect_readonly` 는 이 경로에 없다).
# ⚠️ 이 리소스는 스케일업 장치이자 **가드레일**이다. 지금까지는 상한이 없어 수동
# `update-service` 가 DB 를 넘어뜨릴 수 있었다.
#
# ⚠️ **세션 오케스트레이션과 당분간 공존한다**(ALPHA-910). 세션은 여전히 07:45 에 1,
# 20:05 에 0 을 쓰므로 그 **두 순간에만** 스케일러와 부딪히고, 스케일러가 다음 평가에서
# 큐 깊이대로 되돌린다. 세션의 손을 떼는 것은 후속 PR 이다 — 여기서 함께 떼면 이미지 CD 가
# apply 보다 먼저 착지한 날 **아무도 desired 를 안 올려** 그날 설명이 통째로 없다
# (두 워크플로가 각자 `push: dev` 로 도는 독립 워크플로다).

locals {
  # appautoscaling 의 resource_id 는 클러스터 **이름**을 요구한다(ARN 이 아니다).
  # 모듈은 ARN 만 받으므로 여기서 가른다 — 이름을 변수로 하나 더 받으면 둘이 갈릴 수 있다.
  analysis_cluster_name = split("/", var.cluster_arn)[1]

  analysis_scalable_id = "service/${local.analysis_cluster_name}/${aws_ecs_service.analysis_consumer.name}"
}

resource "aws_appautoscaling_target" "analysis_consumer" {
  service_namespace  = "ecs"
  resource_id        = local.analysis_scalable_id
  scalable_dimension = "ecs:service:DesiredCount"

  # min 0 — 실측상 버스트 배출 뒤 큐가 9시간 넘게 완전히 빈다. 야간·유휴 비용을 0 으로 둔다.
  # 첫 메시지에 알람 60초 + 기동 ~2분이 붙지만, 건당 처리가 10분대라 무시할 만하다.
  min_capacity = 0
  max_capacity = var.analysis_consumer_max_capacity
}

# 큐 깊이 = **가시 메시지 수**다. 처리 중인 메시지와 ReturnsNotReady 로 미룬 메시지는
# 비가시라 여기 안 잡힌다 — 이미 붙잡고 있는 일을 보고 또 올리는 헛 스케일업이 없다.
# ⚠️ `NotVisible` 을 더하면 안 된다: 처리 중 1건이 곧 "일이 남았다"로 읽혀 배출이 끝난
# 뒤에도 대수가 안 내려간다.
resource "aws_cloudwatch_metric_alarm" "analysis_backlog" {
  alarm_name        = "${var.name}-analysis-backlog"
  alarm_description = "설명 큐 대기 깊이 — analysis-consumer 대수를 정하는 유일한 입력(ALPHA-912)."
  namespace         = "AWS/SQS"
  metric_name       = "ApproximateNumberOfMessagesVisible"
  dimensions        = { QueueName = aws_sqs_queue.minute["price-explanation-realtime"].name }

  statistic          = "Maximum"
  period             = 60
  evaluation_periods = 1
  # ⚠️ 임계는 **계단의 원점**이다 — step 의 bound 가 "지표값"이 아니라 `지표값 - threshold`
  # 오프셋이라서. 0 으로 두면 깊이 0 의 오프셋이 0 이 돼 첫 구간 `[0,5)` 에 걸려 **1대로
  # 내려가고 0 대에 영영 못 간다**(min 0 의 비용 근거가 사라진다). 1 로 두면 깊이 0 이
  # 오프셋 -1 이 돼 `(-∞,0)` 구간, 즉 0 대에 닿는다.
  threshold           = 1
  comparison_operator = "GreaterThanThreshold"
  # 큐가 비면 SQS 는 지표를 안 보내는 게 아니라 0 을 보낸다 — missing 은 사실상 안 생기지만,
  # 생긴다면 "모름"이지 "일감 있음"이 아니다(올리는 쪽으로 틀리면 야간에 대수가 남는다).
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_appautoscaling_policy.analysis_scale.arn]
  # ⚠️ OK 액션에도 같은 정책을 건다 — 계단의 하한(대기 0 → 0대)은 알람이 **풀릴 때**
  # 평가된다. 안 걸면 버스트 뒤 대수가 그대로 남아 min 0 의 비용 근거가 사라진다.
  ok_actions = [aws_appautoscaling_policy.analysis_scale.arn]
}

resource "aws_appautoscaling_policy" "analysis_scale" {
  name               = "${var.name}-analysis-backlog-steps"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.analysis_consumer.service_namespace
  resource_id        = aws_appautoscaling_target.analysis_consumer.resource_id
  scalable_dimension = aws_appautoscaling_target.analysis_consumer.scalable_dimension

  step_scaling_policy_configuration {
    # ExactCapacity — 대기 깊이가 곧 목표 대수를 정한다. Change/PercentChange 는 직전
    # 대수에 상대적이라, 알람이 여러 번 울리는 동안 같은 깊이에서도 값이 계속 움직인다.
    adjustment_type = "ExactCapacity"
    # 기동이 59~122초 걸린다(ECS Fargate 실측) — 그보다 짧게 두면 아직 안 뜬 태스크를
    # 못 보고 같은 깊이에 또 올린다.
    cooldown = 180
    # ⚠️ bound 는 지표값이 아니라 **`지표값 - threshold(1)` 오프셋**이다. 아래 구간이
    # 실제로 뜻하는 깊이:
    #   (-∞,0)  → 깊이 0        → 0대   (버스트 종료 · 야간)
    #   [0,5)   → 깊이 1~5      → 1대
    #   [5,15)  → 깊이 6~15     → 2대
    #   [15,30) → 깊이 16~30    → 3대
    #   [30,∞)  → 깊이 31 이상  → 4대
    # 실측 p50 588초 기준 배출 시간(40건): 1대 6.5시간 · 2대 3.3시간 · 3대 2.2시간 · 4대 1.6시간.
    # 알람 statistic 과 **같은 집계**여야 한다 — 갈리면 계단이 다른 값으로 판정된다.
    metric_aggregation_type = "Maximum"

    # 깊이 0 — 알람이 풀린 상태(ok_actions 가 이 평가를 부른다). 버스트가 끝났으니 0 으로.
    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = 0
    }
    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 5
      scaling_adjustment          = 1
    }
    step_adjustment {
      metric_interval_lower_bound = 5
      metric_interval_upper_bound = 15
      scaling_adjustment          = 2
    }
    step_adjustment {
      metric_interval_lower_bound = 15
      metric_interval_upper_bound = 30
      scaling_adjustment          = 3
    }
    step_adjustment {
      metric_interval_lower_bound = 30
      scaling_adjustment          = 4
    }
  }
}
