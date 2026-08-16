# 프록시 사이트 — CloudFront(도메인·TLS 종단) → 커스텀 오리진(평문 박스) + Route53 alias.
# 데모 토폴로지의 서빙 원칙(ALPHA-632): 모든 표면은 박스가 서빙하고 CloudFront 는 도메인별
# 창문이다 — 정적 서빙·라우팅·SPA fallback 은 전부 오리진(박스 컨테이너) 소관이라 기본
# behavior 는 하나다. demo-mts(mock-broker :8080)·demo-console(nginx :8090)이 동일하게 재사용한다.
# 예외 하나(ADR-0053, ALPHA-992): api_origin_port 를 주면 설명 조회 경로(api_path_pattern)만
# 별도 behavior 로 publication-api 컨테이너에 직행한다 — 위젯 직접 호출의 "동일 오리진 경로
# 프록시" 표준형. 이 behavior 는 쿠키·인증 헤더를 오리진에 전달하지 않는다(strip — ADR-0053
# 결정 5: 위젯 도메인의 세션 토큰이 publication-api 로그에 흘러들지 않게).
# S3 정적 호스팅이 실물인 표면(실클라우드 콘솔)은 static-site 모듈을 쓴다 — 섞지 않는다.
# 인증서는 foundation 의 us-east-1 와일드카드를 certificate_arn 으로 받아 쓴다(모듈은 발급 안 함).

# API 직행 behavior 전용 origin request policy — 쿠키·뷰어 헤더 미전달, 쿼리스트링(trade_date)만
# 전달. Managed-AllViewerExceptHostHeader(기본 behavior)는 쿠키까지 전달하므로 여기선 못 쓴다.
resource "aws_cloudfront_origin_request_policy" "api_no_auth" {
  count = var.api_origin_port == null ? 0 : 1

  name    = "${var.name}-api-no-auth"
  comment = "strip cookies/auth headers, forward query strings only (ADR-0053)"

  cookies_config {
    cookie_behavior = "none"
  }
  headers_config {
    header_behavior = "none"
  }
  query_strings_config {
    query_string_behavior = "all"
  }
}

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

  # API 직행 오리진 — 같은 박스의 publication-api 호스트 포트 (api_origin_port 지정 시).
  dynamic "origin" {
    for_each = var.api_origin_port == null ? [] : [var.api_origin_port]
    content {
      domain_name = var.origin_domain
      origin_id   = "api"
      custom_origin_config {
        http_port              = origin.value
        https_port             = 443
        origin_protocol_policy = "http-only"
        origin_ssl_protocols   = ["TLSv1.2"]
      }
    }
  }

  # 설명 조회 경로 직행(ADR-0053) — 읽기 전용 표면이라 GET/HEAD 만. 캐시는 기본 behavior 와
  # 같은 이유로 끈다(차단·정정 반영 지연을 엣지에 더하지 않는다 — 서버 캐시 TTL 이 상한).
  dynamic "ordered_cache_behavior" {
    for_each = var.api_origin_port == null ? [] : [var.api_path_pattern]
    content {
      path_pattern             = ordered_cache_behavior.value
      target_origin_id         = "api"
      viewer_protocol_policy   = "redirect-to-https"
      allowed_methods          = ["GET", "HEAD"]
      cached_methods           = ["GET", "HEAD"]
      compress                 = true
      cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled
      origin_request_policy_id = aws_cloudfront_origin_request_policy.api_no_auth[0].id
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
