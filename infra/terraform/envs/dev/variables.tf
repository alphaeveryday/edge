variable "region" {
  description = "배포 region"
  type        = string
}

variable "vpc_cidr" {
  description = "dev VPC CIDR"
  type        = string
  default     = "10.0.0.0/16"
}

variable "widget_api_image" {
  description = "widget-api ECR 이미지 URI(:태그 포함)"
  type        = string
}

variable "alb_allowed_cidrs" {
  description = "임시 검증 ALB 인바운드 허용 CIDR"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "route53_zone_name" {
  description = "기존 Route53 호스팅 영역 이름(참조용)"
  type        = string
  default     = "edgesignal.dev"
}

variable "edge_domain" {
  description = "엣지 ALB 에 붙일 도메인"
  type        = string
  default     = "edge-dev.edgesignal.dev"
}
