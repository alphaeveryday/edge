# ── S3: 데이터 레이크 버킷 (edge 소유) ─────────────────
# active lake bucket. raw/canonical/curated prefix 를 함께 담는다.
resource "aws_s3_bucket" "lake" {
  bucket = "${var.name}-lake"
}

# 저장 시 암호화(SSE-S3). 뉴스·금융 데이터의 최소 보호.
resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# lake 전환 후 legacy raw/curated 버킷은 더 이상 TF 가 소유하지 않는다.
# 원격 버킷은 삭제하지 않고 state 에서만 forget 한다. 콘솔 수동 정리는 이 apply 이후 가능하다.
removed {
  from = aws_s3_bucket.raw
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket.curated
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_server_side_encryption_configuration.raw
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_server_side_encryption_configuration.curated
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_public_access_block.raw
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_public_access_block.curated
  lifecycle {
    destroy = false
  }
}

# fmp/openai 시크릿은 레거시 SFN(analyze 페이즈)만 쓰던 것이라 ALPHA-549 에서 함께 걷어냈다.
# 현행 data-pipeline 은 자체 시크릿(edge-dev-data-pipeline/*)을 쓴다.
