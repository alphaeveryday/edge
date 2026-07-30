# bootstrap — 원격 state 백엔드

Terraform 원격 state 를 담을 **S3 버킷**만 만드는 얇은 스택. 락은 별도 DynamoDB 없이 **S3 네이티브 락**(`use_lockfile=true`, Terraform 1.11+)으로 건다.

## 왜 별도 스택인가 (닭-달걀)

`envs/*` 가 원격 state 를 쓰려면 그 state 를 담을 버킷이 **먼저** 있어야 한다. 그런데 그 버킷도 Terraform 으로 만들고 싶다 → 자기 state 를 자기가 만드는 버킷에 둘 수 없는 순환. 그래서 이 스택만은 **자기 state 를 로컬**에 두고 한 번만 apply 해서 버킷을 선(先)생성한다.

> **판별 기준**: "Terraform 이 state 를 저장하려면 이게 먼저 있어야 하나?" → **오직 S3+DynamoDB 만** yes. ECR·Route53 등은 아니다(그건 수명/blast-radius 축의 문제지 닭-달걀이 아니며, `envs/*` 가 관리한다).

## 사용 (계정당 한 번)

```bash
cd infra/terraform/bootstrap
terraform init          # 로컬 state (여기 state 는 커밋 금지 — .gitignore 처리됨)
terraform apply         # 버킷·락 테이블 생성
terraform output        # backend.tf 에 넣을 값 확인
```

기본값은 `variables.tf` 에 박혀 있다(버킷 `edge-tfstate-393229433969`, region `ap-northeast-2`). 다른 계정이면 `-var` 나 `terraform.tfvars` 로 덮는다.

## 그 다음 — envs/dev 를 원격 state 로 전환

1. `envs/dev/backend.tf` 의 S3 블록 주석 해제, 위 `output` 값으로 채운다.
2. 아래로 로컬 state 를 원격으로 이관:
   ```bash
   cd ../envs/dev
   terraform init -migrate-state
   ```
3. 이관 확인 후 로컬 `terraform.tfstate*` 는 삭제(이미 `.gitignore` 대상이라 커밋된 적은 없음).

## 주의

- 이 스택은 거의 손대지 않는다. 버킷은 `prevent_destroy` 로 실수 삭제를 막아둔다.
- `terraform destroy` 하지 말 것 — 모든 환경의 state 원본을 담고 있다.
