# 그래프 도구가 읽는 PuppyGraph. 평일 배치 전부터 장 마감 뒤까지만 켠다(ALPHA-1342).
# 10/5 시험 작업(edge-dev-ontology-puppygraph-pilot)은 손으로 띄운 단독 작업이라 멈추면 다시 뜨지 않았고,
# 다시 띄우면 스키마를 다시 올려야 했다. 여기서는 서비스가 켜고 끄며, 같은 작업의 업로더가 기동마다 매핑을 올린다.
# PuppyGraph 1.13 은 스키마 저장소를 컨테이너 안(/home/ubuntu/registry.db)에 두고, 저장 경로를 EFS 에 두면 데이터 백엔드가
# 뜨지 않는다(2026-10-24 실측). 그래서 보존 대신 매번 다시 올린다. 매핑 원본: libs/ontology/.../sql/puppygraph-schema.json

locals {
  logs = {
    logDriver = "awslogs"
    options = {
      "awslogs-group"         = aws_cloudwatch_log_group.this.name
      "awslogs-region"        = var.region
      "awslogs-stream-prefix" = "ecs"
    }
  }
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = 14
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# 비밀번호·DB 계정은 Terraform 밖에서 만든 SecureString 매개변수에서 기동 때 읽는다(값이 상태 파일에 남지 않는다).
resource "aws_iam_role_policy" "execution_password" {
  name = "${var.name}-password"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["ssm:GetParameters"], Resource = [var.password_parameter_arn, var.credentials_parameter_arn] }]
  })
}

resource "aws_security_group" "this" {
  name        = "${var.name}-svc"
  description = "PuppyGraph ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-svc" }
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "image pull, source database"
}

# 7687 = Bolt(그래프 조회), 8081 = 스키마 업로드·관리 화면.
resource "aws_vpc_security_group_ingress_rule" "clients" {
  for_each                     = { for pair in setproduct(var.client_security_group_ids, [7687, 8081]) : "${pair[0]}-${pair[1]}" => pair }
  security_group_id            = aws_security_group.this.id
  referenced_security_group_id = each.value[0]
  ip_protocol                  = "tcp"
  from_port                    = each.value[1]
  to_port                      = each.value[1]
  description                  = "graph clients"
}

# ── 접속 주소 ───────────────────────────────────────────
# 작업은 켜질 때마다 사설 IP가 바뀐다. 예약 실행 태스크(급등주 조사·전망 배치)는 Service Connect 를 못 쓰므로
# VPC 내부 DNS 이름(A 레코드)으로 찾는다: puppygraph.<name>.internal
resource "aws_service_discovery_private_dns_namespace" "this" {
  name = "${var.name}.internal"
  vpc  = var.vpc_id
}

resource "aws_service_discovery_service" "this" {
  name = "puppygraph"
  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.this.id
    routing_policy = "MULTIVALUE"
    dns_records {
      ttl  = 10
      type = "A"
    }
  }
  health_check_custom_config {}
}

# ── 작업과 서비스 ───────────────────────────────────────
resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "2048"
  memory                   = "16384"
  execution_role_arn       = aws_iam_role.execution.arn
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name         = "puppygraph"
    image        = var.image
    essential    = true
    portMappings = [{ containerPort = 7687, protocol = "tcp" }, { containerPort = 8081, protocol = "tcp" }]
    ulimits      = [{ name = "nofile", softLimit = 65536, hardLimit = 65536 }]
    environment = [
      { name = "PUPPYGRAPH_USERNAME", value = var.username },
      { name = "QUERY_TIMEOUT", value = "30s" },
    ]
    secrets          = [{ name = "PUPPYGRAPH_PASSWORD", valueFrom = var.password_parameter_arn }]
    logConfiguration = local.logs
    }, {
    # 서버가 준비되면 매핑을 올리고 끝난다(필수 아님 — 끝나도 작업은 계속 돈다). 실패는 graph_schema_failed 로그로 남는다.
    name      = "schema-loader"
    image     = var.loader_image
    essential = false
    command   = ["python", "-c", file("${path.module}/load_schema.py")]
    environment = [
      { name = "GRAPH_SCHEMA_GZ", value = base64gzip(jsonencode(jsondecode(var.schema_json))) },
      { name = "DB_HOST", value = var.db_host },
      { name = "PUPPYGRAPH_USERNAME", value = var.username },
    ]
    # 관리자 비밀번호는 서버와 같은 매개변수에서 읽는다. DB 계정만 credentials 매개변수에서 쓴다.
    secrets = [
      { name = "PUPPYGRAPH_PASSWORD", valueFrom = var.password_parameter_arn },
      { name = "GRAPH_CREDENTIALS", valueFrom = var.credentials_parameter_arn },
    ]
    logConfiguration = local.logs
  }])
}

resource "aws_ecs_service" "this" {
  name            = var.name
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.this.arn
  launch_type     = "FARGATE"
  desired_count   = 0
  # 그래프는 한 대만 돈다. 교체 때는 먼저 내리고 새로 띄운다.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.this.id]
    assign_public_ip = false
  }
  service_registries {
    registry_arn = aws_service_discovery_service.this.arn
  }
  # 개수는 아래 예약이 정한다.
  lifecycle {
    ignore_changes = [desired_count]
  }
}

resource "aws_appautoscaling_target" "this" {
  service_namespace  = "ecs"
  resource_id        = "service/${element(split("/", var.cluster_arn), 1)}/${aws_ecs_service.this.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 0
  max_capacity       = 1
  # 아래 예약이 낮(1/1)·밤(0/0)으로 바꾼다. 무시하지 않으면 plan 마다 이 값이 변경으로 보인다.
  lifecycle {
    ignore_changes = [min_capacity, max_capacity]
  }
}

resource "aws_appautoscaling_scheduled_action" "on" {
  name               = "${var.name}-on"
  service_namespace  = aws_appautoscaling_target.this.service_namespace
  resource_id        = aws_appautoscaling_target.this.resource_id
  scalable_dimension = aws_appautoscaling_target.this.scalable_dimension
  schedule           = var.on_schedule
  timezone           = "Asia/Seoul"
  scalable_target_action {
    min_capacity = 1
    max_capacity = 1
  }
}

resource "aws_appautoscaling_scheduled_action" "off" {
  name               = "${var.name}-off"
  service_namespace  = aws_appautoscaling_target.this.service_namespace
  resource_id        = aws_appautoscaling_target.this.resource_id
  scalable_dimension = aws_appautoscaling_target.this.scalable_dimension
  schedule           = var.off_schedule
  timezone           = "Asia/Seoul"
  scalable_target_action {
    min_capacity = 0
    max_capacity = 0
  }
  # 같은 대상에 예약을 동시에 만들면 갱신 경합으로 실패할 수 있어 순서를 건다.
  depends_on = [aws_appautoscaling_scheduled_action.on]
}
