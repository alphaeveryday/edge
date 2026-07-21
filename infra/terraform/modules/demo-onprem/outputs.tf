output "instance_id" {
  description = "데모 박스 인스턴스 ID (SSM SendCommand 대상)"
  value       = aws_instance.this.id
}

output "instance_arn" {
  description = "SSM SendCommand 스코프·CD 배선용 ARN"
  value       = aws_instance.this.arn
}

output "public_ip" {
  description = "데모 박스 공개 IP (EIP 사용 시 그 IP)"
  value       = var.associate_eip ? aws_eip.this[0].public_ip : aws_instance.this.public_ip
}

output "security_group_id" {
  description = "데모 박스 SG — CloudFront 오리진 인바운드 배선 등에 참조"
  value       = aws_security_group.this.id
}

output "role_arn" {
  description = "EC2 instance role ARN"
  value       = aws_iam_role.this.arn
}
