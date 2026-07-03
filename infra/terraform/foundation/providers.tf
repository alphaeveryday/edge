# foundation — 계정 전역·장수명 공유자원 스택 (Phase 1).
# 현재 범위: 도메인 와일드카드 ACM 인증서(리전당 1장). 존은 등록 시 수동 생성된 것을 참조만 한다.
# env(Phase 2)는 이 인증서를 data 로 조회해 ALB·CloudFront 에 건다(느슨한 결합).
# state 는 당장 로컬. 원격(S3) 전환은 bootstrap 적용 후.
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
      Scope     = "foundation"
      ManagedBy = "terraform"
    }
  }
}

# CloudFront 용 ACM 은 us-east-1 필수.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = "edge"
      Scope     = "foundation"
      ManagedBy = "terraform"
    }
  }
}
