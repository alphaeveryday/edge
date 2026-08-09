variable "name" {
  description = "리소스 접두어 (예: edge-dev-data-pipeline)"
  type        = string
}

variable "region" {
  description = "awslogs·스케줄러 리전"
  type        = string
}

variable "vpc_id" {
  description = "data-pipeline task SG 를 둘 VPC"
  type        = string
}

variable "subnet_ids" {
  description = "task 를 띄울 private 서브넷(NAT 로 외부 API/ECR 도달)"
  type        = list(string)
}

variable "cluster_arn" {
  description = "배치를 실행할 ECS 클러스터 ARN"
  type        = string
}

variable "image" {
  description = "data-pipeline 컨테이너 이미지 URI(:태그 포함)"
  type        = string
}

variable "lake_bucket_name" {
  description = "raw/canonical/curated prefix 를 담는 lake bucket 이름"
  type        = string
}

variable "lake_bucket_arn" {
  description = "raw/canonical/curated prefix 를 담는 lake bucket ARN"
  type        = string
}

# ── DB (edge RDS, Cloud Event Store) ────────────────────
# 적재 스텝(load-*)만 쓴다. 접속정보는 평문 env, 비밀번호만 RDS 관리형 시크릿에서 주입
# (analysis-engine 모듈과 같은 관례). 단 env 이름은 data-pipeline 의 설정 네임스페이스를
# 따른다 — DATA_PIPELINE_DB__*(DbConfig). PG* 가 아니다.
variable "db_host" {
  description = "edge RDS host (address, 포트 제외)"
  type        = string
}

variable "db_port" {
  description = "edge RDS 포트"
  type        = number
  default     = 5432
}

variable "db_name" {
  description = "edge RDS 데이터베이스 이름"
  type        = string
}

variable "db_user" {
  description = "edge RDS 사용자 (평문)"
  type        = string
}

variable "db_password_secret_arn" {
  description = "DB 비밀번호 시크릿 base ARN. RDS 관리형 시크릿({username,password} JSON). 모듈이 ':password::' 를 붙여 DATA_PIPELINE_DB__PASSWORD 로 주입."
  type        = string
}

# ── DeepSeek LLM (tag-news) ─────────────────────────────
# analysis-engine 과 **같은 시크릿을 공유**하므로 그릇을 이 모듈이 소유하지 않는다(두 모듈이
# 한 리소스를 동시에 소유할 수 없다) — 호출부가 data 로 조회해 ARN 을 넘긴다.
variable "deepseek_secret_arn" {
  description = "DeepSeek API 키 시크릿 base ARN({\"api_key\":\"...\"} JSON). 모듈이 ':api_key::' 를 붙여 LLM_API_KEY 로 주입."
  type        = string
}

# ── analyze 페이즈 (구 analysis-engine 모듈 흡수, ALPHA-408) ───────
# 로직·정확도는 alphamale 레포 소관이고 이 모듈은 실행만 담당한다 — 경계는 이미지다.
variable "analysis_image" {
  description = "analysis-engine 컨테이너 이미지 URI(:태그 포함). data-pipeline 과 다른 코드베이스(alphamale)라 이미지가 따로다."
  type        = string
}

variable "analysis_release_bundle_version" {
  description = "ALPHAMALE_RELEASE_BUNDLE_VERSION — explanation_run 번들 고정. RDS 의 release_bundle(PUBLISHED) 행과 일치해야 한다. 기본값도 null 도 없는 이유: 미주입이면 영속 전제 결손으로 런이 실패한다(ALPHA-797 이 S3 폴백을 폐기) — 런타임 exit 1 보다 plan 단계에서 막는 게 싸다."
  type        = string
  nullable    = false

  # nullable=false 는 누락·명시적 null 만 막는다 — 빈 문자열은 통과해 런타임 exit 1 이
  # 되므로(코드가 `if bundle:` 로 거른다) description 이 내건 "plan 에서 막는다"가
  # 그 한 갈래에서만 깨진다. 둘은 서로 대신하지 못한다.
  validation {
    condition     = var.analysis_release_bundle_version != ""
    error_message = "release_bundle(PUBLISHED) 행과 일치하는 번들 버전이 필요하다 — 빈 값은 런타임 exit 1 이다(ALPHA-797)."
  }
}

# 시각창 집계 Athena 오프로드(ALPHA-780). 둘 다 채워야 자격이 붙는다 — 하나만 주면
# 정책이 반쪽이라 조용히 폴백하므로 함께 비우거나 함께 채운다.
#
# 이 버킷은 **이 모듈이 만들지 않는다**(terraform 관리 밖). ARN 을 받기만 하고 소유권을
# 주장하지 않는다 — `aws_s3_bucket` 리소스를 여기 두면 다음 apply 가 남의 버킷을 집는다.
variable "analysis_market_data_bucket_arn" {
  description = "5분봉 Iceberg 표 데이터와 Athena 결과 CSV 가 사는 버킷 ARN. 비우면 Athena 자격을 부여하지 않는다(엔진은 canonical 합집합으로 폴백 — 질의당 376MB 를 컨테이너로 받는다)."
  type        = string
  default     = ""
}

variable "analysis_athena_workgroup" {
  description = "EDGE_ATHENA_WORKGROUP — 시각창 집계를 보낼 Athena 워크그룹. 결과 위치는 워크그룹이 강제하므로 EDGE_ATHENA_OUTPUT 은 주입하지 않는다(같이 보내면 질의가 시작도 못 한다)."
  type        = string
  default     = ""
}

# ── 설명 소비자의 DuckDB 조인층 (Fargate 실행 조건) ────────────────
# 이 셋이 비면 컨테이너에서 S3 뷰가 통째로 안 붙거나 OOM 으로 죽는데, 둘 다 **사유 없는
# 침묵**으로 나타난다(빈 조인 = 0행 = 판정불가). 그래서 코드 기본값에 맡기지 않고 주입한다.
variable "analysis_duckdb_s3_chain" {
  description = "DUCKDB_S3_CHAIN — DuckDB CREDENTIAL_CHAIN 순서. Fargate 는 컨테이너 자격증명 엔드포인트(instance)로만 붙으므로 그 항목이 반드시 있어야 한다(sso;config;env 만으로는 S3 뷰 전량 실패)."
  type        = string
  default     = "env;instance;config;sso"
}

variable "analysis_duckdb_memory_limit" {
  description = "DUCKDB_MEMORY_LIMIT — task_memory 보다 낮아야 DuckDB 가 DUCKDB_TEMP_DIR 로 spill 하며 버틴다. 같거나 크면 컨테이너 한도를 먼저 쳐서 OOMKilled(사유 없는 exit 137)로 끝난다."
  type        = string
  default     = "1.5GB"
}

variable "analysis_duckdb_temp_dir" {
  description = "DUCKDB_TEMP_DIR — spill 위치. 컨테이너 파일시스템에서 쓰기 가능한 곳은 /tmp(Fargate 임시 스토리지)뿐이다."
  type        = string
  default     = "/tmp"
}

variable "task_cpu" {
  type    = number
  default = 1024
}

variable "task_memory" {
  description = "수집·정제·ops·1분 상주 task-def 의 Fargate 메모리(MiB)."
  type        = number
  default     = 2048
}

# analyze 만 4096 (ALPHA-671). DuckDB 조인층이 `pit_daily`(101MB) 위에 윈도우 함수와 CTE
# 전개를 얹는데 피크가 실측되지 않았고 OOMKilled 전력이 있다. OOM 은 exit 137 만 남기고 어느
# 질의였는지 말하지 않아 원인 추적이 런 재현에 달린다 — 그 침묵을 메모리로 산다.
# **공유 `task_memory` 를 올리지 않은 이유**: 1분 상주 서비스 2개가 24시간 켜져 있어서
# 전량 인상은 analyze 한 번 실행값이 아니라 상주 요금이 된다(월 $1 이 아니다).
# 1024 CPU 의 Fargate 유효 조합(2048~8192, 1GB 단위) 안이다.
variable "analysis_task_memory" {
  description = "analyze task-def 전용 Fargate 메모리(MiB). DuckDB 피크가 상한을 정한다."
  type        = number
  default     = 4096
}

variable "cpu_architecture" {
  description = "이미지 아키텍처와 일치"
  type        = string
  default     = "X86_64"
}

# KR 장마감 기준(ALPHA-414) — KRX 마감 15:30 뒤 10분 여유를 두고 그날 종가 수집 →
# proxy 트리거 산출 → analyze 가 한 런에서 이어진다. 구 기본(미 동부 16:10 = KST 05:10)은
# KR 가격·뉴스가 전날 기준으로 잡히고 KRX ETF(trdDd=오늘)의 기준일이 미게시 시점을
# 가리켰다. 트레이드오프: 이 시각에 US 소스(FMP)는 직전 미 거래일 기준이 된다 —
# MVP 가 국내 ETF(ADR-0024)라 KR 우선이 맞다. 휴장일은 가격 파티션이 안 생겨
# 트리거 없음 → analyze 평온 종료로 자연 처리된다.
variable "schedule_expression" {
  description = "EventBridge Scheduler cron. 기본은 평일 KRX 장 마감 후(15:40 Asia/Seoul)."
  type        = string
  default     = "cron(40 15 ? * MON-FRI *)"
}

variable "schedule_timezone" {
  type    = string
  default = "Asia/Seoul"

  # 원장 슬롯 키(ALPHA-564)가 cron 의 HH:MM 을 **KST 로 읽는다**(`ops_ledger.tf` locals). 파이프라인
  # 전체가 KR 시장 기준이라 `planner.KST` 도 +9 고정이다. 이 타임존을 바꾸면 Planner 는 실제 예약
  # 시각을 KST 로 환산해 다른 슬롯 키를 남기는데 Reconciler 는 cron 숫자 그대로를 찾아, **실제
  # 런이 영영 대조되지 않고 조용히 통과한다**(원장이 관대해지는 방향). 소리 없이 어긋나느니
  # plan 단계에서 멈춘다(Rule 12). KST 아닌 스케줄이 정말 필요해지면 슬롯 키에 타임존을 담는
  # 설계 변경이 선행이다.
  validation {
    condition     = var.schedule_timezone == "Asia/Seoul"
    error_message = "슬롯 키가 cron HH:MM 을 KST 로 해석한다 — Asia/Seoul 외 타임존은 지원하지 않는다(ALPHA-564)."
  }
}

variable "schedule_state" {
  description = "검증 동안은 DISABLED. 컷오버 시 ENABLED."
  type        = string
  default     = "DISABLED"
}

# 뉴스 SFN 스케줄(ALPHA-553). 키는 스케줄 이름 접미사, 값은 cron(Asia/Seoul, schedule_timezone 공유).
# pre-EOD 15:00·15:30 = 정규장 마감구간(종가 동시호가) 뉴스를 EOD analyze 전에 적재. 23:50 = 장외/야간 마무리.
#
# **주 7일인 이유(ALPHA-874)**: 수집 창이 `[어제, 오늘]` 2일이라(run.py `default_window`,
# DEFAULT_LOOKBACK_DAYS=1) 어떤 날은 그날이나 다음 날에 런이 있어야 덮인다. MON-FRI 였을 때
# 일요일은 월요일 런의 창이 덮었지만 **토요일은 토·일 모두 런이 없어 매주 통째로 비었다**
# (2026-08-01 raw 파티션 0 실증). 뉴스는 휴장일에도 나오므로(catalog `kr_trading_calendar=False`)
# 요일을 넓히는 것이 곧 해소다. ⚠️ 다만 **결함분은 토요일 3슬롯뿐**이고 일요일 3슬롯은 월요일
# 런이 이미 덮던 구간이다(얻는 것은 즉시성과 런 유실 대비 중복). 주당 체인 실행이 15 → 21 회가
# 되고 매 실행이 `tag-news`·`assemble-events` 두 LLM 비용 축을 태운다 — 절반은 해소가 아니라 여유다.
#
# ⚠️ 요일 표기가 `? * MON-SUN` 이 아니라 **`* * ?`(DOM=매일, DOW=any)** 인 이유: AWS 의
# day-of-week 은 `1-7 = SUN-SAT` 이라 `MON-SUN` 은 `2-1`, 즉 **내림차순 범위**이고 AWS 문서엔
# 랩어라운드 지원 서술이 없다. `* * ?` 가 문서의 "매일" 예시 그대로이고, DOM·DOW 중 하나는 `?`
# 여야 한다는 규칙 때문에 둘을 맞바꾼 것뿐이다 — 슬롯 시각은 그대로다.
#
# ⚠️ **요일을 MON-FRI 와 주 7일 사이로 좁히지 마라.** 최소 수정은 사실 토요일만 더하는
# `MON-SAT` 인데 원장이 그걸 표현하지 못한다 — 레인의 "주말에도 도는가"가 이진 플래그라
# (ops_ledger.tf) MON-SAT 은 일요일 3슬롯까지 기대하게 만들고, 그 런은 뜰 리 없으니 **닫히지
# 않는** PLANNER_MISSING 이 매주 3개 열린다. 지금은 그런 표기가 plan 단계에서 죽는다.
# **새 슬롯 시각도 같은 이유로 추가하지 마라** — `OPS_NEWS_SCHED_HHMM` 이 평평한 HH:MM 목록이라
# 요일 축이 없어, 한 레인 안에 평일 슬롯과 주말 슬롯을 섞는 것 자체가 표현 불가다.
variable "news_schedule_expressions" {
  description = "뉴스 SFN EventBridge Scheduler cron 맵(키=이름 접미사). 주 7일(ALPHA-874)."
  type        = map(string)
  default = {
    "pre-eod-1" = "cron(0 15 * * ? *)"
    "pre-eod-2" = "cron(30 15 * * ? *)"
    "day-close" = "cron(50 23 * * ? *)"
  }
}

variable "news_schedule_state" {
  description = "뉴스 SFN 스케줄 상태. 검증 동안 DISABLED, 컷오버 시 ENABLED."
  type        = string
  default     = "DISABLED"
}

variable "news_state_machine_timeout_seconds" {
  # 인접 스케줄(15:00·15:30, 30분 간격)보다 **짧아야** 한 실행이 다음 실행과 겹치지 않는다 —
  # 겹치면 두 뉴스 실행이 AssembleEvents 에서 같은 미threaded event 를 동시 처리해 prior-count·
  # lifecycle_stage 레이스가 난다(edge-review P1). 25분(1500s)=8분 실측에 여유 + 30분 간격 아래.
  # 초과분은 fail-loud 타임아웃(무한 LLM 을 조용한 레이스보다 낫게 — 타임아웃 알람이 잡는다).
  # ⚠️ 위 "8분 실측"은 **BigKinds 수집이 40 page 에서 잘리던 때**의 값이다(ALPHA-541 이전).
  # 캡이 실제 창(2일, 108~126 page)에 맞춰지면서 raw 스텝만 최소 +86초 늘었다 — 상한을 올릴
  # 수는 없으므로(30분 간격이 묶는다) **다음 실측 때 이 여유를 다시 재라**. 넘으면 States.Timeout
  # 이라 정의 안 NewsNotifyFailure 를 안 타고 죽는다(news_pipeline.tf 의 타임아웃 알람이 그 자리).
  description = "뉴스 SFN 실행 타임아웃. 인접 스케줄 간격(30분)보다 짧아 실행 간 겹침을 구조적으로 막는다."
  type        = number
  default     = 1500
}

# 공시 SFN 스케줄(ALPHA-722). 키는 스케줄 이름 접미사, 값은 cron(Asia/Seoul, schedule_timezone 공유).
#
# **평일 09:00–18:00 매시 정각 10슬롯.** 근거는 셋이 함께 만족돼야 하는 제약이다:
#   ① 슬롯 = cron 엔트리 — `ops_ledger.tf` 가 cron 의 HH:MM 을 regex 로 파싱해 슬롯 키를 만든다.
#      `rate()` 는 슬롯 키가 안 나와 **쓸 수 없고**, 슬롯 수는 곧 맵 엔트리 수다.
#   ② 비중첩 — 체인이 ECS 3연속 기동(raw → normalize 병렬 → feature)이고 기동 실측이 122초까지
#      오른 적 있다(ALPHA-688). 타임아웃 2400s 가 정상(≈12분)의 3배 여유이면서 간격 3600s 아래다.
#   ③ 접수 분포 — DART 접수 운영시간은 07:30~19:00 이다(장 시간이 아니다).
#
# 양끝에 슬롯을 두지 않는 이유(둘 다 데이터가 아니라 지연만 잃는다):
#   * 07:30~09:00 = 하루의 1.4%. 증분 창이 어제~오늘이라 09:00 슬롯이 창 전체를 다시 읽어 잡는다.
#   * 18:00~19:00 제출분은 `rcept_dt` 가 **다음 영업일**이라(실측) 오늘 창에 애초에 없다 —
#     다음 날 09:00 이 잡고, 금요일 저녁분은 월요일 09:00 이 잡는다.
#
# 피크(16시 = 하루의 27%)에 30분 슬롯을 얹지 않았다: 얻는 것이 그 1시간대의 지연 60→30분
# 하나인데 **읽을 소비자가 아직 0개**(장중 트리거 ALPHA-649 미착수)고, 최소 간격이 30분으로
# 줄면 ②의 타임아웃 여유가 함께 깎인다. 조밀화가 필요해지면 이 맵에 항목을 더하면 되고 원장
# 슬롯 기준(OPS_DISCLOSURE_SCHED_HHMM)은 이 cron 에서 파생되므로 자동으로 따라온다.
variable "disclosure_schedule_expressions" {
  description = "공시 SFN EventBridge Scheduler cron 맵(키=이름 접미사). 평일 09~18시 정각."
  type        = map(string)
  default = {
    "h09" = "cron(0 9 ? * MON-FRI *)"
    "h10" = "cron(0 10 ? * MON-FRI *)"
    "h11" = "cron(0 11 ? * MON-FRI *)"
    "h12" = "cron(0 12 ? * MON-FRI *)"
    "h13" = "cron(0 13 ? * MON-FRI *)"
    "h14" = "cron(0 14 ? * MON-FRI *)"
    "h15" = "cron(0 15 ? * MON-FRI *)"
    "h16" = "cron(0 16 ? * MON-FRI *)"
    "h17" = "cron(0 17 ? * MON-FRI *)"
    "h18" = "cron(0 18 ? * MON-FRI *)"
  }
}

variable "disclosure_schedule_state" {
  description = "공시 SFN 스케줄 상태. 신설 검증 동안 DISABLED, 컷오버(시장 SFN 에서 공시 스텝 제거)와 같은 apply 로 ENABLED."
  type        = string
  default     = "DISABLED"
}

variable "disclosure_state_machine_timeout_seconds" {
  # 슬롯 간격(3600s)보다 **짧아야** 한 실행이 다음 실행과 겹치지 않는다 — 겹치면 두 실행이
  # 같은 rcept_no 본문을 동시에 보고 seen-map(ALPHA-720)의 TOCTOU 로 같은 ZIP 을 두 번 받고,
  # 같은 canonical 파티션을 동시에 병합한다. 40분 = 정상(≈12분)의 3배 여유 + 간격 아래.
  # 초과분은 fail-loud 타임아웃이다(무한 대기보다 낫고, 타임아웃 알람이 잡는다).
  description = "공시 SFN 실행 타임아웃. 슬롯 간격(60분)보다 짧아 실행 간 겹침을 구조적으로 막는다."
  type        = number
  default     = 2400
}

# 장중 수급 SFN 스케줄(ALPHA-769). 키는 스케줄 이름 접미사, 값은 cron(Asia/Seoul, schedule_timezone 공유).
#
# **평일 5슬롯 — 벤더 갱신 시각 + 5분.** 슬롯 수를 우리가 고르는 게 아니라 소스가 정한다:
# KIS HHPTJ04160200 은 하루 4~5회만 갱신하고 유형별로 시각이 갈린다
# (외국인 09:30·11:20·13:20·14:30 / 기관 10:00·11:20·13:20·14:30). 합집합이 5개다 —
# 4개로 줄이면 09:30 외국인 값이 30분 늦게 들어오고, 더 늘려도 같은 값을 다시 받을 뿐이다.
#
# **왜 +5분인가 — 정각 반영 지연이 여전히 미관측이다.** 2026-08-06 dev 실측으로 원문 슬롯 필드
# `bsop_hour_gb` 의 도메인이 `"1"`~`"5"` **코드**임이 확인됐는데(시각 문자열이 아니다), 그래서
# 이 필드로는 반영 지연을 잴 수 없다. 아는 것은 "14:30 슬롯이 14:51 이전에 이미 확정돼 있었다"
# (지연 ≤ 21분)뿐이라 여유를 줄일 근거가 없다. 줄이는 비용은 실재하고(첫 슬롯을 놓치면 그날
# 09:30 값이 다음 슬롯까지 없다) 유지 비용은 0이다 — 응답이 누적이라 늦게 물어도 앞 슬롯이
# 함께 온다. 좁히려면 정각 근처 관측이 따로 필요하다.
#
# **겹침 없음** — 마지막 갱신이 14:30, 마감이 15:30 이라 14:35 이후엔 장중 갱신이 없다.
# 확정치는 EOD 레인(15:40)이 별도 데이터셋으로 받는다.
#
# ⚠️ 슬롯 = cron 엔트리다 — `ops_ledger.tf` 가 cron 의 HH:MM 을 regex 로 파싱해 슬롯 키를 만든다.
# `rate()` 는 슬롯 키가 안 나와 **쓸 수 없고**, 슬롯 수는 곧 맵 엔트리 수다(공시 레인과 같은 제약).
variable "investor_intraday_schedule_expressions" {
  description = "장중 수급 SFN EventBridge Scheduler cron 맵(키=이름 접미사). 평일 5슬롯(벤더 갱신 +5분)."
  type        = map(string)
  default = {
    "s0935" = "cron(35 9 ? * MON-FRI *)"
    "s1005" = "cron(5 10 ? * MON-FRI *)"
    "s1125" = "cron(25 11 ? * MON-FRI *)"
    "s1325" = "cron(25 13 ? * MON-FRI *)"
    "s1435" = "cron(35 14 ? * MON-FRI *)"
  }
}

variable "investor_intraday_schedule_state" {
  # 공시·뉴스와 달리 기본 DISABLED 로 세우지 않는다 — 그 둘은 시장 SFN 이 돌던 스텝의 소유 레인
  # 이동이라 "두 레인이 같은 스텝을 동시에 소유하는" 겹침 창을 막아야 했지만, 이 3스텝은 시장
  # SFN 에 들어간 적이 없는 신설이라 그 창이 존재하지 않는다(investor_intraday_pipeline.tf 도입부).
  description = "장중 수급 SFN 스케줄 상태. 신설이라 겹침 창이 없어 기본 ENABLED."
  type        = string
  default     = "ENABLED"
}

variable "investor_intraday_state_machine_timeout_seconds" {
  # **최소 슬롯 간격(09:35→10:05 = 1800s)보다 짧아야** 한 실행이 다음 실행과 겹치지 않는다.
  # 겹치면 두 실행이 같은 canonical 파티션을 동시에 병합한다.
  # 값 근거는 실측이다(2026-08-06 dev): 수집 210s + 정제·적재 각 ~90s/~60s = 체인 ≈ 6분.
  # 여기에 ECS 3연속 기동(기동 실측이 122초까지 오른 적 있다, ALPHA-688)을 더해도 ≈ 12분이다.
  # 1500s = 정상의 2배 여유이면서 간격 아래. 초과분은 fail-loud 타임아웃이다(무한 대기보다 낫고,
  # 타임아웃 알람이 잡는다).
  description = "장중 수급 SFN 실행 타임아웃. 최소 슬롯 간격(30분)보다 짧아 실행 간 겹침을 구조적으로 막는다."
  type        = number
  default     = 1500
}

# 운영 원장 Reconciler(ALPHA-530) 주기 실행. daily(schedule_state)와 별개로 켠다.
variable "reconcile_schedule_state" {
  description = "Reconciler 스케줄. 검증 동안 DISABLED, 원장 컷오버 시 ENABLED."
  type        = string
  default     = "DISABLED"
}

variable "reconcile_schedule_expression" {
  description = "Reconciler 실행 주기. 미실행·STALLED 탐지 지연 허용치에 맞춘다."
  type        = string
  default     = "rate(15 minutes)"
}

# Planner 의 비거래일(NON_TRADING_DAY) 판정용 KR 평일 공휴일(YYYY-MM-DD). 주말은 코드가 안다.
# ⚠️ 완전한 거래소 캘린더 연동 전까지의 잠정 주입 지점 — 미설정이면 평일 공휴일에도 수집이 돈다.
variable "kr_holidays" {
  description = "KR 평일 휴장일 목록(YYYY-MM-DD). Planner·KRX·KIS 수집이 OPS_KR_HOLIDAYS 로 받는다."
  type        = list(string)
  default     = []

  # 형식이 틀린 항목은 코드에서 **조용히 무시된다** — is_trading_day 는 `day.isoformat()` 과
  # 문자열 비교라 오타 하나가 그 날을 거래일로 되돌리고, KRX 수집은 휴장일 응답(직전 거래일
  # PDF)을 그날 as-of 로 오라벨한다(ALPHA-387 이 막으려는 바로 그 결함). 배포 시점에 잡는다.
  validation {
    condition     = alltrue([for d in var.kr_holidays : can(formatdate("YYYY-MM-DD", "${d}T00:00:00Z"))])
    error_message = "kr_holidays 항목은 달력상 실재하는 YYYY-MM-DD 여야 한다(오타는 조용히 무시돼 그 날이 거래일로 처리된다)."
  }
}

variable "alarm_email" {
  description = "raw ingest 실패 알림 수신 이메일. null 이면 SNS 구독 없이 토픽만."
  type        = string
  default     = null
}

variable "log_retention_days" {
  type    = number
  default = 14
}

# tag-news 는 기사 하나당 LLM 을 한 번 부르고, 창을 안 주면 canonical 전체가 대상이다
# (다른 스텝과 달리 미지정이 증분 기본창이 아니다). run.py 의 --limit 은 "실수로 큰 금액이
# 나가는 걸 호출부가 막을 수 있게" 둔 가드이고, SFN 이 그 호출부다. 상한을 안 주면 재태깅
# 축(TAGGER_VERSION·온톨로지 범프)이 발동한 다음 런이 전 기간을 한 번에 태깅한다.
# 상한에 걸린 잔여분은 다음 런이 이어받는다 — 미태깅 기사만 고르므로 진척이 누적된다.
variable "tag_news_limit" {
  description = "tag-news 가 한 실행에서 새로 LLM 을 부를 기사 수 상한(비용 가드). 잔여는 다음 실행이 이어받는다."
  type        = number
  default     = 10000
}

# 뉴스 SFN TagNews 의 태깅 대상 창(오늘−N일, 주 7일 3슬롯 — ALPHA-553·874). read=O(전체 코퍼스)
# 스캔 상한이 목적이다(ALPHA-540). 넓게 둘수록 창 밖 회수가 튼튼하다 — 한 날짜가 슬롯×(N+1)회
# 스캔돼 일시적 llm_error 가 창 안에서 자가 회복하고(멱등 skip 이라 재스캔 비용은 스캔뿐),
# 창보다 오래된 정정본만 풀스캔 수동 실행이 맡는다. --window-days 미주입(수동·백필)은 풀스캔
# 유지 — 이 변수는 SFN 경로만.
variable "tag_news_window_days" {
  description = "뉴스 SFN tag-news 태깅 대상 창(오늘−N일). 스캔 비용↔창 밖 회수 여유의 트레이드오프."
  type        = number
  default     = 3
  validation {
    # 음수는 역전 창(오늘+N,오늘)이라 전 파티션을 제외해 0건 태깅을 성공으로 위장하고(Rule 12),
    # 소수는 command 로 "3.5" 가 실려 run.py 의 argparse type=int 가 거부해 매 런이 즉시 실패한다.
    # 상한(3650)은 run.py 공통 가드(ALPHA-592)와 짝 — 없으면 plan 은 통과하고 매 런이 거부된다.
    # 셋 다 plan 시점에 잡는다(run.py 도 음수·상한은 런타임에 재차 거른다).
    condition     = var.tag_news_window_days >= 0 && var.tag_news_window_days <= 3650 && floor(var.tag_news_window_days) == var.tag_news_window_days
    error_message = "tag_news_window_days 는 0~3650 의 정수여야 한다(음수=역전 창, 소수=argparse int 거부, 초과=런타임 거부)."
  }
}

# 뉴스 SFN AssembleEvents 의 조립 대상 창(오늘−N일, ALPHA-592). 기본 1 = [어제, 오늘] 겹침 —
# 자정 crossing(23:50 슬롯 기본 경로)과 overnight 갭(D 마감 후 기사를 D+1 런이 조립)을 함께
# 닫는다. 멱등(document-exists skip)이라 겹침 비용은 스캔뿐이다.
variable "assemble_window_days" {
  description = "뉴스 SFN assemble-events 조립 대상 창(오늘−N일). 자정 crossing·overnight 갭 방지 겹침."
  type        = number
  default     = 1
  validation {
    # 음수는 역전 창이라 전 파티션을 제외해 0건 조립을 성공으로 위장하고(Rule 12), 소수는
    # command 로 "1.5" 가 실려 run.py argparse type=int 가 거부해 매 런이 즉시 실패한다.
    # 상한(3650)은 run.py 공통 가드와 짝 — 넘으면 date 연산 하한 초과로 로그 없이 크래시한다.
    condition     = var.assemble_window_days >= 0 && var.assemble_window_days <= 3650 && floor(var.assemble_window_days) == var.assemble_window_days
    error_message = "assemble_window_days 는 0~3650 의 정수여야 한다(음수=역전 창, 소수=argparse int 거부, 초과=런타임 거부)."
  }
}

variable "krx_etf_deadline_sec" {
  description = "KRX ETF 구성종목 수집의 벽시계 상한(초). 닿으면 남은 ETF 를 미시도로 기록하고 받은 것은 저장한 뒤 조기 마감한다(ALPHA-581)."
  type        = number
  default     = 300
  validation {
    # 0·음수는 **첫 대상도 시도하기 전에** 상한에 걸려 매 런이 0건 수집으로 끝난다(상한이
    # 수집을 통째로 막는다). 소수는 command 로 실려도 run.py 의 argparse type=float 가 받으므로
    # 정수 강제는 하지 않는다 — 여기선 "0보다 큰가"만 본다(run.py 도 런타임에 재차 거른다).
    condition     = var.krx_etf_deadline_sec > 0
    error_message = "krx_etf_deadline_sec 는 0 보다 커야 한다(0 이하면 첫 대상도 못 시도하고 끝난다)."
  }
}

variable "state_machine_timeout_seconds" {
  type    = number
  default = 21600
}

# US(FMP) 수집 잡을 raw 병렬에 넣을지(ALPHA-558). false 면 CollectFmpNews·CollectFmpPrice·
# CollectFmpFinancial·CollectFmpEtf 4잡이 raw_ingest_jobs 에서 빠져 SFN 이 실행조차 안 한다.
# 기본 false — 공용 FMP 키의 bandwidth(rolling 30일) 소진 중, US 잡이 매 런 429 로 실패해 daily
# 런을 FAILED 로 마감하던 노이즈를 없앤다(KR 은 독립이라 계속 수집). 복구 시 true 로 되돌린다 —
# 다운 기간 공백의 소스별 복구성(가격·뉴스 windowed 소급 / 재무 재조회 / ETF holdings 영구결손)은
# statemachine.tf 의 us_fmp_ingest_jobs 주석 참조(중복 방지).
variable "us_fmp_enabled" {
  description = "US(FMP) 수집 잡을 raw 병렬에 포함할지. 기본 false — FMP bandwidth 소진 중 daily 런을 clean 하게 유지(ALPHA-558)."
  type        = bool
  default     = false
}


variable "minute_universe_uri" {
  description = "1분 파이프라인 universe 정본 객체 URI(ALPHA-711). 비우면 레이크 버킷의 config/minute/universe.json — planner·worker·consumer 가 같은 객체를 봐야 한다"
  type        = string
  default     = ""
}

variable "minute_trigger_schema_version" {
  # v2(ALPHA-745)로 함께 올린다 — 이 축을 v1 에 두면 규칙이 바뀐 뒤에도 job 원장이
  # 어느 판정 규칙의 window 였는지 구분하지 못한다(변수 설명 그대로의 사유).
  # 판정 자체는 소비자의 detection_policy_version 이 정하므로 이 값은 식별용이고,
  # 소비자는 job 행의 이 값을 기대 상수와 대조하지 않아 롤링 배포에도 안전하다.
  description = "price window job identity 축(ALPHA-706) — 판정 규칙 변경 시 올린다"
  type        = string
  default     = "intraday-anchor-v2"
}

variable "minute_detection_policy_version" {
  # v2(ALPHA-745): 기준선=전일 종가·가변 앵커 재발화·2h 쿨다운 폐지. trigger_id 가 이
  # 값을 포함해 v1 행과 섞이지 않는다.
  description = "분봉 판정 정책 identity(ALPHA-708) — 일 단위 트리거와 축이 달라 별도 값"
  type        = string
  default     = "intraday-anchor-v2"
}

# ── 세션 스케일 오케스트레이션(ALPHA-712) ─────────────────────────────
variable "minute_session_schedule_state" {
  description = "1분 세션 start/stop 스케줄 상태. 다른 스케줄과 같은 규약 — 검증 동안 DISABLED, 컷오버 시 ENABLED"
  type        = string
  default     = "DISABLED"
}

# ⚠️ 두 cron 은 **universe 가 정하는 세션 범위 밖**이어야 한다. 시간외 거래 종목이 하나라도
# 있으면 계획 범위가 08:00–20:00 이고(`plan_session_windows`), 없으면 09:00–15:30 이다.
# 기본값은 넓은 쪽(시간외 포함) 기준이다 — 좁혀 두면 개장 뒤에 뜨거나 마감 전에 내려간다.
variable "minute_session_start_expression" {
  description = "Premarket 스케일업 cron(Asia/Seoul). 세션 첫 window(시간외 08:00) 전이어야 한다"
  type        = string
  default     = "cron(45 7 ? * MON-FRI *)"
}

variable "minute_session_stop_expression" {
  description = "EOD drain+스케일다운 cron(Asia/Seoul). 세션 마지막 window(시간외 20:00) 후여야 한다"
  type        = string
  default     = "cron(5 20 ? * MON-FRI *)"
}

variable "minute_session_dataset" {
  description = "스케일 오케스트레이션이 계획·드레인할 세션 dataset. 상주 서비스의 세션 축이 가격 레인이라 price_minute 고정(뉴스 소비자도 이 세션 수명에 결속)"
  type        = string
  default     = "price_minute"
}

variable "minute_session_news_source_group" {
  description = "news_minute 세션의 source_group(ALPHA-717). 비우면 뉴스 레인 미편입 — start 가 가격 세션만 계획한다"
  type        = string
  default     = "bigkinds"
}

variable "minute_session_inav_source_group" {
  description = "etf_inav_minute 세션의 source_group(ALPHA-882). 비우면 iNAV 레인 미편입 — start 가 iNAV 세션을 계획하지 않고 inav-worker 도 올리지 않는다"
  type        = string
  # iNAV 는 KIS 단독이다 — 토스 분봉 API 에 NAV 축이 없다(`1m`·`1d` 캔들만). 어휘 밖 값은
  # 오케스트레이터가 기동에서 거부한다(`_lane_source_group`).
  default = "kis"
}

variable "minute_session_disclosure_source_group" {
  description = "disclosure_minute 세션의 source_group(ALPHA-875). 비우면 공시 레인 미편입 — 공시는 SFN 10슬롯 레인이 계속 소유한다"
  type        = string
  # ⚠️ 이 값이 **컷오버 스위치**다. 비어 있으면 start 가 공시 세션을 계획하지 않고
  # disclosure-worker 도 안 뜬다(서비스 정의는 착지하되 desired 0). 비면 SFN 레인이 계속
  # 돌고, 채우면 1분 레인이 소유한다 — **둘을 동시에 켜지 않는다**(같은 CLI 를 두 레인이
  # 소유하면 `catalog.by_cli` 가 먼저 온 쪽을 돌려줘 한쪽은 영구 MISSED 가 된다).
  # 그래서 이 값을 채우는 apply 는 아래 SFN 스케줄 비활성과 **같은 apply** 여야 한다.
  default = "dart"
}

variable "minute_session_source_group" {
  description = "그 세션의 source_group. price-worker 의 DATA_PIPELINE_MINUTE_PRICE_WORKER__SOURCE 와 같아야 같은 session_id 가 유도된다"
  type        = string
  # kis(ALPHA-735) — 토스는 초당 5회라 종목당 1콜 × 400종이 60초 창을 넘는다. 이 기본값은
  # `MinutePriceWorkerConfig.source` 와 **함께 움직여야 한다**(계약 테스트가 대조한다).
  default = "kis"
}

variable "super_admin_api_url" {
  description = "ExposureReverted 회수 집행 대상(ALPHA-746) — analysis-consumer 가 부르는 super-admin-api base URL. 무효화(WITHDRAWN 전이·INVALIDATION 발번·감사)의 발화자를 super-admin 하나로 유지한다"
  type        = string
}
