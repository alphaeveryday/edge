variable "name" {
  description = "리소스 접두어 (예: edge-dev-db-query)"
  type        = string
}

variable "region" {
  description = "awslogs 리전."
  type        = string
}

variable "vpc_id" {
  description = "질의 task SG 를 둘 VPC (private RDS 와 같은 VPC 여야 접속된다)"
  type        = string
}

variable "image" {
  description = "질의 CLI 컨테이너 이미지 URI(:태그 포함). 질의기는 analysis-engine 안의 서브커맨드라 data-pipeline 의 analysis_image 와 같은 값을 넘긴다(전용 ECR 을 만들지 않는다)."
  type        = string
}

variable "db_host" {
  description = "RDS address (호스트만, 포트 없이). 컨테이너 PGHOST."
  type        = string
}

variable "db_port" {
  description = "RDS 포트. 컨테이너 PGPORT (env 는 문자열이라 모듈이 tostring 한다)."
  type        = number
}

variable "db_name" {
  description = "질의 대상 데이터베이스명. 컨테이너 PGDATABASE."
  type        = string
}

variable "db_username" {
  description = "접속할 DB 유저(컨테이너 PGUSER). db_password_secret_arn 시크릿의 비밀번호와 같은 유저여야 한다. 원설계의 읽기전용 롤(agent_ro)은 IAM 토큰 전용이었는데 조직 SCP 가 rds-db:connect 를 막아 폐기됐다(main.tf IAM 절) — 읽기전용 강제는 접속 파라미터+SELECT 가드가 진다."
  type        = string
}

variable "query_role" {
  description = "접속 직후 내려앉을 DB 롤(컨테이너 EDGE_QUERY_ROLE → 접속 파라미터 role). 마스터 시크릿 접속에서 SELECT 가드를 통과하는 부작용 함수를 서버 권한으로 막는 층 — 접속 주체가 이 롤의 멤버여야 한다(V202608111500)."
  type        = string
  default     = "agent_ro"
}

variable "db_password_secret_arn" {
  description = "PGPASSWORD 로 주입할 Secrets Manager 시크릿 ARN(JSON 의 password 키). schema-migrate 의 flyway_password_secret_arn 과 같은 패턴 — 실행 역할에 GetSecretValue 가 이 ARN 으로 스코프된다."
  type        = string
}

variable "row_cap" {
  description = "질의 1건이 반환할 최대 행 수. 런타임이 EDGE_QUERY_ROW_CAP 로 읽어 잘라낸다(에이전트의 전체 테이블 덤프 방지)."
  type        = number
  default     = 2000
}

variable "timeout_ms" {
  description = "질의 1건의 제한 시간(ms). 런타임이 EDGE_QUERY_TIMEOUT_MS 로 읽는다 — 무거운 질의가 DB 를 오래 붙잡지 못하게 한다."
  type        = number
  default     = 30000
}

# schema-migrate(512/1024)를 그대로 쓰지 않는다 — 마이그레이션은 SQL 을 순차 적용하지만 질의 task 는
# 커넥션 하나로 최대 row_cap 행을 받아 로그로 넘길 뿐이라, Fargate 최소 조합으로 충분하다.
variable "cpu" {
  description = "Fargate CPU 단위"
  type        = number
  default     = 256
}

variable "memory" {
  description = "Fargate 메모리(MiB). cpu 와 유효한 조합이어야 한다(256 CPU 는 512/1024/2048 만 허용)."
  type        = number
  default     = 512
}

variable "cpu_architecture" {
  description = "질의에 쓰는 이미지(analysis-engine)의 빌드 아키텍처와 일치해야 한다"
  type        = string
  default     = "X86_64"
}

variable "log_retention_days" {
  description = "질의 로그 보존 기간(일). 질의문·결과 요약이 남으므로 감사 목적으로도 읽는다."
  type        = number
  default     = 14
}
