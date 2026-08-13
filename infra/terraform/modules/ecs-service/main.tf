# 재사용 가능한 Fargate ECS 서비스 모듈.
# 현재 호출자는 super-admin-api 하나다 (widget-api·gateway 은퇴, tenant-console-api 는
# onprem 플레인이라 dev ECS 제거 — ADR-0029·0032). 차이는 image·SG·자원 크기 등 변수로만 표현한다.

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

# 실행 역할: 이미지 pull·로그 쓰기·시크릿 읽기 (ECS 에이전트가 사용)
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# secrets 로 주입할 시크릿을 태스크 기동 시 읽을 수 있게. 비어 있으면 정책 없음.
# (RDS 관리형 시크릿은 기본 aws/secretsmanager 키라 GetSecretValue 로 충분. 향후
#  고객관리형(CMK) 시크릿이면 kms:Decrypt 도 추가 필요.)
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

# 태스크 역할: 앱이 런타임에 쓰는 AWS 권한
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# ⚠️ **앱이 실제로 부르는 API 만 적는다.** 정책을 코드보다 넓게 잡으면 안 쓰는 권한이 남고,
# 좁게 잡으면 AccessDenied 가 앱의 폴백에 삼켜져 리소스도 생기고 apply 도 초록인데 그 경로만
# 영영 안 돈다(ALPHA-671 선례). 비면 정책 자체를 안 만든다 — 빈 정책도 감사 대상이다.
resource "aws_iam_role_policy" "task" {
  count = length(var.task_policy_statements) > 0 ? 1 : 0

  name = "${var.name}-task"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [for s in var.task_policy_statements : {
      Effect   = "Allow"
      Action   = s.actions
      Resource = s.resources
    }]
  })
}

# ── 보안그룹 ────────────────────────────────────────────
# 백스톱: 엣지 오설정이 단일 실패점이 되지 않게 서비스 인바운드를 허용자(gateway SG)로 좁힌다.
resource "aws_security_group" "this" {
  name        = "${var.name}-svc"
  description = "ECS service ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-svc" }
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "allow all egress (image pull, DB, external)"
}

# 허용된 SG(예: gateway·ALB)에서만 컨테이너 포트로 인바운드. 비어 있으면 인바운드 없음.
# count 사용: SG id 가 같은 apply 에서 생성돼 값은 unknown 이어도 리스트 길이는 plan 시점에 정해짐.
resource "aws_vpc_security_group_ingress_rule" "from_sg" {
  count                        = length(var.ingress_security_group_ids)
  security_group_id            = aws_security_group.this.id
  referenced_security_group_id = var.ingress_security_group_ids[count.index]
  ip_protocol                  = "tcp"
  from_port                    = var.container_port
  to_port                      = var.container_port
  description                  = "from allowed caller SG"
}

# 특정 CIDR 직접 허용(테스트·내부망 등). 평상시 비워둠.
resource "aws_vpc_security_group_ingress_rule" "from_cidr" {
  for_each          = toset(var.ingress_cidr_blocks)
  security_group_id = aws_security_group.this.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = var.container_port
  to_port           = var.container_port
  description       = "from allowed CIDR"
}

# ── 태스크 정의 ─────────────────────────────────────────
locals {
  container = merge(
    {
      name      = var.name
      image     = var.container_image
      essential = true
      portMappings = [{
        name          = var.name # Service Connect 가 참조할 포트 이름
        containerPort = var.container_port
        protocol      = "tcp"
      }]
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
    },
    # 컨테이너 헬스체크는 이미지에 curl/wget 이 있어야 동작 → 기본 미설정(null).
    var.health_check_command == null ? {} : {
      healthCheck = {
        command     = var.health_check_command
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  )
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
}

# ── 서비스 ──────────────────────────────────────────────
resource "aws_ecs_service" "this" {
  name            = var.name
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.this.id]
    assign_public_ip = var.assign_public_ip
  }

  # Service Connect: 네임스페이스에 <name> 으로 등록 → gateway 가 http://<name>:<port> 로 호출
  service_connect_configuration {
    enabled   = true
    namespace = var.service_connect_namespace_arn

    service {
      port_name      = var.name
      discovery_name = var.name
      client_alias {
        port     = var.container_port
        dns_name = var.name
      }
    }
  }

  # ALB 타깃그룹 연결(선택). null 이면 LB 없이 Service Connect 만.
  dynamic "load_balancer" {
    for_each = var.target_group_arn == null ? [] : [1]
    content {
      target_group_arn = var.target_group_arn
      container_name   = var.name
      container_port   = var.container_port
    }
  }

  # LB 연결 시에만 유효. JVM 부팅 동안 헬스체크 실패로 죽지 않도록 유예.
  health_check_grace_period_seconds = var.target_group_arn == null ? null : var.health_check_grace_period_seconds

  enable_execute_command = var.enable_execute_command

  # task_definition 은 생성 후 CD(deploy-app.yml)가 소유한다 — CD 가 ECR semver 이미지로 새 리비전을
  # 등록해 서비스를 롤링 업데이트하므로, TF 는 실행 리비전을 되돌리지 않는다(위 aws_ecs_task_definition 은
  # baseline 으로만 남음). 없으면 매 apply 가 CD 배포를 tfvars 핀으로 롤백한다(ECS+외부 CD 표준 패턴).
  lifecycle {
    ignore_changes = [task_definition]
  }
}
