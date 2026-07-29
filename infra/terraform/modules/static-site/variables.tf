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
# 정적 사이트에 API 백엔드를 같은 도메인으로 붙일 때(데모 온프렘 MTS → 박스 mock-broker,
# super-admin 콘솔 → admin ALB). 비우면 정적 전용(기존 동작). 뷰어는 항상 HTTPS —
# 오리진 프로토콜은 api_origin_protocol 로 고른다(평문 박스 http-only | TLS ALB https-only).
variable "api_origin_domain" {
  description = "선택: api_path_pattern 을 프록시할 커스텀 오리진 도메인(예: 데모 박스 EC2 public DNS, ALB 도메인). 비우면 API 오리진 없음."
  type        = string
  default     = ""
}

variable "api_origin_port" {
  description = "API 오리진 HTTP 포트(http-only 일 때만 사용. https-only 는 443 고정)."
  type        = number
  default     = 8080
}

variable "api_origin_protocol" {
  description = "API 오리진 프로토콜 정책. 평문 HTTP 박스(demo mock-broker)는 http-only, TLS ALB(super-admin)는 https-only(:443 고정) — https-only 는 오리진 도메인이 오리진 인증서와 SNI 일치해야 한다(ALB raw dns_name 불가)."
  type        = string
  default     = "http-only" # 기존 호출자(demo mts_site) 하위호환

  validation {
    condition     = contains(["http-only", "https-only"], var.api_origin_protocol)
    error_message = "api_origin_protocol 은 http-only 또는 https-only 만 지원한다."
  }
}

variable "api_allowed_methods" {
  description = "API behavior 의 allowed_methods. CloudFront 는 [GET,HEAD] / [GET,HEAD,OPTIONS] / 7종 전체 세 조합만 허용 — 쓰기 API(POST·PATCH·DELETE)를 프록시하려면 7종 전체."
  type        = list(string)
  default     = ["GET", "HEAD", "OPTIONS"] # 기존 호출자 하위호환(읽기 전용)
}

variable "api_path_pattern" {
  description = "API 오리진으로 라우팅할 경로 패턴."
  type        = string
  default     = "/api/*"
}
