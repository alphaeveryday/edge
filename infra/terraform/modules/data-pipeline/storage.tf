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

# KRX 그릇은 ALPHA-336 때 CLI 로 먼저 만들어져 TF 밖에 있었다. 형제 시크릿과 같은 소유
# 구조로 되돌리되, 기존 그릇 입양(import)은 호출부에서 한다 — 시크릿 ARN 은 AWS 가 붙이는
# 랜덤 접미사를 포함해 환경마다 다르고, import 는 그 ARN 을 id 로 받기 때문이다.
resource "aws_secretsmanager_secret" "krx" {
  name        = "${var.name}/krx/login"
  description = "KRX 스크립트 로그인 계정 for ${var.name} (수동 주입: {\"mbr_id\":\"...\",\"pw\":\"...\"})"
}
