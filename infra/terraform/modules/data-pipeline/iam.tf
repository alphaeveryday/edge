data "aws_caller_identity" "current" {}

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

resource "aws_iam_role_policy" "execution_secrets" {
  name = "${var.name}-exec-secrets"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_secretsmanager_secret.fmp.arn,
          aws_secretsmanager_secret.kis.arn,
          aws_secretsmanager_secret.dart.arn,
          # 1분 price-worker 의 토스 자격증명(ALPHA-711) — 여기 없으면 시크릿 주입 뒤에도
          # ResourceInitializationError 로 태스크가 시작되지 않는다
          aws_secretsmanager_secret.toss.arn,
          aws_secretsmanager_secret.krx.arn,
          var.deepseek_secret_arn,
          var.db_password_secret_arn,
        ]
      },
      {
        # ExposureReverted 회수 자격(ALPHA-746) — analysis-consumer 주입용 SSM SecureString.
        # AWS 관리 키(alias/aws/ssm)라 별도 kms 문장 불요(kis 토큰 캐시와 같은 근거).
        Effect   = "Allow"
        Action   = ["ssm:GetParameters"]
        Resource = [local.super_admin_email_param_arn, local.super_admin_password_param_arn]
      },
    ]
  })
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task" {
  name = "${var.name}-task"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = [var.lake_bucket_arn, "${var.lake_bucket_arn}/*"]
      },
      {
        # KIS 토큰 공유 캐시(ALPHA-573) — 이 역할까지가 읽기·쓰기 주체다. 시크릿(앱키)은 지금도
        # execution 역할이 주입하고, 여긴 그 앱키로 받은 **토큰**만 다룬다. 실패하면 컨테이너가
        # 각자 발급하는 현행 동작으로 폴백하므로 권한 부족이 런을 깨지는 않는다(느려질 뿐).
        # SecureString 은 AWS 관리 키(alias/aws/ssm)를 쓴다 — 그 키 정책이 SSM 경유 호출에
        # 계정 주체를 허용하므로 별도 kms 문장이 필요 없다.
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:PutParameter"]
        Resource = [local.kis_token_param_arn]
      },
    ]
  })
}

# analyze 태스크 역할 — 공용 task 역할(레이크 전체 RW)과 달리 레이크는 읽기만, 쓰기는 설명
# 결과 prefix 에 한정한다(구 analysis-engine 모듈의 최소권한 유지). RDS 쓰기는 IAM 무관(암호 인증).
# 반면 execution 역할은 공용을 그대로 쓴다(모듈 관례: task-def 마다 주입 시크릿은 다르지만
# 기동 주체는 하나) — 구 모듈의 2개(deepseek·db)보다 읽기 가능 시크릿이 벤더 4종만큼 넓어진다.
# 주입 시크릿 목록(analysis_secrets)은 불변이라 컨테이너 표면 노출은 그대로다.
resource "aws_iam_role" "analysis_task" {
  name               = "${var.name}-analysis-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# 시각창 집계 Athena 오프로드 자격(ALPHA-780). **카탈로그 권한만으로는 못 읽는다** —
# Athena 는 호출자 자격으로 표 데이터를 스캔하고 결과 CSV 도 호출자 자격으로 쓴다.
# 그 CSV 를 DuckDB 가 `read_csv` 로 받는다(`statics/athena.py`) — 읽기·쓰기가 다 필요하다.
#
# 왜 필요한가: 구간 모드는 시각창 집계를 **언제나** Athena 로 보내고(`layers.py`), 폴백인
# DuckDB 경로는 질의당 376.4MB 를 컨테이너로 받는다(실측). 1분 주기가 못 버틴다.
#
# 버킷·워크그룹이 비면 문장을 만들지 않는다 — `analysis_release_bundle_version` 과 같은
# 조건부 주입 패턴이다. 반쪽 정책보다 부재가 낫다(폴백 사유가 로그에 남는다).
locals {
  analysis_athena_statements = (
    var.analysis_market_data_bucket_arn == "" || var.analysis_athena_workgroup == ""
    ? []
    : [
      {
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
        ]
        Resource = [
          "arn:aws:athena:${var.region}:${data.aws_caller_identity.current.account_id}:workgroup/${var.analysis_athena_workgroup}",
        ]
      },
      {
        # 표 데이터 스캔. glue:Get* 는 "이 표가 어디 있나" 까지만 답한다.
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [var.analysis_market_data_bucket_arn, "${var.analysis_market_data_bucket_arn}/*"]
      },
      {
        # 결과 CSV 쓰기. 위치는 워크그룹이 강제하므로 prefix 를 그 규약에 맞춘다.
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${var.analysis_market_data_bucket_arn}/athena-results/*"]
      },
    ]
  )
}

resource "aws_iam_role_policy" "analysis_task" {
  name = "${var.name}-analysis-task"
  role = aws_iam_role.analysis_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [var.lake_bucket_arn, "${var.lake_bucket_arn}/*"]
      },
      {
        Effect = "Allow"
        Action = [
          "glue:GetCatalog",
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetPartition",
          "glue:GetPartitions",
          "glue:GetTable",
          "glue:GetTables",
        ]
        Resource = ["*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${var.lake_bucket_arn}/${local.analysis_result_s3_prefix}*"]
      },
    ], local.analysis_athena_statements)
  })
}

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.name}-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

resource "aws_iam_role_policy" "sfn" {
  name = "${var.name}-sfn"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ecs:RunTask"]
        Resource = concat(
          [for task_definition in aws_ecs_task_definition.this : task_definition.arn],
          [aws_ecs_task_definition.analysis.arn],
        )
      },
      {
        Effect    = "Allow"
        Action    = ["ecs:StopTask", "ecs:DescribeTasks"]
        Resource  = ["*"]
        Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } }
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn, aws_iam_role.analysis_task.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
        Resource = ["arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"]
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = [aws_sns_topic.alarms.arn]
      },
    ]
  })
}

data "aws_iam_policy_document" "scheduler_assume" {
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
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.name}-scheduler"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # 운영 원장(ALPHA-530): daily·reconcile 스케줄이 Planner/Reconciler ECS 태스크를 띄운다.
        # StartExecution 은 이제 스케줄러가 아니라 Planner(ops_task 역할)가 소유한다(스펙 §5).
        Effect = "Allow"
        Action = ["ecs:RunTask"]
        # 1분 세션 스케일 오케스트레이션(ALPHA-712)도 같은 스케줄러 역할로 뜬다 —
        # 실행체 형태가 daily·reconcile 과 같은 ECS RunTask 라 역할을 새로 만들 이유가 없다.
        Resource = [aws_ecs_task_definition.ops.arn, aws_ecs_task_definition.minute_session.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.execution.arn, aws_iam_role.ops_task.arn, aws_iam_role.minute_session.arn]
      },
      {
        # 전달 실패 이벤트를 DLQ 로 흘린다(스펙 §5).
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = [aws_sqs_queue.scheduler_dlq.arn]
      },
    ]
  })
}
