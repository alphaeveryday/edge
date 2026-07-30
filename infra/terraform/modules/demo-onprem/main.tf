# 가상 온프렘 데모 박스 — 단일 EC2 + Docker Compose (ADR-0017·0033).
# terraform 은 서버·부트스트랩까지만 만든다. 온프렘 스택(compose·이미지)은
# CD(SSM Run Command)가 얹는다 — apply(인프라)와 코드 배포는 별개 수명주기다.

data "aws_region" "current" {}

# 표준 AL2023 만. `al2023-ami-*` 로 넓히면 most_recent 가 ECS-optimized(al2023-ami-ecs-*·
# -ecs-neuron-*)나 minimal 변종을 골라, ecs-init 이 딸려와 부팅마다 ecs-agent 가 뜬다(무의미·메모리
# 낭비). `al2023-ami-2023.*` 는 표준 이미지(al2023-ami-2023.x.YYYYMMDD.N-kernel-…)만 매치한다.
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

# ── IAM: SSM 관리형 + ECR pull + cert 파라미터 read ─────
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.name}-ec2"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# SSM 관리형 인스턴스(에이전트 ↔ SSM 컨트롤플레인) — CD 가 SSM Run Command 로 배포하는 전제.
# AWS 관리형 AmazonSSMManagedInstanceCore 대신 최소 인라인을 쓴다: 그 관리형 정책은
# ssm:GetParameter* 를 Resource=* 로 허용해 아래 cert 한정 정책을 무효화하기 때문(계정 내 임의
# Parameter Store 열람 가능). 여기 담은 건 에이전트 등록 + Run Command/Session 메시징 채널뿐이다.
resource "aws_iam_role_policy" "ssm_core" {
  name = "${var.name}-ssm-core"
  role = aws_iam_role.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      # AmazonSSMManagedInstanceCore 에서 ssm:GetParameter* 만 뺀 집합 — Run Command 가 문서를
      # 가져와(GetDocument) 실행하는 데 필요한 문서·association 액션까지 포함한다.
      # (파라미터 읽기는 아래 cert 한정 정책이 유일한 경로로 유지)
      Action = [
        "ssm:UpdateInstanceInformation",
        "ssm:ListAssociations",
        "ssm:ListInstanceAssociations",
        "ssm:DescribeAssociation",
        "ssm:UpdateAssociationStatus",
        "ssm:UpdateInstanceAssociationStatus",
        "ssm:GetDocument",
        "ssm:DescribeDocument",
        "ssm:GetManifest",
        "ssm:GetDeployablePatchSnapshotForInstance",
        "ssm:PutInventory",
        "ssm:PutComplianceItems",
        "ssm:PutConfigurePackageResult",
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
        "ec2messages:AcknowledgeMessage",
        "ec2messages:DeleteMessage",
        "ec2messages:FailMessage",
        "ec2messages:GetEndpoint",
        "ec2messages:GetMessages",
        "ec2messages:SendReply",
      ]
      Resource = "*"
    }]
  })
}

# ECR pull(로그인 + 레이어). 저장소는 var 로 스코프(비면 전체).
resource "aws_iam_role_policy" "ecr" {
  name = "${var.name}-ecr-pull"
  role = aws_iam_role.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
        ]
        Resource = length(var.ecr_repository_arns) > 0 ? var.ecr_repository_arns : ["*"]
      },
    ]
  })
}

# 데모 mTLS cert(SSM SecureString) read. 복호화는 aws/ssm 관리형 키 경유 조건으로 한정.
resource "aws_iam_role_policy" "cert" {
  name = "${var.name}-cert-read"
  role = aws_iam_role.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = var.cert_parameter_arn
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "ssm.${data.aws_region.current.name}.amazonaws.com" }
        }
      },
    ]
  })
}

resource "aws_iam_instance_profile" "this" {
  name = "${var.name}-ec2"
  role = aws_iam_role.this.name
}

# ── 보안그룹: outbound all / inbound 는 prefix list(예: CloudFront)만. SSM·SSH inbound 0. ─
resource "aws_security_group" "this" {
  name        = "${var.name}-ec2"
  description = "demo on-prem box ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-ec2" }
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "all egress (ECR pull, cloud sync mTLS, SSM)"
}

# CloudFront 오리진 프리픽스 등에서만 mock-broker 포트로 인바운드. publication-api 는 비공개(내부 유지).
resource "aws_vpc_security_group_ingress_rule" "from_prefix" {
  count             = length(var.ingress_prefix_list_ids)
  security_group_id = aws_security_group.this.id
  prefix_list_id    = var.ingress_prefix_list_ids[count.index]
  ip_protocol       = "tcp"
  from_port         = var.mock_broker_port
  to_port           = var.mock_broker_port
  description       = "mock-broker from allowed prefix list (e.g. CloudFront origin-facing)"
}

# 검수 콘솔(tenant-console-ui) 포트 인바운드(ALPHA-627) — CloudFront 오리진 프록시 대상.
# 진입 게이트는 콘솔 로그인 화면(ALPHA-626, autosession 폐기)이 담당한다.
resource "aws_vpc_security_group_ingress_rule" "console_from_prefix" {
  count             = var.console_port != null ? length(var.ingress_prefix_list_ids) : 0
  security_group_id = aws_security_group.this.id
  prefix_list_id    = var.ingress_prefix_list_ids[count.index]
  ip_protocol       = "tcp"
  from_port         = var.console_port
  to_port           = var.console_port
  description       = "tenant console from allowed prefix list (e.g. CloudFront origin-facing)"
}

# ── EC2 ────────────────────────────────────────────────
resource "aws_instance" "this" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [aws_security_group.this.id]
  iam_instance_profile        = aws_iam_instance_profile.this.name
  associate_public_ip_address = true

  # user_data_replace_on_change 는 두지 않는다 — user_data 에 compose_version 이 담겨,
  # true 면 사소한 버전 범프도 인스턴스 교체(→ 루트 EBS·PG named volume 유실)를 부른다.
  # 스왑은 fstab 등록으로 한 번만 만들면 재부팅·in-place 리사이징에도 지속되므로, 신규 박스는
  # cloud-init 이 자동 생성하고 기존 박스는 일회성 조치로 얹는다(교체 훅 불필요).
  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    compose_version = var.compose_version
  })

  root_block_device {
    volume_size = var.root_volume_size
    volume_type = var.root_volume_type
    iops        = var.root_volume_iops
    encrypted   = true
  }

  # IMDSv2 강제 — 공개 데모 박스라 IMDSv1(토큰 없는 metadata)로 instance-profile 자격증명이
  # SSRF·컨테이너 탈출로 새는 걸 막는다. hop_limit=1: 호스트 docker 는 ECR pull 에 IMDS 를 쓰지만
  # 컨테이너는 IMDS 에 닿지 못하게(온프렘 컨테이너는 AWS API 를 안 쓴다).
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # CD(SSM Run Command)가 이 태그로 인스턴스를 스코프한다.
  tags = {
    Name        = var.name
    "edge:role" = "demo-onprem"
  }

  # ami 는 replacement-only 속성이다. 위 필터 교정으로 data.aws_ami 가 기존 박스의 AMI 와
  # 다른 id 로 resolve 되면, 무관한 변경(IAM·SG)의 apply 마저 인스턴스 교체(→ 루트 EBS·PG
  # named volume 유실)를 계획한다. AMI 드리프트를 무시해 기존 박스를 보존한다 — 신규 박스는
  # create 시 교정된 AMI 를 받고(ignore_changes 는 update 에만 적용), 기존 박스의 AMI 갱신이
  # 정말 필요하면 의도적 `-replace` 로 명시한다.
  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_eip" "this" {
  count    = var.associate_eip ? 1 : 0
  instance = aws_instance.this.id
  domain   = "vpc"
  tags     = { Name = "${var.name}-eip" }
}
