# 상태 백엔드.
# 기본: 로컬 상태(이 블록 주석 유지) — 바로 `terraform init` 가능.
#
# "어디서나 공유"하려면 원격 상태(S3+DynamoDB 락)로 전환한다. 단, 버킷/테이블은
# Terraform 이 자기 상태를 둘 곳이라 닭-달걀 → 별도 부트스트랩으로 먼저 만든다.
#   1) S3 버킷 + DynamoDB 락 테이블 생성(콘솔 또는 작은 bootstrap)
#   2) 아래 블록 주석 해제 후 `terraform init -migrate-state`
#
# terraform {
#   backend "s3" {
#     bucket         = "edge-tfstate-<account-id>"
#     key            = "envs/dev/terraform.tfstate"
#     region         = "ap-northeast-2"
#     dynamodb_table = "edge-tflock"
#     encrypt        = true
#   }
# }
