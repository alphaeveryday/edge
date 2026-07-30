data "aws_caller_identity" "current" {}

locals {
  prefix = "edge-demo-onprem"

  # 박스가 compose 로 pull 하는 데모 이미지(ALPHA-533 foundation ECR). ARN 은 account/region
  # 으로 구성한다. 소비처 2곳이 같은 목록을 공유한다(드리프트 방지, ALPHA-559):
  #   ① deploy-role.tf — 배포 역할의 ECR push 스코프
  #   ② module.demo_onprem.ecr_repository_arns — 인스턴스 프로파일의 pull 스코프
  demo_image_names = ["publication-api", "screening-worker", "intake", "sync-agent", "mock-broker", "tenant-console-api", "tenant-console-ui"]
  demo_ecr_arns = [
    for n in local.demo_image_names :
    "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/edge/${n}"
  ]

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
  console_port            = var.console_port
  cert_parameter_arn      = local.cert_param_arn
  ingress_prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  # pull 권한을 데모 이미지 7종으로 스코프 — 미주입 시 모듈이 ["*"] 폴백이라 계정 전체
  # private ECR 이 열린다(ALPHA-559 하드닝).
  ecr_repository_arns = local.demo_ecr_arns
}

# 가상 MTS 페이지 (S3 + CloudFront). CloudFront 의 /api/* → EC2 오리진 프록시 배선은
# mock-broker 컨테이너가 생기는 런타임과 함께(ADR-0033 범위 밖).
module "mts_site" {
  source = "../../modules/static-site"

  name            = "${local.prefix}-mts"
  domain_name     = var.mts_domain
  zone_id         = data.terraform_remote_state.foundation.outputs.zone_id
  certificate_arn = data.terraform_remote_state.foundation.outputs.wildcard_cdn_certificate_arn
  # 단일 페이지(클라이언트 탭 전환, pushState/해시 라우팅 없음)라 SPA fallback 불필요 — false.
  # (spa=true 의 fallback 은 ALPHA-617 부터 CF Function 리라이트(default behavior 한정)라
  #  /api/* 를 가리지 않지만, 이 페이지는 딥링크 자체가 없어 켤 이유가 없다.)
  spa = false

  # /api/* → 데모 박스 mock-broker(HTTP :mock_broker_port). 브라우저 MTS 의 AI 탭이
  # 같은 도메인으로 박스를 실호출한다(mixed-content 회피: 뷰어 HTTPS, 오리진 HTTP).
  # 박스 SG 인바운드는 CloudFront origin-facing 프리픽스로 이미 열려 있다.
  api_origin_domain = module.demo_onprem.public_dns
  api_origin_port   = var.mock_broker_port
}
