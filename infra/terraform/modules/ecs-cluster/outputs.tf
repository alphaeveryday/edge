output "cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "namespace_arn" {
  value = aws_service_discovery_http_namespace.this.arn
}
