# PostgreSQL RDS (그린필드, dev). private 서브넷에만 두고 외부 비공개.
# 비밀번호는 코드/state 에 두지 않고 RDS 관리형 Secrets Manager 시크릿으로 자동 생성한다.

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.subnet_ids
  tags       = { Name = var.name }
}

# DB SG. 인바운드는 여기서 만들지 않는다 — 접속 서비스(widget-api)의 SG 는
# 이 DB 의 endpoint/시크릿을 역으로 참조하므로, 모듈 안에서 그 SG 를 인바운드로
# 걸면 모듈 간 순환 의존이 된다. 5432 인그레스는 호출부(env)에서 독립 리소스로 건다.
resource "aws_security_group" "this" {
  name        = "${var.name}-rds"
  description = "RDS ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-rds" }
}

resource "aws_db_instance" "this" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  db_name  = var.db_name
  username = var.master_username
  # 마스터 비밀번호를 RDS 가 만들어 Secrets Manager 에 보관·로테이션(코드에 평문 없음).
  manage_master_user_password = true

  allocated_storage = var.allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  publicly_accessible    = false
  multi_az               = var.multi_az

  backup_retention_period    = var.backup_retention_period
  auto_minor_version_upgrade = true

  deletion_protection = var.deletion_protection
  skip_final_snapshot = var.skip_final_snapshot

  tags = { Name = var.name }
}
