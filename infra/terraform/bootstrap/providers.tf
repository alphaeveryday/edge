# 부트스트랩 스택 — 원격 state 백엔드(S3+DynamoDB)를 만드는 "닭-달걀" 해소용.
# 자기 state 는 로컬에 둔다(자기가 만드는 버킷에 자기 state 를 둘 수 없으므로).
# 계정당 한 번만 apply 하면 되고, 이후 거의 손대지 않는다.
terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "edge"
      Scope     = "bootstrap"
      ManagedBy = "terraform"
    }
  }
}
