# 원격 상태 백엔드 (S3). 버킷은 bootstrap 스택이 만든다.
terraform {
  backend "s3" {
    bucket       = "edge-tfstate-393229433969"
    key          = "foundation/terraform.tfstate"
    region       = "ap-northeast-2"
    use_lockfile = true
    encrypt      = true
  }
}
