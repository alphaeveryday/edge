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
  description = "ALPHAMALE_RELEASE_BUNDLE_VERSION — explanation_run 번들 고정. null 이면 주입 안 함(앱이 S3 fallback)."
  type        = string
  default     = null
}

# ALPHA-470 — analyze 페이즈 Map 팬아웃의 유니버스 배열. 발화 무관 전량 병렬 분석한다.
# ⚠️ SSOT 는 앱 config `sources.toml [krx_etf.source.etf_map]` 키다 — terraform 이 TOML 을
# 못 읽어(네이티브 파서 없음) 여기 미러한다. 유니버스 변경(드묾) 시 두 곳을 함께 고쳐야 한다.
variable "analysis_etf_universe" {
  description = "분석 팬아웃 대상 ETF 티커 배열. sources.toml [krx_etf.source.etf_map] 키의 미러(SSOT=그쪽)."
  type        = list(string)
  default = [
    "069500", "396500", "0167A0", "091160", "395160", "395270", "139260",
    "091230", "469150", "455850", "474590", "471990", "475300", "0210A0",
    "0182R0", "471780", "388420", "363580", "494220", "0093A0", "0190G0",
    "0005G0", "471760", "266370", "486240", "475310", "476260", "261060",
    "482030", "0176P0", "488210",
    "300950", "305720",
  ]
}

variable "task_cpu" {
  type    = number
  default = 1024
}

variable "task_memory" {
  type    = number
  default = 2048
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
variable "news_schedule_expressions" {
  description = "뉴스 SFN EventBridge Scheduler cron 맵(키=이름 접미사). 평일만."
  type        = map(string)
  default = {
    "pre-eod-1" = "cron(0 15 ? * MON-FRI *)"
    "pre-eod-2" = "cron(30 15 ? * MON-FRI *)"
    "day-close" = "cron(50 23 ? * MON-FRI *)"
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
  description = "뉴스 SFN 실행 타임아웃. 인접 스케줄 간격(30분)보다 짧아 실행 간 겹침을 구조적으로 막는다."
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

# 뉴스 SFN TagNews 의 태깅 대상 창(오늘−N일, 평일 3슬롯 — ALPHA-553). read=O(전체 코퍼스)
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
  description = "price window job identity 축(ALPHA-706) — 판정 규칙 변경 시 올린다"
  type        = string
  default     = "intraday-open-v1"
}

variable "minute_detection_policy_version" {
  description = "분봉 판정 정책 identity(ALPHA-708) — 일 단위 트리거와 축이 달라 별도 값"
  type        = string
  default     = "intraday-open-v1"
}
