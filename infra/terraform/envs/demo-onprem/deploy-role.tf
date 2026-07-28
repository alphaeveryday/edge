# 데모 온프렘 배포 역할 — GitHub Actions(deploy-demo-onprem.yml)가 OIDC 로 assume.
# 격리 스택이 자기 배포 역할을 소유한다(envs/dev 의 공유 배포 역할과 별개 — blast-radius 격리,
# 모든 데모 리소스가 이 스택 state 안에 있어 ARN 참조가 깔끔). github-oidc-deploy 모듈은
# schema-migrate/ECS 중심(필수 변수 migration·cluster·family)이라 재사용 대신 데모 전용으로 직접 둔다.
# 최소 스코프: 데모 ECR push · MTS S3 sync · CloudFront 무효화 · 박스 SSM Run Command(인스턴스 태그 스코프).

locals {
  # demo_image_names·demo_ecr_arns 는 main.tf locals 로 승격됨(ALPHA-559) —
  # 배포 역할 push 스코프와 인스턴스 프로파일 pull 스코프가 같은 목록을 공유한다.
  deploy_github_org_repo = "alphaeveryday/edge"
}

# 신뢰: foundation OIDC provider + dev 브랜치 ref 핀(deploy-app.yml 과 동일 모델). environment 미사용.
data "aws_iam_policy_document" "deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"
    principals {
      type        = "Federated"
      identifiers = [data.terraform_remote_state.foundation.outputs.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${local.deploy_github_org_repo}:ref:refs/heads/dev"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "${local.prefix}-gha-deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_trust.json
}

data "aws_iam_policy_document" "deploy_permissions" {
  # ECR 로그인 토큰은 리소스 스코프가 없다.
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  # 데모 이미지 push 전용 — 워크플로는 build+push 만 한다(pull 은 박스 인스턴스 역할 소관).
  # BatchGetImage 는 push 경로에도 필요하다(ALPHA-595): 레이어가 전부 "already exists"인
  # 웜 캐시 러너(self-hosted)에서 docker 가 기존 manifest 존재를 HEAD(=BatchGetImage)로
  # 확인한다. GetDownloadUrlForLayer(레이어 pull)는 계속 부여하지 않는다(최소 권한).
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage",
    ]
    resources = local.demo_ecr_arns
  }
  # MTS 정적 sync (버킷 + 객체).
  statement {
    sid       = "MtsS3Sync"
    actions   = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [module.mts_site.bucket_arn, "${module.mts_site.bucket_arn}/*"]
  }
  # MTS CloudFront 무효화.
  statement {
    sid       = "MtsInvalidate"
    actions   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
    resources = [module.mts_site.distribution_arn]
  }
  # 박스 SSM Run Command — AWS 공개 문서 + 인스턴스는 태그(edge:role=demo-onprem)로 스코프.
  # 박스 ARN 이 아니라 태그라 인스턴스 재생성에도 안정.
  statement {
    sid       = "SsmSendCommandDocument"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ssm:${var.region}::document/AWS-RunShellScript"]
  }
  statement {
    sid       = "SsmSendCommandInstance"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/edge:role"
      values   = ["demo-onprem"]
    }
  }
  statement {
    sid       = "SsmReadInvocation"
    actions   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_permissions.json
}
