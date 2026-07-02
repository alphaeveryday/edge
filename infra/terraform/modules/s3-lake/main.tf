# 데이터 레이크 단일 버킷.
# raw / canonical / derived / operations_archive 레이어를 프리픽스로 나눈다
# (계층구조 SSOT 는 데이터 파이프라인 설계 문서). 버킷은 하나만 두고
# 라이프사이클·IAM 을 프리픽스 단위로 건다.

resource "aws_s3_bucket" "this" {
  bucket = var.bucket_name
}

# 레이크는 전부 내부 데이터 — 퍼블릭 접근 전면 차단.
resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# canonical 은 article_id 키 덮어쓰기(멱등 병합)라, 잘못된 덮어쓰기 복구용으로 버저닝을 켠다.
resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3(AES256). 저장 시 암호화는 충족하되 KMS 운영비용(권한·키·요청)을 지지 않는다.
# 결정 근거: 지금은 키 단위 감사·접근제어 요구가 없어 SSE-S3 로 단순화한다(YAGNI).
#   - 프로토타입(news-pipeline CDK)은 전용 CMK 로 SSE-KMS 를 썼다(alias/news-pipeline-dev-data).
#     감사·규제 요구가 생기면 그때 전용 CMK 를 만들어 이 rule 을 aws:kms 로 승격한다.
#     (기존 객체는 재암호화되지 않으니 데이터가 적은 초기에 전환하는 게 싸다.)
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  # raw 는 run_id 별 append(재현성용 원본) — 오래되면 Glacier 로 내리고 결국 만료.
  # canonical 은 서비스가 읽는 최신본이라 라이프사이클을 걸지 않는다(보존).
  rule {
    id     = "raw-archive"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    transition {
      days          = var.raw_glacier_days
      storage_class = "GLACIER"
    }

    expiration {
      days = var.raw_expiration_days
    }

    # 버저닝 때문에 위 만료는 delete marker 만 남기고 객체를 noncurrent 로 바꾼다.
    # noncurrent 타이머는 그 시점부터 새로 돌므로, 여기에 raw_expiration_days 를 또
    # 쓰면 실보존이 2배(만료+만료)가 된다 — 짧은 유예만 두고 실제 삭제한다.
    noncurrent_version_expiration {
      noncurrent_days = var.raw_noncurrent_grace_days
    }
  }

  # 실패한 멀티파트 업로드 조각이 과금되지 않게 정리(버킷 전체).
  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
