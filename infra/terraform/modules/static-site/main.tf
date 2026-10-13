# 정적 사이트 호스팅 — S3(프라이빗) + CloudFront(OAC) + Route53 alias.
# 인증서는 foundation 의 us-east-1 인증서를 certificate_arn 으로 받아 쓴다(모듈은 발급 안 함).
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

# ── SPA fallback 함수 (spa=true 전용) ───────────────────
# 확장자 없는 뷰어 요청을 /index.html 로 리라이트(viewer-request). default behavior 에만
# 연결해 /api/* 의 403/404 JSON 이 왜곡 없이 통과한다 — 로직·전제는 spa-rewrite.js 주석 참조.
resource "aws_cloudfront_function" "spa_rewrite" {
  count   = var.spa ? 1 : 0
  name    = "${var.name}-spa-rewrite"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = file("${path.module}/spa-rewrite.js")
}

# ── www 이동 함수 (redirect_www=true 전용) ──────────────
# www.<도메인> 으로 온 요청을 https://<도메인> 으로 영구 이동시킨다(viewer-request, GET·HEAD 301·그 밖 308).
# viewer-request 함수는 behavior 당 하나라 SPA fallback 과 같이 쓸 수 없다.
resource "aws_cloudfront_function" "www_redirect" {
  count   = var.redirect_www ? 1 : 0
  name    = "${var.name}-www-redirect"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = file("${path.module}/www-redirect.js")

  lifecycle {
    precondition {
      condition     = !var.spa
      error_message = "redirect_www 와 spa 는 함께 켤 수 없다(viewer-request 함수는 behavior 당 하나)."
    }
  }
}

# ── CloudFront 배포 ─────────────────────────────────────
resource "aws_cloudfront_distribution" "this" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  aliases             = concat([var.domain_name], var.redirect_www ? ["www.${var.domain_name}"] : [])
  price_class         = var.price_class
  comment             = var.name

  origin {
    domain_name              = aws_s3_bucket.this.bucket_regional_domain_name
    origin_id                = "s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.this.id
  }

  # 선택: /api/* 를 프록시할 커스텀 오리진. 뷰어는 항상 CloudFront 가 TLS 종단 —
  # 오리진 쪽은 평문 박스(http-only, 데모 mock-broker) 또는 TLS ALB(https-only)를 변수로 고른다.
  dynamic "origin" {
    for_each = var.api_origin_domain != "" ? [1] : []
    content {
      domain_name = var.api_origin_domain
      origin_id   = var.api_origin_id
      dynamic "custom_header" {
        for_each = var.api_origin_header_name == "" ? [] : [1]
        content {
          name  = var.api_origin_header_name
          value = var.api_origin_header_value
        }
      }
      custom_origin_config {
        http_port              = var.api_origin_port
        https_port             = 443
        origin_protocol_policy = var.api_origin_protocol
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

    # SPA fallback — 요청 단계 리라이트라 딥링크가 에러 경로를 타지 않고, 캐시 키도
    # /index.html 로 수렴한다. 없는 정적 에셋(/assets/nope.js)은 S3 404 가 그대로 노출(정직한 에러).
    dynamic "function_association" {
      for_each = var.spa ? [1] : []
      content {
        event_type   = "viewer-request"
        function_arn = aws_cloudfront_function.spa_rewrite[0].arn
      }
    }

    dynamic "function_association" {
      for_each = var.redirect_www ? [1] : []
      content {
        event_type   = "viewer-request"
        function_arn = aws_cloudfront_function.www_redirect[0].arn
      }
    }
  }

  # /api/* → API 오리진. 캐시 비활성 + AllViewer 로 쿼리스트링(ticker 등)을 오리진에 전달.
  dynamic "ordered_cache_behavior" {
    for_each = var.api_origin_domain != "" ? [1] : []
    content {
      path_pattern             = var.api_path_pattern
      target_origin_id         = var.api_origin_id
      viewer_protocol_policy   = "redirect-to-https"
      allowed_methods          = var.api_allowed_methods
      cached_methods           = ["GET", "HEAD"]
      compress                 = true
      cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled
      origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac" # Managed-AllViewerExceptHostHeader

      # www 로 온 API 요청도 이동시킨다 — www 가 두 번째 API 주소로 남지 않게 한다.
      dynamic "function_association" {
        for_each = var.redirect_www ? [1] : []
        content {
          event_type   = "viewer-request"
          function_arn = aws_cloudfront_function.www_redirect[0].arn
        }
      }
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

# ── S3 버킷 정책: 이 배포의 OAC 만 GetObject·ListBucket 허용 ─
# ListBucket 이 없으면 S3 는 없는 키에 404 대신 403(AccessDenied)을 준다. 검색엔진이 없는 주소를
# 접근 거부로 읽지 않게 404 로 맞춘다. 뷰어가 목록을 볼 길은 없다 — 버킷 루트 요청은
# default_root_object 로 바뀌고, 쿼리스트링은 S3 오리진으로 전달하지 않는다.
resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudFrontOAC"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.this.arn}/*"
        Condition = {
          StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.this.arn }
        }
      },
      {
        Sid       = "AllowCloudFrontOACList"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.this.arn
        Condition = {
          StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.this.arn }
        }
      },
    ]
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

# ── Route53 www alias → CloudFront (redirect_www=true 전용) ─
resource "aws_route53_record" "www_alias" {
  for_each = var.redirect_www ? toset(["A", "AAAA"]) : toset([])

  zone_id = var.zone_id
  name    = "www.${var.domain_name}"
  type    = each.value

  alias {
    name                   = aws_cloudfront_distribution.this.domain_name
    zone_id                = aws_cloudfront_distribution.this.hosted_zone_id
    evaluate_target_health = false
  }
}
