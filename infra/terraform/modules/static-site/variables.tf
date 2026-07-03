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
