# ECS 클러스터 + Service Connect 네임스페이스 + Fargate capacity provider.
# 클러스터는 무료 논리 그룹이라, service/worker 를 분리해도 비용 동일.

# Service Connect 용 네임스페이스(HTTP 네임스페이스로 충분 — gateway→API 단방향 호출)
resource "aws_service_discovery_http_namespace" "this" {
  name        = var.namespace_name
  description = "Service Connect namespace for ${var.name}"
}

resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled"
  }

  # 클러스터 기본 네임스페이스 — 서비스가 service_connect 시 자동 사용
  service_connect_defaults {
    namespace = aws_service_discovery_http_namespace.this.arn
  }
}

# Fargate(상시 서비스) + Fargate Spot(배치 워커가 저비용으로 쓸 수 있도록) 둘 다 활성
resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}
