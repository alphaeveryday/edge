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
}

variable "schedule_state" {
  description = "검증 동안은 DISABLED. 컷오버 시 ENABLED."
  type        = string
  default     = "DISABLED"
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
  description = "KR 평일 휴장일 목록(YYYY-MM-DD). Planner 가 OPS_KR_HOLIDAYS 로 받는다."
  type        = list(string)
  default     = []
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

variable "state_machine_timeout_seconds" {
  type    = number
  default = 21600
}
