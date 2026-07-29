output "task_definition_family" {
  description = "RunTask 에 쓸 task 정의 family. 호출부가 RunTask 권한을 이 family 의 모든 리비전으로 좁힐 때도 쓴다."
  value       = aws_ecs_task_definition.this.family
}

output "task_definition_arn" {
  description = "현재 리비전 ARN. 호출부가 RunTask 대상으로 그대로 넘긴다."
  value       = aws_ecs_task_definition.this.arn
}

output "security_group_id" {
  description = "질의 task SG. 호출부(env)가 RDS 인바운드 5432 로 허용한다(모듈이 직접 열면 순환 의존)."
  value       = aws_security_group.this.id
}

output "execution_role_arn" {
  description = "RunTask 호출자에게 필요한 iam:PassRole 대상 1 (ECS 에이전트가 맡는 역할)."
  value       = aws_iam_role.execution.arn
}

output "task_role_arn" {
  description = "RunTask 호출자에게 필요한 iam:PassRole 대상 2. rds-db:connect 가 붙은 역할이라 DB 접속 권한의 소재를 추적할 때도 이 ARN 을 본다."
  value       = aws_iam_role.task.arn
}

output "log_group_name" {
  description = "질의 결과·오류를 읽는 로그 그룹. 호출부가 RunTask 뒤 이 그룹의 query/* 스트림을 tail 한다."
  value       = aws_cloudwatch_log_group.this.name
}

output "log_group_arn" {
  description = "위 그룹 ARN. 호출부가 로그 조회 권한(logs:GetLogEvents 등) 대상으로 참조한다."
  value       = aws_cloudwatch_log_group.this.arn
}
