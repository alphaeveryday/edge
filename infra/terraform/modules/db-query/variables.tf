variable "name" {
  description = "리소스 접두어 (예: edge-dev-db-query)"
  type        = string
}

variable "region" {
  description = "awslogs 리전. rds-db:connect ARN 과 컨테이너 AWS_REGION 에도 쓴다(IAM 토큰 서명 리전이라 DB 와 같아야 한다)."
  type        = string
}

variable "account_id" {
  description = "rds-db:connect ARN 조립용 계정 ID. 모듈이 caller identity 를 조회하지 않고 호출부(env)가 넘긴다 — dev/main.tf 가 ECR ARN 을 조립하는 방식과 동일."
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
  description = "IAM 인증으로 붙을 DB 롤. 마스터 유저가 아니라 읽기 전용 롤이어야 한다 — 이 이름이 rds-db:connect ARN 의 dbuser 라, 여기 마스터를 넣으면 IAM 토큰만으로 쓰기까지 열린다."
  type        = string
  default     = "agent_ro"
}

variable "db_resource_id" {
  description = "RDS 인스턴스의 resource ID(db-XXXX 형식, 인스턴스 식별자와 다르다). rds-db:connect ARN 에 쓴다. 인스턴스를 재생성하면 값이 바뀌므로 rds 모듈 출력을 넘긴다."
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
