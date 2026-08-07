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
# 버킷·워크그룹이 비면 문장을 만들지 않는다. 반쪽 정책보다 부재가 낫다(폴백 사유가 로그에
# 남는다). ⚠️ `analysis_release_bundle_version` 은 더 이상 같은 패턴이 아니다 — 그쪽은
# nullable=false 필수 변수가 됐다(ALPHA-797).
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
          # 타임아웃 중단. `statics/athena.py` 의 `_wait` 가 상한을 넘기면
          # `stop_query_execution` 을 부르고 **실패를 삼킨다** — 권한이 없으면 원 질의는
          # 계속 스캔되는데 DuckDB 폴백까지 돌아 비용·부하가 겹친다(조용히).
          #
          # ⚠️ 받아들인 비용: 이 액션의 **최소 리소스 단위가 워크그룹**이다(Athena 는
          # query id 단위 ARN 이 없다). 그런데 `market_data` 는 공용이라 canonical
          # 적재기도 같은 워크그룹을 쓴다(`data_pipeline/canonical/athena.py`
          # `WORKGROUP` 기본값). 즉 이 역할은 **남의 질의도 중단할 수 있다** — query id
          # 를 알아야 하지만 로그에 남는다. 더 좁힐 방법은 워크그룹 분리뿐이고, 그건
          # 이 모듈 밖(버킷·워크그룹은 terraform 관리 밖)이라 여기서 할 수 없다.
          "athena:StopQueryExecution",
        ]
        Resource = [
          "arn:aws:athena:${var.region}:${data.aws_caller_identity.current.account_id}:workgroup/${var.analysis_athena_workgroup}",
        ]
      },
      {
        # 표 데이터 스캔. glue:Get* 는 "이 표가 어디 있나" 까지만 답한다.
        #
        # `s3:GetBucketLocation` 은 **결과 위치 해석**에 든다 — Athena 는 질의 제출 시
        # 출력 버킷의 리전을 호출자 자격으로 확인한다. 워크그룹이 결과 위치를 강제해도
        # (`Enforce=True`) 그 확인은 호출자 몫이라 없으면 제출 자체가 AccessDenied 다.
        # 여기 붙이는 이유: 강제된 결과 위치가 이 표버킷의 `athena-results/` prefix 라
        # 대상 버킷이 같다(아래 PutObject 문장과 같은 버킷).
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [var.analysis_market_data_bucket_arn, "${var.analysis_market_data_bucket_arn}/*"]
      },
      {
        # 결과 CSV 쓰기. 위치는 워크그룹이 강제하므로 prefix 를 그 규약에 맞춘다.
        #
        # multipart 두 액션이 함께 든다 — Athena 는 결과를 multipart upload 로 쓴다
        # (AWS 표준 Athena 정책이 `PutObject` 와 같이 싣는 짝). 없으면 실패가 조용하다:
        # 쓰기 AccessDenied 는 `AthenaUnavailable` 로 접혀 DuckDB 폴백(질의당 376.4MB)
        # 으로 내려간다.
        #
        # `athena.py` 의 `MAX_RESULT_BYTES`(32MB)는 **이 업로드를 막지 못한다** — 그
        # 검사는 질의가 끝나 결과가 S3 에 다 쓰인 뒤에 크기를 보고 거절하는 것이라,
        # 상한을 넘는 결과도 일단 올라간다. 즉 파트 수를 상한으로 가늠하면 안 된다.
        #
        # ⚠️ `AbortMultipartUpload` 는 **정리를 보장하지 않는다** — 중단이 호출됐을 때
        # 조각을 지울 수 있게 할 뿐이다. 실패·취소 후 남는 미완료 업로드는 버킷
        # lifecycle 의 abort 규칙이 치우는 것이 AWS 권고인데, 이 버킷은 terraform
        # 관리 밖이라 그 규칙의 유무를 여기서 알 수 없다(보관 비용은 남을 수 있다).
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:ListMultipartUploadParts",
          "s3:AbortMultipartUpload",
        ]
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
        Resource = [for task_definition in aws_ecs_task_definition.this : task_definition.arn]
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
