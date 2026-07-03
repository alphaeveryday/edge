# ── Terraform state 버킷 ────────────────────────────────
# 원격 state 의 그릇. envs/*/backend.tf 가 이 버킷을 가리킨다.
# 락은 별도 DynamoDB 없이 S3 네이티브 락(backend 의 use_lockfile=true)으로 건다
# — Terraform 1.11+ 정식 지원. state 옆에 .tflock 을 조건부 쓰기로 만들어 동시 apply 를 막는다.
resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name

  # state 는 인프라의 원본. 실수로 이 스택을 destroy 해도 버킷째 날아가지 않게 막는다.
  lifecycle {
    prevent_destroy = true
  }
}

# 버전 관리 — state 손상·오적용 시 이전 버전으로 복구 가능. 원격 state 의 필수 안전장치.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

# 저장 시 암호화(SSE-S3). state 에는 민감값(시크릿 ARN·엔드포인트 등)이 들어갈 수 있다.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 퍼블릭 접근 전면 차단. state 버킷은 절대 공개되면 안 된다.
resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
