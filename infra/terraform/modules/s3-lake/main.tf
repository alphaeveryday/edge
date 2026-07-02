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

# SSE-KMS(AWS 관리형 aws/s3 키). Bucket Key 로 KMS 요청 비용을 줄인다.
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

# 위 SSE-KMS 가 쓰는 관리형 키(aws/s3)의 ARN. 레이크에 R/W 하는 역할은 S3 권한만으론
# 부족하고 이 키에 대한 kms:Decrypt/GenerateDataKey 가 필요하다(그렇지 않으면 AccessDenied).
# 암호화 방식은 이 모듈이 소유하므로, 키 ARN 도 여기서 노출해 호출부가 IAM 에 반영한다.
data "aws_kms_alias" "s3" {
  name = "alias/aws/s3"
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
