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
        Resource = concat([
          aws_secretsmanager_secret.fmp.arn,
          aws_secretsmanager_secret.kis.arn,
          aws_secretsmanager_secret.dart.arn,
          # 1분 price-worker 의 토스 자격증명(ALPHA-711) — 여기 없으면 시크릿 주입 뒤에도
          # ResourceInitializationError 로 태스크가 시작되지 않는다
          aws_secretsmanager_secret.toss.arn,
          aws_secretsmanager_secret.krx.arn,
          # 원천 관측 매크로 키(ALPHA-1136, 수동 등록 그릇 — storage.tf data)
          data.aws_secretsmanager_secret.macro["ecos"].arn,
          data.aws_secretsmanager_secret.macro["kosis"].arn,
          data.aws_secretsmanager_secret.macro["eia"].arn,
          data.aws_secretsmanager_secret.macro["fred"].arn,
          var.deepseek_secret_arn,
          var.db_password_secret_arn,
          ],
          # 분 가격 워커 전용 KIS 키(ALPHA-1248) — 켰을 때만. 여기 없으면 워커가 ResourceInitializationError 로 못 뜬다
          local.price_worker_dedicated_kis ? [data.aws_secretsmanager_secret.kis_price_worker[0].arn] : []
        )
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
        # 장중 뉴스 미러 조각(ALPHA-900)과 ETF canonical 구형 part(ALPHA-1017)의 압축 삭제.
        # 각 writer가 단일 part에 병합한 뒤 같은 데이터셋 안의 나머지 part만 지운다.
        #
        # ⚠️ **위 문장과 합치지 마라.** 레이크 전역 `DeleteObject` 는 raw 존의 불변
        # 계약을 깬다 — 거기 바이트는 판정의 근거라 지우면 복원할 수 없는데, 그 계약을
        # 지금 지키는 것은 `delete_keys` 독스트링의 산문뿐이다. prefix 로 좁혀 **IAM 이
        # 그 경계를 지게** 둔다 — 미러 구역 밖을 지우려는 코드는 런타임 AccessDenied 로
        # 드러난다.
        #
        # 첫 Resource의 `*` 둘은 `language=…`·`published_date=…` 다. IAM 의 `*` 는 `/` 를
        # 넘어 매칭하지만 part 파일 키에는 `minute/` 세그먼트가 없어 이 문장에 안 걸린다.
        Effect = "Allow"
        Action = ["s3:DeleteObject"]
        Resource = [
          "${var.lake_bucket_arn}/feature/news/assertions/*/*/minute/*",
          "${var.lake_bucket_arn}/canonical/holdings/etf_holdings/market=*/as_of_date=*/part-*.parquet",
        ]
      },
      {
        # S3 ARN wildcard는 `/`도 넘는다. 위 Allow가 날짜 파티션 아래 중첩 객체까지 잡지 못하게
        # 한 단계 더 깊은 키는 명시적으로 거부한다(직접 자식 part 파일만 삭제 가능).
        Effect   = "Deny"
        Action   = ["s3:DeleteObject"]
        Resource = ["${var.lake_bucket_arn}/canonical/holdings/etf_holdings/market=*/as_of_date=*/*/*"]
      },
      {
        # KIS 토큰 공유 캐시(ALPHA-573) — 이 역할까지가 읽기·쓰기 주체다. 시크릿(앱키)은 지금도
        # execution 역할이 주입하고, 여긴 그 앱키로 받은 **토큰**만 다룬다. 실패하면 컨테이너가
        # 각자 발급하는 현행 동작으로 폴백하므로 권한 부족이 런을 깨지는 않는다(느려질 뿐).
        # SecureString 은 AWS 관리 키(alias/aws/ssm)를 쓴다 — 그 키 정책이 SSM 경유 호출에
        # 계정 주체를 허용하므로 별도 kms 문장이 필요 없다.
        # 두 번째 이름은 분 가격 워커 전용 키(2번)의 토큰 캐시다(ALPHA-1248) — 켰을 때만 든다.
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:PutParameter"]
        Resource = concat([local.kis_token_param_arn], local.price_worker_dedicated_kis ? [local.kis_price_worker_token_param_arn] : [])
      },
    ]
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
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
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
        Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
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
        # StartExecution 은 **원장을 타는 레인에선** 스케줄러가 아니라 Planner(ops_task 역할)가
        # 소유한다(스펙 §5). 예외는 장전 레인 하나뿐이고 아래에 그 문장이 따로 있다.
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
        # 장전 유니버스 레인(ALPHA-963)만 스케줄러가 SFN 을 **직접** 시작한다 — 그 레인은
        # 원장 밖이라 Planner 를 안 거치기 때문이다(premarket_pipeline.tf 도입부).
        # ⚠️ 이 SFN **하나로 한정**한다. 목록을 넓히면 원장을 타야 할 레인이 Planner 를
        # 건너뛰고 시작될 수 있는 문이 열리고, 그 실행은 expected_task 없이 돌아 원장에
        # 안 보인다 — 그건 조용한 실패다(스펙 §5 가 StartExecution 을 Planner 로 모은 이유).
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = [aws_sfn_state_machine.premarket.arn]
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
