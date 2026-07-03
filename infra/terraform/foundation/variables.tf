variable "region" {
  description = "리전 서비스(ALB)용 ACM 을 둘 region"
  type        = string
  default     = "ap-northeast-2"
}

variable "zone_name" {
  description = "와일드카드를 발급할 도메인의 Route53 호스팅 영역 이름"
  type        = string
  default     = "edgesignal.dev"
}

variable "zone_id" {
  description = "import 대상 기존 호스팅 영역 ID"
  type        = string
  default     = "Z06054342H0SMFWZIEVP0"
}

variable "github_oidc_thumbprint" {
  description = "GitHub Actions OIDC provider thumbprint (AWS 는 신뢰저장소를 쓰므로 검증엔 미사용이나 인자상 필요)"
  type        = string
  default     = "6938fd4d98bab03faadb97b34396831e3780aea1"
}
