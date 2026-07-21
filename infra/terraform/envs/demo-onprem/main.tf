locals {
  prefix = "edge-demo-onprem"
}

# 데모는 신규 VPC 를 만들지 않고 default VPC 의 public 서브넷을 쓴다(ADR-0033 — dev 와 격리·단순).
# 계정에 default VPC 가 없으면 이 data 가 실패한다 — 그때는 특정 서브넷을 변수로 넘기도록 전환.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default_public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# CloudFront 오리진 프리픽스 — 브라우저→CloudFront→EC2(mock-broker) 프록시의 인바운드 스코프.
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

# 데모 mTLS 클라이언트 인증서 — pre-provisioned(값은 apply 밖 수동 주입, ADR-0033).
# placeholder 로 그릇만 만들고, 실제 cert 는 콘솔/CLI 로 이 파라미터에 넣는다(ignore_changes).
resource "aws_ssm_parameter" "demo_cert" {
  name  = "/${local.prefix}/sync/client-cert"
  type  = "SecureString"
  value = "REPLACE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}

# 가상 온프렘 박스 (EC2 + compose 부트스트랩).
module "demo_onprem" {
  source = "../../modules/demo-onprem"

  name      = local.prefix
  vpc_id    = data.aws_vpc.default.id
  subnet_id = data.aws_subnets.default_public.ids[0]

  instance_type    = var.instance_type
  root_volume_size = var.root_volume_size
  root_volume_type = var.root_volume_type
  root_volume_iops = var.root_volume_iops

  mock_broker_port        = var.mock_broker_port
  cert_parameter_arn      = aws_ssm_parameter.demo_cert.arn
  ingress_prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  # ECR 은 데모 이미지 저장소 확정 전까지 스코프 미지정(전체 pull). 저장소 생기면 좁힌다.
}

# 가상 MTS 페이지 (S3 + CloudFront). CloudFront 의 /api/* → EC2 오리진 프록시 배선은
# mock-broker 컨테이너가 생기는 런타임과 함께(ADR-0033 범위 밖).
module "mts_site" {
  source = "../../modules/static-site"

  name            = "${local.prefix}-mts"
  domain_name     = var.mts_domain
  zone_id         = data.terraform_remote_state.foundation.outputs.zone_id
  certificate_arn = data.terraform_remote_state.foundation.outputs.wildcard_cdn_certificate_arn
  spa             = true
}
