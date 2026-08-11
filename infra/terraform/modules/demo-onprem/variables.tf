variable "name" {
  description = "리소스 접두어 (예: edge-demo-onprem)"
  type        = string
}

variable "vpc_id" {
  description = "EC2 를 둘 VPC (envs 가 default VPC 등에서 해석해 전달)"
  type        = string
}

variable "subnet_id" {
  description = "EC2 를 둘 public 서브넷 (public IP 부여 대상)"
  type        = string
}

variable "instance_type" {
  description = "EC2 인스턴스 타입. 데모는 작게, 부하 실험은 크게(변수로 승격 — ADR-0033)"
  type        = string
  default     = "t3.large"
}

variable "root_volume_size" {
  description = "루트 EBS 크기(GB) — compose 의 PG named volume 이 여기 얹힌다"
  type        = number
  default     = 50
}

variable "root_volume_type" {
  description = "루트 EBS 타입. audit heavy-write 실험 시 io2 로 승격"
  type        = string
  default     = "gp3"
}

variable "root_volume_iops" {
  description = "io2/gp3 프로비저닝 IOPS. null 이면 타입 기본값(io2 선택 시 필수 설정)"
  type        = number
  default     = null
}

variable "ingress_prefix_list_ids" {
  description = "mock_broker_port 인바운드를 허용할 prefix list(예: CloudFront origin-facing). 비우면 인바운드 없음"
  type        = list(string)
  default     = []
}

variable "console_port" {
  description = "검수 콘솔(tenant-console-ui nginx) 호스트 포트 — CloudFront 오리진이 프록시할 대상. null 이면 인바운드 없음(비공개 유지)"
  type        = number
  default     = null
}

variable "mock_broker_port" {
  description = "가상 증권사 backend(mock-broker) 컨테이너 포트 — CloudFront 오리진이 프록시할 대상"
  type        = number
  default     = 8080
}

variable "cert_parameter_arn" {
  description = "데모 mTLS 클라이언트 인증서를 담은 SSM SecureString 파라미터 ARN(instance profile 이 읽음)"
  type        = string
}

variable "ecr_repository_arns" {
  description = "인스턴스가 pull 할 ECR 저장소 ARN 목록(instance profile 스코프). 비우면 전체(*)"
  type        = list(string)
  default     = []
}

variable "compose_version" {
  description = "설치할 docker compose 플러그인 버전"
  type        = string
  default     = "v2.29.7"
}

variable "associate_eip" {
  description = "안정 공개 IP(EIP) 부여 여부"
  type        = bool
  default     = true
}
