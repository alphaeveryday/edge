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

# 공시 completed manifest의 winner를 DB pending 원장에 넣기 전까지 mutable canonical 덮어쓰기를
# 견디는 run-scoped snapshot이다. 분당 실행마다 전체 날짜 파티션을 복사하므로 영구 보존하면
# run 수 × 파티션 크기로 자란다. 정상 재시도보다 충분히 긴 30일을 두되, DB 원장 자체의
# until-success 보존에는 만료를 두지 않는다.
# 버킷당 수명 규칙 리소스는 하나뿐이다 — 규칙을 더할 때 이 블록에 rule 을 더한다(리소스를 새로 만들면 서로 덮어쓴다).
resource "aws_s3_bucket_lifecycle_configuration" "canonical_run_artifacts" {
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "expire-canonical-run-artifacts"
    status = "Enabled"

    filter {
      prefix = "operations_archive/canonical_run_artifacts/"
    }

    expiration {
      days = 30
    }
  }

  # 분석 엔진(analysis-engine-v2) 워커가 분석 1건마다 남기는 관측 기록. 읽는 곳은 개발자 로컬 대시보드뿐이다
  # (cloud/dashboard.py). 만료가 없어 2026-11-24 기준 117.7GiB, 분석이 모두 실패하는 날에도 하루 2.6GiB 씩 늘었다.
  # 그 이전 기록은 이 규칙을 걸기 전에 Linux PC HDD 에 보관했다. 객체마다 자기 생성일 기준으로 만료된다.
  rule {
    id     = "expire-analysis-v2-runs"
    status = "Enabled"

    filter {
      prefix = "analysis-v2/runs/"
    }

    expiration {
      days = 30
    }
  }
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
