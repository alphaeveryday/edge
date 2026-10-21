# 투자정보 기만 검증 에이전트(ADR-0062) — 매시 사건 갱신을 ECS Fargate 단발 태스크로 돌린다.
# 새 사건 찾기(discover)는 일정에 두지 않는다 — 모델은 기존 사건 관리에만 쓴다. 새 사건은 사람이 seed 로 넣는다.
#
# 스케줄러 → ecs:RunTask(worker 클러스터). 태스크는 레이크의 뉴스를 읽고, 바뀐 사건만 모델 세션을 연 뒤,
# 검사를 통과한 결과를 비공개 버킷에 저장하고 랜딩 버킷 truth/data/ 에 게시한다. 상시 서비스는 없다.
# 겹친 실행은 런타임의 잠금(state/lease.json, 55분)이 막는다.
data "aws_caller_identity" "current" {}

locals {
  account_id     = data.aws_caller_identity.current.account_id
  container_name = "truth-agent"
  publish_prefix = "truth/data"
  # 런타임이 읽는 레이크 경로 두 곳뿐이다(news.py — data-pipeline 키 함수와 같은 접두어).
  lake_prefixes = [
    "raw/source=bigkinds/dataset=news_minute/",
    "canonical/news/news_articles/",
  ]
}

# ── 비공개 저장소: 사건 원본·뉴스 말뭉치·실행 상태·실행 보고 ─────────
# 버전을 켠다 — 에이전트가 고친 사건을 사람이 되돌릴 수 있어야 한다. 매시 쓰는 상태 파일의
# 이전 버전이 쌓이므로 30일 뒤 지운다.
resource "aws_s3_bucket" "store" {
  bucket = "${var.name}-store"
}

resource "aws_s3_bucket_versioning" "store" {
  bucket = aws_s3_bucket.store.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "store" {
  bucket = aws_s3_bucket.store.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "store" {
  bucket                  = aws_s3_bucket.store.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "store" {
  bucket     = aws_s3_bucket.store.id
  depends_on = [aws_s3_bucket_versioning.store]

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 30 }
  }

  # 실행 보고(runs/<날짜>/...)는 매시 하나씩 쌓인다. 운영 확인용이라 90일이면 충분하다.
  rule {
    id     = "expire-run-reports"
    status = "Enabled"
    filter { prefix = "runs/" }
    expiration { days = 90 }
  }
}

# ── 키: TF 는 그릇만 만든다. 값은 TF 밖에서 넣는다(state 에 평문을 남기지 않는다) ──
# aws secretsmanager put-secret-value --secret-id <name> \
#   --secret-string '{"DEEPSEEK_RESIST_NEWS_API_KEY":"...","EXA_API_KEY":"...","TRUTH_KIS_APP_KEY":"...","TRUTH_KIS_APP_SECRET":"...","TRUTH_DART_API_KEY":"..."}'
# 값이 없으면 태스크가 시작하지 못하고(TaskFailedToStart) ECS 태스크 상태(stopCode)와 로그에 남는다(알림은 보내지 않는다).
resource "aws_secretsmanager_secret" "runtime" {
  name        = "${var.name}/runtime"
  description = "truth-agent 실행 키 (수동 주입: {\"DEEPSEEK_RESIST_NEWS_API_KEY\":\"...\",\"EXA_API_KEY\":\"...\"})"
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = 30
}

# ── IAM ────────────────────────────────────────────────
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secret" {
  name = "${var.name}-execution-secret"
  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [aws_secretsmanager_secret.runtime.arn] }
  ] })
}

# 태스크 역할: 비공개 저장소 읽기·쓰기, 레이크 뉴스 두 경로 읽기, 랜딩 버킷 truth/data/ 쓰기·지우기.
# ListBucket 은 없는 키를 404 로 받기 위해 필요하다(없으면 403 이 와서 '아직 없음'과 권한 오류가 섞인다).
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task" {
  name = "${var.name}-task"
  role = aws_iam_role.task.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${aws_s3_bucket.store.arn}/*" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.store.arn },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = [for p in local.lake_prefixes : "${var.lake_bucket_arn}/${p}*"] },
    # 접두어 조건을 걸지 않는다 — 없는 키의 HEAD(아직 안 생긴 정제본 파티션)는 접두어 없이 ListBucket 을
    # 평가해, 조건을 걸면 404 대신 403 이 와서 매 실행이 멈춘다. 목록은 키 이름뿐이고 읽기는 위 두 경로뿐이다.
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = var.lake_bucket_arn },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:DeleteObject"], Resource = "${var.landing_bucket_arn}/${local.publish_prefix}/*" },
    # 게시 때 공개 위치를 나열해 빠진 파일을 되올린다(수동 재게시 뒤 복구).
    {
      Effect    = "Allow", Action = ["s3:ListBucket"], Resource = var.landing_bucket_arn,
      Condition = { StringLike = { "s3:prefix" = ["${local.publish_prefix}/*"] } }
    },
  ] })
}

# ── 네트워크: 밖으로 HTTPS 만 (DeepSeek·Exa·ECR·S3) ─────────
resource "aws_security_group" "this" {
  name        = "${var.name}-task"
  description = "truth-agent scheduled task ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.this.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
  description       = "HTTPS only (model API, web search, ECR, S3)"
}

# ── 태스크 정의 ─────────────────────────────────────────
# 1 vCPU·3GB: 세션은 하나씩(pi Node + 도구 Python), 끝에 Chromium 렌더 한 번. 로컬 실측 세션 하나 수 분.
resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 3072
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = local.container_name
    image     = var.image
    essential = true
    command   = ["run", "hourly"]
    environment = [for k, v in {
      TRUTH_STORE       = "s3://${aws_s3_bucket.store.bucket}"
      TRUTH_PUBLISH     = "s3://${var.landing_bucket_name}/${local.publish_prefix}"
      TRUTH_LAKE_BUCKET = var.lake_bucket_name
    } : { name = k, value = v }]
    # 시크릿 JSON 에 키가 없으면 태스크가 시작하지 못한다 — 키를 추가할 때는 시크릿에 값(비어 있어도)을 먼저 넣고 적용한다.
    secrets = [for key in ["DEEPSEEK_RESIST_NEWS_API_KEY", "EXA_API_KEY", "TRUTH_KIS_APP_KEY", "TRUTH_KIS_APP_SECRET", "TRUTH_DART_API_KEY"] : {
      name = key, valueFrom = "${aws_secretsmanager_secret.runtime.arn}:${key}::"
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "run"
      }
    }
  }])
}

# ── 스케줄 ─────────────────────────────────────────────
resource "aws_iam_role" "scheduler" {
  name               = "${var.name}-scheduler"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    {
      Effect    = "Allow", Action = ["ecs:RunTask"], Resource = "arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${var.name}:*",
      Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } }
    },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn] },
  ] })
}

locals {
  schedules = {
    hourly = var.hourly_schedule_expression
    movers = var.movers_schedule_expression # 오늘의 급등주(docs/design/truth-movers.md)
  }
}

resource "aws_scheduler_schedule" "this" {
  for_each                     = local.schedules
  name                         = "${var.name}-${each.key}"
  state                        = var.schedule_state
  schedule_expression          = each.value
  schedule_expression_timezone = "Asia/Seoul"

  flexible_time_window { mode = "OFF" }

  # 범용 대상(aws-sdk:ecs:runTask): input 이 RunTask 요청 그대로라 실행 모드(command)를 확실히 덮는다.
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      Cluster        = var.cluster_arn
      TaskDefinition = aws_ecs_task_definition.this.arn
      LaunchType     = "FARGATE"
      NetworkConfiguration = {
        AwsvpcConfiguration = {
          Subnets        = var.subnet_ids
          SecurityGroups = [aws_security_group.this.id]
          AssignPublicIp = "DISABLED"
        }
      }
      Overrides = { ContainerOverrides = [{ Name = local.container_name, Command = ["run", each.key] }] }
    })

    # 늦은 재시도는 다음 시각 실행과 겹칠 뿐이라(잠금이 막는다) 짧게 둔다.
    retry_policy {
      maximum_event_age_in_seconds = 900
      maximum_retry_attempts       = 2
    }
  }
}

# 실패 알림은 두지 않는다 — 실패는 로그(/ecs/${var.name})와 저장소 runs/ 실행 보고로만 본다.
