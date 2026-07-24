# 정적 사이트 호스팅 — S3(프라이빗) + CloudFront(OAC) + Route53 alias.
# 인증서는 foundation 의 us-east-1 와일드카드를 certificate_arn 으로 받아 쓴다(모듈은 발급 안 함).
# 빌드 산출물 업로드(s3 sync)와 무효화(invalidation)는 CD 가 한다 — 이 모듈은 인프라만 소유한다.
# widget-ui·tenant-console-ui 등 프론트가 이 모듈을 동일하게 재사용한다.

# ── S3 (프라이빗 — CloudFront OAC 로만 접근) ─────────────
resource "aws_s3_bucket" "this" {
  bucket = var.name
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    object_ownership = "BucketOwnerEnforced" # ACL 비활성 — 정책 기반 접근만
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ── CloudFront Origin Access Control (S3 프라이빗 유지) ─
resource "aws_cloudfront_origin_access_control" "this" {
  name                              = var.name
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ── CloudFront 배포 ─────────────────────────────────────
resource "aws_cloudfront_distribution" "this" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  aliases             = [var.domain_name]
  price_class         = var.price_class
  comment             = var.name

  origin {
    domain_name              = aws_s3_bucket.this.bucket_regional_domain_name
    origin_id                = "s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.this.id
  }

  # 선택: /api/* 를 프록시할 커스텀 오리진(데모 박스 mock-broker). 뷰어 HTTPS → 오리진 HTTP.
  dynamic "origin" {
    for_each = var.api_origin_domain != "" ? [1] : []
    content {
      domain_name = var.api_origin_domain
      origin_id   = "api"
      custom_origin_config {
        http_port              = var.api_origin_port
        https_port             = 443
        origin_protocol_policy = "http-only" # 오리진(박스)은 평문 HTTP — CloudFront 가 뷰어 TLS 종단
        origin_ssl_protocols   = ["TLSv1.2"]
      }
    }
  }

  default_cache_behavior {
    target_origin_id       = "s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = "658327ea-f89d-4fab-a63d-7e88639e58f6" # AWS Managed-CachingOptimized
  }

  # /api/* → API 오리진. 캐시 비활성 + AllViewer 로 쿼리스트링(ticker 등)을 오리진에 전달.
  dynamic "ordered_cache_behavior" {
    for_each = var.api_origin_domain != "" ? [1] : []
    content {
      path_pattern             = var.api_path_pattern
      target_origin_id         = "api"
      viewer_protocol_policy   = "redirect-to-https"
      allowed_methods          = ["GET", "HEAD", "OPTIONS"]
      cached_methods           = ["GET", "HEAD"]
      compress                 = true
      cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled
      origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac" # Managed-AllViewerExceptHostHeader
    }
  }

  # SPA 라우팅: 존재하지 않는 경로도 index.html 로 200 응답(클라이언트 라우터가 처리).
  dynamic "custom_error_response" {
    for_each = var.spa ? toset([403, 404]) : toset([])
    content {
      error_code            = custom_error_response.value
      response_code         = 200
      response_page_path    = "/index.html"
      error_caching_min_ttl = 10
    }
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

# ── S3 버킷 정책: 이 배포의 OAC 만 GetObject 허용 ───────
resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.this.arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.this.arn }
      }
    }]
  })
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
