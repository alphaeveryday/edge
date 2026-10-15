# Adopt the existing portfolio host. Infrastructure changes run through dev CD only.
# Workflows preserve the existing gate via read-only CloudFront configuration access.
variable "portfolio_origin_header" {
  type      = string
  sensitive = true
  validation {
    condition     = length(var.portfolio_origin_header) >= 32
    error_message = "The existing portfolio origin gate must be supplied by CI."
  }
}

resource "aws_secretsmanager_secret" "portfolio" {
  name = "edge/portfolio/runtime"
  tags = { Project = "edge-portfolio" }
  lifecycle { prevent_destroy = true }
}

# Secret value is bootstrapped by the dev release workflow, never stored in Terraform state.
resource "aws_secretsmanager_secret" "portfolio_admin" {
  name = "edge/portfolio/admin"
  tags = { Project = "edge-portfolio" }
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket" "portfolio_releases" {
  bucket = "edge-portfolio-deploy-393229433969"
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_versioning" "portfolio_releases" {
  bucket = aws_s3_bucket.portfolio_releases.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_public_access_block" "portfolio_releases" {
  bucket                  = aws_s3_bucket.portfolio_releases.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "portfolio_releases" {
  bucket = aws_s3_bucket.portfolio_releases.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_iam_role" "portfolio" {
  name               = "edge-portfolio"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "portfolio_ssm" {
  role       = aws_iam_role.portfolio.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "portfolio_runtime" {
  name = "portfolio-runtime"
  role = aws_iam_role.portfolio.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [aws_secretsmanager_secret.portfolio.arn, aws_secretsmanager_secret.portfolio_admin.arn, "arn:aws:secretsmanager:ap-northeast-2:393229433969:secret:edge/analysis-v2/api-reader-am6PPC"] },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.portfolio_releases.arn}/*" }
  ] })
}
resource "aws_iam_instance_profile" "portfolio" {
  name = "edge-portfolio"
  role = aws_iam_role.portfolio.name
}
resource "aws_security_group" "portfolio" {
  name        = "edge-portfolio"
  description = "Portfolio HTTPS from CloudFront, ACME HTTP challenge"
  vpc_id      = module.network.vpc_id
  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = ["pl-22a6434b"]
  }
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "ACME HTTP challenge only"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_security_group_rule" "portfolio_outlook" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = "sg-0f5712330f2cbd2b1"
  source_security_group_id = aws_security_group.portfolio.id
  description              = "Portfolio read-only outlook"
}
resource "aws_instance" "portfolio" {
  ami                         = "ami-0870825cefcaafcc8"
  instance_type               = "t3.small"
  subnet_id                   = "subnet-0fb30bfa3f1004918"
  vpc_security_group_ids      = [aws_security_group.portfolio.id]
  iam_instance_profile        = aws_iam_instance_profile.portfolio.name
  associate_public_ip_address = true
  user_data                   = file("${path.module}/portfolio-bootstrap.sh")
  root_block_device {
    volume_size           = 30
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = false
  }
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  tags = { Name = "edge-portfolio", Jira = "ALPHA-1266" }
  lifecycle { prevent_destroy = true }
}
resource "aws_eip" "portfolio" {
  domain = "vpc"
  lifecycle { prevent_destroy = true }
}
resource "aws_eip_association" "portfolio" {
  instance_id   = aws_instance.portfolio.id
  allocation_id = aws_eip.portfolio.id
}
resource "aws_route53_record" "portfolio_origin" {
  zone_id = data.aws_route53_zone.etforca.zone_id
  name    = "portfolio-origin.etforca.com"
  type    = "A"
  ttl     = 60
  records = [aws_eip.portfolio.public_ip]
}

# Existing dev-only OIDC role performs application releases; it cannot change CloudFront configuration.
resource "aws_iam_role_policy" "portfolio_deploy" {
  name = "portfolio-release"
  role = split("/", module.gha_deploy_dev.role_arn)[1]
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"], Resource = aws_secretsmanager_secret.portfolio_admin.arn },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject"], Resource = "${aws_s3_bucket.portfolio_releases.arn}/releases/*" },
    { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = [aws_instance.portfolio.arn, "arn:aws:ssm:ap-northeast-2::document/AWS-RunShellScript"] },
    { Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = "*" },
    { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = [aws_ssm_parameter.portfolio_deploy.arn, aws_ssm_parameter.portfolio_admin_connection.arn] },
    { Effect = "Allow", Action = ["ssm:StartSession"], Resource = aws_instance.portfolio.arn, Condition = { BoolIfExists = { "ssm:SessionDocumentAccessCheck" = "true" } } },
    { Effect = "Allow", Action = ["ssm:StartSession"], Resource = aws_ssm_document.portfolio_admin.arn },
    { Effect = "Allow", Action = ["ssmmessages:OpenDataChannel"], Resource = "arn:aws:ssm:ap-northeast-2:393229433969:session/$${aws:userid}-*" },
    { Effect = "Allow", Action = ["ssm:TerminateSession"], Resource = "arn:aws:ssm:ap-northeast-2:393229433969:session/*", Condition = { StringEquals = { "ssm:resourceTag/aws:ssmmessages:target-id" = aws_instance.portfolio.id, "ssm:resourceTag/aws:ssmmessages:session-id" = "$${aws:userid}" } } }
  ] })
}

resource "aws_ssm_parameter" "portfolio_deploy" {
  name  = "/edge/portfolio/deployment"
  type  = "String"
  value = jsonencode({ instance_id = aws_instance.portfolio.id, bucket = aws_s3_bucket.portfolio_releases.id, landing_bucket = module.landing_site.bucket_name, distribution_id = module.landing_site.distribution_id })
}

# This document has no caller-selectable port or remote host: only the loopback admin service.
resource "aws_ssm_document" "portfolio_admin" {
  name            = "edge-portfolio-admin-tunnel"
  document_type   = "Session"
  document_format = "JSON"
  content = jsonencode({
    schemaVersion = "1.0"
    description   = "Portfolio administrator on fixed port 18080; no shell or remote host"
    sessionType   = "Port"
    properties    = { portNumber = "18080", localPortNumber = "18080", type = "LocalPortForwarding" }
  })
}

resource "aws_ssm_parameter" "portfolio_admin_connection" {
  name  = "/edge/portfolio/admin-connection"
  type  = "String"
  value = jsonencode({ instance_id = aws_instance.portfolio.id, document = aws_ssm_document.portfolio_admin.name })
}

# Attach only to the operator identity designated by the user. No IAM user/access key is created.
resource "aws_iam_policy" "portfolio_operator" {
  name        = "edge-portfolio-ssm-operator"
  description = "SSM sessions on the portfolio host only; no SSH, port forwarding or cloud mutation"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ssm:StartSession"], Resource = [aws_instance.portfolio.arn, "arn:aws:ssm:ap-northeast-2:393229433969:document/SSM-SessionManagerRunShell"] },
    { Effect = "Allow", Action = ["ssm:ResumeSession", "ssm:TerminateSession"], Resource = "arn:aws:ssm:ap-northeast-2:393229433969:session/$${aws:userid}-*" }
  ] })
}

# Attach only to the operator identity designated by the user. No IAM user/access key is created.
resource "aws_iam_policy" "portfolio_admin_access" {
  name        = "edge-portfolio-admin-access"
  description = "Fixed administrator tunnel only; no shell, arbitrary port, remote host or cloud mutation"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = aws_ssm_parameter.portfolio_admin_connection.arn },
    { Effect = "Allow", Action = ["ssm:StartSession"], Resource = aws_instance.portfolio.arn, Condition = { BoolIfExists = { "ssm:SessionDocumentAccessCheck" = "true" } } },
    { Effect = "Allow", Action = ["ssm:StartSession"], Resource = aws_ssm_document.portfolio_admin.arn },
    { Effect = "Allow", Action = ["ssmmessages:OpenDataChannel"], Resource = "arn:aws:ssm:ap-northeast-2:393229433969:session/$${aws:userid}-*" },
    { Effect = "Allow", Action = ["ssm:ResumeSession", "ssm:TerminateSession"], Resource = "arn:aws:ssm:ap-northeast-2:393229433969:session/*", Condition = { StringEquals = { "ssm:resourceTag/aws:ssmmessages:target-id" = aws_instance.portfolio.id, "ssm:resourceTag/aws:ssmmessages:session-id" = "$${aws:userid}" } } }
  ] })
}

# Local operator authenticates with the existing work profile, then uses temporary tunnel-only credentials.
resource "aws_iam_role" "portfolio_admin_local" {
  name                 = "edge-portfolio-admin-local"
  max_session_duration = 3600
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = "sts:AssumeRole", Principal = { AWS = "arn:aws:iam::393229433969:user/junyoung727" } }
  ] })
}

resource "aws_iam_role_policy_attachment" "portfolio_admin_local" {
  role       = aws_iam_role.portfolio_admin_local.name
  policy_arn = aws_iam_policy.portfolio_admin_access.arn
}
