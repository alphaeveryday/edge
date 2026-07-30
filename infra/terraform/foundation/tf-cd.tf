# Terraform CD 역할 — envs/dev 파이프라인이 assume 한다(ALPHA-311).
# 보안 경계는 권한 범위가 아니라 trust 의 sub 조건이다:
#   - plan : pull_request sub 만 → PR 에서만. 정책은 ReadOnlyAccess(mutate 불가).
#   - apply: ref:refs/heads/dev sub 만 → dev 브랜치 push(=머지)에서만. PR 컨텍스트는 이 sub 를
#     가질 수 없으므로 apply role 을 assume 하지 못한다 — 승인 게이트 없이도 PR 코드가 인프라를
#     바꾸지 못한다. apply 는 오직 리뷰·머지된 dev 코드에서만 실행된다.
# bootstrap·foundation 스택은 CD 대상이 아니다(수동). 이 역할은 envs/dev 전용이지만 OIDC provider·
# trust 가 foundation 소유라 여기서 만든다(env 가 자기 apply role 을 스스로 못 만드는 chicken-egg 회피).

data "aws_iam_policy_document" "tf_cd_trust" {
  for_each = {
    plan  = "repo:${var.github_org_repo}:pull_request"
    apply = "repo:${var.github_org_repo}:ref:refs/heads/dev"
  }

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [each.value]
    }
  }
}

# plan: 읽기 전용. terraform plan 의 refresh 는 Describe/Get/List 만 필요하다.
# AWS 관리형 ReadOnlyAccess 는 secretsmanager:GetSecretValue 등 민감 읽기를 제외한다.
resource "aws_iam_role" "tf_plan" {
  name               = "edge-tf-plan"
  assume_role_policy = data.aws_iam_policy_document.tf_cd_trust["plan"].json
}

resource "aws_iam_role_policy_attachment" "tf_plan_readonly" {
  role       = aws_iam_role.tf_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# plan 역할은 시크릿 값 읽기 권한이 없다(ReadOnlyAccess 는 GetSecretValue 를 제외). pipeline 모듈이
# 시크릿 버전을 TF 로 관리하지 않게 바꿔(ALPHA-312) plan refresh 가 GetSecretValue 를 부르지 않으므로,
# 이전의 edge-dev-pipeline/* GetSecretValue 보완 정책은 제거했다 — PR 컨텍스트(신뢰 불가)가 시크릿에 접근 불가.

# apply: 전 서비스 권한. envs/dev 는 VPC·RDS·ECS·IAM·ALB·CloudFront·SFN 등 거의 모든 리소스를
# 관리하므로 실질적으로 admin 급이 필요하다. 실 통제는 trust(ref:refs/heads/dev = 머지된 코드만)이며,
# 권한을 서비스별로 좁히면 새 리소스 타입마다 apply 가 깨져 파이프라인을 막는다(brittle) — 범위가 아닌
# trust 를 보안 경계로 삼는다.
resource "aws_iam_role" "tf_apply" {
  name               = "edge-tf-apply"
  assume_role_policy = data.aws_iam_policy_document.tf_cd_trust["apply"].json
}

resource "aws_iam_role_policy_attachment" "tf_apply_admin" {
  role       = aws_iam_role.tf_apply.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
