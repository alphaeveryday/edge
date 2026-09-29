# 격리 검증 자원(ALPHA-1119) — 실제 AWS 에서 EdgeStep 의 제출·추적·보류 경로를 확인하는 데만 쓴다.
#
# 무엇과 격리되나:
# - 업무 데이터: 검증 원장은 업무 RDS 인스턴스 안의 **별도 DB(edge_verify)** 이고, 검증 태스크는 그 DB 를 소유한
#   전용 역할(airflow_verify)로만 붙는다 — 업무 DB(edge)의 테이블 권한이 없다. 인스턴스 자원(메모리·CPU·연결 상한)은
#   공유한다(README "기존 RDS 재사용 검증").
# - 레이크: 검증 전용 버킷. 업무 레이크 버킷 권한 없음.
# - KIS: 검증 태스크 정의에 KIS 시크릿이 없다. 수집 스텝은 shim 이 버킷의 저장 응답을 재생한다.
# - 업무 클러스터: 검증 태스크는 Airflow 클러스터(Fargate)에서 돈다 — 업무 Reconciler 의 sweep 이 보는
#   worker 클러스터에 나타나지 않는다(같은 슬롯이면 run_id 가 업무와 같아지므로 섞이면 안 된다).
# 태스크 정의는 업무와 같은 **이미지·명령·원장 코드**(배포된 data-pipeline 이미지 + shim)를 쓴다.

locals {
  verify_taskdef_keys = ["ops", "kis", "bigkinds", "rds"]
  verify_db_name      = "edge_verify"
  verify_db_user      = "airflow_verify"
}

# 검증 원장 역할(airflow_verify)의 비밀번호. 값은 TF 가 만들지 않는다(검증 절차 run.py secrets 가 넣는다).
resource "aws_secretsmanager_secret" "verify" {
  count       = var.verify_enabled ? 1 : 0
  name        = "${var.name}/verify"
  description = "검증 원장 전용 역할 airflow_verify 비밀번호(verify_db_password)"
}

resource "aws_s3_bucket" "verify" {
  count         = var.verify_enabled ? 1 : 0
  bucket        = "${var.name}-verify-${local.account_id}"
  force_destroy = true # 검증 산출물만 — 버킷을 걷을 때 비울 필요가 없다
}

resource "aws_s3_bucket_public_access_block" "verify" {
  count                   = var.verify_enabled ? 1 : 0
  bucket                  = aws_s3_bucket.verify[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "verify" {
  count  = var.verify_enabled ? 1 : 0
  bucket = aws_s3_bucket.verify[0].id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
  }
}

resource "aws_cloudwatch_log_group" "verify" {
  count             = var.verify_enabled ? 1 : 0
  name              = "/ecs/${var.name}-verify"
  retention_in_days = 14
}

resource "aws_security_group" "verify" {
  count       = var.verify_enabled ? 1 : 0
  name        = "${var.name}-verify"
  description = "Airflow isolated verification tasks ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-verify" }
}

resource "aws_vpc_security_group_egress_rule" "verify_all" {
  count             = var.verify_enabled ? 1 : 0
  security_group_id = aws_security_group.verify[0].id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "verify DB, S3, ECR via NAT"
}

resource "aws_vpc_security_group_ingress_rule" "db_from_verify" {
  count                        = var.verify_enabled ? 1 : 0
  security_group_id            = var.db_security_group_id
  referenced_security_group_id = aws_security_group.verify[0].id
  ip_protocol                  = "tcp"
  from_port                    = var.db_port
  to_port                      = var.db_port
  description                  = "Airflow verify tasks to edge_verify DB"
}

resource "aws_iam_role" "verify_execution" {
  count              = var.verify_enabled ? 1 : 0
  name               = "${var.name}-verify-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "verify_execution" {
  count      = var.verify_enabled ? 1 : 0
  role       = aws_iam_role.verify_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "verify_execution_secret" {
  count = var.verify_enabled ? 1 : 0
  name  = "${var.name}-verify-execution-secret"
  role  = aws_iam_role.verify_execution[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.verify[0].arn]
    }]
  })
}

resource "aws_iam_role" "verify_task" {
  count              = var.verify_enabled ? 1 : 0
  name               = "${var.name}-verify-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "verify_task" {
  count = var.verify_enabled ? 1 : 0
  name  = "${var.name}-verify-task"
  role  = aws_iam_role.verify_task[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.verify[0].arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${aws_s3_bucket.verify[0].arn}/*"]
      },
      {
        # 검증 원장의 reconcile(report·sweep)이 ECS 종료 증거를 읽는다 — 이 클러스터만.
        Effect    = "Allow"
        Action    = ["ecs:DescribeTasks", "ecs:ListTasks"]
        Resource  = ["*"]
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } }
      },
    ]
  })
}

locals {
  verify_db_env = {
    DATA_PIPELINE_DB__HOST = var.db_host
    DATA_PIPELINE_DB__PORT = tostring(var.db_port)
    DATA_PIPELINE_DB__NAME = local.verify_db_name
    DATA_PIPELINE_DB__USER = local.verify_db_user
  }
  verify_env = merge(local.verify_db_env, {
    AWS_REGION_NAME                = var.region
    DATA_PIPELINE_STORAGE__BACKEND = "s3"
    DATA_PIPELINE_STORAGE__BUCKET  = var.verify_enabled ? aws_s3_bucket.verify[0].bucket : ""
    OPS_KR_HOLIDAYS                = join(",", var.kr_holidays)
    # shim(검증 계수·재생 입력)이 쓰는 버킷.
    VERIFY_BUCKET = var.verify_enabled ? aws_s3_bucket.verify[0].bucket : ""
    # 검증 원장의 reconcile — Airflow 런 대조·sweep 이 이 클러스터를 본다. 수명은 검증 DAG 의
    # dagrun_timeout(900초)보다 길어야 한다(sweep 이 시간 초과 run 을 판정하는 기준).
    OPS_CLUSTER_ARN                  = aws_ecs_cluster.this.arn
    OPS_AIRFLOW_RUN_LIFETIME_SECONDS = "1200"
  })
}

resource "aws_ecs_task_definition" "verify" {
  for_each = var.verify_enabled ? toset(local.verify_taskdef_keys) : toset([])

  family                   = "${var.name}-verify-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.verify_execution[0].arn
  task_role_arn            = aws_iam_role.verify_task[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # 업무 data-pipeline 이미지와 같다
  }

  container_definitions = jsonencode([{
    name        = "data-pipeline" # EdgeStep 이 이 이름으로 override 한다(업무 태스크 정의와 같다)
    image       = var.verify_image
    essential   = true
    entryPoint  = ["python", "/verify/shim.py"]
    command     = ["reconcile"]
    environment = [for k, v in local.verify_env : { name = k, value = v }]
    secrets = [{
      name = "DATA_PIPELINE_DB__PASSWORD", valueFrom = "${aws_secretsmanager_secret.verify[0].arn}:verify_db_password::"
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.verify[0].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = each.key == "ops" ? "ops" : "raw-ingest"
      }
    }
  }])
}

# ── 관리 태스크(dbadmin) — 업무 RDS 마스터로 전용 DB·역할을 만들고 정리한다 ──
# 하는 일은 verify/dbadmin.sh 의 명령 목록뿐이다(스크립트를 접어 RunTask override 로 넘긴다): 역할·DB 생성, 검증 원장 스키마 복제
# (업무 DB 의 스키마만 pg_dump -s → edge_verify, 행은 복사하지 않는다), 연결·부하 조회, 백업, 정리. 마스터 비밀번호를 읽는
# 유일한 역할이라 execution 역할을 따로 둔다. 검증이 끝나면 verify_enabled=false 로 태스크 정의·역할이 사라진다.
resource "aws_iam_role" "dbadmin_execution" {
  count              = var.verify_enabled ? 1 : 0
  name               = "${var.name}-dbadmin-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "dbadmin_execution" {
  count      = var.verify_enabled ? 1 : 0
  role       = aws_iam_role.dbadmin_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "dbadmin_execution_secret" {
  count = var.verify_enabled ? 1 : 0
  name  = "${var.name}-dbadmin-execution-secret"
  role  = aws_iam_role.dbadmin_execution[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [var.master_db_secret_arn, aws_secretsmanager_secret.airflow.arn, aws_secretsmanager_secret.verify[0].arn]
    }]
  })
}

resource "aws_ecs_task_definition" "dbadmin" {
  count                    = var.verify_enabled ? 1 : 0
  family                   = "${var.name}-dbadmin"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.dbadmin_execution[0].arn
  task_role_arn            = aws_iam_role.verify_task[0].arn # 백업을 검증 버킷에 올린다(그 버킷만)

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name       = "dbadmin"
    image      = "public.ecr.aws/docker/library/postgres:16"
    essential  = true
    entryPoint = ["bash", "-c"]
    command    = ["echo 'dbadmin: 명령은 RunTask override 로 준다(verify/run.py)'; exit 2"]
    environment = [
      { name = "PGHOST", value = var.db_host },
      { name = "PGPORT", value = tostring(var.db_port) },
      { name = "PGUSER", value = var.master_db_user },
      { name = "PGDATABASE", value = var.business_db_name },
      { name = "PGSSLMODE", value = "require" },
      { name = "META_DB", value = var.db_name },
      { name = "META_USER", value = var.db_user },
      { name = "VERIFY_DB", value = local.verify_db_name },
      { name = "VERIFY_USER", value = local.verify_db_user },
    ]
    secrets = [
      { name = "PGPASSWORD", valueFrom = "${var.master_db_secret_arn}:password::" },
      { name = "META_PW", valueFrom = "${aws_secretsmanager_secret.airflow.arn}:meta_db_password::" },
      { name = "VERIFY_PW", valueFrom = "${aws_secretsmanager_secret.verify[0].arn}:verify_db_password::" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.verify[0].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "dbadmin"
      }
    }
  }])
}

# 호스트 관측기가 쓴 기록을 SSM(send-command → S3 출력)으로 수거한다 — 검증 버킷의 obs/ 에만 쓴다.
resource "aws_iam_role_policy" "host_observer_upload" {
  count = var.verify_enabled && var.host_observer ? 1 : 0
  name  = "${var.name}-host-observer-upload"
  role  = aws_iam_role.host.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject"]
      Resource = ["${aws_s3_bucket.verify[0].arn}/obs/*"]
    }]
  })
}
