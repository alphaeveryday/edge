# 프록시 사이트 — CloudFront(도메인·TLS 종단) → 단일 커스텀 오리진(평문 박스) + Route53 alias.
# 데모 토폴로지의 서빙 원칙(ALPHA-632): 모든 표면은 박스가 서빙하고 CloudFront 는 도메인별
# 창문이다 — 정적 서빙·라우팅·SPA fallback 은 전부 오리진(박스 컨테이너) 소관이라 behavior 는
# 하나면 된다. demo-mts(mock-broker :8080)·demo-console(nginx :8090)이 동일하게 재사용한다.
# S3 정적 호스팅이 실물인 표면(실클라우드 콘솔)은 static-site 모듈을 쓴다 — 섞지 않는다.
# 인증서는 foundation 의 us-east-1 와일드카드를 certificate_arn 으로 받아 쓴다(모듈은 발급 안 함).

resource "aws_cloudfront_distribution" "this" {
  enabled         = true
  is_ipv6_enabled = true
  aliases         = [var.domain_name]
  price_class     = var.price_class
  comment         = var.name

  origin {
    domain_name = var.origin_domain
    origin_id   = "proxy"
    custom_origin_config {
      http_port              = var.origin_port
      https_port             = 443
      origin_protocol_policy = "http-only" # 뷰어는 CloudFront 가 TLS 종단, 오리진은 평문 박스
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # 전 경로 캐시 비활성 — 세션 쿠키 인증 표면(콘솔)이 캐시로 교차 오염되면 안 되고,
  # AllViewerExceptHostHeader 로 쿠키·쿼리스트링을 오리진에 그대로 전달한다.
  # 정적(MTS)도 캐시하지 않는 건 의도다: 무효화 경로가 없으므로(ALPHA-632 로 CD 무효화 잡 제거)
  # 엣지 캐시를 켜면 이미지 재배포 후 최대 TTL 만큼 스테일이 남는다 — 데모 트래픽 규모에서
  # 오리진 직행 비용이 스테일 함정보다 싸다.
  default_cache_behavior {
    target_origin_id         = "proxy"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = true
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac" # Managed-AllViewerExceptHostHeader
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = var.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

# ── Route53 alias → CloudFront (A/AAAA) ─────────────────
resource "aws_route53_record" "alias" {
  for_each = toset(["A", "AAAA"])

  zone_id = var.zone_id
  name    = var.domain_name
  type    = each.value

  alias {
    name                   = aws_cloudfront_distribution.this.domain_name
    zone_id                = aws_cloudfront_distribution.this.hosted_zone_id
    evaluate_target_health = false
  }
}
