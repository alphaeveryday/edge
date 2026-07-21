# 컨테이너 이미지 ECR 레포 — 모든 이미지 레포는 foundation 이 `edge/*` 로 소유한다(ADR-0009).
# clean slate 라 전부 신규 생성(과거 import 대상은 삭제됨). env 의 ECS 는 이미지를 URI(tfvars)로,
# schema-migrate 는 repo URL/ARN 을 data 로 참조 — 하드 크로스스택 의존 없음.
locals {
  image_repositories = toset([
    "edge/super-admin-api",
    "edge/tenant-sync-api",
    "edge/tenant-console-api",
    "edge/pipeline",       # news-pipeline SFN 배치 이미지
    "edge/schema-migrate", # Flyway one-off 이미지
    # ── 은퇴 대기(ADR-0032) ── gateway·widget-api 는 코드가 삭제됐지만 ECR 키는 남겨 둔다.
    # 키를 지금 세트에서 빼면 destroy 가 기존 state(force_delete=false)로 실행돼 RepositoryNotEmpty 로 막힌다.
    # force_delete=true 가 이 apply 로 두 레포 state 에 먼저 반영된 뒤, 후속 PR 에서 키를 제거하면 안전히 destroy 된다(2단계).
    "edge/gateway",
    "edge/widget-api",
  ])
}

resource "aws_ecr_repository" "this" {
  for_each             = local.image_repositories
  name                 = each.key
  image_tag_mutability = "MUTABLE"
  # 은퇴 대기 레포(gateway·widget-api)에만 강제 삭제를 허용해 안전한 후속 제거를 준비한다(위 주석).
  # 활성 레포는 force_delete=false 로 둬 RepositoryNotEmpty 가드(실수 삭제·리네임 방지)를 유지한다.
  force_delete = contains(["edge/gateway", "edge/widget-api"], each.key)

  image_scanning_configuration {
    scan_on_push = true
  }
}
