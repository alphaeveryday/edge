terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.56"
    }
  }
}

# 데모 스택은 단일 리전(apne2)만 쓴다. static-site 는 us-east-1 인증서를 foundation 아웃풋에서
# ARN 으로 받아 쓰므로(모듈이 발급 안 함) us_east_1 프로바이더 별칭이 필요 없다.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "edge"
      Env       = "demo-onprem"
      ManagedBy = "terraform"
    }
  }
}
