# 1분 파이프라인 상주 실행체 (ALPHA-711) — 큐 4종 + 상주 서비스 3종.
#
# SFN 단발 task 와 달리 **ECS Service** 다: Worker(수집)·Relay(outbox 발행)·
# Consumer(가격 판정)는 tick 루프 상주 프로세스고, 재기동 책임이 ECS 에 있다
# (DB 오류는 프로세스가 죽어서 드러낸다 — 각 *_cli 계약).
# news-worker 는 프로덕션 feed 부재로 제외(ALPHA-707 — BigKinds 승인 선행).
#
# ⚠️ desired_count 는 **세션 오케스트레이션(SFN)이 런타임에 바꾸는 값**이다 —
# lifecycle ignore_changes 가 없으면 무관한 apply 가 장중에 워커를 내린다.
# 초기값 0: 큐·서비스 정의가 먼저 착지하고, 스케일은 오케스트레이션 소관이다.

locals {
  # 큐 어휘 — jobs.py DESTINATION_JOB_KINDS(3종) + TRIGGER_EVENT_DESTINATIONS(1종)와
  # 같은 이름이어야 한다(relay 기동 검증 KNOWN_DESTINATIONS 가 4종 전부를 요구한다).
  minute_job_destinations = ["price-analysis-realtime", "news-extraction-realtime", "news-extraction-backfill"]
  minute_all_destinations = concat(local.minute_job_destinations, ["price-explanation-realtime"])

  # universe 정본 객체 — planner·worker·consumer 가 **같은 URI** 를 봐야 세 표면의
  # universe(version·hash)가 한 곳에서 나온다. ⚠️ 이 객체의 생산 파이프라인은 아직
  # 없다(ALPHA-711 범위 밖) — 객체가 없으면 worker/consumer 는 기동 시 fail-loud 다.
  minute_universe_uri = (
    var.minute_universe_uri != "" ? var.minute_universe_uri
    : "s3://${var.lake_bucket_name}/config/minute/universe.json"
  )

  minute_queue_urls = {
    for name in local.minute_all_destinations : name => aws_sqs_queue.minute[name].url
  }
}

# ── SQS — 원 큐 4종 + DLQ ──────────────────────────────────────────────
# maxReceiveCount 는 **transport 상한**이다 — 논리 재시도 예산(DB, max_attempts=5)보다
# 넉넉해야 DB 가 재시도의 권위로 남는다(v0.7 12.4 — 반대면 transport 가 먼저 포기).
resource "aws_sqs_queue" "minute_dlq" {
  for_each = toset(local.minute_all_destinations)

  name = "${var.name}-${each.key}-dlq"
  # DLQ 는 근거 보존소다 — 대사(dlq-reconcile)는 메시지를 지우지 않으므로 보존을
  # 최대(14일)로 둬 사람이 볼 시간을 확보한다.
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "minute" {
  for_each = toset(local.minute_all_destinations)

  name                       = "${var.name}-${each.key}"
  visibility_timeout_seconds = 300 # Consumer visibility 기본과 일치(ConsumerConfig)
  message_retention_seconds  = 345600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.minute_dlq[each.key].arn
    maxReceiveCount     = 8
  })
}

# ── 토스 자격증명 그릇 — 값은 운영자가 CLI 로 주입한다(state 에 평문 금지) ──
resource "aws_secretsmanager_secret" "toss" {
  name = "${var.name}-toss"
}

# ── 상주 서비스 3종 ────────────────────────────────────────────────────
locals {
  minute_services = {
    price-worker = {
      command = ["price-worker", "--universe", local.minute_universe_uri]
      environment = merge(local.env, local.db_env, {
        DATA_PIPELINE_MINUTE_PRICE_WORKER__TRIGGER_SCHEMA_VERSION = var.minute_trigger_schema_version
      })
      secrets = {
        DATA_PIPELINE_DB__PASSWORD                       = "${var.db_password_secret_arn}:password::"
        DATA_PIPELINE_MINUTE_PRICE_WORKER__CLIENT_ID     = "${aws_secretsmanager_secret.toss.arn}:client_id::"
        DATA_PIPELINE_MINUTE_PRICE_WORKER__CLIENT_SECRET = "${aws_secretsmanager_secret.toss.arn}:client_secret::"
      }
    }
    relay = {
      command = ["relay"]
      environment = merge(local.env, local.db_env, {
        # JSON 한 변수 — destination 이름에 하이픈이 있어 nested env 형태는 셸·로더
        # 어느 쪽도 못 받는다(MinuteRelayConfig docstring). 4종 전부 필수다.
        DATA_PIPELINE_MINUTE_RELAY__QUEUE_URLS = jsonencode(local.minute_queue_urls)
      })
      secrets = {
        DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
      }
    }
    price-consumer = {
      command = ["price-consumer", "--universe", local.minute_universe_uri]
      environment = merge(local.env, local.db_env, {
        DATA_PIPELINE_MINUTE_PRICE_CONSUMER__QUEUE_URL                = aws_sqs_queue.minute["price-analysis-realtime"].url
        DATA_PIPELINE_MINUTE_PRICE_CONSUMER__DETECTION_POLICY_VERSION = var.minute_detection_policy_version
      })
      secrets = {
        DATA_PIPELINE_DB__PASSWORD = "${var.db_password_secret_arn}:password::"
      }
    }
  }
}

resource "aws_ecs_task_definition" "minute" {
  for_each = local.minute_services

  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name        = local.container_name
    image       = var.image
    essential   = true
    command     = each.value.command
    environment = [for k, v in each.value.environment : { name = k, value = v }]
    secrets     = [for k, v in each.value.secrets : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = local.log_options
    }
  }])
}

resource "aws_ecs_service" "minute" {
  for_each = local.minute_services

  name            = "${var.name}-${each.key}"
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.minute[each.key].arn
  desired_count   = 0
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = false
  }

  lifecycle {
    # desired_count: SFN 오케스트레이션이 런타임에 바꾼다 — 없으면 무관한 apply 가
    #   장중에 워커를 내린다(ALPHA-711 의 존재 이유).
    # task_definition: 이미지 CD 가 새 revision 을 등록한다(ecs-service 모듈 동형).
    ignore_changes = [desired_count, task_definition]
  }
}

# ── IAM — 새 큐에 대한 최소 권한 ───────────────────────────────────────
resource "aws_iam_role_policy" "minute_queues" {
  name = "minute-queues"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Relay 발행 — 원 큐 4종
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = [for q in aws_sqs_queue.minute : q.arn]
      },
      {
        # Consumer 소비(가격 job 큐) + DLQ 대사(job DLQ 3종 — 조회만, 삭제는 배선
        # 오류 정리 케이스뿐이지만 같은 API 라 함께 허용)
        Effect = "Allow"
        Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
        Resource = concat(
          [aws_sqs_queue.minute["price-analysis-realtime"].arn],
          [for name in local.minute_job_destinations : aws_sqs_queue.minute_dlq[name].arn],
        )
      },
    ]
  })
}
