output "state_machine_arn" { value = aws_sfn_state_machine.this.arn }
output "observer_role_arn" { value = aws_iam_role.observer.arn }
output "task_family" { value = aws_ecs_task_definition.this.family }
output "model_secret_arn" { value = aws_secretsmanager_secret.model.arn }
