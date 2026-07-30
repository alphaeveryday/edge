# GitHub Actions OIDC provider (계정당 1개, 전역). clean slate 라 신규 생성(과거 것은 teardown 으로 삭제됨).
# env(github-oidc-deploy)는 create_github_oidc_provider=false + 이 provider ARN 을 참조한다.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [var.github_oidc_thumbprint]
}
