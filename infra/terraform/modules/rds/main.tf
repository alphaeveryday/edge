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

  # IAM DB 인증은 끈다(ALPHA-933). 조직 SCP 가 rds-db:connect 를 explicitDeny 해
  # 이 계정에서 IAM 토큰 접속은 원리적으로 불가하고 — 켜 두면 죽은 기능이기만 한 게
  # 아니라 **지뢰**다: rds_iam 멤버십이 롤 상속(GRANT agent_ro TO edge → rds_iam)을
  # 타고 마스터에 닿는 순간 RDS 가 마스터의 비밀번호 인증을 PAM 으로 돌려 전 앱의
  # 신규 접속이 죽는다(2026-08-11 dev 실증 — V202608111500 직후 전면 PAM 실패,
  # 수동 비활성으로 복구). V202608111600 이 rds_iam 멤버십도 회수한다. SCP 가 풀려
  # 되켤 때는 그 상속 경로부터 재점검하라.
  # Postgres 에서 이 플래그는 in-place 변경이다 — 재부팅도 다운타임도 없으니
  # 운영 인스턴스에 그냥 apply 해도 된다(MySQL 과 달리 재시작이 걸리지 않는다).
  iam_database_authentication_enabled = false

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

  # ⚠️ 이 플래그가 없으면(기본 false) ModifyDBInstance 가 유지보수 창까지 대기한다 —
  # apply 성공이 반영이 아니다(ALPHA-723 이 iam_database_authentication_enabled 에서 실증:
  # 창이 tue:20:14 UTC 라 최대 일주일 — 이 창은 AWS 배정이고 이 모듈이 안 잡는다).
  # 상향은 그 대기를 감당할 수 없다: 2026-08-10 에 하루 다섯 번 죽었고, 마지막 사망
  # (22:14 KST) 기준 다음 창까지 31시간이라 그 사이 거래일이 통째로 들어간다.
  # 🔴 대가를 정확히 알고 써라. 이 레포엔 "apply 하는 사람"이 없다 — terraform-apply.yml
  # 이 dev push 에 걸려 있어 **머지가 곧 apply 이고, 이제 곧 즉시 재부팅**이다. 그래서
  # 이 모듈을 바꾸는 PR 은 장 마감 후에 **머지**한다(apply 시점을 따로 고를 여지가 없다).
  # 그리고 유지보수 창 대기는 함정이기만 한 게 아니라 **방지턱이기도 했다** — 그 대기가
  # 있는 동안은 어떤 머지도 장중에 이 DB 를 재부팅시킬 수 없었다. 이 줄이 그 턱을
  # 이 모듈의 모든 미래 변경에 대해 없앤다.
  # ⚠️ 즉시 반영은 이번 변경만이 아니라 **AWS 대기 큐 전체**를 함께 터뜨린다(AWS 원문:
  # "this request and any pending modifications"). auto_minor_version_upgrade 가 켜져 있어
  # 사람이 아무것도 안 해도 큐가 찰 수 있고, 위 ignore_changes 는 terraform diff 만 가릴 뿐
  # AWS 큐를 막지 않는다. 머지 직전에 PendingModifiedValues 가 {} 인지 확인하라.
  # 소비자가 envs/dev 하나뿐이라 변수로 빼지 않았다. 두 번째 env(특히 prod)가 생기면
  # ALPHA-723 이 열어 둔 그 결정을 그때 다시 하라 — prod 는 기본값이 옳을 수 있다.
  apply_immediately = true

  # 아래 FreeableMemory 알람은 "메모리가 말랐다"까지만 말한다. 2026-08-10 사고에 없었던
  # 것은 지나간 시점을 되감는 소급성이다 — 재기동하면 증거가 사라져 원인 규명 없이 끝났다.
  # (modules/db-query 도 그 자리를 못 메운다. agent_ro 는 pg_read_all_stats 가 없어
  # 남의 세션 query·state 가 전부 NULL 이다.)
  # PI 가 주는 것은 대기 이벤트별 DB load 와 질의 통계이고, OS 레벨 메모리 분해는
  # Enhanced Monitoring 소관이라 여기 없다.
  # ⚠️ PI 에이전트 자체가 DB 호스트의 CPU·메모리를 쓴다(AWS 원문 "limited" — 수치는 안 준다).
  # 메모리로 죽는 인스턴스에 소비자를 하나 더 붙이는 것이니, 켠 뒤 평시 여유를 다시 재고
  # 아래 알람 임계를 재교정하라.
  # 🔴 다만 지금은 **읽을 수가 없다** — 조직 SCP 가 `pi:*` 를 명시 거부한다(ALPHA-923).
  # 그래도 켜 둔다: 수집은 계속되므로 풀리는 순간 **그때부터가 아니라 그 시점 기준 직전
  # 7일치**를 얻는다. ⚠️ 보관은 롤링이라 그보다 오래된 것은 없다 — 해제가 늦어질수록
  # 편익이 줄고 오버헤드만 남는다. 오래 안 풀리면 끄는 것도 선택지다(ALPHA-923 에서 판단).
  # 위 오버헤드 경고와의 교환은 상향(2GiB, 여유 700MB+) 뒤라 부담이 줄었다.
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
# 하루 다섯 번(10:20·13:23·15:22·18:36·22:14) 메모리 고갈로 죽어 장중 1분 레인 5종이
# 전부 정지했다. 유실은 0. 파이프라인 쪽은 원장·reconcile·DLQ·SFN 타임아웃까지 촘촘히
# 계측하는데 정작 AWS/RDS 네임스페이스 알람이 0건이라, 그날 아침 09:30 에 이미
# FreeableMemory 가 72MB 였는데도 아무도 몰랐다. 이 알람 하나가 그 아침을 잡는다.
# ⏭ 아래 임계·수치는 전부 **micro(1GiB) 기준 관측**이고, 이 커밋의 상향으로 낡았다.
# 새 평시값을 측정해 임계를 재교정하는 것이 ALPHA-924 의 남은 절반이다(PR B).
# 임계 근거(2026-08-10 micro 실측): 재기동 직후 256MB → 낮 평시 100~110MB →
# 아침 다운 직전 68~72MB → 밤 고원 80~90MB.
# ⚠️ 이 알람이 메모리 사망을 전부 잡는다고 믿지 마라. 08-10 에 형태가 둘이었다 —
# 아침(10:20)은 1시간 넘게 지속 하강해 이 설정이 65분 앞서 잡지만, 밤(22:14)은 밤 고원
# (89~90MB)에서 12분 만에 갔고 직전 15분 창의 임계미만이 6/15 라 발화가 0이었다. 후자는
# 임계·지속을 어떻게 조여도 밤 고원과 겹쳐 못 가른다 — 그건 인스턴스 크기 문제고
# 이 상향이 그 답이다. 알람은 느린 형태 전용이라고 읽어라.
resource "aws_cloudwatch_metric_alarm" "freeable_memory" {
  alarm_name        = "${var.name}-rds-freeable-memory"
  alarm_description = "RDS 여유 메모리가 최근 15분 중 12분 이상 80MiB 미만이다 — 곧 OOM 으로 인스턴스가 죽고 DB 를 쓰는 모든 레인이 함께 멈춘다. 할 일 ① CloudWatch 에서 CPUUtilization 과 ReadIOPS 를 같은 창에 겹쳐 계단이 생긴 시각을 찾아라 — 그 시각이 원인 워크로드의 시작점이고, 파이프라인 스케줄·ops_* 원장과 대조하면 어느 레인인지 좁혀진다(2026-08-10 실증: 21:44 에 CPU 9%→24%·ReadIOPS 18→119, 30분 뒤 사망). ② DatabaseConnections 로 유휴 커넥션이 각자 버퍼를 쥔 형태인지 가른다 — 유휴 세션은 DB load 에 안 잡힌다. ③ Performance Insights 의 상위 질의가 가장 빠른 길이지만 조직 SCP 가 pi:* 를 막고 있다(ALPHA-923) — 열리는지 먼저 확인하고 안 되면 ①로 가라. 못 끊으면 장 마감 후 인스턴스 상향이 답이다 — 재기동은 시간만 벌 뿐 원인을 해결하지 않는다."

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

  # 지표 단절은 인스턴스 다운의 신호다 — 관측된 다운 중 데이터포인트가 통째로 사라진
  # 경우가 있다. 표기는 첫 결측→마지막 결측이다: 아침 10:07→10:23(17개), 밤
  # 22:03→22:17(15개). (#711 은 아침을 10:06→10:25 라 적었는데 그 두 분은 데이터가
  # 있다 — 1분 해상도로 재측정해 바로잡았다.) 기본값(missing)이면 그 구간에 아무 판정도
  # 안 한다. 🔴 다만 breaching 이 그 자리를 메워 주리라 기대하지 마라 — 밤 15개 결측에서
  # 이 알람은 **끝내 ALARM 으로 가지 않았다**(이력에 전이 0건). CloudWatch 는 evaluation
  # range 안에 실데이터가 evaluation_periods 만큼 남아 있으면 결측 처리를 통째로 무시하기
  # 때문이다(AWS 문서). ⚠️ 그러니 문턱을 evaluation_periods(15)로 읽지 마라 — 정확히 15개
  # 결측으로는 모자랐다. 실제 문턱은 evaluation range 폭(AWS 미공개)과 M-of-N 이 함께
  # 정하고, 이번 실측이 주는 것은 하한뿐이다. 요컨대 이 값을 죽음 탐지로 쓰지 마라 —
  # 이 설정의 실제 값은 "죽는 순간을 잡는다"가 아니라 "긴 단절을 OK 로 오독하지 않는다"이고,
  # 대가는 auto_minor_version_upgrade 재시작이 그만큼 길면 유지보수 창에 오탐 한 통이다.
  # 죽음 자체의 통보는 이 축이 아니라 아래 aws_db_event_subscription 이 진다(ALPHA-928).
  treat_missing_data = "breaching"

  alarm_actions = [var.alarm_topic_arn]
}

# DB 가 죽었다는 사실 자체의 통보. 위 FreeableMemory 알람은 **죽기 전 예고**이고 그것도
# 느린 하강 형태에서만 듣는다 — 2026-08-10 22:14 사망은 15개 결측(=evaluation_periods)에도
# 끝내 ALARM 으로 안 갔다(이력 전이 0건). 지표 공백으로 죽음을 추론하는 방식은 CloudWatch 의
# evaluation range 규칙에 구조적으로 지므로, 임계·통계·M-of-N 을 어떻게 조여도 안 닫힌다.
# 이 구독은 RDS 가 이벤트를 SNS 로 **직접** 밀어 주는 다른 축이라 그 규칙을 안 탄다.
#
# 카테고리는 08-10 사망 5회의 실제 이벤트를 describe-events 로 분류해 골랐다(추측 아님):
#   recovery     — "Recovery of the DB instance has started"  ← 가장 이른 신호(재시작 4분 전)
#   availability — "DB instance restarted" · "DB instance shutdown"
#   notification — "A database workload is causing the system to run critically low on
#                   memory … set shared_buffers 23081 → 11295"  ← 원인이 담긴 유일한 메시지
#   failure      — 08-10 에는 안 나왔지만 실제 실패의 카테고리라 넣는다
# 뺀 것: configuration change(terraform apply 마다 온다) · backup(매일 온다) ·
# low storage(FreeStorageSpace 16.2GB 로 여유가 크고 근거가 없다 — 필요해지면 그때).
resource "aws_db_event_subscription" "this" {
  name      = "${var.name}-rds-events"
  sns_topic = var.alarm_topic_arn

  source_type = "db-instance"
  source_ids  = [aws_db_instance.this.identifier]

  event_categories = ["availability", "recovery", "notification", "failure"]
}
