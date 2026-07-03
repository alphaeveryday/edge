# 스키마 마이그레이션 one-off 실행기 (ECS Fargate task).
#
# GitHub-hosted 러너는 VPC 밖이라 private RDS 에 직접 접속할 수 없다. 그래서 Flyway 는
# 이 VPC 내부 task 에서 실행한다. GitHub Actions 는 OIDC 로 인증해 이 task 를 RunTask 로
# 트리거하기만 한다(별도 github-oidc-deploy 모듈이 그 권한을 만든다).
#
# 이 모듈은 "상시 서비스"가 아니라 실행할 task 정의만 만든다(aws_ecs_service 없음).

# 마이그레이션 이미지 ECR 은 foundation(edge/schema-migrate)이 소유한다 — 모든 이미지 레포를
# 한 곳에 모으는 규약(ADR-0009). 이 모듈은 그 repo 의 URL/ARN 을 입력으로 받아 참조만 한다.

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

# ── IAM ────────────────────────────────────────────────
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# 실행 역할: 이미지 pull·로그 쓰기·DB 비밀번호 시크릿 읽기 (ECS 에이전트가 사용).
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# 비밀번호 시크릿을 task 기동 시 읽기 위한 최소 권한.
# (RDS 관리형 시크릿은 기본 aws/secretsmanager 키라 GetSecretValue 로 충분. CMK 면 kms:Decrypt 추가.)
resource "aws_iam_role_policy" "execution_secret" {
  name = "${var.name}-secret"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [var.flyway_password_secret_arn]
    }]
  })
}

# 태스크 역할: 마이그레이션 컨테이너 자체는 AWS API 를 호출하지 않으므로 비워 둔다.
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# ── 보안그룹 ────────────────────────────────────────────
# egress 는 이미지 pull(ECR/NAT)·RDS 접속을 위해 전체 허용. RDS 5432 인바운드 허용은
# 호출부(env)가 이 SG 를 RDS 인바운드로 거는 방식(순환 의존 회피, rds 모듈 주석 참고).
resource "aws_security_group" "this" {
  name        = "${var.name}-task"
  description = "schema migration one-off task ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "allow all egress (ECR image pull, RDS)"
}

# ── 태스크 정의 ─────────────────────────────────────────
# Flyway CLI 이미지. locations/정책 플래그는 이미지 안 flyway.conf 에 있고(libs/schema/build.gradle 와 미러),
# 접속값만 env/secret 으로 주입한다. command 로 migrate 를 명시한다.
locals {
  # 부트스트랩 이미지(저장소 생성 직후엔 아직 없음). 워크플로가 실제 SQL 이미지를 push 하고
  # 새 task 정의 리비전을 등록해 실행하므로, 여기 값은 최초 apply 를 통과시키는 placeholder 다.
  bootstrap_image = "${var.ecr_repository_url}:bootstrap"

  container = {
    name      = var.name
    image     = local.bootstrap_image
    essential = true
    command   = ["migrate"]
    environment = [
      { name = "FLYWAY_URL", value = var.flyway_url },
      { name = "FLYWAY_USER", value = var.flyway_user },
    ]
    # RDS 관리형 시크릿은 {username,password} JSON. ':password::' 로 password 필드만 주입한다.
    # (IAM 정책 Resource 는 base ARN 이어야 한다 — ECS 에이전트는 접미사를 떼고 GetSecretValue 호출.)
    secrets = [
      { name = "FLYWAY_PASSWORD", valueFrom = "${var.flyway_password_secret_arn}:password::" },
    ]
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

  # 워크플로가 새 이미지로 새 리비전을 등록·실행하므로, TF 는 image 변경을 무시해
  # 매 배포마다 apply 가 필요하지 않게 한다(TF 는 base 구성만 소유).
  lifecycle {
    ignore_changes = [container_definitions]
  }
}
