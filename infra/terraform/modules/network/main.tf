# VPC + public/private/data 서브넷(2 AZ) + IGW + NAT.
# AZ 는 기본적으로 region 에서 동적으로 골라 "어디서나" 재현되게 하되, 특정 AZ 를 원하면
# availability_zones 로 명시 오버라이드한다(길이는 az_count 와 같아야 함).

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = length(var.availability_zones) > 0 ? var.availability_zones : slice(data.aws_availability_zones.available.names, 0, var.az_count)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = var.name }
}

# public: ALB(추후 gateway)·NAT 가 위치
resource "aws_subnet" "public" {
  count                   = var.az_count
  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.name}-public-${local.azs[count.index]}", Tier = "public" }
}

# private(compute): Fargate task(API·워커)가 위치. NAT 로 아웃바운드(이미지 pull·외부 수집).
resource "aws_subnet" "private" {
  count             = var.az_count
  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 100)
  tags              = { Name = "${var.name}-private-${local.azs[count.index]}", Tier = "private" }
}

# data: RDS 전용 격리 서브넷. 아웃바운드 인터넷 경로 없음(NAT/IGW 미연결) — DB 는 egress 불필요.
# 컴퓨트 tier 와 라우트/서브넷을 분리해 데이터 계층을 격리한다(다층 방어).
resource "aws_subnet" "data" {
  count             = var.az_count
  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 200)
  tags              = { Name = "${var.name}-data-${local.azs[count.index]}", Tier = "data" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = { Name = "${var.name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# NAT — dev 는 single(비용 절감), prod 는 AZ당 1개로 가용성 확보
resource "aws_eip" "nat" {
  count  = var.enable_nat ? (var.single_nat_gateway ? 1 : var.az_count) : 0
  domain = "vpc"
  tags   = { Name = "${var.name}-nat-${count.index}" }
}

resource "aws_nat_gateway" "this" {
  count         = var.enable_nat ? (var.single_nat_gateway ? 1 : var.az_count) : 0
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = { Name = "${var.name}-nat-${count.index}" }
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route_table" "private" {
  count  = var.az_count
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-private-${count.index}" }
}

resource "aws_route" "private_nat" {
  count                  = var.enable_nat ? var.az_count : 0
  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = var.single_nat_gateway ? aws_nat_gateway.this[0].id : aws_nat_gateway.this[count.index].id
}

resource "aws_route_table_association" "private" {
  count          = var.az_count
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# data 라우트 테이블 — local 경로만(격리). 0.0.0.0/0 없음 → 인터넷 왕복 불가.
# AZ 별로 나눌 이유 없음(NAT 미연결이라 전부 동일) → 단일 테이블 공유.
resource "aws_route_table" "data" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-data" }
}

resource "aws_route_table_association" "data" {
  count          = var.az_count
  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.data.id
}

# ── S3 gateway VPC endpoint (ALPHA-349) ─────────────────
# 무료(gateway 형은 과금 없음) — private(compute) 라우트 테이블에만 연결해 S3 트래픽
# (data-pipeline 레이크 적재·ECR 레이어 pull 등)이 NAT 데이터 처리 요금을 우회한다.
# data 라우트 테이블은 제외 — 격리 tier 에 없던 S3 도달성을 새로 부여하지 않는다.
# Interface endpoint(ECR api/dkr·Logs 등)는 시간당 과금이라 범위 밖(NAT 처리량 실측 후 별도 판단).
data "aws_region" "current" {}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id
  tags              = { Name = "${var.name}-s3" }
}
