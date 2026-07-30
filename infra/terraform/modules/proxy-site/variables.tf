variable "name" {
  description = "리소스 이름·comment (예: edge-demo-onprem-mts)"
  type        = string
}

variable "domain_name" {
  description = "뷰어 도메인 (Route53 alias + CloudFront aliases)"
  type        = string
}

variable "zone_id" {
  description = "Route53 hosted zone ID"
  type        = string
}

variable "certificate_arn" {
  description = "us-east-1 ACM 인증서 ARN (foundation 와일드카드)"
  type        = string
}

variable "origin_domain" {
  description = "커스텀 오리진 도메인 (예: 데모 박스 public DNS)"
  type        = string
}

variable "origin_port" {
  description = "커스텀 오리진 HTTP 포트 (박스 컨테이너 호스트 포트)"
  type        = number
}

variable "price_class" {
  description = "CloudFront 엣지 범위"
  type        = string
  default     = "PriceClass_200" # 아시아(서울 엣지) 포함
}
