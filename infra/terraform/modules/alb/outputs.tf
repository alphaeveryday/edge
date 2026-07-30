output "dns_name" {
  value = aws_lb.this.dns_name
}

output "zone_id" {
  description = "ALB 의 호스팅 zone id (Route53 ALIAS 레코드용)"
  value       = aws_lb.this.zone_id
}

output "security_group_id" {
  description = "타깃 서비스가 인바운드 허용 대상으로 참조"
  value       = aws_security_group.alb.id
}

output "target_group_arn" {
  description = "ECS 서비스가 load_balancer 로 등록할 타깃그룹"
  value       = aws_lb_target_group.this.arn
}

output "arn" {
  description = "ALB ARN (WAFv2 WebACL association 등 리소스 연결용)"
  value       = aws_lb.this.arn
}
