# 기한 종료와 호스트 메모리 표본(ALPHA-1141) — 운영자 PC·에이전트 세션이 없어도 AWS 쪽에서 돈다.
# 형태는 검증 종료 장치의 "강제 종료"(verify_shutdown.tf: 스케줄러가 API 를 직접 부른다, 코드 없음)와 같다.
#
# 1) host_until 에 서비스 desired 0, 10분 뒤 호스트 ASG 0, 20분 뒤 서비스 desired 0 을 한 번 더.
#    메타DB·역할·시크릿·로그 그룹은 건드리지 않는다. 업무 ECS(worker 클러스터)·SFN 도 건드리지 않는다.
#    - 두 번째 서비스 호출: 기한 직전에 시작한 deploy-airflow 는 정지 확인을 지난 뒤 `--desired-count 1` 을 쓴다.
#      첫 호출 뒤에 그 쓰기가 오면 desired 1 이 남아, 나중에 호스트를 다시 올릴 때 명시 기동 없이 태스크가 뜬다.
#      기한 뒤에 시작한 배포는 desired 0 을 보고 건너뛴다 — 20분이면 앞의 경우를 덮는다.
# 2) 그 뒤의 자동 apply 가 호스트를 되살리지 않게, envs/dev 가 host_count 를 같은 시각으로 계산한다
#    (기한 뒤 plan 은 host_count = 0 → ASG 0, 경보·관리 태스크 제거).
#    이 파일의 스케줄·역할은 host_count 가 아니라 **host_until 에만** 묶는다 — 기한 직후 apply 가 아직 돌지 않은
#    종료 스케줄을 지우면 서비스가 desired 1 로 남기 때문이다. 스케줄은 host_until 을 비우는 정리 PR 에서 걷는다.
# 3) 호스트 메모리: EC2 기본 지표에는 메모리가 없다. 5분마다 SSM Run Command 로 한 줄을 찍는다 — 출력은 SSM 명령
#    이력에 30일 남는다(호스트가 사라져도 남는다). 조회: `aws ssm list-command-invocations --details`,
#    Comment 가 "${var.name}-host-mem" 인 것. 표본이 비면 그 시각의 호스트 값은 없는 것이다(나중에 만들 수 없다).
locals {
  default_stop = var.host_until != ""
  # 스케줄러의 at() 는 초 단위·시간대 없는 문자열이다 — 같은 시각(UTC)에서 만든다.
  stop_service_at = { for k, d in { service = "0m", service-recheck = "20m" } :
  k => formatdate("YYYY-MM-DD'T'hh:mm:ss", timeadd(var.host_until, d)) if local.default_stop }
  stop_host_at   = local.default_stop ? formatdate("YYYY-MM-DD'T'hh:mm:ss", timeadd(var.host_until, "10m")) : ""
  host_mem_probe = "echo \"HOSTMEM t=$(date +%s) avail_kb=$(awk '/^MemAvailable/{print $2}' /proc/meminfo) free_kb=$(awk '/^MemFree/{print $2}' /proc/meminfo) task_cg_mib=$(cat /sys/fs/cgroup/ecstasks.slice/*/memory.current 2>/dev/null | sort -n | tail -1 | awk '{print int($1/1048576)}') cg_oom_kill=$(cat /sys/fs/cgroup/ecstasks.slice/*/memory.events 2>/dev/null | awk '/^oom_kill /{s+=$2} END{print s+0}') kmsg_oom=$(journalctl -k --no-pager 2>/dev/null | grep -ciE 'out of memory|oom-kill|oom_kill') uptime_s=$(cut -d. -f1 /proc/uptime)\""
}

resource "aws_iam_role" "default_stop" {
  count              = local.default_stop ? 1 : 0
  name               = "${var.name}-default-stop"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "default_stop" {
  count = local.default_stop ? 1 : 0
  name  = "${var.name}-default-stop"
  role  = aws_iam_role.default_stop[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecs:UpdateService"], Resource = [aws_ecs_service.airflow.id] },
      { Effect = "Allow", Action = ["autoscaling:UpdateAutoScalingGroup"], Resource = [aws_autoscaling_group.host.arn] },
      # 호스트 메모리 표본 — 문서와 대상 인스턴스(이 ASG 의 것만)를 따로 허용한다(태그 조건은 인스턴스에만 걸린다).
      { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = ["arn:aws:ssm:${var.region}::document/AWS-RunShellScript"] },
      {
        Effect    = "Allow"
        Action    = ["ssm:SendCommand"]
        Resource  = ["arn:aws:ec2:${var.region}:${local.account_id}:instance/*"]
        Condition = { StringEquals = { "ssm:resourceTag/aws:autoscaling:groupName" = aws_autoscaling_group.host.name } }
      },
    ]
  })
}

resource "aws_scheduler_schedule" "default_stop_service" {
  for_each                     = local.stop_service_at
  name                         = "${var.name}-default-stop-${each.key}"
  description                  = "ALPHA-1141 기한 종료 — Airflow 서비스 desired 0(신규 제출 중단). 메타DB·로그는 남는다"
  schedule_expression          = "at(${each.value})"
  schedule_expression_timezone = "UTC"
  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:updateService"
    role_arn = aws_iam_role.default_stop[0].arn
    input    = jsonencode({ Cluster = aws_ecs_cluster.this.arn, Service = aws_ecs_service.airflow.name, DesiredCount = 0 })
    retry_policy {
      maximum_event_age_in_seconds = 900
      maximum_retry_attempts       = 5
    }
  }
}

resource "aws_scheduler_schedule" "default_stop_host" {
  count                        = local.default_stop ? 1 : 0
  name                         = "${var.name}-default-stop-host"
  description                  = "ALPHA-1141 기한 종료 — 서비스 0 의 10분 뒤 호스트 ASG 0"
  schedule_expression          = "at(${local.stop_host_at})"
  schedule_expression_timezone = "UTC"
  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:autoscaling:updateAutoScalingGroup"
    role_arn = aws_iam_role.default_stop[0].arn
    input = jsonencode({
      AutoScalingGroupName = aws_autoscaling_group.host.name, MinSize = 0, MaxSize = 0, DesiredCapacity = 0
    })
    retry_policy {
      maximum_event_age_in_seconds = 900
      maximum_retry_attempts       = 5
    }
  }
}

resource "aws_scheduler_schedule" "host_mem" {
  count                        = local.default_stop ? 1 : 0
  name                         = "${var.name}-host-mem"
  description                  = "ALPHA-1141 호스트 메모리 표본(5분) — 출력은 SSM 명령 이력(30일)"
  schedule_expression          = "rate(5 minutes)"
  schedule_expression_timezone = "UTC"
  end_date                     = var.host_until
  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ssm:sendCommand"
    role_arn = aws_iam_role.default_stop[0].arn
    input = jsonencode({
      DocumentName   = "AWS-RunShellScript"
      Comment        = "${var.name}-host-mem"
      TimeoutSeconds = 60
      Targets        = [{ Key = "tag:aws:autoscaling:groupName", Values = [aws_autoscaling_group.host.name] }]
      Parameters     = { commands = [local.host_mem_probe] }
    })
    retry_policy {
      maximum_event_age_in_seconds = 120
      maximum_retry_attempts       = 0 # 놓친 표본은 다시 만들지 않는다 — 다음 5분이 온다
    }
  }
}
