# ── S3: 원시/정제 데이터 버킷 (edge 소유로 신설) ────────
resource "aws_s3_bucket" "raw" {
  bucket = "${var.name}-raw"
}

resource "aws_s3_bucket" "curated" {
  bucket = "${var.name}-curated"
}

# 저장 시 암호화(SSE-S3). 뉴스·금융 원시데이터의 최소 보호.
resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "curated" {
  bucket = aws_s3_bucket.curated.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "curated" {
  bucket                  = aws_s3_bucket.curated.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── 외부 API 키 시크릿 (edge 소유로 신설, 값은 수동 주입) ─────────────
# TF 는 시크릿 "그릇"만 만든다. 실제 키 값은 apply 후 콘솔/CLI 로 넣고, 이후 TF 가 덮지 않도록
# secret_string 변경을 무시한다. 앱은 '{"apikey": "..."}' 형태를 기대한다(valueFrom ':apikey::').
resource "aws_secretsmanager_secret" "fmp" {
  name        = "${var.name}/fmp/api-key"
  description = "FMP API key for ${var.name} (수동 주입: {\"apikey\":\"...\"})"
}

resource "aws_secretsmanager_secret_version" "fmp" {
  secret_id     = aws_secretsmanager_secret.fmp.id
  secret_string = jsonencode({ apikey = "REPLACE_ME" })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "openai" {
  name        = "${var.name}/openai/api-key"
  description = "OpenAI API key for ${var.name} (수동 주입: {\"apikey\":\"...\"})"
}

resource "aws_secretsmanager_secret_version" "openai" {
  secret_id     = aws_secretsmanager_secret.openai.id
  secret_string = jsonencode({ apikey = "REPLACE_ME" })

  lifecycle {
    ignore_changes = [secret_string]
  }
}
