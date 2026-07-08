removed {
  from = aws_s3_bucket.lake
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_server_side_encryption_configuration.lake
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_public_access_block.lake
  lifecycle {
    destroy = false
  }
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
