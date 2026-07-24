variable "region" {
  description = "AWS 리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "mts_domain" {
  description = "가상 MTS 페이지 도메인 (foundation 와일드카드 하위)"
  type        = string
  default     = "demo-mts.edgesignal.dev"
}

variable "instance_type" {
  description = "데모 박스 인스턴스 타입. 시연은 작게, 부하 실험은 크게"
  type        = string
  default     = "t3.large"
}

variable "root_volume_size" {
  description = "데모 박스 루트 EBS 크기(GB)"
  type        = number
  default     = 50
}

variable "root_volume_type" {
  description = "데모 박스 루트 EBS 타입. audit heavy-write 실험 시 io2"
  type        = string
  default     = "gp3"
}

variable "root_volume_iops" {
  description = "io2/gp3 프로비저닝 IOPS (io2 선택 시 필수). null 이면 타입 기본값"
  type        = number
  default     = null
}

variable "mock_broker_port" {
  description = "가상 증권사 backend(mock-broker) 포트 — CloudFront 오리진이 프록시할 대상"
  type        = number
  default     = 8080
}

variable "subnet_id" {
  description = "데모 박스를 둘 public 서브넷 ID. 비우면 default VPC 의 첫 public 서브넷(계정에 default VPC 없으면 이 변수로 지정)"
  type        = string
  default     = null
}
