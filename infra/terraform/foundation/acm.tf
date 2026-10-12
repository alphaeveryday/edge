# 와일드카드 ACM — 리전당 1장. `*.edgesignal.dev` 이 모든 직접 서브도메인
# (widget-dev·app-dev·edge-dev·admin…, dev/prod 접미사 포함)을 커버한다.
# → 새 서브도메인 추가 시 인증서 재발급 불필요. "존만 있으면 SSL 은 TF 가 알아서".
#
# 두 인증서(apne2·us-east-1)는 같은 도메인이라 ACM DNS 검증 CNAME 이 동일하다.
# 그래서 검증 레코드를 한 번만 만들어(아래 wildcard_validation) 양쪽 검증에 재사용한다.

# 존은 route53.tf 가 소유(import)한다 — 여기선 그 zone_id 를 참조한다.

# ── ap-northeast-2 (ALB 등 리전 서비스용) ───────────────
resource "aws_acm_certificate" "wildcard" {
  domain_name       = "*.${var.zone_name}"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# ── us-east-1 (CloudFront 필수) ─────────────────────────
resource "aws_acm_certificate" "wildcard_cdn" {
  provider          = aws.us_east_1
  domain_name       = "*.${var.zone_name}"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# 검증 레코드는 한 벌만(같은 도메인 → 같은 CNAME). apne2 인증서의 옵션으로 만들되
# 이 레코드가 us-east-1 인증서도 함께 검증한다.
resource "aws_route53_record" "wildcard_validation" {
  for_each = {
    for dvo in aws_acm_certificate.wildcard.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }

  zone_id         = aws_route53_zone.main.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "wildcard" {
  certificate_arn         = aws_acm_certificate.wildcard.arn
  validation_record_fqdns = [for r in aws_route53_record.wildcard_validation : r.fqdn]
}

resource "aws_acm_certificate_validation" "wildcard_cdn" {
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.wildcard_cdn.arn
  validation_record_fqdns = [for r in aws_route53_record.wildcard_validation : r.fqdn]
}

# ── etforca.com (us-east-1, CloudFront 용) ──────────────
# 와일드카드는 루트 도메인을 덮지 않아서 루트를 주 이름, 와일드카드를 SAN 으로 둔다.
# ALB 용(ap-northeast-2) 인증서는 두지 않았다 — etforca.com 으로 API 를 서빙하게 되면 추가한다.
#
# aws_acm_certificate_validation 이 없는 이유: 그 자원은 발급될 때까지 apply 를 붙잡는데,
# 가비아에 넣을 네임서버가 이 apply 에서 처음 나온다. 그래서 apply 는 발급을 기다리지 않고
# 끝내고, 네임서버 위임이 전파되면 ACM 이 혼자 발급한다.
# 72시간 안에 위임하지 못하면 검증 시간 초과로 끝난다 — foundation 을 다시 apply 하면 재발급된다.
# env 는 statuses = ["ISSUED"] 로 조회하므로 발급 전에는 plan 이 실패한다.
resource "aws_acm_certificate" "etforca_cdn" {
  provider                  = aws.us_east_1
  domain_name               = var.etforca_zone_name
  subject_alternative_names = ["*.${var.etforca_zone_name}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# 루트와 와일드카드의 검증 CNAME 은 같다. 둘 다 만들면 레코드 하나를 자원 둘이 소유해
# 한쪽을 지울 때 남은 쪽의 레코드까지 사라지므로 루트 것만 만든다.
resource "aws_route53_record" "etforca_validation" {
  for_each = {
    for dvo in aws_acm_certificate.etforca_cdn.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    } if dvo.domain_name == var.etforca_zone_name
  }

  zone_id         = aws_route53_zone.etforca.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}
