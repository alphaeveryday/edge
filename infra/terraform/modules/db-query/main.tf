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

# DB 비밀번호는 schema-migrate 와 같은 방식으로 시크릿에서 주입한다(ALPHA-933).
# 원설계는 IAM 토큰(rds-db:connect)의 비밀번호 없는 접속이었으나 **조직 SCP 가
# rds-db:connect 를 explicitDeny** 해(2026-08-11 simulate-principal-policy 실측,
# AllowedByOrganizations=false) 이 계정에서는 IAM DB 인증이 원리적으로 불가하다 —
# LakeFormation 이 같은 이유로 막힌 전례와 동일 계열. 읽기전용 안전은 남은 두 층이
# 진다: 접속 파라미터 default_transaction_read_only=on(연결 단위라 SQL 로 못 되돌림)
# + 런타임 SELECT 가드(adapters/readonly.py).
# (RDS 관리형 시크릿은 기본 aws/secretsmanager 키라 GetSecretValue 로 충분. CMK 면 kms:Decrypt 추가.)
resource "aws_iam_role_policy" "execution_secret" {
  name = "${var.name}-execution-secret"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [var.db_password_secret_arn]
    }]
  })
}

# 태스크 역할: 컨테이너 자체 권한은 없다(질의는 DB 안에서 끝난다). rds-db:connect
# 정책이 있던 자리다 — SCP 차단으로 죽은 경로라 남겨 두면 "IAM 으로 붙는다"는
# 오독만 낳아 제거했다. SCP 가 풀리면 원설계(git 히스토리)로 되돌릴 수 있다.
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
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
  # 런타임(edge_analysis)이 읽는 접속 컨텍스트. 비밀번호는 env 가 아니라 아래
  # 태스크 정의의 secrets 블록(PGPASSWORD)으로 주입된다 — SCP 가 IAM 토큰 경로를
  # 막아 시크릿 방식으로 전환했다(위 IAM 절 주석).
  environment = {
    PGHOST     = var.db_host
    PGPORT     = tostring(var.db_port)
    PGDATABASE = var.db_name
    PGUSER     = var.db_username
    PGSCHEMA   = "public"

    # 접속 직후 이 롤로 내려앉는다(접속 파라미터 role) - 마스터 시크릿으로 붙어도
    # 질의 권한은 읽기전용 롤이 서버 강제한다. GRANT agent_ro TO 마스터는
    # V202608111500 마이그레이션이 선행한다.
    EDGE_QUERY_ROLE = var.query_role

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

  # entryPoint 를 query 서브커맨드까지 고정한다 — RunTask 오버라이드는 command(인자)만
  # 바꿀 수 있고 entryPoint 는 못 바꾼다. 이 task 는 마스터 시크릿을 주입받으므로(SCP 가
  # IAM 경로를 막아서), entryPoint 를 이미지 기본(python -m edge_analysis)에 두면 RunTask
  # 권한만으로 쓰기 서브커맨드(load-* 류)를 마스터로 돌릴 수 있다. 서브커맨드를 박아
  # 도달 가능한 코드가 읽기 질의 경로(query: read-only 접속 파라미터+SELECT 가드)뿐이게
  # 한다. 호출부는 command 로 `--sql <SQL>` 또는 `--file <경로>` 만 넘긴다 — 질의문이
  # task 정의에 박히지 않는(리비전을 늘리지 않는) 계약은 그대로다.
  container_definitions = jsonencode([{
    name        = "db-query"
    image       = var.image
    essential   = true
    entryPoint  = ["python", "-m", "edge_analysis", "query"]
    environment = [for k, v in local.environment : { name = k, value = v }]
    secrets = [
      { name = "PGPASSWORD", valueFrom = "${var.db_password_secret_arn}:password::" },
    ]
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
