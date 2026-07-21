# foundation 아웃풋만 참조한다(dev 스택은 참조하지 않음 — 격리). Route53 zone·CDN 인증서·ECR.
data "terraform_remote_state" "foundation" {
  backend = "s3"
  config = {
    bucket = "edge-tfstate-393229433969"
    key    = "foundation/terraform.tfstate"
    region = "ap-northeast-2"
  }
}
