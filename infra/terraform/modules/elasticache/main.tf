# ElastiCache for Redis. 클러스터 모드 끔, 프라이머리 1 + 레플리카 N, Multi-AZ 자동 페일오버.
# 첫 호출자는 app-api(ETF Orca 앱 서버, ADR-0056). 앱은 Lettuce 로 프라이머리 엔드포인트 하나만
# 보며, 페일오버 시 DNS 가 새 프라이머리를 가리킨다(Sentinel 실험과 같은 그림, ADR-0056).
# 클러스터 모드는 필요해질 때 별도 모듈로 한다. 앱의 서킷 범위(shard)가 그때 의미를 갖는다.

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name}-redis"
  subnet_ids = var.subnet_ids
}

resource "aws_security_group" "this" {
  name        = "${var.name}-redis"
  description = "ElastiCache ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-redis" }
}

# 인바운드는 호출자(env)가 서비스 SG 를 참조해 연다. rds 모듈과 같은 패턴. 아웃바운드 없음.

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.name}-redis"
  description          = "${var.name} redis (cluster mode disabled)"

  engine               = "redis"
  engine_version       = var.engine_version
  node_type            = var.node_type
  port                 = 6379
  parameter_group_name = var.parameter_group_name

  num_cache_clusters         = var.num_cache_clusters
  automatic_failover_enabled = var.num_cache_clusters > 1
  multi_az_enabled           = var.num_cache_clusters > 1

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.this.id]

  # 전송 암호화 = TLS. 앱은 rediss:// 로 붙는다. 저장 암호화는 기본 키.
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  # 로테이션 창은 rds 모듈과 같은 원칙으로 장중을 피한다(ALPHA-986). 토요일 새벽 UTC = 토요일 오전 KST.
  maintenance_window       = "sat:00:00-sat:02:00"
  snapshot_window          = "20:00-21:00"
  snapshot_retention_limit = var.snapshot_retention_limit

  apply_immediately          = false
  auto_minor_version_upgrade = true

  tags = { Name = "${var.name}-redis" }
}
