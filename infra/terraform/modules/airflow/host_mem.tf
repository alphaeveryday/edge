# 호스트 메모리 표본(ALPHA-1141) — EC2 기본 지표에는 메모리가 없다. 5분마다 SSM Run Command 로 한 줄을 찍는다.
# 출력은 SSM 명령 이력에 30일 남는다(호스트가 사라져도 남는다, 운영자·에이전트 없이 쌓인다).
# 조회: `aws ssm list-command-invocations --details`, Comment 가 "<name>-host-mem" 인 것.
# 표본이 비면 그 시각의 호스트 값은 없는 것이다(나중에 만들 수 없다). 매번 호스트에서 셸 하나를 잠깐 띄운다.
locals {
  host_mem_probe = "echo \"HOSTMEM t=$(date +%s) avail_kb=$(awk '/^MemAvailable/{print $2}' /proc/meminfo) free_kb=$(awk '/^MemFree/{print $2}' /proc/meminfo) task_cg_mib=$(cat /sys/fs/cgroup/ecstasks.slice/*/memory.current 2>/dev/null | sort -n | tail -1 | awk '{print int($1/1048576)}') cg_oom_kill=$(cat /sys/fs/cgroup/ecstasks.slice/*/memory.events 2>/dev/null | awk '/^oom_kill /{s+=$2} END{print s+0}') kmsg_oom=$(journalctl -k --no-pager 2>/dev/null | grep -ciE 'out of memory|oom-kill|oom_kill') uptime_s=$(cut -d. -f1 /proc/uptime)\""
}

resource "aws_iam_role" "host_mem" {
  count              = local.stop_enabled ? 1 : 0
  name               = "${var.name}-host-mem"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "host_mem" {
  count = local.stop_enabled ? 1 : 0
  name  = "${var.name}-host-mem"
  role  = aws_iam_role.host_mem[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # 문서와 대상 인스턴스(이 ASG 의 것만)를 따로 허용한다(태그 조건은 인스턴스에만 걸린다).
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

resource "aws_scheduler_schedule" "host_mem" {
  count                        = local.stop_enabled ? 1 : 0
  name                         = "${var.name}-host-mem"
  description                  = "ALPHA-1141 호스트 메모리 표본(5분) — 출력은 SSM 명령 이력(30일)"
  schedule_expression          = "rate(5 minutes)"
  schedule_expression_timezone = "UTC"
  flexible_time_window { mode = "OFF" }
  # 역할에 정책이 붙은 뒤에 이 역할로 바꾼다 — 사이에 5분 주기가 오면 그 표본이 거부되고 재시도가 없다.
  depends_on = [aws_iam_role_policy.host_mem]

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ssm:sendCommand"
    role_arn = aws_iam_role.host_mem[0].arn
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
