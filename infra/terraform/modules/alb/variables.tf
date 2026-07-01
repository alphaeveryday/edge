variable "name" {
  description = "ALB·타깃그룹 이름 (≤32자)"
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  description = "ALB 가 위치할 public 서브넷"
  type        = list(string)
}

variable "listener_port" {
  description = "리스너 포트(HTTP). HTTPS 는 ACM 도입 후 별도"
  type        = number
  default     = 80
}

variable "target_port" {
  description = "타깃(컨테이너) 포트"
  type        = number
  default     = 8080
}

variable "health_check_path" {
  description = "타깃그룹 헬스체크 경로"
  type        = string
  default     = "/actuator/health"
}

variable "allowed_cidrs" {
  description = "리스너 인바운드 허용 CIDR"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "enable_https" {
  description = "true 면 HTTPS:443 리스너 + HTTP→HTTPS 리다이렉트. plan 시점에 알아야 하므로 ARN 이 아닌 불리언으로 분기"
  type        = bool
  default     = false
}

variable "certificate_arn" {
  description = "ACM 인증서 ARN(enable_https=true 일 때 사용). 값은 apply 후 결정돼도 됨"
  type        = string
  default     = null
}
