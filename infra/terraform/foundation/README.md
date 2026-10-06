# foundation — 계정 전역·장수명 공유자원 (Phase 1)

`env` 를 destroy 해도 남아야 하는 공유자원을 `envs/*` 와 분리해 소유한다.

## 현재 범위

와일드카드 ACM(`acm.tf`, 아래 표), Route53 호스팅 영역(`route53.tf`, 기존 존 import), 이미지 ECR 저장소(`ecr.tf`), GitHub OIDC provider(`oidc.tf`), Terraform CD 역할 `edge-tf-plan`·`edge-tf-apply`(`tf-cd.tf`).

| 자원 | 리전 | 소비자 |
|------|------|--------|
| `*.edgesignal.dev` ACM | ap-northeast-2 | ALB 등 리전 서비스 |
| `*.edgesignal.dev` ACM | us-east-1 | CloudFront(정적 사이트) |

- **도메인 등록(NS 위임)만 수동**, 존부터 SSL 은 TF 가 발급·DNS 자동검증한다.
- 와일드카드라 **새 서브도메인(admin. 등) 추가 시 인증서 재발급 불필요** — env 는 이 인증서를 그대로 참조.
- 두 인증서는 같은 도메인이라 DNS 검증 CNAME 이 동일 → 검증 레코드는 한 벌만 만들어 재사용.

> 처음에는 ACM 부터 만들고 호스팅 영역 import·앱 ECR·GitHub OIDC provider 를 확장 예정으로 뒀다. 지금은 모두 위 "현재 범위"의 파일로 들어와 있다.

## env 에서 참조 (느슨한 결합)

env 는 remote_state 없이 `data` 로 조회한다:

```hcl
data "aws_acm_certificate" "wildcard_cdn" {
  provider    = aws.us_east_1
  domain      = "*.edgesignal.dev"
  statuses    = ["ISSUED"]
  most_recent = true
}
# → static-site 모듈의 certificate_arn 으로 전달
```

## 적용

```bash
cd infra/terraform/foundation
terraform init
terraform apply   # 와일드카드 2장 발급 + DNS 검증(수 분)
```

foundation apply 가 **env 보다 먼저**여야 한다(env 가 이 인증서를 data 로 찾으므로).

## ECR 이미지 보존

`ecr.tf` 의 `aws_ecr_lifecycle_policy` 가 모든 이미지 저장소에 수명 주기 정책을 건다(ALPHA-1236). 이미지는 git 에서 다시 빌드할 수 있는 캐시로 본다. 남기는 것은 지금 실행 중인 이미지와, 배포가 연달아 실패할 때 서비스가 아직 붙들고 있는 이전 버전이 밀려나지 않을 여유뿐이다. 롤백용 이력은 남기지 않는다.

| 대상 | 규칙 | 이유 |
|------|------|------|
| 모든 저장소의 태그 달린 버전 | 최근 10개만 남긴다 | 조회된 배포 실행 기록에서 연속 실패가 최장 9회였다(2026-09-30 Airflow 구축기) |
| 모든 저장소의 태그 없는 이미지 | push 후 7일이 지나면 만료 | 태그가 다른 이미지로 옮겨 간 뒤 남은 이미지다. 태그로는 실행할 수 없다 |
| `edge/pipeline` 의 `*-latest` | 개수와 무관하게 남긴다 | 수집·정제 태스크와 db-query 가 이 태그로 실행한다 |
| `edge/pipeline` 의 `analysis-v2-*` | 최근 20개 | 배포 한 번에 워커·API 이미지 2개가 올라가 10번 치다 |
| `edge/pipeline` 전체 | 최근 60개 | analysis-v2 하루 최대 배포 24회 × 2개에 기본 10개와 db-query 몫을 더한 값 |
| `edge/airflow` 의 `verify*` | 최근 3개(전체 13개) | 격리 검증 이미지는 드물게 올라가 운영 배포에 밀린다 |

`edge/pipeline`·`edge/airflow` 만 규칙이 더 있는 이유는 여러 태그 계열이 한 저장소를 쓰기 때문이다. 계열을 나누지 않으면 자주 배포되는 계열이 다른 계열의 실행 이미지를 밀어낸다. `edge/pipeline` 의 data-pipeline 이미지는 태그가 SHA 뿐이라 패턴으로 따로 셀 수 없다. 그래서 `data-pipeline-latest` 가 가리키는 이미지는 확실히 남지만, 그 이전 이미지는 "전체 60개" 안에서 다른 계열과 함께 세어져 보장되지 않는다.

### 이미지를 올리는 사람이 알아둘 것

- **ECR 은 ECS·Lambda 가 무엇을 실행 중인지 모른다.** 실행 이미지는 `*-latest` 가 가리키거나, 그 계열에서 가장 최근에 올린 10개 안에 있어야 남는다.
- **보존 범위보다 오래된 버전은 이미지로 되돌릴 수 없다.** 그 커밋에서 다시 빌드해 올린다.
- **공용 저장소에 새 태그 계열을 만들면 `ecr.tf` 의 `ecr_repository_overrides` 에 그 계열의 규칙을 같이 추가한다.** 추가하지 않으면 저장소 전체 개수에만 걸려, 다른 계열 배포에 밀려 지워질 수 있다.
- **같은 태그로 다시 push 하면 이전 이미지는 태그 없는 이미지가 된다.** 만료 기준은 태그를 잃은 시각이 아니라 push 시각이라, 7일 넘게 쓰던 이미지는 다음 평가(24시간 안)에 지워진다. 서비스가 새 이미지로 넘어가기 전에 지워지면 안 되는 경우에는 이전 이미지에 다른 태그를 남겨 둔다.
- **배포가 10번 넘게 연달아 실패한 채로 두면 실행 중 이미지가 밀려난다.** 이미 떠 있는 태스크는 계속 돌지만 재시작할 때 이미지를 받지 못한다.
- **Terraform 의 baseline 이미지 태그도 지워진다.** `envs/dev/terraform.tfvars` 의 `*_image` 와 `envs/dev/analysis-v2.tf` 의 `image`·`api_image` 는 리소스를 처음 만들 때만 쓰는 값이라 오래된 태그를 가리킨다. 리소스를 새로 만들거나 교체하기 전에 지금 배포된 태그로 올린다.
- 새 저장소는 `image_repositories` 에 넣으면 기본 규칙(최근 10개·태그 없음 7일)이 함께 걸린다.

### 규칙을 바꿀 때

삭제는 되돌릴 수 없다. apply 전에 `ecr_lifecycle_preview.py` 로 만료 대상에 사용 중 이미지가 없는지 확인한다. 이 스크립트는 ECR 미리보기만 쓰고 아무것도 지우지 않는다.

```bash
cd infra/terraform/foundation
terraform plan -out=plan.bin
terraform show -json plan.bin | python3 ecr_lifecycle_preview.py
```

저장소마다 만료 대상 수와, ECS 태스크 정의·서비스·실행 중 태스크와 Lambda 가 참조하는 이미지 가운데 만료 대상에 든 것을 출력한다. 하나라도 있으면 종료 코드 1 이다. 서비스 없이 남은 태스크 정의 패밀리는 `--ignore-family <패밀리>` 로 뺀다. 데모 박스처럼 EC2 compose 로 받아 쓰는 이미지는 AWS API 로 보이지 않아 이 확인에 들어가지 않는다. 적용 뒤 실제 삭제는 24시간 안에 일어난다.
