# Adopt existing resources without recreating them. Imports execute in GitHub dev apply.
import {
  to = aws_secretsmanager_secret.portfolio
  id = "arn:aws:secretsmanager:ap-northeast-2:393229433969:secret:edge/portfolio/runtime-yRVFeL"
}

import {
  to = aws_s3_bucket.portfolio_releases
  id = "edge-portfolio-deploy-393229433969"
}

import {
  to = aws_s3_bucket_versioning.portfolio_releases
  id = "edge-portfolio-deploy-393229433969"
}

import {
  to = aws_s3_bucket_public_access_block.portfolio_releases
  id = "edge-portfolio-deploy-393229433969"
}

import {
  to = aws_s3_bucket_server_side_encryption_configuration.portfolio_releases
  id = "edge-portfolio-deploy-393229433969"
}

import {
  to = aws_iam_role.portfolio
  id = "edge-portfolio"
}

import {
  to = aws_iam_role_policy_attachment.portfolio_ssm
  id = "edge-portfolio/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

import {
  to = aws_iam_role_policy.portfolio_runtime
  id = "edge-portfolio:portfolio-runtime"
}

import {
  to = aws_iam_instance_profile.portfolio
  id = "edge-portfolio"
}

import {
  to = aws_security_group.portfolio
  id = "sg-0531959279a7193eb"
}

import {
  to = aws_security_group_rule.portfolio_outlook
  id = "sg-0f5712330f2cbd2b1_ingress_tcp_5432_5432_sg-0531959279a7193eb"
}

import {
  to = aws_instance.portfolio
  id = "i-096934effbf36bf5a"
}

import {
  to = aws_eip.portfolio
  id = "eipalloc-001a7767a35b26387"
}

import {
  to = aws_eip_association.portfolio
  id = "eipassoc-022ca63cc36eadfc7"
}

import {
  to = aws_route53_record.portfolio_origin
  id = "Z06863483D0LGERU5VXVX_portfolio-origin.etforca.com_A"
}
