resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == null ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  raw_ingest_jobs = [
    {
      state        = "CollectFmpNews"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-raw', '--source', 'fmp', '--run-id', $.run_id)"
    },
    {
      state        = "CollectFmpPrice"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-price-raw', '--source', 'fmp', '--run-id', $.run_id)"
    },
    {
      state        = "CollectFmpFinancial"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-raw-financial', '--source', 'fmp', '--run-id', $.run_id)"
    },
    {
      state        = "CollectBigKindsNews"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('ingest-raw', '--source', 'bigkinds', '--run-id', $.run_id)"
    },
    {
      state        = "CollectKisPrice"
      taskdef_key  = "kis"
      command_expr = "States.Array('ingest-price-raw', '--source', 'kis', '--run-id', $.run_id)"
    },
    {
      state        = "CollectDartFinancial"
      taskdef_key  = "dart"
      command_expr = "States.Array('ingest-raw-financial', '--source', 'dart', '--run-id', $.run_id)"
    },
    {
      state        = "CollectDartDisclosure"
      taskdef_key  = "dart"
      command_expr = "States.Array('ingest-raw-disclosure', '--run-id', $.run_id)"
    },
    {
      # ETF NAV(ALPHA-380·458) — KIS ETF NAV비교추이(일). 가격과 같은 kis task-def·같은
      # 앱키를 쓰므로 CollectKisPrice 와 동시에 토큰을 발급한다. KIS 는 앱키당 분당 1회만
      # 발급하므로 kis_auth 가 403(EGW00133)을 만나면 61초 대기 후 1회 재시도한다 —
      # 그 재시도가 없으면 매 런에서 두 브랜치 중 하나가 죽는다(ALPHA-458 실측 근거).
      state        = "CollectKisNav"
      taskdef_key  = "kis"
      command_expr = "States.Array('ingest-raw-nav', '--run-id', $.run_id)"
    },
    {
      state        = "CollectFmpEtf"
      taskdef_key  = "fmp"
      command_expr = "States.Array('ingest-raw-etf', '--run-id', $.run_id)"
    },
    # ⚠️ 컷오버 잔여(ALPHA-387) — 스케줄이 KST 15:40(장 마감 후)으로 바뀌며(ALPHA-414)
    # "기준일이 PDF 미게시 시점을 가리킨다"는 구조 문제는 해소됐다. 남은 확인 2개:
    # ① trdDd 백필 수단 부재 — 실패한 날의 스냅샷은 다음 런이 못 줍는다.
    # ② 휴장일 trdDd 응답의 정체 — 7-17 휴장일에도 응답이 왔고 as_of=당일로 라벨됐다
    #    (전 거래일 구성으로 추정. 휴장이면 구성 변동도 없어 실해는 없으나 확인 대상).
    # KRX ETF 는 `trdDd`=오늘 스냅샷이고 빈 응답을 fail-loud 한다(krx_etf.py 의 의도된
    # 설계) — raw 전량성공 게이트라 실패 시 뒤 페이즈 전부가 막힌다.
    # **ENABLED 로 바꾸기 전에 ALPHA-387 을 닫아라.**
    {
      state        = "CollectKrxEtf"
      taskdef_key  = "krx"
      command_expr = "States.Array('ingest-raw-etf', '--source', 'krx', '--run-id', $.run_id)"
    },
  ]

  # raw 성공 뒤 도는 정제 스테이지(ALPHA-355). raw 와 같은 브랜치 구조를 재사용하되 잡만 다르다.
  # normalize 는 벤더 API 키가 필요 없고(레이크만 읽고 canonical 을 쓴다) 모든 task-def 가 같은
  # task_role(레이크 RW)을 공유하므로, 시크릿 없는 bigkinds task-def 를 재사용한다 — 새 task-def·
  # IAM 불요. normalize-financial 은 아직 canonical 스텝이 없어 제외한다(재무는 raw-only).
  #
  # **`--input-run-id $.run_id` = 이 실행이 수집한 raw 만 정제한다**(ALPHA-389). 정제는
  # 데이터셋별 1잡이고 벤더를 합치는 자리라(한 task 가 source= 로 FMP·KIS 를 함께 읽는다),
  # 9개 raw 브랜치가 같은 run_id 를 쓰는 덕에 스코프 안에 그 런의 전 벤더가 들어온다.
  # 적재 자체는 여전히 멱등이다 — canonical 병합이 파티션의 기존 행을 읽어 합친다.
  # 실패 런 raw 재처리는 아래 NormalizeParallel 주석 참조.
  normalize_jobs = [
    {
      state        = "NormalizeNews"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-news', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizePrice"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-price', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeDisclosure"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-disclosure', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeDisclosureSegment"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-disclosure-segment', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeEtf"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-etf', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
    {
      state        = "NormalizeEtfNav"
      taskdef_key  = "bigkinds"
      command_expr = "States.Array('normalize-etf-nav', '--run-id', $.run_id, '--input-run-id', $.run_id)"
    },
  ]

  # feature/factor 스테이지(구 derive, ALPHA-386→408 개명). canonical 을 소비해 분석이 읽을
  # 산출물을 만든다. normalize 와 갈라 둔 이유는 의존이다 — 전부 canonical 을 읽으므로 정제가
  # 끝난 뒤라야 한다. 세 잡은 서로 독립이고(뉴스 feature vs ETF 마스터 vs 가격변동 트리거)
  # 쓰는 대상이 다르다: tag-news 는 레이크 feature 존, load-instruments·load-price-triggers 는
  # Cloud Event Store(RDB, 서로 다른 테이블·같은 rds task-def). 시크릿이 다른 잡은 task-def 도 따로다.
  #
  # 이 페이즈의 최종 범위(ALPHA-408): 뉴스/공시 assertion·event·event_thread 추출 + 가격이벤트
  # 생성까지. 추출 스텝들은 alphamale 로직의 data-pipeline 이관 합의 후 여기 잡으로 편입된다.
  # 로직·정확도(정준영)와 실행·부하·적재(김진기)의 협업 경계가 이 잡 리스트다.
  feature_jobs = [
    {
      state        = "TagNews"
      taskdef_key  = "deepseek"
      command_expr = "States.Array('tag-news', '--run-id', $.run_id, '--limit', '${var.tag_news_limit}')"
    },
    {
      state        = "LoadInstruments"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-instruments', '--run-id', $.run_id)"
    },
    {
      # ETF 가격변동 트리거(ALPHA-406) — canonical 일봉 → price_movement_trigger.
      # 창 미지정 = canonical 전체 스캔 + 멱등 skip 이라 놓친 날을 다음 런이 자연 회복한다.
      state        = "LoadPriceTriggers"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-price-triggers', '--run-id', $.run_id)"
    },
    {
      # ETF NAV 마트 적재(ALPHA-383) — canonical etf_nav → etf_nav_daily. feature 페이즈에
      # 두는 이유는 의존이다: normalize 가 canonical 을 쓴 뒤라야 읽을 대상이 있다.
      # 창 미지정 = canonical 전체 스캔 + 멱등(같은 값이면 no-op, 정정이면 UPDATE).
      state        = "LoadEtfNav"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-etf-nav', '--run-id', $.run_id)"
    },
    {
      # 문서 마스터(ALPHA-374·410) — canonical 뉴스 → document. 창 미지정 = 전체 스캔 +
      # 자연키 멱등 skip. assertion 적재(LoadAssertions, 페이즈 뒤 직렬)의 FK 선행이다.
      state        = "LoadDocuments"
      taskdef_key  = "rds"
      command_expr = "States.Array('load-documents', '--run-id', $.run_id)"
    },
  ]

  raw_ingest_success_checks = [
    for index, _ in local.raw_ingest_jobs : {
      Variable     = "$.branch_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

  normalize_success_checks = [
    for index, _ in local.normalize_jobs : {
      Variable     = "$.normalize_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

  feature_success_checks = [
    for index, _ in local.feature_jobs : {
      Variable     = "$.feature_results[${index}].status"
      StringEquals = "succeeded"
    }
  ]

  ecs_run_task_base = {
    Resource   = "arn:aws:states:::ecs:runTask.sync"
    ResultPath = "$.ecs"
    Parameters = {
      Cluster         = var.cluster_arn
      LaunchType      = "FARGATE"
      PlatformVersion = "LATEST"
      NetworkConfiguration = {
        AwsvpcConfiguration = {
          Subnets        = var.subnet_ids
          SecurityGroups = [aws_security_group.task.id]
          AssignPublicIp = "DISABLED"
        }
      }
    }
  }

  # 모든 페이즈가 동일한 브랜치 구조라 잡 리스트만 바꿔 한 빌더로 재생성한다(ALPHA-355·386).
  # analyze 페이즈는 예외 — 단일 태스크·다른 이미지라 빌더를 안 거치고 아래에 직접 정의한다.
  branches_by_phase = {
    for phase, jobs in { raw = local.raw_ingest_jobs, normalize = local.normalize_jobs, feature = local.feature_jobs } :
    phase => [
      for job in jobs : {
        StartAt = job.state
        States = {
          (job.state) = merge(local.ecs_run_task_base, {
            Type = "Task"
            Next = "${job.state}CheckExitCode"
            Catch = [{
              ErrorEquals = ["States.ALL"]
              ResultPath  = "$.error"
              Next        = "${job.state}TaskFailed"
            }]
            Parameters = merge(local.ecs_run_task_base.Parameters, {
              TaskDefinition = aws_ecs_task_definition.this[job.taskdef_key].arn
              Overrides = {
                ContainerOverrides = [{
                  Name        = local.container_name
                  "Command.$" = job.command_expr
                }]
              }
            })
          })
          "${job.state}CheckExitCode" = {
            Type = "Choice"
            Choices = [{
              Variable      = "$.ecs.Containers[0].ExitCode"
              NumericEquals = 0
              Next          = "${job.state}Succeeded"
            }]
            Default = "${job.state}Failed"
          }
          "${job.state}Succeeded" = {
            Type = "Pass"
            End  = true
            Parameters = {
              job           = job.state
              status        = "succeeded"
              "exit_code.$" = "$.ecs.Containers[0].ExitCode"
              "task_arn.$"  = "$.ecs.TaskArn"
            }
          }
          "${job.state}Failed" = {
            Type = "Pass"
            End  = true
            Parameters = {
              job           = job.state
              status        = "failed"
              cause         = "${job.state} container exited non-zero"
              "exit_code.$" = "$.ecs.Containers[0].ExitCode"
              "task_arn.$"  = "$.ecs.TaskArn"
            }
          }
          "${job.state}TaskFailed" = {
            Type = "Pass"
            End  = true
            Parameters = {
              job       = job.state
              status    = "failed"
              "error.$" = "$.error"
            }
          }
        }
      }
    ]
  }

  raw_ingest_branches = local.branches_by_phase["raw"]
  normalize_branches  = local.branches_by_phase["normalize"]
  feature_branches    = local.branches_by_phase["feature"]

  sfn_definition = jsonencode({
    StartAt        = "RawIngestParallel"
    TimeoutSeconds = var.state_machine_timeout_seconds
    States = {
      RawIngestParallel = {
        Type       = "Parallel"
        Branches   = local.raw_ingest_branches
        ResultPath = "$.branch_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NotifyFailure" }]
        Next       = "RawIngestCheckResults"
      }
      RawIngestCheckResults = {
        Type = "Choice"
        Choices = [{
          And  = local.raw_ingest_success_checks
          Next = "NormalizeParallel"
        }]
        Default = "NotifyFailure"
      }
      # raw 전량 성공일 때만 정제로 넘어간다 — 이번 실행의 raw 수집이 불완전하면 정제를
      # 헛돌리지 않는다. ALPHA-351 로 흔한 절단은 raw 브랜치에서 성공 처리되므로, 여기 걸리는
      # 건 진짜 실패다.
      #
      # ⚠️ **정제는 이 실행의 raw 만 본다**(`--input-run-id $.run_id`, ALPHA-389). 예전엔
      # full-scan 이라 "이전 실패 실행이 저장한 raw 도 다음 성공 실행이 함께 주워간다"는
      # 자동 구제가 있었는데, **그게 없어졌다.** 대가로 정제 비용이 여태 쌓인 raw 전체가
      # 아니라 이번 런에 비례한다(옛 구조는 영구히 O(전체 raw)였다).
      #
      # 그래서 **실패한 실행의 raw 는 명시적으로 주워와야 한다** — 자동으로 안 된다:
      #   normalize-<step> --run-id <새 id> --input-run-id <실패한 run_id>   # 그 런만
      #   normalize-<step> --run-id <새 id>                                  # 전체 백필
      #
      # 이 절차의 트리거는 NotifyFailure 알림이고, 제목에 실패한 run_id 가 박혀 나온다.
      # ⚠️ **그래서 `pipeline_alarm_email` 이 반드시 설정돼 있어야 한다** — null 이면 구독
      # 리소스가 count=0 으로 안 생겨 알림이 구독자 없는 토픽으로 사라지고, 그러면 미승격
      # run 을 **아무도 모른다**(ALPHA-389 착수 시 dev 토픽 구독자가 실제로 0이었다).
      # 수집 창이 '오늘'인 소스(BigKinds·DART·KRX ETF)는 다음 런이 그 날짜를 재수집하지도
      # 않으므로, 알림을 놓치면 그 날 데이터는 raw 에만 남고 canonical 에 영영 없다.
      #
      # 자동 구제가 나아 보이지만, 옛 구조는 "언젠가 주워진다"라 **아무도 그게 언제였는지
      # 몰랐다**. 명시적 재처리는 누가 언제 무엇을 승격했는지가 남는다 — 단 그 대가로 알림이
      # 살아 있어야 한다는 조건이 붙는다.
      NormalizeParallel = {
        Type       = "Parallel"
        Branches   = local.normalize_branches
        ResultPath = "$.normalize_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NotifyFailure" }]
        Next       = "NormalizeCheckResults"
      }
      NormalizeCheckResults = {
        Type = "Choice"
        Choices = [{
          And  = local.normalize_success_checks
          Next = "FeatureParallel"
        }]
        Default = "NotifyFailure"
      }
      # 정제 전량 성공일 때만 feature 로 넘어간다 — 실행 내 순서 제어다.
      #
      # ⚠️ 위 raw→normalize 게이트와 **성격이 다르다**(ALPHA-389 이후). 거기는 정제가 이제
      # run 스코프라 실패 런의 raw 가 자동으로 안 주워진다(영구 격리 — 사람이 재처리). 반면
      # 두 적재 잡은 **canonical 을 full-scan** 하므로 여기 걸린 건 자동 회복된다: 이번 실행이
      # 멈춰도 다음 성공 실행이 밀린 canonical 을 함께 소비한다. tag-news 는 미태깅 기사만
      # 고르고(태거·온톨로지 버전 + 입력 지문으로 판정) load-instruments 는 자연키 멱등이라
      # 재실행이 중복을 만들지 않는다. 즉 feature 는 아직 옛 모델이고, 그래서 안전하다.
      FeatureParallel = {
        Type       = "Parallel"
        Branches   = local.feature_branches
        ResultPath = "$.feature_results"
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NotifyFailure" }]
        Next       = "FeatureCheckResults"
      }
      FeatureCheckResults = {
        Type = "Choice"
        Choices = [{
          And  = local.feature_success_checks
          Next = "LoadAssertions"
        }]
        Default = "NotifyFailure"
      }
      # assertion 적재(ALPHA-376·410) — feature 병렬 페이즈 **뒤 직렬**이다: document FK
      # 의존(LoadDocuments 산출)이 같은 페이즈 병렬이면 레이스라, 페이즈 전량 성공 뒤에 돈다.
      # 자연키 멱등 + missing_document 는 다음 런 회복이라 재실행 안전.
      LoadAssertions = merge(local.ecs_run_task_base, {
        Type = "Task"
        Next = "LoadAssertionsCheckExitCode"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "NotifyFailure"
        }]
        Parameters = merge(local.ecs_run_task_base.Parameters, {
          TaskDefinition = aws_ecs_task_definition.this["rds"].arn
          Overrides = {
            ContainerOverrides = [{
              Name        = local.container_name
              "Command.$" = "States.Array('load-assertions', '--run-id', $.run_id)"
            }]
          }
        })
      })
      LoadAssertionsCheckExitCode = {
        Type = "Choice"
        Choices = [{
          Variable      = "$.ecs.Containers[0].ExitCode"
          NumericEquals = 0
          Next          = "AssembleEvents"
        }]
        Default = "NotifyFailure"
      }
      # 이벤트 조립(ALPHA-412) — 엔진 추출 체인(분류→event 계보→threading)의 이식.
      # LoadAssertions 뒤 직렬: document/assertion 자연키 브리지가 선적재 행에 수렴하려면
      # 로더들이 먼저다. analyze 는 이 스텝이 만든 event 를 소비한다(ADR-0028).
      AssembleEvents = merge(local.ecs_run_task_base, {
        Type = "Task"
        Next = "AssembleEventsCheckExitCode"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "NotifyFailure"
        }]
        Parameters = merge(local.ecs_run_task_base.Parameters, {
          TaskDefinition = aws_ecs_task_definition.this["events"].arn
          Overrides = {
            ContainerOverrides = [{
              Name        = local.container_name
              "Command.$" = "States.Array('assemble-events', '--run-id', $.run_id)"
            }]
          }
        })
      })
      AssembleEventsCheckExitCode = {
        Type = "Choice"
        Choices = [{
          Variable      = "$.ecs.Containers[0].ExitCode"
          NumericEquals = 0
          Next          = "RunAnalysis"
        }]
        Default = "NotifyFailure"
      }
      # analyze 페이즈(ALPHA-408) — 구 analysis-engine SFN 의 완전 흡수. feature(직렬
      # LoadAssertions 포함) 전량 성공일 때만 돈다: **분석은 feature 산출물만 읽는다**
      # (canonical/feature 존 + Cloud Event Store 의 price_movement_trigger·instrument·
      # document·assertion)가 페이즈 경계 계약이다. 지금은 날짜 동기(command
      # 미지정 = ENTRYPOINT 기본 = 오늘 Asia/Seoul)지만, 이 계약 덕에 나중에 수집 빈도가 줄면
      # 이 스텝만 가격이벤트 트리거 기반 비동기 실행으로 떼어낼 수 있다.
      # 특정일(trade_date) 수동 재실행은 SFN 라우팅이 아니라 ecs run-task 레시피다(tasks.tf 주석).
      RunAnalysis = merge(local.ecs_run_task_base, {
        Type = "Task"
        Next = "AnalysisCheckExitCode"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "NotifyFailure"
        }]
        Parameters = merge(local.ecs_run_task_base.Parameters, {
          TaskDefinition = aws_ecs_task_definition.analysis.arn
        })
      })
      AnalysisCheckExitCode = {
        Type = "Choice"
        Choices = [{
          Variable      = "$.ecs.Containers[0].ExitCode"
          NumericEquals = 0
          Next          = "PipelineSucceeded"
        }]
        Default = "NotifyFailure"
      }
      PipelineSucceeded = { Type = "Succeed" }
      NotifyFailure = {
        Type       = "Task"
        Resource   = "arn:aws:states:::sns:publish"
        ResultPath = null
        Next       = "PipelineFailed"
        Parameters = {
          TopicArn = aws_sns_topic.alarms.arn
          # run_id 를 제목에 박는다 — 정제가 run 스코프가 된 뒤로(ALPHA-389) 실패 런의 raw 는
          # **사람이 그 run_id 로 명시 재처리**해야 승격된다. 제목이 전부 "pipeline FAILED" 로
          # 같으면 메일함에서 어느 런을 주워와야 하는지 알 수 없어 절차가 시작되지 않는다.
          # 본문(전체 상태 JSON)에 어느 브랜치가 실패했는지가 들어 있다.
          "Subject.$" = "States.Format('[${var.name}] FAILED — run {}', $.run_id)"
          "Message.$" = "States.JsonToString($)"
        }
      }
      PipelineFailed = { Type = "Fail", Cause = "pipeline failed" }
    }
  })
}

# 이름에 접미사를 두지 않는다(ALPHA-408, 구 "-raw-ingest") — raw 수집만이 아니라
# raw → normalize → feature → analyze 전체가 이 상태머신이다. 이름 변경은 destroy+recreate 지만
# SFN 은 무상태라 안전하다(실행 이력만 새 ARN 에서 다시 시작).
resource "aws_sfn_state_machine" "this" {
  name       = var.name
  role_arn   = aws_iam_role.sfn.arn
  definition = local.sfn_definition
}

# 상태머신 정의 안의 NotifyFailure 는 **정의가 살아 있을 때만** 통보한다. 최상위
# TimeoutSeconds 로 실행이 죽으면 States.Timeout 이 실행 자체를 끝내므로 어떤 Catch 도
# 타지 않고 — 즉 SNS 로 아무것도 안 나간다. LLM 을 부르는 페이즈(feature 의 tag-news, analyze)가
# 들어오면서 이 경로가 실질 도달 가능해졌다(LLM 호출은 소요시간 상한이 없다. tag_news_limit 이 1차 방어).
# 알람은 정의 밖에서 도는 유일한 통보 수단이라 그 구멍을 정확히 메운다.
# ExecutionsFailed 는 안 건다 — NotifyFailure 가 이미 덮고, 겹치면 같은 실패에 두 통이 온다.
resource "aws_cloudwatch_metric_alarm" "execution_timed_out" {
  alarm_name        = "${var.name}-execution-timed-out"
  alarm_description = "SFN 실행이 TimeoutSeconds 초과로 죽었다 — 정의 안의 NotifyFailure 가 못 잡는 경로다."
  namespace         = "AWS/States"
  metric_name       = "ExecutionsTimedOut"
  dimensions        = { StateMachineArn = aws_sfn_state_machine.this.arn }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
}

resource "aws_scheduler_schedule" "daily" {
  name                         = "${var.name}-daily"
  state                        = var.schedule_state
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.this.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ mode = "incremental", run_id = "<aws.scheduler.scheduled-time>" })

    retry_policy {
      maximum_event_age_in_seconds = 86400
      maximum_retry_attempts       = 185
    }
  }
}
