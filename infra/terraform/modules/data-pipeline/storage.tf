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

# KIS 액세스 토큰 공유 캐시(ALPHA-573) — **TF 는 이름만 고정하고 파라미터는 만들지 않는다.**
#
# 왜 필요한가: kis 브랜치 4개가 같은 앱키로 동시에 발급하면 KIS 분당 1회 제한이 직렬 큐를
# 만들어 마지막 브랜치가 222초를 기다린다(2026-07-24 실측). 토큰은 24시간 유효하므로 공유하면
# 발급이 하루 1회로 수렴한다.
#
# 왜 `aws_ssm_parameter` 를 안 쓰나: TF 가 SecureString 을 소유하면 apply 때마다 refresh 가
# **복호화된 토큰을 state 에 평문으로** 적어 넣는다. `ignore_changes = [value]` 는 diff 만
# 막고 refresh 기록은 못 막는다 — 시크릿을 state 에 두지 않는다는 원칙(ADR-0009)이 깨진다
# (edge-review 지적). 그래서 그릇 생성·갱신을 수집 컨테이너의 `put_parameter` 에 맡긴다
# (없으면 그때 만들어진다). 시크릿 그릇들이 "TF 는 그릇만, 값은 수동 주입"인 것의 한 칸 더
# 나아간 형태다 — 여기선 값이 매일 자동으로 바뀌므로 그릇조차 TF 가 잡을 이유가 없다.
#
# 파라미터가 없거나 권한이 없으면 컨테이너는 각자 발급하는 현행 동작으로 폴백한다 — 이
# 캐시가 통째로 사라져도 데이터·가용성은 안 다치고 느려질 뿐이다.
locals {
  kis_token_param_name = "/${var.name}/kis/access-token"
  kis_token_param_arn  = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.kis_token_param_name}"
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
