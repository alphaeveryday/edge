resource "aws_s3_bucket" "lake" {
  bucket = var.lake_bucket_name
}

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

# TF 는 시크릿 "그릇"만 만든다. 값(버전)은 TF 밖에서 수동 주입한다.
resource "aws_secretsmanager_secret" "fmp" {
  name        = "${var.name}/fmp/api-key"
  description = "FMP API key for ${var.name} (수동 주입: {\"apikey\":\"...\"})"
}

resource "aws_secretsmanager_secret" "kis" {
  name        = "${var.name}/kis/oauth"
  description = "KIS OAuth credentials for ${var.name} (수동 주입: {\"app_key\":\"...\",\"app_secret\":\"...\"})"
}

resource "aws_secretsmanager_secret" "dart" {
  name        = "${var.name}/dart/api-key"
  description = "OpenDART API key for ${var.name} (수동 주입: {\"apikey\":\"...\"})"
}
