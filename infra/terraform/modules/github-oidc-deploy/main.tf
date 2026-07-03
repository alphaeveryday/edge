# GitHub Actions → AWS 를 OIDC(웹 아이덴티티)로 잇는 배포 역할.
# 장기 액세스 키 없이, 지정한 repo 의 지정한 브랜치 워크플로만 이 역할을 assume 한다.
# 권한은 최소 스코프다: 마이그레이션(이미지 push + ECS one-off RunTask)에 더해, app_* 변수를
# 채우면 앱 배포(앱 이미지 push + 서비스 롤링 UpdateService + 앱 역할 PassRole)까지 부여한다.
# schema-migrate 와 앱 배포가 이 한 역할을 공유한다(같은 AWS_DEPLOY_ROLE_ARN).

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # provider 를 새로 만들면 그 ARN, 아니면 넘겨받은 기존 ARN 을 쓴다.
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.oidc_provider_arn

  # RunTask 대상 task 정의(모든 리비전).
  task_definition_arn_wildcard = "arn:aws:ecs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:task-definition/${var.task_definition_family}:*"
}

# GitHub OIDC provider. 계정당 하나면 충분하므로, 이미 있으면 create_oidc_provider=false.
# thumbprint 는 이 provider 에 대해 AWS 가 검증에 쓰지 않지만(신뢰 저장소 사용) 인자상 필요하다.
resource "aws_iam_openid_connect_provider" "github" {
  count           = var.create_oidc_provider ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # 지정 repo 의 지정 environment 를 쓰는 워크플로만 허용.
    # (deploy job 이 `environment:` 를 참조하므로 sub 는 branch 가 아니라 environment 형태다.
    #  environment 의 배포 브랜치 정책으로 어느 브랜치가 배포할지 별도 제한한다.)
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for e in var.github_environments : "repo:${var.github_org_repo}:environment:${e}"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = data.aws_iam_policy_document.trust.json

  # create_oidc_provider=false 인데 oidc_provider_arn 을 안 넘기면 trust principal 이 [null] 이 되어
  # 불명확한 apply 실패가 난다. 명확한 메시지로 조기 차단한다.
  lifecycle {
    precondition {
      condition     = var.create_oidc_provider || var.oidc_provider_arn != null
      error_message = "create_oidc_provider=false 이면 oidc_provider_arn(기존 GitHub OIDC provider ARN)을 넘겨야 합니다."
    }
  }
}

data "aws_iam_policy_document" "permissions" {
  # ECR 로그인 토큰은 리소스 스코프가 없다.
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # 마이그레이션 + 앱(app_ecr_repository_arns) 저장소에 이미지 push/pull.
  statement {
    sid = "EcrPushPull"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      # 워크플로의 "이미지 이미 존재?" 가드·버전 조회가 쓰는 권한(없으면 AccessDenied → push 실패).
      "ecr:DescribeImages",
    ]
    resources = concat([var.ecr_repository_arn], var.app_ecr_repository_arns)
  }

  # 새 이미지로 task 정의 리비전 등록. Register/Describe 는 리소스 스코프를 지원하지 않아 * 이다.
  statement {
    sid       = "EcsRegisterDescribe"
    actions   = ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]
    resources = ["*"]
  }

  # 지정 family 의 task 정의를 지정 클러스터에서만 RunTask.
  statement {
    sid       = "EcsRunTask"
    actions   = ["ecs:RunTask"]
    resources = [local.task_definition_arn_wildcard]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [var.ecs_cluster_arn]
    }
  }

  # 실행한 task 상태 폴링. 태스크 ID 는 동적이라 리소스 * + 클러스터 조건으로 좁힌다.
  statement {
    sid       = "EcsDescribeTasks"
    actions   = ["ecs:DescribeTasks"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [var.ecs_cluster_arn]
    }
  }

  # 앱 롤링 배포 — task 정의 교체 후 서비스 업데이트 + 안정화 폴링. app_service_arns 가 있을 때만.
  # 리소스가 서비스 ARN(클러스터명 포함)으로 완전 특정되므로 별도 클러스터 조건은 불필요.
  dynamic "statement" {
    for_each = length(var.app_service_arns) > 0 ? [1] : []
    content {
      sid       = "AppEcsRollingDeploy"
      actions   = ["ecs:UpdateService", "ecs:DescribeServices"]
      resources = var.app_service_arns
    }
  }

  # task 정의가 execution·task 역할을 쓰도록 PassRole(마이그레이션 + 앱 역할). 서비스 조건으로 좁힌다.
  statement {
    sid       = "PassEcsRoles"
    actions   = ["iam:PassRole"]
    resources = concat(var.pass_role_arns, var.app_pass_role_arns)
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  # 마이그레이션 로그 조회(실패 원인 확인).
  statement {
    sid       = "ReadMigrationLogs"
    actions   = ["logs:GetLogEvents", "logs:FilterLogEvents", "logs:DescribeLogStreams"]
    resources = ["${var.log_group_arn}:*"]
  }
}

resource "aws_iam_role_policy" "this" {
  name   = "${var.name}-policy"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.permissions.json
}
