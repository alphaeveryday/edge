# 운영 원장 MVP — Planner/Reconciler 실행 인프라 (ALPHA-530).
#
# Planner(plan-run)와 Reconciler(reconcile)는 data-pipeline 이미지를 그대로 쓰되(수집·정제와 같은
# 컨테이너), DB(원장) + SFN/ECS 권한이 필요하다. 공용 task 역할(레이크 RW)에 SFN/ECS 를 얹으면
# 수집기까지 과권한이 되므로 **전용 ops_task 역할**을 둔다(analysis_task 선례와 같은 결).
#
# ⚠️ 이 파일은 운영 배포하지 않는다(ALPHA-530 범위: 로컬 구현·검증까지). schedule state 기본
# DISABLED, 적용 시 daily 트리거가 SFN 직접 시작에서 **Planner 경유**로 컷오버된다(스펙 §5).

# Reconciler 가 "예정 지난 슬롯"을 만들 때 쓰는 KST 시각. **cron 에서 뽑는다 — 별도 변수로
# 두지 않는다**(ALPHA-564). 슬롯 키가 HH:MM 을 담게 되면서, 이 값이 schedule_expression 과
# 어긋나면 Reconciler 가 존재하지 않는 슬롯(`...T15:40`)을 찾는다 — 거짓 PLANNER_MISSING 을
# 여는 데 그치지 않고 **실제 런(`...T15:30`)이 영영 대조되지 않는다**. 같은 사실을 두 변수가
# 들고 있으면 언제 어긋나도 이상하지 않으므로 출처를 하나로 줄였다.
# cron 이 아닌 표현식(rate 등)이면 plan 단계에서 실패한다 — Planner 설계가 일 1회 cron 슬롯을
# 전제하므로 조용히 기본값으로 떨어지는 것보다 낫다(Rule 12).
locals {
  # [0]=분, [1]=시. format 의 %02d 가 한 자리 시각(cron(5 15 ...) → "15:05")도 zero-pad 한다 —
  # `_sched_hhmm` 이 int() 로 파싱하므로 자릿수가 어긋나도 조용히 넘어가지 않게.
  _daily_cron_hm      = regex("^cron\\(([0-9]+) ([0-9]+) ", var.schedule_expression)
  daily_schedule_hhmm = format("%02d:%02d", tonumber(local._daily_cron_hm[1]), tonumber(local._daily_cron_hm[0]))

  # 뉴스 레인 슬롯 목록(ALPHA-591) — daily 와 같은 이유로 news_schedule_expressions cron 에서
  # 뽑는다(`_lane_sched_hhmms` 가 파싱). sort 로 키 순서 무관 결정적 값을 만든다.
  _news_cron_hms = [
    for k in sort(keys(var.news_schedule_expressions)) :
    regex("^cron\\(([0-9]+) ([0-9]+) ", var.news_schedule_expressions[k])
  ]
  news_schedule_hhmm = join(",", sort([
    for hm in local._news_cron_hms : format("%02d:%02d", tonumber(hm[1]), tonumber(hm[0]))
  ]))

  # 공시 레인 슬롯 목록(ALPHA-722) — 뉴스와 같은 이유로 cron 에서 뽑는다.
  _disclosure_cron_hms = [
    for k in sort(keys(var.disclosure_schedule_expressions)) :
    regex("^cron\\(([0-9]+) ([0-9]+) ", var.disclosure_schedule_expressions[k])
  ]
  # ⚠️ **스케줄이 ENABLED 일 때만 값을 낸다.** 슬롯 기준은 Reconciler 에게 "이 시각엔 런이
  # 있어야 한다"는 주장이라, 스케줄이 꺼진 채 이 값만 주입하면 Planner 가 뜰 리 없는 슬롯을
  # 매시간 결측으로 판정해 **참인 PLANNER_MISSING** 을 10개씩 연다(resolve 되지 않는다 —
  # 실제로 안 돌았으니까). 뉴스 레인엔 이 조건이 없는데, dev 에서 스케줄과 원장 편입이 같은
  # 시점에 켜져 꺼진 구간이 존재하지 않았기 때문이다. 공시는 신설→검증→컷오버로 나뉘어
  # 그 구간이 실재하므로 여기서 묶는다(entry.py `_lane_sched_hhmms` 는 빈 값 = 판정 없음).
  disclosure_schedule_hhmm = var.disclosure_schedule_state != "ENABLED" ? "" : join(",", sort([
    for hm in local._disclosure_cron_hms : format("%02d:%02d", tonumber(hm[1]), tonumber(hm[0]))
  ]))

  # 장중 수급 레인 슬롯 목록(ALPHA-769) — 앞 셋과 같은 이유로 cron 에서 뽑는다.
  _investor_intraday_cron_hms = [
    for k in sort(keys(var.investor_intraday_schedule_expressions)) :
    regex("^cron\\(([0-9]+) ([0-9]+) ", var.investor_intraday_schedule_expressions[k])
  ]
  # 공시와 같은 ENABLED 조건을 건다. 이 레인은 기본이 ENABLED 라 평소엔 항상 값이 나오지만,
  # 조건 자체가 필요한 이유는 같다: 누군가 스케줄을 끄면(사고 대응·비용) 슬롯 기준만 남아
  # Reconciler 가 뜰 리 없는 슬롯을 매번 결측으로 판정해 **참인 PLANNER_MISSING** 을 하루 5개씩
  # 연다(실제로 안 돌았으니 resolve 되지 않는다). 끄는 행위 하나로 두 사실이 함께 꺼져야 한다.
  investor_intraday_schedule_hhmm = var.investor_intraday_schedule_state != "ENABLED" ? "" : join(",", sort([
    for hm in local._investor_intraday_cron_hms : format("%02d:%02d", tonumber(hm[1]), tonumber(hm[0]))
  ]))

  # 레인별 "주말에도 도는가"(ALPHA-874) — HH:MM 과 같은 이유로 **cron 의 일·요일 필드에서
  # 파생**한다. cron 은 6필드다: cron(분 시 **일** 월 **요일** 연). 별도 변수로 두면 스케줄과
  # 어긋나는 순간 Reconciler 가 주말을 잘못 본다. 그리고 **두 방향 다 비싸다**:
  # * 평일 전용 레인을 주말에 기대 → 매 토·일 거짓 PLANNER_MISSING. 뜰 런이 없으니
  #   `run_present` 로 영영 RESOLVE 되지 않는다(주말 하루당 16개가 영구 OPEN).
  # * 주말에도 도는 레인을 평일 전용으로 판정 → 그 레인의 주말 결측 탐지가 **조용히 0**.
  #
  # 그래서 표기를 추측하지 않고 **표에서 찾는다.** 표에 없는 표기는 맵 인덱스가 **plan 단계에서
  # 죽인다** — 배포된 뒤 닫히지 않는 이슈를 여느니 apply 전에 멈추는 쪽이 싸다
  # (`schedule_timezone` 의 validation 과 같은 자세, Rule 12). 표현 가능한 값이 둘뿐인 것은
  # 플래그가 이진이기 때문이고, 그 근거는 variables.tf 의 뉴스 스케줄 주석에 있다.
  #
  # ⭐ 키는 요일 하나가 아니라 **(일, 요일) 쌍**이다. 실제로 쓰이는 표기는 둘 중 하나가 `?`
  # (무의미)라 날짜 선택의 의미가 두 필드 사이를 오간다 — `? * MON-FRI` 는 요일이, `* * ?` 는
  # 일이 정한다. 요일만 키로 쓰면 `cron(0 15 1 * ? *)`(매월 1일)·`L`·`15W` 가 전부 "매일"로
  # 통과해, 원장이 매일 슬롯을 기대하는데 런은 한 달에 한 번 뜨는 상태가 된다(= 닫히지 않는
  # 이슈가 매일). 이 관찰이 틀려도 설계는 안전하다: 표가 화이트리스트라 예상 못 한 **(일, 요일)**
  # 조합은 키 부재로 plan 에서 죽지 조용히 통과하지 않는다. ⚠️ 키에 **월·연은 없다** — 월·연을
  # 제한한 크론(`* 1 ? *` 같은)은 두 층 모두 조용히 통과한다. 그런 스케줄을 쓸 이유가 이 네
  # 레인엔 없어 표를 늘리지 않았다(늘리면 키가 배로 는다, Rule 2).
  #
  # ⚠️ 표에 값을 더할 때: AWS 요일은 **1=SUN** 이라 `1-5` 는 평일이 아니라 SUN-THU 다.
  # "평일이니까"로 false 를 넣으면 정확히 반대로 판정한다. 동의어(`2-6`·소문자·`SUN-SAT`)도
  # 넣지 마라 — 표기 스타일이 하나뿐인 레포에서 얻는 게 없고, AWS 의 평가 의미와 이 표가 갈릴
  # 여지만 는다. 못 쓰는 표기를 만나면 표를 늘리기 전에 **그 스케줄이 이진 플래그로 표현되는지**
  # 부터 따져라(MON-SAT 처럼 표현 불가인 것이 대부분이다).
  _day_weekend = {
    "?|MON-FRI" = false # 평일 전용
    "*|?"       = true  # 매일 — AWS 문서의 "매일" 형 cron(m h * * ? *)
    "?|*"       = true  # 매일(요일 전부)
  }
  _cron_day_re = "^cron\\([^ ]+ [^ ]+ ([^ ]+) [^ ]+ ([^ ]+) " # (일, 요일)

  # 맵 레인은 `one(distinct(...))` — 한 레인의 슬롯끼리 표기가 다르면 plan 에서 죽는다. 위 HH:MM
  # 모델에 요일 축이 없어 혼합은 애초에 표현할 수 없고, 조용히 한쪽으로 접으면 그 레인의 판정이
  # 어느 방향으로든 틀린 채 배포된다. ⚠️ 부수효과로 **빈 맵도 plan 에서 죽는다**(`one([])`=null
  # → Invalid index). 레인을 끄려면 맵을 비우지 말고 `*_schedule_state` 를 DISABLED 로 둬라.
  daily_schedule_weekend = tostring(local._day_weekend[join("|", regex(local._cron_day_re, var.schedule_expression))])
  news_schedule_weekend = tostring(local._day_weekend[one(distinct([
    for e in values(var.news_schedule_expressions) : join("|", regex(local._cron_day_re, e))
  ]))])
  disclosure_schedule_weekend = tostring(local._day_weekend[one(distinct([
    for e in values(var.disclosure_schedule_expressions) : join("|", regex(local._cron_day_re, e))
  ]))])
  investor_intraday_schedule_weekend = tostring(local._day_weekend[one(distinct([
    for e in values(var.investor_intraday_schedule_expressions) : join("|", regex(local._cron_day_re, e))
  ]))])
}

# ── Planner/Reconciler 전용 task 역할 ──
resource "aws_iam_role" "ops_task" {
  name               = "${var.name}-ops-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "ops_task" {
  name = "${var.name}-ops-task"
  role = aws_iam_role.ops_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Planner 가 SFN 을 시작한다(StartExecution 소유는 스케줄러가 아니라 Planner, 스펙 §5).
        # 뉴스 SFN 도 Planner 경유다(ALPHA-591).
        Effect = "Allow"
        Action = ["states:StartExecution"]
        Resource = [
          aws_sfn_state_machine.this.arn,
          aws_sfn_state_machine.news.arn,
          aws_sfn_state_machine.disclosure.arn,
          aws_sfn_state_machine.investor_intraday.arn,
        ]
      },
      {
        # Planner(ExecutionAlreadyExists 확인)·Reconciler(실행 동기화·history)가 실행을 읽는다.
        Effect = "Allow"
        Action = ["states:DescribeExecution", "states:GetExecutionHistory"]
        Resource = [
          "${replace(aws_sfn_state_machine.this.arn, ":stateMachine:", ":execution:")}:*",
          "${replace(aws_sfn_state_machine.news.arn, ":stateMachine:", ":execution:")}:*",
          "${replace(aws_sfn_state_machine.disclosure.arn, ":stateMachine:", ":execution:")}:*",
          "${replace(aws_sfn_state_machine.investor_intraday.arn, ":stateMachine:", ":execution:")}:*",
        ]
      },
      {
        # Reconciler 가 ECS 실제 종료를 확인한다(증거 없이 RUNNING 을 뒤집지 않기 위해, 스펙 §7).
        Effect    = "Allow"
        Action    = ["ecs:DescribeTasks"]
        Resource  = ["*"]
        Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } }
      },
    ]
  })
}

# ── Planner/Reconciler task-def (data-pipeline 이미지 재사용) ──
# 기동 주체(execution 역할)는 공용을 쓴다 — db password 시크릿 읽기는 이미 허용(rds task-def 와
# 같은 시크릿). command 기본은 reconcile 이고, 스케줄러가 plan-run/reconcile 로 각각 덮는다.
resource "aws_ecs_task_definition" "ops" {
  family                   = "${var.name}-ops"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.ops_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name      = local.container_name
    image     = var.image
    essential = true
    command   = ["reconcile"]
    environment = [for k, v in merge(local.env, {
      DATA_PIPELINE_DB__HOST = var.db_host
      DATA_PIPELINE_DB__PORT = tostring(var.db_port)
      DATA_PIPELINE_DB__NAME = var.db_name
      DATA_PIPELINE_DB__USER = var.db_user
      # Planner 가 시작할 상태머신. 카탈로그 버전은 배포 SHA 를 CD 가 주입(없으면 unknown).
      OPS_STATE_MACHINE_ARN = aws_sfn_state_machine.this.arn
      # Reconciler 가 ECS DescribeTasks 를 이 클러스터로 조회한다(생략하면 default 클러스터를
      # 봐서 실제 태스크를 못 찾는다, edge-review). Planner 의 비거래일 판정용 KR 공휴일 목록도.
      OPS_CLUSTER_ARN = var.cluster_arn
      OPS_KR_HOLIDAYS = join(",", var.kr_holidays)
      # Reconciler 의 "예정 지난 슬롯" 판정 기준 — 위 locals 가 cron 에서 뽑는다(드리프트 불가).
      # `_WEEKEND` 는 같은 cron 의 일·요일 필드에서 나온다(ALPHA-874) — 시각만으로는
      # 그 슬롯이 주말에도 예정된 것인지 알 수 없어, 없으면 레인 구분 없이 주말을 통째로 건너뛴다.
      OPS_DAILY_SCHED_HHMM    = local.daily_schedule_hhmm
      OPS_DAILY_SCHED_WEEKEND = local.daily_schedule_weekend
      # 뉴스 레인(ALPHA-591): Planner 의 뉴스 SFN ARN + Reconciler 의 뉴스 슬롯 판정 기준
      # (ALPHA-893 이후 2슬롯 — 값은 news_schedule_expressions 에서 파생되니 여기 세지 마라).
      OPS_NEWS_STATE_MACHINE_ARN = aws_sfn_state_machine.news.arn
      OPS_NEWS_SCHED_HHMM        = local.news_schedule_hhmm
      OPS_NEWS_SCHED_WEEKEND     = local.news_schedule_weekend
      # 공시 레인(ALPHA-722). ARN 은 상시 주입한다(Planner 가 그 레인으로 뜰 때만 읽으므로
      # 무해하고, 없으면 fail-loud 다). 슬롯 기준은 스케줄이 켜졌을 때만 — 위 locals 참조.
      # `_WEEKEND` 엔 그 조건이 없다: 슬롯 기준이 빈 값이면 그 레인은 판정 대상에서 통째로
      # 빠지므로(entry.py `_due_slots`) 요일 플래그만 남아도 아무것도 기대되지 않는다.
      OPS_DISCLOSURE_STATE_MACHINE_ARN = aws_sfn_state_machine.disclosure.arn
      OPS_DISCLOSURE_SCHED_HHMM        = local.disclosure_schedule_hhmm
      OPS_DISCLOSURE_SCHED_WEEKEND     = local.disclosure_schedule_weekend
      # 장중 수급 레인(ALPHA-769) — 공시와 같은 규약(ARN 상시, 슬롯 기준은 ENABLED 조건부).
      OPS_INVESTOR_INTRADAY_STATE_MACHINE_ARN = aws_sfn_state_machine.investor_intraday.arn
      OPS_INVESTOR_INTRADAY_SCHED_HHMM        = local.investor_intraday_schedule_hhmm
      OPS_INVESTOR_INTRADAY_SCHED_WEEKEND     = local.investor_intraday_schedule_weekend
    }) : { name = k, value = v }]
    secrets = [{
      name = "DATA_PIPELINE_DB__PASSWORD", valueFrom = "${var.db_password_secret_arn}:password::"
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.log_options, { "awslogs-stream-prefix" = "ops" })
    }
  }])
}

# ── 스케줄러 DLQ (스펙 §5: retry 는 있으나 DLQ 부재였다) ──
# 전달 실패(재시도 소진)한 스케줄 이벤트를 잃지 않고 여기 남긴다 — Planner 미기동의 증거이자
# PLANNER_MISSING 탐지의 백스톱.
resource "aws_sqs_queue" "scheduler_dlq" {
  name                      = "${var.name}-scheduler-dlq"
  message_retention_seconds = 1209600 # 14일
}

# 🔴 **큐에 쌓이는 것만으로는 아무도 모른다**(ALPHA-963). 이 DLQ 는 여태 소비자도 알람도
# 없었다 — 원장을 타는 레인들은 Planner 미기동이 PLANNER_MISSING 으로도 드러나 견딜 수
# 있었기 때문이다. 그런데 장전 유니버스 레인(premarket_pipeline.tf)은 **원장 밖**이라
# 그 백스톱이 없고, 전달 실패(StartExecution 거부·역할 오류·스로틀로 재시도 소진)는
# SFN 실행이 아예 안 생기므로 그쪽 알람 둘도 안 운다. 그 경로의 유일한 흔적이 이 큐다.
# 그래서 깊이를 직접 센다 — 공용 큐라 다섯 레인 전부가 덤으로 덮인다.
resource "aws_cloudwatch_metric_alarm" "scheduler_dlq_arrivals" {
  alarm_name        = "${var.name}-scheduler-dlq-arrivals"
  alarm_description = "스케줄 이벤트 전달이 재시도를 소진해 DLQ 로 갔다 — 그 슬롯은 아예 안 돌았다. 장전 유니버스 레인은 원장 밖이라 이 알람이 유일한 신호다."
  namespace         = "AWS/SQS"
  # 🔴 **깊이가 아니라 도착 수를 센다.** `ApproximateNumberOfMessagesVisible`(레벨)로 재면
  # 알람이 **래칭**된다 — CloudWatch 는 OK→ALARM **전이**에서만 통보하는데, 이 큐는 대사도
  # 소비자도 없어 한 번 쌓이면 사람이 비울 때까지 깊이가 0 으로 안 돌아온다. 그러면 첫 실패
  # 뒤의 **모든** 실패가 무음이다 — 다른 레인이 남긴 메시지 하나가 장전 레인의 알람을 영구히
  # 삼키는 형태이고, 이 레인엔 그걸 받쳐 줄 원장 백스톱이 없다.
  # `NumberOfMessagesSent` 는 그 창에 **새로 도착한 수**라 조용한 창에서 0 으로 돌아온다 —
  # 실패할 때마다 새 전이가 나고, 그게 이 알람이 물어야 할 사건("또 놓쳤다")과 일치한다.
  metric_name = "NumberOfMessagesSent"
  dimensions  = { QueueName = aws_sqs_queue.scheduler_dlq.name }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  # 도착이 없는 창은 지표가 아예 안 나온다 — 그건 정상(대부분의 창)이다.
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
}

resource "aws_sqs_queue_policy" "scheduler_dlq" {
  queue_url = aws_sqs_queue.scheduler_dlq.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = ["sqs:SendMessage"]
      Resource  = aws_sqs_queue.scheduler_dlq.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_iam_role.scheduler.arn } }
    }]
  })
}

# ── Reconciler 주기 스케줄 ──
# ECS RunTask universal target — 컨테이너 command 를 reconcile 로 덮고, 스케줄 시각을 env 로 넘긴다.
resource "aws_scheduler_schedule" "reconcile" {
  name                         = "${var.name}-reconcile"
  state                        = var.reconcile_schedule_state
  schedule_expression          = var.reconcile_schedule_expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      Cluster        = var.cluster_arn
      TaskDefinition = aws_ecs_task_definition.ops.arn
      LaunchType     = "FARGATE"
      NetworkConfiguration = {
        AwsvpcConfiguration = {
          Subnets        = var.subnet_ids
          SecurityGroups = [aws_security_group.task.id]
          AssignPublicIp = "DISABLED"
        }
      }
      Overrides = {
        ContainerOverrides = [{
          Name    = local.container_name
          Command = ["reconcile"]
        }]
      }
    })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 5
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dlq.arn }
  }
}
