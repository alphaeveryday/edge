# 에이전트 읽기전용 DB 질의 one-off 실행기 (ECS Fargate task).
#
# private RDS 는 VPC 밖에서 접속할 수 없다. 에이전트/운영자도 GitHub 러너와 같은 처지라
# 질의를 VPC 내부 task 에서 실행한다 — schema-migrate 가 Flyway 로 같은 제약을 푸는 방식과 동일.
# 호출부는 RunTask 로 이 task 정의를 띄우고 CMD args 만 덮는다.
#
# 이 모듈은 "상시 서비스"가 아니라 실행할 task 정의만 만든다(aws_ecs_service 없음).

# 이미지는 분석 엔진과 같은 것을 쓴다(질의 CLI 가 edge_analysis 안에 있다) — 새 ECR 을 만들지 않고
# data-pipeline 에 넘기는 analysis_image 와 동일한 값을 입력으로 받는다(ADR-0009: 레포는 foundation 소유).

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

# 실행 역할: 이미지 pull·로그 쓰기 (ECS 에이전트가 사용).
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# schema-migrate 에 있는 Secrets Manager 읽기 정책이 여기에는 의도적으로 없다.
# 이 task 는 DB 비밀번호를 아예 주입받지 않으므로(아래 태스크 역할의 IAM 토큰으로 접속)
# 실행 역할이 읽을 시크릿이 없다. 빠뜨린 것이 아니니 추가하지 마라.

# 태스크 역할: 컨테이너가 접속 직전 rds:generate-db-auth-token 으로 만드는 IAM 토큰의 근거.
# 이 정책이 곧 DB 접속 권한이다 — IAM 에서 떼면 발급된 토큰의 수명(15분)과 무관하게 즉시 회수된다.
# 그래서 비밀번호도, 비밀번호 회전도 필요 없다. Resource 가 dbuser 단위라 agent_ro 로만 붙을 수 있고,
# 읽기 권한 범위는 DB 안에서 그 롤의 GRANT 가 정한다(IAM 은 "누구로 붙을 수 있나"만 정한다).
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy" "task_rds_connect" {
  name = "${var.name}-rds-connect"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["rds-db:connect"]
      Resource = ["arn:aws:rds-db:${var.region}:${var.account_id}:dbuser:${var.db_resource_id}/${var.db_username}"]
    }]
  })
}

# ── 보안그룹 ────────────────────────────────────────────
# egress 는 이미지 pull(ECR/NAT)·RDS 접속을 위해 전체 허용. RDS 5432 인바운드 허용은
# 호출부(env)가 이 SG 를 RDS 인바운드로 거는 방식(순환 의존 회피, rds 모듈 주석 참고).
resource "aws_security_group" "this" {
  name        = "${var.name}-task"
  description = "agent read-only db query one-off task ${var.name}"
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
locals {
  # 런타임(edge_analysis)이 읽는 접속 컨텍스트. 비밀번호 env 가 하나도 없는 것이 이 티켓의 요점이다 —
  # 비밀번호를 두지 않으려고 IAM 토큰 방식을 택했으니 secrets 블록도 없다.
  # AWS_REGION 은 명시해야 한다(Lambda 와 달리 Fargate 는 리전을 env 로 주지 않아 토큰 서명이 실패한다).
  environment = {
    PGHOST     = var.db_host
    PGPORT     = tostring(var.db_port)
    PGDATABASE = var.db_name
    PGUSER     = var.db_username
    PGSCHEMA   = "public"
    AWS_REGION = var.region

    # 에이전트가 무심코 전체 테이블을 끌어오는 사고를 런타임에서 끊는 가드.
    # SG·IAM 으로는 막을 수 없는 종류라 env 로 계약한다(런타임이 이 두 값을 읽는다).
    EDGE_QUERY_ROW_CAP    = tostring(var.row_cap)
    EDGE_QUERY_TIMEOUT_MS = tostring(var.timeout_ms)
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

  # command 미지정: 이미지 ENTRYPOINT(python -m edge_analysis)에 RunTask 가 CMD args 를 덮어
  # `query --sql <SQL>` 또는 `query --file <경로>` 로 질의를 넘긴다 — 질의문이 task 정의에
  # 박히지 않는(리비전을 늘리지 않는) 계약이다. 여기에 command 를 쓰면 RunTask 가 덮어 무의미하다.
  container_definitions = jsonencode([{
    name        = "db-query"
    image       = var.image
    essential   = true
    environment = [for k, v in local.environment : { name = k, value = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "query"
      }
    }
  }])
}
