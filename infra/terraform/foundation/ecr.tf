# 컨테이너 이미지 ECR 레포 — 모든 이미지 레포는 foundation 이 `edge/*` 로 소유한다(ADR-0009).
# clean slate 라 전부 신규 생성(과거 import 대상은 삭제됨). env 의 ECS 는 이미지를 URI(tfvars)로,
# schema-migrate 는 repo URL/ARN 을 data 로 참조 — 하드 크로스스택 의존 없음.
locals {
  # 은퇴 레포(gateway·widget-api)는 2단계 제거 완료(ADR-0032): 1단계에서 force_delete=true 를
  # state 에 반영한 뒤(2026-07-21 apply), 2단계(ALPHA-475)에서 키를 빼 안전히 destroy 했다.
  image_repositories = toset([
    "edge/super-admin-api",
    "edge/tenant-sync-api",
    "edge/tenant-console-api",
    "edge/pipeline",       # news-pipeline SFN 배치 이미지
    "edge/schema-migrate", # Flyway one-off 이미지
  ])
}

resource "aws_ecr_repository" "this" {
  for_each             = local.image_repositories
  name                 = each.key
  image_tag_mutability = "MUTABLE"
  # force_delete=false — RepositoryNotEmpty 가드(실수 삭제·리네임 방지). 레포 은퇴 시에는
  # true 를 먼저 apply 한 뒤 키를 빼는 2단계로 제거한다(위 주석 — gateway·widget-api 전례).
  force_delete = false

  image_scanning_configuration {
    scan_on_push = true
  }
}
