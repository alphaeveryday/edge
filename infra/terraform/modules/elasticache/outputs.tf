output "primary_endpoint" {
  description = "프라이머리 엔드포인트 호스트 (쓰기·읽기). 페일오버 시 DNS 가 따라간다"
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "reader_endpoint" {
  description = "리더 엔드포인트 호스트 (읽기 분산용, 현재 미사용)"
  value       = aws_elasticache_replication_group.this.reader_endpoint_address
}

output "port" {
  value = aws_elasticache_replication_group.this.port
}

output "security_group_id" {
  description = "이 Redis 의 SG. 접속 서비스 SG 를 인바운드로 허용할 때 참조"
  value       = aws_security_group.this.id
}
