# 인터넷 facing Application Load Balancer (공개 엣지) — 재사용 모듈.
# 진입점은 호스트(도메인) 단위로 분리한다 — 서비스당 ALB 1개, 경로 라우팅 없음(ADR-0034).
# mTLS 는 리스너 단위 설정이라 인증 방식이 다른 표면(sync mTLS vs admin 일반 TLS)을 한
# 진입점에 섞을 수 없는 것이 분리의 근거다. 현재 호출자: envs/dev 의 sync ALB.

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "ALB ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-alb" }
}

# HTTP:80 — cert 유무와 무관하게 열어 둠(없으면 포워드, 있으면 443 리다이렉트)
resource "aws_vpc_security_group_ingress_rule" "http" {
  for_each          = toset(var.allowed_cidrs)
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  description       = "HTTP in"
}

# HTTPS:443 — 인증서 있을 때만
resource "aws_vpc_security_group_ingress_rule" "https" {
  for_each          = var.enable_https ? toset(var.allowed_cidrs) : toset([])
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "HTTPS in"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.alb.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "to targets"
}

resource "aws_lb" "this" {
  name               = var.name
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids
}

# Fargate(awsvpc)라 target_type=ip. ECS 서비스가 task IP 를 자동 등록한다.
resource "aws_lb_target_group" "this" {
  name        = var.name
  port        = var.target_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    path                = var.health_check_path
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30
}

# HTTP:80 리스너 — 인증서 있으면 443 으로 리다이렉트, 없으면 타깃 포워드
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = var.enable_https ? [] : [1]
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.this.arn
    }
  }

  dynamic "default_action" {
    for_each = var.enable_https ? [1] : []
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}

# HTTPS:443 리스너 — ACM 인증서 있을 때만
resource "aws_lb_listener" "https" {
  count             = var.enable_https ? 1 : 0
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }

  # mTLS(verify) — trust store 가 주입된 경우에만. 검증 실패 연결은 핸드셰이크에서 끊긴다.
  dynamic "mutual_authentication" {
    for_each = var.mtls_trust_store_arn == null ? [] : [1]
    content {
      mode            = "verify"
      trust_store_arn = var.mtls_trust_store_arn
    }
  }
}
