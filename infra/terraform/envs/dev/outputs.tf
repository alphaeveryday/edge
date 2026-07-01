output "vpc_id" {
  value = module.network.vpc_id
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "service_cluster_name" {
  value = module.service_cluster.cluster_name
}

output "widget_api_service_name" {
  value = module.widget_api.service_name
}

output "widget_api_security_group_id" {
  description = "gateway 도입 시 이 SG 를 ingress 허용 대상으로 참조"
  value       = module.widget_api.security_group_id
}

output "alb_dns_name" {
  description = "ALB 직접 DNS"
  value       = module.edge_alb.dns_name
}

output "edge_url" {
  description = "임시 검증용 공개 URL(HTTPS)"
  value       = "https://${var.edge_domain}"
}
