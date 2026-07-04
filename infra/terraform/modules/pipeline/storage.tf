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
# TF 는 시크릿 "그릇"만 만든다. 값(버전)은 TF 밖에서 주입한다: `aws secretsmanager put-secret-value
# --secret-id <name>/fmp/api-key --secret-string '{"apikey":"..."}'`. 앱은 valueFrom ':apikey::' 로 읽는다.
# 보안(ALPHA-312): placeholder 버전조차 TF 로 관리하지 않는다 — 관리하면 plan refresh 가 GetSecretValue
# 를 호출해야 하고, read-only plan 역할(PR 컨텍스트=신뢰 불가)이 시크릿 값을 읽게 되기 때문이다.
resource "aws_secretsmanager_secret" "fmp" {
  name        = "${var.name}/fmp/api-key"
  description = "FMP API key for ${var.name} (수동 주입: {\"apikey\":\"...\"})"
}

resource "aws_secretsmanager_secret" "openai" {
  name        = "${var.name}/openai/api-key"
  description = "OpenAI API key for ${var.name} (수동 주입: {\"apikey\":\"...\"})"
}

# placeholder 버전 리소스는 TF 관리에서 제거한다 — state 에서만 forget 하고 AWS 의 기존 버전은 보존한다
# (destroy=false). 이전엔 REPLACE_ME placeholder 를 만들었고, 이제 값은 위 CLI 로만 주입한다.
removed {
  from = aws_secretsmanager_secret_version.fmp
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_secretsmanager_secret_version.openai
  lifecycle {
    destroy = false
  }
}
