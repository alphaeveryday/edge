variable "name" {
  description = "리소스 이름 접두어 (예: edge-dev)"
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR. /16 권장(서브넷은 /24 로 잘림)"
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "사용할 AZ 수(ALB·RDS Multi-AZ 대비 최소 2)"
  type        = number
  default     = 2
}

variable "enable_nat" {
  description = "private 서브넷의 아웃바운드(이미지 pull·외부 수집)용 NAT 생성 여부"
  type        = bool
  default     = true
}

variable "single_nat_gateway" {
  description = "true=NAT 1개 공유(dev, 저비용), false=AZ당 1개(prod, 고가용)"
  type        = bool
  default     = true
}
