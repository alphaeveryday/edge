output "endpoint" {
  description = "address:port (JDBC 호스트에 그대로 사용 가능)"
  value       = aws_db_instance.this.endpoint
}

output "address" {
  value = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "db_name" {
  value = aws_db_instance.this.db_name
}

output "master_username" {
  value = aws_db_instance.this.username
}

output "security_group_id" {
  description = "이 DB 의 SG. 접속 서비스 SG 를 인바운드로 허용할 때 참조"
  value       = aws_security_group.this.id
}

output "master_user_secret_arn" {
  description = "RDS 관리형 마스터 비밀번호 시크릿 ARN. {username,password} JSON"
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

# IAM 인증 정책의 rds-db:connect 리소스 ARN 이
# arn:aws:rds-db:<region>:<account>:dbuser:<resource_id>/<db_username> 형태다.
# 인스턴스 이름(identifier)이 아니라 이 resource id 를 쓰며, 인스턴스를 재생성하면
# 값이 바뀌므로 정책에 하드코딩할 수 없다 — 항상 이 출력을 참조한다.
output "resource_id" {
  description = "DB 인스턴스의 불변 resource id (dbi-...). rds-db:connect 정책 ARN 에 사용"
  value       = aws_db_instance.this.resource_id
}
