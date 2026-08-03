# 1분 파이프라인 상주 실행체 (ALPHA-711) — 큐 4종 + 상주 서비스 3종.
#
# SFN 단발 task 와 달리 **ECS Service** 다: Worker(수집)·Relay(outbox 발행)·
# Consumer(가격 판정)는 tick 루프 상주 프로세스고, 재기동 책임이 ECS 에 있다
# (DB 오류는 프로세스가 죽어서 드러낸다 — 각 *_cli 계약).
# news-worker 는 프로덕션 feed 부재로 제외(ALPHA-707 — BigKinds 승인 선행).
#
# ⚠️ desired_count 는 **세션 오케스트레이션이 런타임에 바꾸는 값**이다 —
# lifecycle ignore_changes 가 없으면 무관한 apply 가 장중에 워커를 내린다.
# 초기값 0: 큐·서비스 정의가 먼저 착지하고, 스케일은 오케스트레이션 소관이다.
# 그 주체는 이 파일 아래의 `aws_scheduler_schedule.minute_session` 이다(ALPHA-712).

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
    # ⚠️ receive 가 곧 실행이 아니다 — lease(600) > visibility(300) 라 교대 receive 가
    # contended 로 소비돼 실행 attempt 는 receive 의 절반꼴이다. 8 이면 DB 예산(5)이
    # 권위가 되기 전에 transport 가 먼저 포기한다(v0.7 12.4 위반).
    maxReceiveCount = 16
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
    name      = local.container_name
    image     = var.image
    essential = true
    # SIGTERM 후 in-flight(LLM 없는 판정이라도 S3·DB 왕복)를 끝낼 시간 — 기본 30초는
    # close() 계약(끝까지 기다린다)을 강제 종료로 자를 수 있다. Fargate 상한 120.
    stopTimeout = 120
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
    # desired_count 만 무시한다 — 세션 오케스트레이션이 런타임에 바꾸는 값이라, 없으면
    # 무관한 apply 가 장중에 워커를 내린다(ALPHA-711 의 존재 이유).
    # ⚠️ task_definition 은 무시하지 **않는다** — ecs-service 모듈과 달리 이 서비스들의
    # CD(deploy-data-pipeline.yml)는 revision 을 등록하지 않고 mutable 태그를
    # force-new-deployment 로 재당길 뿐이라, terraform 이 task-def 의 유일한 author 다.
    # 무시하면 명령·env·시크릿 변경이 apply 돼도 서비스에 영영 반영되지 않는다.
    ignore_changes = [desired_count]
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

# ── 세션 스케일 오케스트레이션 (ALPHA-712) ─────────────────────────────
# 위 서비스들의 desired_count 는 ignore_changes 로 terraform 이 손을 뗀 값이다 — 이 아래가
# 그 값을 바꾸는 **유일한 주체**다. 실행체는 EventBridge Scheduler → ECS RunTask 로,
# daily·news·reconcile 스케줄과 같은 형태다(근거는 session_ops.py 모듈 docstring).

# 전용 역할이다 — 공용 `aws_iam_role.task` 에 붙이면 **모든 수집·정제 배치 task-def**
# (`aws_ecs_task_definition.this`)가 상주 서비스를 내릴 권한을 함께 갖는다. 권한 자체는
# 3종 서비스로 좁혀도, 그것을 행사할 수 있는 실행체가 레인 밖까지 넓어진다(analysis_task 선례).
resource "aws_iam_role" "minute_session" {
  name               = "${var.name}-minute-session"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "minute_session" {
  name = "${var.name}-minute-session"
  role = aws_iam_role.minute_session.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # universe 정본 읽기 — plan 단계가 window 범위와 universe_hash 를 여기서 뽑는다.
        # 쓰기는 없다(이 태스크는 레이크에 아무것도 안 만든다).
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [var.lake_bucket_arn, "${var.lake_bucket_arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:UpdateService"]
        Resource = [for service in aws_ecs_service.minute : service.id]
      },
      {
        # 내리기 전 큐 깊이 확인 — 소비자를 함께 내리는 큐 하나뿐이다.
        Effect   = "Allow"
        Action   = ["sqs:GetQueueAttributes"]
        Resource = [aws_sqs_queue.minute["price-analysis-realtime"].arn]
      },
    ]
  })
}

resource "aws_ecs_task_definition" "minute_session" {
  family                   = "${var.name}-minute-session"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.minute_session.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name      = local.container_name
    image     = var.image
    essential = true
    # stop 쪽은 원장 게이트가 빌 때까지 폴링한다 — SIGTERM 으로 중간에 끊기면 서비스가
    # 안 내려간 채 끝나므로, 그 판단이 끝날 시간을 준다(Fargate 상한 120).
    stopTimeout = 120
    # command 는 스케줄 target 이 덮는다(start/stop). 기본값은 실수로 뜬 태스크가
    # 아무것도 안 하도록 계획만 하는 쪽으로 둔다.
    command = ["start-minute-session"]
    environment = [for k, v in merge(local.env, local.db_env, {
      # 비거래일 판정 — Planner·KRX·KIS 와 **같은** 공휴일 집합이어야 "Planner 는 쉬는
      # 날로 건너뛴 날에 1분 세션만 뜬다"는 모순이 안 생긴다.
      OPS_KR_HOLIDAYS = join(",", var.kr_holidays)

      MINUTE_SESSION_CLUSTER = var.cluster_arn
      # 서비스명을 코드에서 다시 조립하지 않는다 — rename 이 조용한 no-op 스케일링이 된다.
      MINUTE_SESSION_SERVICES = join(",", [for service in aws_ecs_service.minute : service.name])
      # 내리기 전에 비어야 하는 큐. 소비자를 함께 내리는 큐만 센다 — 소비자가 없는
      # 큐(price-explanation-realtime, ALPHA-710 대기)를 넣으면 게이트가 영영 안 빈다.
      MINUTE_SESSION_GATE_QUEUES = aws_sqs_queue.minute["price-analysis-realtime"].url
    }) : { name = k, value = v }]
    secrets = [{
      name = "DATA_PIPELINE_DB__PASSWORD", valueFrom = "${var.db_password_secret_arn}:password::"
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-stream-prefix" = "minute-session" })
    }
  }])
}

locals {
  minute_session_schedules = {
    start = {
      expression = var.minute_session_start_expression
      command = ["start-minute-session",
        "--dataset", var.minute_session_dataset,
        "--source-group", var.minute_session_source_group,
      "--universe", local.minute_universe_uri]
    }
    stop = {
      expression = var.minute_session_stop_expression
      command = ["stop-minute-session",
        "--dataset", var.minute_session_dataset,
      "--source-group", var.minute_session_source_group]
    }
  }
}

resource "aws_scheduler_schedule" "minute_session" {
  for_each = local.minute_session_schedules

  name                         = "${var.name}-minute-session-${each.key}"
  state                        = var.minute_session_schedule_state
  schedule_expression          = each.value.expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      Cluster        = var.cluster_arn
      TaskDefinition = aws_ecs_task_definition.minute_session.arn
      LaunchType     = "FARGATE"
      NetworkConfiguration = {
        AwsvpcConfiguration = {
          Subnets        = var.subnet_ids
          SecurityGroups = [aws_security_group.task.id]
          AssignPublicIp = "DISABLED"
        }
      }
      Overrides = {
        ContainerOverrides = [{
          Name    = local.container_name
          Command = each.value.command
        }]
      }
    })

    # 재시도는 **컨테이너 기동 실패**만 덮는다 — 스케줄러는 RunTask **제출**까지만 보므로
    # 컨테이너가 뜬 뒤의 exit≠0(DB 장애로 start 가 2, 상한 초과로 stop 이 1)은 스케줄러엔
    # 성공으로 보인다. ⚠️ 그 공백을 메울 백스톱이 이 레인엔 아직 없다(daily 레인은
    # Reconciler 가 메운다) — start 가 그렇게 실패하면 **그 날은 통째로 안 돈다**.
    # 지금의 신호는 컨테이너 로그와 desired_count 뿐이다.
    # 상한을 짧게 두는 이유: start 는 개장 전에 떠야 의미가 있고, stop 의 늦은 재시도는
    # 이미 지난 세션을 상대로 돌아 게이트가 비자마자 내려 무해하다.
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 5
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dlq.arn }
  }
}
