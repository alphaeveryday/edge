# 오늘 오른 종목 조사 에이전트(truth-research) — truth-agent 와 같은 구조(pi 하네스 + DeepSeek, ECS Fargate 단발 태스크).
# 장중 매시 :35 에 돈다(truth-agent 사건 갱신 :05 와 30분 어긋나게). 저장소 버킷·실행 키는 truth-agent 것을 함께 쓴다.
#
# 스케줄러 → ecs:RunTask(worker 클러스터). 태스크는 상승률 상위·5분 시세(Naver·Yahoo, 키 없음)·KIND 공시(키 없음)를 모으고,
# 판정이 필요한 종목만 ACH 조사 세션을 연 뒤 랜딩 버킷 truth/data/movers/ 에 게시한다. 겹친 실행은 런타임 잠금이 막는다.
data "aws_caller_identity" "current" {}

locals {
  account_id     = data.aws_caller_identity.current.account_id
  container_name = "truth-research"
  publish_prefix = "truth/data"
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
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [var.runtime_secret_arn] }
  ] })
}

# 태스크 역할: 저장소 읽기·쓰기(뉴스 말뭉치 읽기, 급등주 상태·실행 보고 쓰기), 랜딩 버킷의 급등주·로고 자리 쓰기.
# 공개 버킷은 읽지 않는다 — 날짜 목록 원본도 저장소에 둔다.
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task" {
  name = "${var.name}-task"
  role = aws_iam_role.task.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${var.store_bucket_arn}/*" },
    # 없는 키를 404 로 받기 위해 필요하다(없으면 403 이 와서 '아직 없음'과 권한 오류가 섞인다)
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = var.store_bucket_arn },
    {
      Effect   = "Allow", Action = ["s3:PutObject"],
      Resource = ["${var.landing_bucket_arn}/${local.publish_prefix}/movers/*", "${var.landing_bucket_arn}/${local.publish_prefix}/logos/*"]
    },
    # 매분 감시가 새 급등주를 보면 같은 태스크 정의로 '새 종목만' 조사 실행을 띄운다(이 클러스터에서만)
    {
      Effect    = "Allow", Action = ["ecs:RunTask"], Resource = "arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${var.name}:*",
      Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } }
    },
    {
      Effect    = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn],
      Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
    },
  ] })
}

# ── 네트워크: 밖으로 HTTPS 만 (DeepSeek·Exa·Naver·Yahoo·KIND·회사 홈페이지·ECR·S3) ─────────
resource "aws_security_group" "this" {
  name        = "${var.name}-task"
  description = "truth-research scheduled task ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.this.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
  description       = "HTTPS only (model API, web search, quotes, KIND, ECR, S3)"
}

# 로고: 회사 홈페이지 상당수가 http 만 연다(KIND 회사 개요의 주소가 http).
resource "aws_vpc_security_group_egress_rule" "http" {
  security_group_id = aws_security_group.this.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
  description       = "HTTP (company homepages for logos)"
}

# ── 태스크 정의 ─────────────────────────────────────────
# 1 vCPU·3GB: 조사 세션 3개가 동시에 돈다(세션마다 pi Node + 도구 Python). Chromium 은 없다.
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
    command   = ["run"]
    environment = [for k, v in {
      TRUTH_STORE   = "s3://${var.store_bucket_name}"
      TRUTH_PUBLISH = "s3://${var.landing_bucket_name}/${local.publish_prefix}"
      # 감시(prices)가 '새 종목만' 조사 실행을 띄울 때 쓰는 자리 — 스케줄과 같은 클러스터·네트워크
      TRUTH_RUN_CLUSTER   = var.cluster_arn
      TRUTH_RUN_TASK      = var.name
      TRUTH_RUN_SUBNETS   = join(",", var.subnet_ids)
      TRUTH_RUN_SG        = aws_security_group.this.id
      TRUTH_RUN_CONTAINER = local.container_name
    } : { name = k, value = v }]
    # truth-agent 와 같은 시크릿·같은 키
    secrets = [for key in ["DEEPSEEK_RESIST_NEWS_API_KEY", "EXA_API_KEY"] : {
      name = key, valueFrom = "${var.runtime_secret_arn}:${key}::"
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

resource "aws_scheduler_schedule" "this" {
  name                         = var.name
  state                        = var.schedule_state
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = "Asia/Seoul"

  flexible_time_window { mode = "OFF" }

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
    })

    # 늦은 재시도는 다음 시각 실행과 겹칠 뿐이라(잠금이 막는다) 짧게 둔다.
    retry_policy {
      maximum_event_age_in_seconds = 900
      maximum_retry_attempts       = 2
    }
  }
}

# 가격 갱신·새 급등주 감시: 같은 태스크 정의를 명령만 바꿔(prices) 장중 매분 돌린다. 모델을 부르지 않아 0.25 vCPU·0.5GB 로 줄인다.
# 판정·글은 건드리지 않고 오늘 게시한 종목의 가격·그래프만 고친다. 아직 판정하지 않은 급등주가 생기면 '새 종목만' 조사 실행을 띄운다. 늦은 재시도는 다음 분 실행과 겹칠 뿐이라 두지 않는다.
resource "aws_scheduler_schedule" "prices" {
  name                         = "${var.name}-prices"
  state                        = var.schedule_state
  schedule_expression          = var.prices_schedule_expression
  schedule_expression_timezone = "Asia/Seoul"

  flexible_time_window { mode = "OFF" }

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
      Overrides = {
        Cpu                = "256"
        Memory             = "512"
        ContainerOverrides = [{ Name = local.container_name, Command = ["prices"] }]
      }
    })

    retry_policy {
      maximum_event_age_in_seconds = 60
      maximum_retry_attempts       = 0
    }
  }
}

# 실패 알림은 두지 않는다 — 실패는 로그(/ecs/${var.name})와 저장소 runs/ 실행 보고로만 본다.
