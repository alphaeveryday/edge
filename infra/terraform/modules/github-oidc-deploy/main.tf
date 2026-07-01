# GitHub Actions → AWS 를 OIDC(웹 아이덴티티)로 잇는 배포 역할.
# 장기 액세스 키 없이, 지정한 repo 의 지정한 브랜치 워크플로만 이 역할을 assume 한다.
# 권한은 "마이그레이션 이미지 push + ECS one-off RunTask" 에 필요한 최소로 좁힌다.

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
    # 지정 repo 의 지정 브랜치 워크플로만 허용.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for r in var.subject_refs : "repo:${var.github_org_repo}:ref:refs/heads/${r}"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

data "aws_iam_policy_document" "permissions" {
  # ECR 로그인 토큰은 리소스 스코프가 없다.
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # 마이그레이션 저장소에 이미지 push/pull.
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
      # 워크플로의 "이미지 이미 존재?" 가드가 쓰는 조회 권한(없으면 AccessDenied → 재실행 시 push 실패).
      "ecr:DescribeImages",
    ]
    resources = [var.ecr_repository_arn]
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

  # task 정의가 execution·task 역할을 쓰도록 PassRole. 서비스 조건으로 좁힌다.
  statement {
    sid       = "PassEcsRoles"
    actions   = ["iam:PassRole"]
    resources = var.pass_role_arns
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
