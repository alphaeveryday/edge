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

  # 에이전트 읽기전용 질의 경로는 비밀번호를 받지 않고 태스크 역할로
  # rds:generate-db-auth-token 을 만들어 붙는다. 그래서 IAM 인증이 필요하다.
  # Postgres 에서 이 플래그는 in-place 변경이다 — 재부팅도 다운타임도 없으니
  # 운영 인스턴스에 그냥 apply 해도 된다(MySQL 과 달리 재시작이 걸리지 않는다).
  iam_database_authentication_enabled = true

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

  # 아래 FreeableMemory 알람은 "메모리가 말랐다"까지만 말한다. 2026-08-10 사고에 없었던
  # 것은 지나간 시점을 되감는 소급성이다 — 재기동하면 증거가 사라져 원인 규명 없이 끝났다.
  # (modules/db-query 도 그 자리를 못 메운다. agent_ro 는 pg_read_all_stats 가 없어
  # 남의 세션 query·state 가 전부 NULL 이다.)
  # PI 가 주는 것은 대기 이벤트별 DB load 와 질의 통계이고, OS 레벨 메모리 분해는
  # Enhanced Monitoring 소관이라 여기 없다.
  # ⚠️ PI 에이전트 자체가 DB 호스트의 CPU·메모리를 쓴다(AWS 원문 "limited" — 수치는 안 준다).
  # 메모리로 죽는 인스턴스에 소비자를 하나 더 붙이는 것이니, 켠 뒤 평시 여유를 다시 재고
  # 아래 알람 임계를 재교정하라.
  # 켜고 끄는 데 재부팅·다운타임이 없고, 유지보수 창을 무시하고 즉시 반영된다(AWS 문서).
  # 콘솔은 2026-07-31 EOL 로 CloudWatch Database Insights(Standard)에 승계됐다. terraform
  # 설정·API·요금은 그대로다. 보관 7일은 무료(인스턴스 클래스 무관), 그 이상은 vCPU 당 과금.
  performance_insights_enabled          = true
  performance_insights_retention_period = 7

  # 16.x auto minor upgrade 는 AWS 가 관리한다. 의도적 major upgrade 전에는 이 ignore 를 제거한다.
  lifecycle {
    ignore_changes = [engine_version]
  }

  tags = { Name = var.name }
}

# DB 메모리 고갈 경보. 이 DB 는 파이프라인 전체가 올라탄 단일 지점인데 2026-08-10 dev 에서
# 하루 세 번(10:20·13:23·15:22) 메모리 고갈로 죽어 1분 레인 5종이 전부 정지했다. 유실은 0,
# 복구는 6시간. 파이프라인 쪽은 원장·reconcile·DLQ·SFN 타임아웃까지 촘촘히 계측하는데
# 정작 AWS/RDS 네임스페이스 알람이 0건이라, 그날 아침 09:30 에 이미 FreeableMemory 가
# 72MB 였는데도 아무도 몰랐다. 이 알람 하나가 그 아침을 잡는다.
# 임계 근거(2026-08-10 실측): 재기동 직후 256MB → 평시 100~110MB → 다운 직전 68~72MB.
# 위 PI 를 켜거나 인스턴스를 상향하면 평시값이 움직인다 — 그때 이 임계를 다시 잡아라.
resource "aws_cloudwatch_metric_alarm" "freeable_memory" {
  alarm_name        = "${var.name}-rds-freeable-memory"
  alarm_description = "RDS 여유 메모리가 최근 15분 중 12분 이상 80MiB 미만이다 — 곧 OOM 으로 인스턴스가 죽고 DB 를 쓰는 모든 레인이 함께 멈춘다. 할 일: Database Insights 에서 DB load 상위 세션·질의를 보고 폭주 소비자를 끊어라. 거기가 비어 있으면 유휴 커넥션이 각자 버퍼를 쥔 형태이므로 DatabaseConnections 를 봐라(유휴 세션은 DB load 에 안 잡힌다). 못 끊으면 장 마감 후 인스턴스 상향이 답이다 — 재기동은 시간만 벌 뿐 원인을 해결하지 않는다."

  namespace   = "AWS/RDS"
  metric_name = "FreeableMemory"
  dimensions  = { DBInstanceIdentifier = aws_db_instance.this.identifier }

  # RDS 는 지표를 1분 주기로 낸다(AWS 문서). period 를 창으로 묶으면 통계가 창 안을
  # 뭉개서 판정이 창 모양에 휘둘린다 — 300+Minimum 이면 창마다 1분씩 총 3분만 찍혀도
  # 울리고, 300+Average 면 절반이 임계 아래여도 안 울린다. period=60 이면 창당 샘플이
  # 하나라 통계 선택이 무의미해지고 evaluation_periods 가 곧 "분"이 된다.
  # M-of-N 을 명시하는 이유: 생략하면 M=N=15 라 15분이 한 번도 안 끊겨야 울리고, 그러면
  # 하강 중 체크포인터·autovacuum 이 만든 1분짜리 반등 하나가 카운트를 리셋해 죽을 때까지
  # 안 울린다. 12/15 는 3분까지의 반등을 견디면서, 매분 도는 작업이 만드는 짧은 딥
  # (15분 중 1~3분)은 그대로 무시한다.
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 15
  datapoints_to_alarm = 12
  threshold           = 83886080 # 80 MiB
  comparison_operator = "LessThanThreshold"

  # 지표가 통째로 끊기는 것이 곧 인스턴스 다운이다 — 08-10 세 번 다 데이터포인트가
  # 사라졌다(10:06→10:25 공백). 기본값(missing)이면 정작 죽은 그 순간에 안 울린다.
  # 대가: auto_minor_version_upgrade 재시작이 12분 넘게 지표를 끊으면 유지보수 창에
  # 오탐 한 통. 실제 전이는 그보다 조금 늦다 — CloudWatch 는 evaluation range 안에 남은
  # 실데이터를 먼저 쓰고 결측 처리는 최소한으로만 한다(그래서 탐지 지연도 12분 이상이다).
  treat_missing_data = "breaching"

  alarm_actions = [var.alarm_topic_arn]
}
