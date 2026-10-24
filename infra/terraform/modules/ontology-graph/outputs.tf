output "service_name" { value = aws_ecs_service.this.name }
output "security_group_id" { value = aws_security_group.this.id }
output "bolt_uri" { value = "bolt://${aws_service_discovery_service.this.name}.${aws_service_discovery_private_dns_namespace.this.name}:7687" }
