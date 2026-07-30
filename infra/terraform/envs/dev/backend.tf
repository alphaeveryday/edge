# 원격 상태 백엔드 (S3). 버킷은 bootstrap 스택이 만든다.
# 락은 별도 DynamoDB 없이 S3 네이티브 락(use_lockfile) — Terraform 1.11+.
terraform {
  backend "s3" {
    bucket       = "edge-tfstate-393229433969"
    key          = "envs/dev/terraform.tfstate"
    region       = "ap-northeast-2"
    use_lockfile = true
    encrypt      = true
  }
}
