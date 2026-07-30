# 데모 검수 콘솔 공개 배선(ALPHA-627) — CloudFront → 박스 tenant-console-ui(nginx :console_port).
# S3 없는 순수 프록시 배포라 static-site 모듈(S3+OAC 중심)을 재사용하지 않고 여기 둔다.
# 박스 nginx 가 SPA fallback(try_files)과 /api 프록시를 모두 담당하므로 behavior 는 하나면 된다.
# 진입 게이트는 콘솔 로그인 화면(ALPHA-626) — autosession(무인증 관리자 세션) 빌드는 폐기됐다.
# 데모 자세 결정: "검수 콘솔은 내부망 전용(SSM 터널)" 재현보다 시연 편의를 우선(2026-07-30).

resource "aws_cloudfront_distribution" "console" {
  enabled         = true
  is_ipv6_enabled = true
  aliases         = [var.console_domain]
  price_class     = "PriceClass_200" # 아시아(서울 엣지) 포함 — mts_site 와 동일
  comment         = "${local.prefix}-console"

  origin {
    domain_name = module.demo_onprem.public_dns
    origin_id   = "console"
    custom_origin_config {
      http_port              = var.console_port
      https_port             = 443
      origin_protocol_policy = "http-only" # 뷰어는 CloudFront 가 TLS 종단, 오리진은 평문 박스
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # 전 경로 캐시 비활성 — 세션 쿠키 인증 콘솔이라 캐시가 응답을 교차 오염시키면 안 되고,
  # AllViewerExceptHostHeader 로 쿠키·쿼리스트링을 오리진에 그대로 전달한다(mts /api 와 동일 정책).
  default_cache_behavior {
    target_origin_id         = "console"
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
    acm_certificate_arn      = data.terraform_remote_state.foundation.outputs.wildcard_cdn_certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

resource "aws_route53_record" "console_alias" {
  for_each = toset(["A", "AAAA"])

  zone_id = data.terraform_remote_state.foundation.outputs.zone_id
  name    = var.console_domain
  type    = each.value

  alias {
    name                   = aws_cloudfront_distribution.console.domain_name
    zone_id                = aws_cloudfront_distribution.console.hosted_zone_id
    evaluate_target_health = false
  }
}
