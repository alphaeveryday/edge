output "service_name" {
  value = aws_ecs_service.this.name
}

output "security_group_id" {
  description = "이 서비스의 SG. 호출자(gateway) ingress 허용에 참조"
  value       = aws_security_group.this.id
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.this.arn
}

output "task_role_name" {
  description = "런타임 권한을 추가 부착할 태스크 역할 이름"
  value       = aws_iam_role.task.name
}
