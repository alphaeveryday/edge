variable "name" {
  description = "리소스 접두어 겸 S3 버킷 이름 (예: edge-dev-widget). S3 는 전역 유일해야 함."
  type        = string
}

variable "domain_name" {
  description = "서빙할 커스텀 도메인 (예: widget-dev.edgesignal.dev)"
  type        = string
}

variable "zone_id" {
  description = "도메인이 속한 Route53 호스팅 영역 ID (alias 레코드를 여기 건다)"
  type        = string
}

variable "certificate_arn" {
  description = "CloudFront 뷰어 인증서 ARN. foundation 의 us-east-1 와일드카드(*.edgesignal.dev). 반드시 us-east-1 인증서여야 함."
  type        = string
}

variable "spa" {
  description = "SPA(클라이언트 사이드 라우팅)면 true — 403/404 를 /index.html(200) 로 재작성. 위젯 같은 정적 파일 묶음이면 false."
  type        = bool
  default     = false
}

variable "price_class" {
  description = "CloudFront 엣지 범위. dev 는 비용 절감으로 좁혀도 됨(PriceClass_100=북미·유럽)."
  type        = string
  default     = "PriceClass_200" # 아시아(서울 엣지) 포함
}

# ── 선택: /api/* 프록시 오리진 ─────────────────────────
# 정적 사이트에 API 백엔드를 같은 도메인으로 붙일 때(데모 온프렘 MTS → 박스 mock-broker).
# 비우면 정적 전용(기존 동작). 브라우저 mixed-content 회피: 뷰어는 HTTPS, 오리진은 HTTP.
variable "api_origin_domain" {
  description = "선택: api_path_pattern 을 프록시할 커스텀 오리진 도메인(예: 데모 박스 EC2 public DNS). 비우면 API 오리진 없음."
  type        = string
  default     = ""
}

variable "api_origin_port" {
  description = "API 오리진 HTTP 포트(api_origin_domain 설정 시)."
  type        = number
  default     = 8080
}

variable "api_path_pattern" {
  description = "API 오리진으로 라우팅할 경로 패턴."
  type        = string
  default     = "/api/*"
}
