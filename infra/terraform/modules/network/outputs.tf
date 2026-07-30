output "vpc_id" {
  value = aws_vpc.this.id
}

output "vpc_cidr" {
  value = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "컴퓨트 tier (Fargate) — NAT 아웃바운드 있음"
  value       = aws_subnet.private[*].id
}

output "data_subnet_ids" {
  description = "데이터 tier (RDS) — 격리(아웃바운드 없음)"
  value       = aws_subnet.data[*].id
}
