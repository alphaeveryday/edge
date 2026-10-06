# 컨테이너 이미지 ECR 레포 — 모든 이미지 레포는 foundation 이 `edge/*` 로 소유한다(ADR-0009).
# clean slate 라 전부 신규 생성(과거 import 대상은 삭제됨). env 의 ECS 는 이미지를 URI(tfvars)로,
# schema-migrate 는 repo URL/ARN 을 data 로 참조 — 하드 크로스스택 의존 없음.
locals {
  # 은퇴 레포(gateway·widget-api)는 2단계 제거 완료(ADR-0032): 1단계에서 force_delete=true 를
  # state 에 반영한 뒤(2026-07-21 apply), 2단계(ALPHA-475)에서 키를 빼 안전히 destroy 했다.
  image_repositories = toset([
    "edge/super-admin-api",
    "edge/app-api", # ETF Orca 앱 API (ADR-0056)
    "edge/tenant-sync-api",
    "edge/tenant-console-api",
    "edge/pipeline",       # news-pipeline SFN 배치 이미지
    "edge/schema-migrate", # Flyway one-off 이미지
    # Airflow 실행 환경(ALPHA-1119) — DAG 를 구운 Airflow 이미지(태그=커밋)와 격리 검증 태스크 이미지(verify-*).
    "edge/airflow",
    # 데모 온프렘 박스(envs/demo-onprem)가 compose 로 pull 하는 온프렘/데모 런타임 이미지.
    # cloud 앱과 달리 ECS 서비스가 아니라 EC2 compose 로 뜬다(ADR-0033) — 저장소만 foundation 소유.
    "edge/publication-api",
    "edge/screening-worker",
    "edge/intake",
    "edge/sync-agent",
    "edge/mock-broker",       # 데모 증권사 backend(Node) — demo/mock-broker
    "edge/tenant-console-ui", # 검수 콘솔 UI(nginx) — 박스 co-host (ALPHA-554). tenant-console-api 는 위에 존재.
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

# ── 이미지 수명 주기 정책 (ALPHA-1236) ─────────────────────────────────
# 이미지는 git 에서 다시 빌드할 수 있는 캐시로 본다. 남기는 이유는 둘뿐이다 — ①지금 실행 중인
# 이미지, ②배포가 연달아 실패할 때 서비스가 아직 붙들고 있는 이전 버전이 밀려나지 않을 여유.
# 롤백용 이력은 이유가 아니다(보존 범위보다 오래된 버전은 그 커밋에서 다시 빌드한다).
#
# ⚠️ ECR 은 ECS·Lambda 가 무엇을 실행 중인지 모른다 — 규칙은 태그·개수·나이로만 고른다.
# 실행 이미지를 지키는 장치는 이 규칙뿐이라, 바꿀 때는 apply 전에 미리보기로 만료 대상을
# 확인한다(삭제는 되돌릴 수 없다). 절차와 팀이 알아둘 것은 README "ECR 이미지 보존".
locals {
  # 저장소 기본값 — 태그 달린 버전 최근 N개. 10 = 조회된 배포 실행 기록의 연속 실패 최장 9회
  # (2026-09-30 Airflow 구축기) 보다 큰 값.
  ecr_keep_tagged_default = 10

  # 여러 태그 계열이 한 저장소를 쓰는 곳만 규칙을 더한다. 계열을 나누지 않으면 자주 배포되는
  # 계열이 다른 계열의 실행 이미지를 "최근 N개" 밖으로 밀어낸다.
  ecr_repository_overrides = {
    # data-pipeline(<sha>·data-pipeline-latest) · analysis-v2 · analysis-v2-api · db-query 네 계열.
    "edge/pipeline" = {
      protected_rules = [
        # 움직이는 태그가 가리키는 이미지 = 수집·정제 태스크와 db-query 가 실행하는 이미지.
        # ECR 에는 "보존" 동작이 없어, 일어나지 않을 만료 조건으로 쓴다(이런 태그는 2개뿐이다).
        # 상위 규칙의 태그 조건에 맞은 이미지는 하위 규칙이 만료하지 못한다.
        { patterns = ["*-latest"], keep = 10 },
        # 배포 한 번에 워커·API 이미지 2개가 올라간다 → 20 = 배포 10번 치.
        { patterns = ["analysis-v2-*"], keep = 20 },
      ]
      # data-pipeline 은 태그가 SHA 뿐이라 패턴으로 따로 세지 못한다. 저장소 전체로 세되
      # analysis-v2 20개에 data-pipeline 10개가 밀리지 않게 합친 값이다.
      keep_tagged = 30
    }
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [
      for i, selection in concat(
        [
          for r in try(local.ecr_repository_overrides[each.key].protected_rules, []) : {
            tagStatus      = "tagged"
            tagPatternList = r.patterns
            countType      = "imageCountMoreThan"
            countNumber    = r.keep
          }
        ],
        [
          # 태그 없는 이미지 = 태그가 다른 이미지로 옮겨 간 뒤 남은 것. 7일 = 실패한 배포를
          # 알아채고 고칠 시간. 태그 달린 인덱스의 자식(태그 없는 실제 이미지)은 인덱스가 남아
          # 있는 동안 만료되지 않는다.
          {
            tagStatus   = "untagged"
            countType   = "sinceImagePushed"
            countUnit   = "days"
            countNumber = 7
          },
          # `any` 가 아니라 tagged `*` 로 센다 — `any` 는 인덱스의 자식까지 세어 "N개"가
          # N개 버전이 아니게 된다.
          {
            tagStatus      = "tagged"
            tagPatternList = ["*"]
            countType      = "imageCountMoreThan"
            countNumber    = try(local.ecr_repository_overrides[each.key].keep_tagged, local.ecr_keep_tagged_default)
          },
        ]
      ) : { rulePriority = i + 1, selection = selection, action = { type = "expire" } }
    ]
  })
}
