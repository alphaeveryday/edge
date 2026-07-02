# 스케줄 배치 태스크 (EventBridge Scheduler → Fargate RunTask).
#
# ecs-service 는 상시 서비스(ALB·Service Connect)라 배치성 워커(data-pipeline)에는
# 맞지 않는다. 이 모듈은 태스크 정의 + 스케줄만 만들고 aws_ecs_service 는 만들지 않는다.
# 하나의 태스크 정의를 여러 스케줄이 command 오버라이드로 나눠 쓴다
# (예: ingest_raw 와 normalize 는 같은 이미지의 다른 진입 명령).

resource "aws_ecr_repository" "this" {
  # 앱 이미지 저장소는 edge/<앱> 네임스페이스(widget-api 등과 동일) — 리소스 이름과 분리.
  name                 = coalesce(var.ecr_repository_name, var.name)
  image_tag_mutability = var.image_tag_mutability

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

# ── IAM (태스크) ────────────────────────────────────────
data "aws_iam_policy_document" "assume_task" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# 실행 역할: 이미지 pull·로그 쓰기·시크릿 읽기 (ECS 에이전트가 사용)
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.assume_task.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# secrets 로 주입할 시크릿을 태스크 기동 시 읽을 수 있게. 비어 있으면 정책 없음.
resource "aws_iam_role_policy" "execution_secrets" {
  count = length(var.secret_arns) > 0 ? 1 : 0
  name  = "${var.name}-secrets"
  role  = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.secret_arns
    }]
  })
}

# 태스크 역할: 앱이 런타임에 쓰는 AWS 권한. 정책 내용(예: 레이크 프리픽스 R/W)은
# 호출부(env)가 JSON 으로 넘긴다 — 모듈은 대상 리소스를 모른다.
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume_task.json
}

resource "aws_iam_role_policy" "task" {
  count  = var.task_policy_json == null ? 0 : 1
  name   = "${var.name}-task"
  role   = aws_iam_role.task.id
  policy = var.task_policy_json
}

# ── 보안그룹 ────────────────────────────────────────────
# 배치 태스크라 인바운드 없음. egress 는 이미지 pull(ECR/NAT)·외부 API 호출용 전체 허용.
resource "aws_security_group" "this" {
  name        = "${var.name}-task"
  description = "scheduled batch task ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "allow all egress (image pull, external API, S3)"
}

# ── 태스크 정의 ─────────────────────────────────────────
locals {
  # 부트스트랩 이미지(저장소 생성 직후엔 아직 없음). 배포 파이프라인이 실제 이미지를
  # push 하고 새 리비전을 등록하므로, 최초 apply 를 통과시키는 placeholder 다
  # (schema-migrate 와 같은 패턴).
  bootstrap_image = "${aws_ecr_repository.this.repository_url}:bootstrap"

  container = {
    name        = var.name
    image       = local.bootstrap_image
    essential   = true
    environment = [for k, v in var.environment : { name = k, value = v }]
    secrets     = [for k, v in var.secrets : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([local.container])

  # 배포 파이프라인이 새 이미지로 새 리비전을 등록하므로 TF 는 base 구성만 소유.
  lifecycle {
    ignore_changes = [container_definitions]
  }
}

# ── IAM (스케줄러) ──────────────────────────────────────
data "aws_iam_policy_document" "assume_scheduler" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.assume_scheduler.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.name}-scheduler"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # 배포 파이프라인이 새 리비전을 등록하므로 리비전 와일드카드로 허용.
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = ["${aws_ecs_task_definition.this.arn_without_revision}:*"]
        Condition = {
          ArnEquals = { "ecs:cluster" = var.cluster_arn }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
    ]
  })
}

# ── 스케줄 ──────────────────────────────────────────────
resource "aws_scheduler_schedule" "this" {
  for_each = var.schedules

  name  = "${var.name}-${each.key}"
  state = each.value.enabled ? "ENABLED" : "DISABLED"

  schedule_expression          = each.value.schedule_expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = var.cluster_arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      # 리비전 없는 ARN → 항상 최신 ACTIVE 리비전을 실행(배포 후 재-apply 불필요).
      task_definition_arn = aws_ecs_task_definition.this.arn_without_revision
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = var.subnet_ids
        security_groups  = [aws_security_group.this.id]
        assign_public_ip = false
      }
    }

    # RunTask 오버라이드 — 스케줄마다 실행 명령만 다르다.
    input = jsonencode({
      containerOverrides = [{
        name    = var.name
        command = each.value.command
      }]
    })
  }
}
