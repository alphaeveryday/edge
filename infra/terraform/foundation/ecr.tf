# 컨테이너 이미지 ECR 레포 — 모든 이미지 레포는 foundation 이 `edge/*` 로 소유한다.
# env 의 ECS 는 이미지를 URI(tfvars)로, schema-migrate 는 repo URL/ARN 을 data 로 참조하므로
# 하드 크로스스택 의존은 없다(느슨한 결합). ADR-0009.
#
# 두 부류:
#  (1) 기존 수동 앱 레포 → import 로 채택. 옵션은 실물과 동일(MUTABLE·scan off)해 import diff 0.
#  (2) 신규 소유 레포(pipeline·schema-migrate) → 새로 생성. 새 레포라 제약 없어 IMMUTABLE + scan on.
#      - edge/pipeline: CDK news-pipeline 이미지를 대체(레포 소스로 재빌드해 여기 push, 컷오버 독립).
#      - edge/schema-migrate: env 의 edge-dev-schema-migrate 를 대체(ephemeral → import 없이 신규).

locals {
  # (1) import 로 채택하는 기존 앱 레포 (설정을 실물에 맞춤)
  imported_app_repositories = toset([
    "edge/widget-api",
    "edge/gateway",
    "edge/super-admin-api",
    "edge/tenant-console-api",
  ])

  # (2) foundation 이 새로 소유하는 레포 (권장 하이진)
  managed_image_repositories = toset([
    "edge/pipeline",
    "edge/schema-migrate",
  ])
}

resource "aws_ecr_repository" "app" {
  for_each             = local.imported_app_repositories
  name                 = each.key
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = false
  }
}

import {
  for_each = local.imported_app_repositories
  to       = aws_ecr_repository.app[each.value]
  id       = each.value
}

resource "aws_ecr_repository" "managed" {
  for_each             = local.managed_image_repositories
  name                 = each.key
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}
