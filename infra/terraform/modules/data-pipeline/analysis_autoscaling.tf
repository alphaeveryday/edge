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

# 잔여 일감 = **가시 + 처리중(비가시)** 이다. 둘을 더하는 것이 이 알람의 핵심이다.
# ⚠️ 가시 수만 보면 **마지막 한 건이 영영 안 끝난다**: 소비자가 메시지를 집는 순간
# 비가시가 돼 가시 깊이가 0 이 되고, 알람이 OK 로 전이하면 `ok_actions` 가 **매분** 0 대를
# 써서 그 건을 처리 중이던 태스크를 죽인다(Fargate `stopTimeout` 상한 120초 < 건당 588초).
# 900초 뒤 재배달되면 같은 순환이 반복돼 `maxReceiveCount` 16 을 소진하고 DLQ 로 간다.
# 단발 트리거와 버스트 꼬리가 전부 여기 걸린다. 더하면 인플라이트가 남아 있는 한 대수가
# 0 으로 안 내려가고, 밤에는 둘 다 0 이라 `min_capacity = 0` 의 비용 근거는 그대로다.
# ⚠️ 그래도 **스케일인 자체는 처리 중인 건을 자른다**(4→2 면 두 건이 잘려 900초 뒤
# 재배달·LLM 재과금). Fargate 가 `stopTimeout` 을 120초로 묶어 터미널에서 못 막는다 —
# 태스크 스케일인 보호(소비자가 처리 중임을 ECS 에 알리는 것)는 코드라 후속 PR 이다.
resource "aws_cloudwatch_metric_alarm" "analysis_backlog" {
  alarm_name        = "${var.name}-analysis-backlog"
  alarm_description = "설명 큐 잔여 일감(가시+처리중) — analysis-consumer 대수를 정하는 유일한 입력(ALPHA-912)."

  metric_query {
    id          = "backlog"
    expression  = "visible + inflight"
    label       = "설명 큐 잔여 일감"
    return_data = true
  }

  metric_query {
    id = "visible"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      dimensions  = { QueueName = aws_sqs_queue.minute["price-explanation-realtime"].name }
      period      = 60
      stat        = "Maximum"
    }
  }

  metric_query {
    id = "inflight"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      dimensions  = { QueueName = aws_sqs_queue.minute["price-explanation-realtime"].name }
      period      = 60
      stat        = "Maximum"
    }
  }

  evaluation_periods = 1
  # ⚠️ 임계는 **계단의 원점**이다 — step 의 bound 가 "지표값"이 아니라 `지표값 - threshold`
  # 오프셋이라서. 그리고 AWS 는 지표가 임계 **아래**일 때 bound 의 포함/배제를 뒤집는다
  # (아래면 lower 배제·upper 포함, 위면 그 반대). 정수로 두면 어떤 깊이의 오프셋이 bound 에
  # 정확히 얹혀 **그 뒤집힘이 대수를 가른다** — 예컨대 임계 1 에서 깊이 1 은 breach 가
  # 아니라 오프셋 0 이 `(-∞,0]` 에 걸려 0 대가 된다(의도는 1 대). 0.5 로 두면 모든 깊이의
  # 오프셋이 bound 사이에 떨어져 그 규칙과 **무관**해지고, 깊이 0 은 -0.5 로 0 대에 닿는다.
  threshold           = 0.5
  comparison_operator = "GreaterThanThreshold"
  # 큐가 비면 SQS 는 지표를 안 보내는 게 아니라 0 을 보낸다 — missing 은 사실상 안 생기지만,
  # 생긴다면 "모름"이지 "일감 있음"이 아니다(올리는 쪽으로 틀리면 야간에 대수가 남는다).
  treat_missing_data = "notBreaching"

  # 오토스케일링 액션은 **상태가 바뀔 때만이 아니라 그 상태에 머무는 동안 매분** 다시
  # 불린다(CloudWatch 계약 — SNS 액션과 다른 점). 그래서 버스트가 커지는 동안 ALARM 을
  # 유지해도 계단이 재평가돼 대수가 따라 오른다. 한 번 걸리고 마는 게 아니다.
  alarm_actions = [aws_appautoscaling_policy.analysis_scale.arn]
  # ⚠️ OK 액션에도 같은 정책을 건다 — 계단의 하한(잔여 0 → 0대)은 알람이 **풀릴 때**
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
    # ⚠️ bound 는 지표값이 아니라 **`지표값 - threshold(0.5)` 오프셋**이다. 아래 구간이
    # 실제로 뜻하는 잔여 일감(가시+처리중):
    #   (-∞,0)  → 0건         → 0대   (버스트 종료 · 야간)
    #   [0,5)   → 1~5건       → 1대
    #   [5,15)  → 6~15건      → 2대
    #   [15,30) → 16~30건     → 3대
    #   [30,∞)  → 31건 이상   → 4대
    # 임계가 반정수라 오프셋(n-0.5)이 bound 위에 얹히지 않는다 — 포함/배제 규칙이 뒤집혀도
    # 같은 대수가 나온다(계약 테스트가 두 해석을 모두 대조한다).
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
