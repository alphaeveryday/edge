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
      Env       = "dev"
      ManagedBy = "terraform"
    }
  }
}

# CloudFront 커스텀 도메인 ACM 인증서는 반드시 us-east-1 에 있어야 한다(CloudFront 는 글로벌
# 서비스라 인증서만 버지니아에서 찾음). 정적 사이트 모듈이 이 alias 로 ACM 을 발급한다.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = "edge"
      Env       = "dev"
      ManagedBy = "terraform"
    }
  }
}
