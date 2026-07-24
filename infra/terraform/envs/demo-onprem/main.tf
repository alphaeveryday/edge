data "aws_caller_identity" "current" {}

locals {
  prefix = "edge-demo-onprem"

  # 데모 mTLS 클라이언트 인증서용 SSM SecureString. terraform 은 값을 관리하지 않는다 —
  # SecureString 을 terraform 이 관리하면 평문이 state 에 저장되고, refresh 가 수동 주입한
  # 실제 cert 를 공유 state 로 끌어와 노출한다. 운영자가 CLI 로 생성하고(아래 주석),
  # 여기서는 ARN 만 구성해 instance profile 정책에 넘긴다(deepseek 시크릿과 같은 패턴).
  #   aws ssm put-parameter --name /edge-demo-onprem/sync/client-cert --type SecureString --value "$(cat client.pem)"
  cert_param_name = "/${local.prefix}/sync/client-cert"
  cert_param_arn  = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.cert_param_name}"
}

# 데모는 신규 VPC 를 만들지 않는다(ADR-0033 — dev 와 격리·단순).
# subnet_id 를 주면 그 서브넷을, 없으면 default VPC 의 첫 public 서브넷을 쓴다.
data "aws_vpc" "default" {
  count   = var.subnet_id == null ? 1 : 0
  default = true
}

data "aws_subnets" "default_public" {
  count = var.subnet_id == null ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }
}

# 선택된 서브넷(명시 or default VPC) — vpc_id 는 여기서 파생한다.
data "aws_subnet" "selected" {
  id = var.subnet_id != null ? var.subnet_id : data.aws_subnets.default_public[0].ids[0]
}

# CloudFront 오리진 프리픽스 — 브라우저→CloudFront→EC2(mock-broker) 프록시의 인바운드 스코프.
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

# 가상 온프렘 박스 (EC2 + compose 부트스트랩).
module "demo_onprem" {
  source = "../../modules/demo-onprem"

  name      = local.prefix
  vpc_id    = data.aws_subnet.selected.vpc_id
  subnet_id = data.aws_subnet.selected.id

  instance_type    = var.instance_type
  root_volume_size = var.root_volume_size
  root_volume_type = var.root_volume_type
  root_volume_iops = var.root_volume_iops

  mock_broker_port        = var.mock_broker_port
  cert_parameter_arn      = local.cert_param_arn
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
  # 단일 페이지(클라이언트 탭 전환, pushState/해시 라우팅 없음)라 SPA fallback 불필요.
  # spa=true 면 배포 전역 custom_error_response(403/404→index.html 200)가 /api/* 에도 적용돼
  # mock-broker 오류를 HTML 200 으로 가린다(Codex 지적) — false 로 두어 API 오류가 그대로 통과.
  spa = false

  # /api/* → 데모 박스 mock-broker(HTTP :mock_broker_port). 브라우저 MTS 의 AI 탭이
  # 같은 도메인으로 박스를 실호출한다(mixed-content 회피: 뷰어 HTTPS, 오리진 HTTP).
  # 박스 SG 인바운드는 CloudFront origin-facing 프리픽스로 이미 열려 있다.
  api_origin_domain = module.demo_onprem.public_dns
  api_origin_port   = var.mock_broker_port
}
