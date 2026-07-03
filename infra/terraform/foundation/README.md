# foundation — 계정 전역·장수명 공유자원 (Phase 1)

`env` 를 destroy 해도 남아야 하는 공유자원을 `envs/*` 와 분리해 소유한다.

## 현재 범위: 와일드카드 ACM

| 자원 | 리전 | 소비자 |
|------|------|--------|
| `*.edgesignal.dev` ACM | ap-northeast-2 | ALB 등 리전 서비스 |
| `*.edgesignal.dev` ACM | us-east-1 | CloudFront(정적 사이트) |

- **도메인 등록(NS 위임)만 수동**, 존부터 SSL 은 TF 가 발급·DNS 자동검증한다.
- 와일드카드라 **새 서브도메인(admin. 등) 추가 시 인증서 재발급 불필요** — env 는 이 인증서를 그대로 참조.
- 두 인증서는 같은 도메인이라 DNS 검증 CNAME 이 동일 → 검증 레코드는 한 벌만 만들어 재사용.

> 확장 예정(원 foundation 범위): 호스팅 영역 import, 앱 ECR, GitHub OIDC provider. clean slate 재건 중이라 지금은 ACM 부터.

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
