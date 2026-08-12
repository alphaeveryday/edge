# 장전 유니버스 레인 (ALPHA-963)
#
# 07:00 KST 에 홀딩스를 받아 분 레인 universe 정본을 세션 계획(07:45) **전에** 갱신한다.
#   CollectKrxEtf → NormalizeEtf → BuildMinuteUniverse
# 신규 편입 종목이 당일 분봉에 들어오게 하는 것이 목적이다 — 마감후(15:40) 수집만 있던
# 동안은 그날 계획이 이미 선 뒤에 홀딩스가 착지해 편입 종목이 하루를 통째로 놓쳤다
# (2026-08-11 오전 49종 결손, ALPHA-936).
#
# 🔴 **07:30 안에 끝나야 한다.** `build-minute-universe` 는 거래일 07:30(REBUILD_CUTOFF_KST)
# 이후 교체를 스스로 거부한다(ALPHA-953) — 세션이 이미 그 유니버스로 계획됐을 수 있기
# 때문이다. 07:00 시작이면 30분 예산이고, KRX 수집은 `--deadline-sec` 로 상한이 걸려 있다.
# 늦어서 거부당하는 것은 **의도한 실패**다: 계획 뒤에 갈아끼우는 것보다 그날 재빌드를
# 거르는 편이 낫다(전자는 그날 분봉이 영구 결손, 후자는 편입이 하루 늦을 뿐이다).
#
# ── 🔴 이 레인만 Planner 를 안 거친다 ────────────────────────────────────
# 형제 레인 넷(뉴스·공시·장중수급·시장)은 전부 스케줄러가 `plan-run` 을 띄우고 Planner 가
# 원장에 pipeline_run+expected_task 를 남긴 뒤 SFN 을 시작한다. 이 레인만 스케줄러가
# **SFN 을 직접** 시작한다. 이유는 작업 정체성이다:
#
#   `catalog.py` 서문 — "같은 스텝을 두 레인이 동시에 소유하면 `by_cli` 가 먼저 온 엔트리를
#   돌려줘 … 영구 MISSED다. **'둘 다 등록'이라는 선택지가 애초에 없고**, 소유 레인을 옮기는
#   것이 컷오버의 본체다."
#
# 뉴스(ALPHA-591)·공시(ALPHA-724)·장중수급(ALPHA-769)은 전부 **컷오버**로 풀었다. 이 레인은
# 컷오버가 **불가능**하다 — 마감후 수집이 비중 정본이라(장전 raw 엔 수량·구성종목만 오고
# 비중이 없다) 같은 CLI 가 하루 두 번, 두 레인에서 정당하게 돈다. **장전=유니버스용 ·
# 마감후=가중치용.** 카탈로그가 겪어본 적 없는 형태다.
#
# 그래서 원장 **밖**에 둔다: expected_task 가 아예 안 생기므로 `find_expected_task` 가 늘
# None 이고, 컨테이너의 계측 wrapper 는 투명하게 통과한다(원장 손상 0). 선례는
# `sector_index_minute` — "전부 밖 = bounded" 로 시작했다(ALPHA-887).
#
# ⚠️ **대가는 관측 부재다.** 형제 레인은 Planner 가 pipeline_run 을 먼저 커밋해 Reconciler 의
# PLANNER_MISSING 백스톱이 도는데, 이 레인엔 그게 없다. 그 구멍은 아래 **실패 알람 두 개 +
# 스케줄러 DLQ** 가 메운다 — 이 레인은 "조용히 안 돌았다"가 정확히 막으려던 결함과 같은
# 모양이라(신규 편입이 소리 없이 빠진다) 침묵을 그대로 둘 수 없다.
#
# ⏭ 원장에 넣는 길은 열려 있다 — **작업을 0개 등록한 레인**으로 Planner 를 태우면 by_cli
# 충돌 없이 pipeline_run 만 남길 수 있다(그러면 PLANNER_MISSING 도 살아난다). 코드 변경
# (`catalog` 레인 상수 · `entry._LANE_STATE_MACHINE_ARN_ENV` · `ops_ledger.tf` 배선)과 배포
# 순서 제약이 붙어 **별 티켓**이다.

locals {
  # 앞 두 잡은 **기존 리스트의 부분집합 재사용**이다(ALPHA-769 와 같은 방식) — command_expr·
  # taskdef_key 를 베끼면 시장 레인이 인자를 고칠 때 이쪽만 낡는다. 새 ASL state 정의는
  # BuildMinuteUniverse 하나뿐이다.
  premarket_raw_jobs       = [for j in local.raw_ingest_jobs : j if j.state == "CollectKrxEtf"]
  premarket_normalize_jobs = [for j in local.normalize_jobs : j if j.state == "NormalizeEtf"]

  # 유니버스 재빌드(ALPHA-953). taskdef_key 는 NormalizeEtf 와 같은 `bigkinds` 를 쓴다 —
  # 이 스텝은 벤더 자격증명이 없고 storage 만 필요한데, 그 task-def 가 레이크 쓰기 권한을
  # 가진 공용 task role(`aws_iam_role.task`)을 이미 달고 있다(iam.tf — 수집·정제 전부가
  # 쓰는 그 권한). 새 역할·새 문장이 필요 없다.
  # ⚠️ `--universe` 는 **소비자가 읽는 그 URI** 다 — planner·worker·consumer 가 받는
  # `local.minute_universe_uri` 를 그대로 넘긴다. 스텝이 이 인자를 필수로 요구하는 이유가
  # 그것이다(상수로 박으면 생산자와 소비자가 조용히 갈린다, ALPHA-953).
  premarket_universe_jobs = [
    {
      state        = "BuildMinuteUniverse"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('build-minute-universe', '--run-id', $.run_id, '--universe', '${local.minute_universe_uri}')"
    },
  ]

  premarket_raw_branches       = local.branches_by_phase["premarket_raw"]
  premarket_normalize_branches = local.branches_by_phase["premarket_normalize"]
  premarket_universe_branches  = local.branches_by_phase["premarket_universe"]

  # 페이즈별 전량성공 게이트 — 네 선례와 같은 패턴(인덱스별 정적 생성).
  premarket_raw_success_checks = [
    for index, _ in local.premarket_raw_jobs : {
      Variable     = "$.branch_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]
  premarket_normalize_success_checks = [
    for index, _ in local.premarket_normalize_jobs : {
      Variable     = "$.normalize_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]
  premarket_universe_success_checks = [
    for index, _ in local.premarket_universe_jobs : {
      Variable     = "$.universe_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

  # 장전 SFN 정의. 형태는 네 선례와 같되 **앞에 run_id 생성 두 상태가 붙는다** — 이 레인만
  # Planner 를 안 거쳐서 실행 입력에 run_id 가 없기 때문이다(위 절).
  #
  # 스케줄러가 넘기는 `scheduled_time` 은 ISO8601(`2026-08-12T07:00:00Z`)인데, run_id 는
  # 그대로 S3 키(`run_id=…`)가 되므로 콜론이 들어가면 안 된다. 구분자를 떼어 붙여
  # `make_run_id()` 와 **같은 형식**(`%Y%m%dT%H%M%SZ`)으로 만든다 — 그래야 이 레인의 raw
  # 파티션이 다른 레인 것과 같은 모양이고, 사람이 키만 보고 레인을 구분하려 들지 않는다.
  #
  # ⚠️ **슬롯 시각을 쓰는 것이 핵심이다**(실행 시각이 아니라). 재시도가 늦게 떠도 같은
  # run_id 로 수렴해 raw 를 같은 자리에 겹쳐 쓴다 — 재현 실행 규약(run.py 서문)과 같다.
  premarket_sfn_definition = jsonencode({
    StartAt        = "PremarketSplitScheduledTime"
    TimeoutSeconds = var.premarket_state_machine_timeout_seconds
    States = {
      PremarketSplitScheduledTime = {
        Type = "Pass"
        Parameters = {
          # `-`·`:`·`T`·`Z` 를 전부 구분자로 줘 [YYYY,MM,DD,hh,mm,ss] 여섯 조각을 얻는다.
          "parts.$" = "States.StringSplit($.scheduled_time, '-:TZ')"
        }
        ResultPath = "$.slot"
        Next       = "PremarketMakeRunId"
      }
      PremarketMakeRunId = {
        Type = "Pass"
        Parameters = {
          "run_id.$" = "States.Format('{}{}{}T{}{}{}Z', $.slot.parts[0], $.slot.parts[1], $.slot.parts[2], $.slot.parts[3], $.slot.parts[4], $.slot.parts[5])"
        }
        Next = "PremarketRawIngestParallel"
      }
      PremarketRawIngestParallel = {
        Type       = "Parallel"
        Branches   = local.premarket_raw_branches
        ResultPath = "$.branch_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "PremarketNotifyFailure" }]
        Next       = "PremarketRawIngestCheckResults"
      }
      # 여기서 raw 가 실패하면 **계속 가지 않는다** — 네 선례의 "알리고 계속" 규약과 다른
      # 유일한 지점이다. 그 규약은 다운스트림이 부분 raw 로도 의미 있는 산출을 내는 레인의
      # 것이고, 이 레인의 산출은 **유니버스 하나**다. 결손 홀딩스로 만든 유니버스는 그날
      # 수집 대상이 쪼그라든 채 초록으로 도는 형태라(빠진 종목은 기대 집합에서도 빠져
      # 결손으로 안 잡힌다) 부분 성공이 가장 나쁜 결과다.
      # ⚠️ 멈춰도 **어제 유니버스가 그대로 살아 있다** — 정본 객체를 안 건드리므로 그날
      # 레인은 정상으로 뜬다. 잃는 것은 신규 편입 하루뿐이고, 그건 알람으로 드러난다.
      PremarketRawIngestCheckResults = {
        Type    = "Choice"
        Choices = [{ And = local.premarket_raw_success_checks, Next = "PremarketNormalizeParallel" }]
        Default = "PremarketNotifyFailure"
      }
      PremarketNormalizeParallel = {
        Type       = "Parallel"
        Branches   = local.premarket_normalize_branches
        ResultPath = "$.normalize_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "PremarketNotifyFailure" }]
        Next       = "PremarketNormalizeCheckResults"
      }
      PremarketNormalizeCheckResults = {
        Type    = "Choice"
        Choices = [{ And = local.premarket_normalize_success_checks, Next = "PremarketBuildUniverseParallel" }]
        Default = "PremarketNotifyFailure"
      }
      PremarketBuildUniverseParallel = {
        Type       = "Parallel"
        Branches   = local.premarket_universe_branches
        ResultPath = "$.universe_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "PremarketNotifyFailure" }]
        Next       = "PremarketUniverseCheckResults"
      }
      PremarketUniverseCheckResults = {
        Type    = "Choice"
        Choices = [{ And = local.premarket_universe_success_checks, Next = "PremarketSucceeded" }]
        Default = "PremarketNotifyFailure"
      }
      PremarketSucceeded = { Type = "Succeed" }
      PremarketNotifyFailure = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "PremarketFailed"
        Parameters = {
          TopicArn = aws_sns_topic.alarms.arn
          # 이 레인엔 Reconciler 백스톱이 없다 — 이 통보가 "장전이 안 돌았다"의 **유일한**
          # 능동 신호다. run_id 를 제목에 박아 어느 슬롯을 회수해야 하는지 바로 보이게 한다.
          "Subject.$" = "States.Format('[${var.name}-premarket] FAILED — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      PremarketFailed = { Type = "Fail", Cause = "premarket universe pipeline failed" }
    }
  })
}

# 네 선례와 같은 sfn 역할을 재사용한다 — 이 SFN 은 같은 task-def 집합의 부분집합
# (krx·bigkinds)만 runTask 하므로 권한이 이미 충분하다.
resource "aws_sfn_state_machine" "premarket" {
  name       = "${var.name}-premarket"
  role_arn   = aws_iam_role.sfn.arn
  definition = local.premarket_sfn_definition
}

# 네 선례와 같은 이유의 타임아웃 알람 — 최상위 TimeoutSeconds 로 실행이 죽으면
# States.Timeout 이 Catch 를 안 타 정의 안 PremarketNotifyFailure 가 못 잡는다.
resource "aws_cloudwatch_metric_alarm" "premarket_execution_timed_out" {
  alarm_name        = "${var.name}-premarket-execution-timed-out"
  alarm_description = "장전 유니버스 SFN 실행이 TimeoutSeconds 초과로 죽었다 — 정의 안 PremarketNotifyFailure 가 못 잡는 경로다."
  namespace         = "AWS/States"
  metric_name       = "ExecutionsTimedOut"
  dimensions        = { StateMachineArn = aws_sfn_state_machine.premarket.arn }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
}

# 🔴 **이 레인에만 있는 알람** — 형제 레인엔 없다. 저쪽은 Planner 가 pipeline_run 을 먼저
# 커밋해 Reconciler 가 "실행 자체가 없었다"를 잡지만(PLANNER_MISSING), 이 레인은 원장 밖이라
# 그 백스톱이 없다. SFN 이 뜨지도 못한 경우(StartExecution 실패·역할 오류)는 위 두 신호
# (정의 안 SNS·ExecutionsTimedOut) 어느 것도 못 잡으므로 여기서 직접 센다.
resource "aws_cloudwatch_metric_alarm" "premarket_execution_failed" {
  alarm_name        = "${var.name}-premarket-execution-failed"
  alarm_description = "장전 유니버스 SFN 실행이 실패했다 — 이 레인은 원장 밖이라 Reconciler 백스톱이 없다. 오늘 신규 편입 종목이 분봉을 못 받는다."
  namespace         = "AWS/States"
  metric_name       = "ExecutionsFailed"
  dimensions        = { StateMachineArn = aws_sfn_state_machine.premarket.arn }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
}

# 장전 스케줄(KST) — 07:00 MON-FRI 기본. 07:30 마감 앞 30분이 이 체인의 예산이다.
# ⚠️ 공휴일에도 뜬다(평일 cron). 그날은 KRX 가 직전 거래일 PDF 를 주고 유니버스 구성은
# 안 바뀌므로 `build-minute-universe` 가 no-op 으로 끝난다 — 조용한 정상이다.
resource "aws_scheduler_schedule" "premarket" {
  name                         = "${var.name}-premarket"
  state                        = var.premarket_schedule_state
  schedule_expression          = var.premarket_schedule_expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window { mode = "OFF" }

  # 🔴 형제 레인과 **다른 타깃**이다 — `plan-run` RunTask 가 아니라 SFN 직접 시작이다
  # (파일 서두의 원장 절). 그래서 Planner 가 주던 run_id 를 SFN 이 스스로 만든다.
  target {
    arn      = aws_sfn_state_machine.premarket.arn
    role_arn = aws_iam_role.scheduler.arn

    # ⚠️ 플레이스홀더는 jsonencode **바깥**에서 주입한다(ALPHA-593) — jsonencode 가 `<`/`>` 를
    # 이스케이프해 EventBridge 가 컨텍스트 속성 패턴을 인식하지 못한다. 그 함정을 밟으면
    # 리터럴 `<aws.scheduler.scheduled-time>` 이 그대로 들어와 run_id 파싱이 깨진다.
    input = replace(
      jsonencode({ scheduled_time = "SCHEDULED_TIME_TOKEN" }),
      "SCHEDULED_TIME_TOKEN", "<aws.scheduler.scheduled-time>",
    )

    # 재시도 3 — 형제 레인(0회)과 다르다. 저쪽은 슬롯이 하루 여러 번이라 재시도 창이 다음
    # 슬롯과 겹치는 것이 더 나빴는데, 이 레인은 **하루 한 번이고 마감(07:30)까지 30분**이라
    # 겹칠 다음 슬롯이 없다. 그 안에 못 들면 스텝이 스스로 거부하므로 늦은 재시도가
    # 계획을 흔들 수도 없다 — 재시도의 하방이 막혀 있다.
    retry_policy {
      maximum_event_age_in_seconds = 1800
      maximum_retry_attempts       = 3
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dlq.arn }
  }
}
