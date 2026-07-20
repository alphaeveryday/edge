# 컨테이너 이미지 ECR 레포 — 모든 이미지 레포는 foundation 이 `edge/*` 로 소유한다(ADR-0009).
# clean slate 라 전부 신규 생성(과거 import 대상은 삭제됨). env 의 ECS 는 이미지를 URI(tfvars)로,
# schema-migrate 는 repo URL/ARN 을 data 로 참조 — 하드 크로스스택 의존 없음.
locals {
  image_repositories = toset([
    "edge/super-admin-api",
    "edge/tenant-console-api",
    "edge/pipeline",       # news-pipeline SFN 배치 이미지
    "edge/schema-migrate", # Flyway one-off 이미지
  ])
}

resource "aws_ecr_repository" "this" {
  for_each             = local.image_repositories
  name                 = each.key
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}
