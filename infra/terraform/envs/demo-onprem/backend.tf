# 원격 상태 백엔드 (S3). 버킷은 bootstrap 스택이 만든다. dev 와 같은 버킷, 다른 key —
# 데모 apply/destroy 가 실 클라우드(envs/dev) state 를 못 건드리게 격리한다(ADR-0033).
terraform {
  backend "s3" {
    bucket       = "edge-tfstate-393229433969"
    key          = "envs/demo-onprem/terraform.tfstate"
    region       = "ap-northeast-2"
    use_lockfile = true
    encrypt      = true
  }
}
