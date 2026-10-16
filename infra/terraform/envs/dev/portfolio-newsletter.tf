resource "aws_sesv2_configuration_set" "portfolio_newsletter" {
  configuration_set_name = "edge-portfolio-newsletter"
  delivery_options { tls_policy = "REQUIRE" }
  reputation_options { reputation_metrics_enabled = true }
  suppression_options { suppressed_reasons = ["BOUNCE", "COMPLAINT"] }
}

resource "aws_sesv2_email_identity" "portfolio_newsletter" {
  email_identity         = "etforca.com"
  configuration_set_name = aws_sesv2_configuration_set.portfolio_newsletter.configuration_set_name
  dkim_signing_attributes { next_signing_key_length = "RSA_2048_BIT" }
}

resource "aws_route53_record" "portfolio_newsletter_dkim" {
  count   = 3
  zone_id = data.aws_route53_zone.etforca.zone_id
  name    = "${aws_sesv2_email_identity.portfolio_newsletter.dkim_signing_attributes[0].tokens[count.index]}._domainkey.etforca.com"
  type    = "CNAME"
  ttl     = 300
  records = ["${aws_sesv2_email_identity.portfolio_newsletter.dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
}

resource "aws_sesv2_contact_list" "portfolio_newsletter" {
  contact_list_name = "edge-portfolio-newsletter"
  description       = "Email-confirmed ETF Orca portfolio newsletter subscribers"
  topic {
    topic_name                  = "daily-portfolio"
    display_name                = "ETF Orca 보유 종목 데일리"
    default_subscription_status = "OPT_OUT"
  }
}

# Only the deterministic mail sender uses the host role; analysis containers keep IMDS disabled.
resource "aws_iam_role_policy" "portfolio_newsletter" {
  name = "portfolio-newsletter"
  role = aws_iam_role.portfolio.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    {
      Effect    = "Allow"
      Action    = ["ses:SendEmail"]
      Resource  = [aws_sesv2_email_identity.portfolio_newsletter.arn, aws_sesv2_configuration_set.portfolio_newsletter.arn]
      Condition = { StringEquals = { "ses:FromAddress" = "newsletter@etforca.com" } }
    },
    {
      Effect   = "Allow"
      Action   = ["ses:CreateContact", "ses:GetContact", "ses:UpdateContact"]
      Resource = "arn:aws:ses:${var.region}:${data.aws_caller_identity.current.account_id}:contact-list/${aws_sesv2_contact_list.portfolio_newsletter.contact_list_name}"
    },
    { Effect = "Allow", Action = ["ses:GetAccount"], Resource = "*" },
    { Effect = "Allow", Action = ["ses:GetEmailIdentity"], Resource = aws_sesv2_email_identity.portfolio_newsletter.arn }
  ] })
}
