# GitHub Actions OIDC provider (계정당 1개, 전역). 기존에 env(github-oidc-deploy)가 만든 것을
# 여기로 채택한다 — env destroy 가 계정 공유 provider 를 지우는 blast-radius 제거.
# 채택 순서(런타임): ① 여기 apply 로 import → ② env 를 create_github_oidc_provider=false 로
# 전환하고 env state 에서 이 리소스를 제거(rm) → 실물은 그대로, 소유만 foundation 으로 이동.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [var.github_oidc_thumbprint]
}

import {
  to = aws_iam_openid_connect_provider.github
  id = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

data "aws_caller_identity" "current" {}
