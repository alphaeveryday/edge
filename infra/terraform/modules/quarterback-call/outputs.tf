# CloudFront 오리진 도메인 — https://<id>.lambda-url.<region>.on.aws/ 에서 호스트만 뗀다.
output "function_url_domain" {
  value = regex("^https://([^/]+)/", aws_lambda_function_url.this.function_url)[0]
}

# CloudFront 가 /api/* 오리진 요청에 붙일 값(헤더 이름은 handler.mjs ORIGIN_HEADER).
output "origin_header_value" {
  value     = local.origin_header_value
  sensitive = true
}
