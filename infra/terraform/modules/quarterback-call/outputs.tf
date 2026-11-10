# CloudFront 오리진 도메인 — https://<id>.lambda-url.<region>.on.aws/ 에서 호스트만 뗀다.
output "function_url_domain" {
  value = regex("^https://([^/]+)/", aws_lambda_function_url.this.function_url)[0]
}
