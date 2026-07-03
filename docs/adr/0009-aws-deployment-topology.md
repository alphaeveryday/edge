# ADR-0009: AWS 배포 토폴로지 — Terraform IaC와 단계(phase) 스택

- 상태: 제안됨
- 날짜: 2026-07-03

## 맥락
[architecture.md](../architecture.md)는 신뢰 경계·서비스 분리(§4)까지만 다루고 **클라우드/배포 토폴로지는 범위 밖(§6)으로 미뤄**, "인프라가 정해지면 별도 문서/ADR"로 남겨 뒀다. 그 인프라가 이제 Terraform 으로 실체화됐다 — VPC·RDS·ECS(Fargate)·ALB·ACM, 그리고 CDK 로 운영되던 분석 배치 파이프라인(Step Functions)의 이관.

동시에 두 운영 리스크가 드러났다: ① 모든 자원을 한 state 에 담으면 `terraform destroy` 한 번이 DNS 루트·이미지 레포까지 날린다(blast-radius). ② state 가 로컬에만 있으면 RDS 를 IaC 가 소유하는 순간부터 유실 위험. 이 토폴로지와 소유 모델을 기록한다.

## 결정
AWS 인프라를 **Terraform 으로 소유**한다(그린필드 — click-ops 참조 없이 VPC 부터 재현). 자원을 **수명·blast-radius 로 3단계 스택**으로 나누며, 각 단계는 독립 state·독립 apply 다:

- **bootstrap** — 원격 state 그릇(S3 버킷 + 네이티브 락). 자기 state 만 로컬, 계정당 1회.
- **foundation** — 계정 전역·장수명 자원(Route53 호스팅 영역·앱 ECR 레포·GitHub OIDC provider). env 를 destroy 해도 살아남아야 하는 것. **도메인 등록(구매)만 수동**이고 영역부터는 TF 가 소유(기존 자원은 import 로 채택).
- **envs/\<env\>** — 폐기 가능한 환경별 자원(VPC·RDS·ECS·ALB·배치 파이프라인).

토폴로지 요지:
- **리전은 `ap-northeast-2` 단일** — ALB·ACM·ECR·RDS 모두 동일 리전.
- **ECS Fargate**, 클러스터를 **상시 API(`*-service`)와 배치 워커(`*-worker`)로 분리**(클러스터는 무료라 cost-neutral). API 간 내부 호출은 Service Connect.
- **네트워크 경계** — gateway 만 공개 엣지(ALB), 나머지는 private 서브넷. 신뢰 경계 결정 자체는 [[0006-gateway-single-edge]]; 여기선 그 네트워크 구현만 다룬다.
- **분석 배치는 Step Functions** — ECS `runTask.sync` 로 수집→정제→분석 8스텝을 순차 오케스트레이션. 기존 CDK 파이프라인을 **병행 재작성 → 검증 → 컷오버(A안)**로 이관하고, 그 과정에서 VPC·RDS 를 env 로 통합한다.
- **비밀번호는 코드/state 에 두지 않는다** — RDS 관리형 시크릿, 외부 API 키는 Secrets Manager(값 수동 주입).
- **단계 간 결합은 느슨하게** — env 는 foundation 을 이미지 URI·zone `data`·OIDC ARN 으로 참조한다(remote_state 강결합 회피).

## 대안
- **단일 모놀리식 state** — 배선은 단순하나 blast-radius 격리가 없다(env destroy 가 zone·ECR 까지 파괴). 폐기.
- **CDK 유지(TF·CDK 이원 운영)** — 파이프라인만 CDK 로 남기면 도구가 갈린다. TF 단일화로 수렴.
- **import(B안)로 CDK 파이프라인 흡수** — CDK 물리명·커스텀 리소스가 TF 와 diff 지옥. 병행 재작성(A안) 채택.
- **배치를 Queue→Worker 로**([proposals](../proposals/system-architecture.md) 초안) — 분석 파이프라인은 **순차 다단계**라 상태·재시도·관측에서 Step Functions 가 낫다. 큐 패턴은 위젯쪽 비동기(이메일·웹훅·알림)에 남긴다.
- **`-target` 기반 단계 apply** — break-glass 도구라 상시 운영엔 부적합. 스택 분리로 단계 경계를 만든다.

## 결과
- **단계적 apply** — bootstrap → foundation → env 순으로 각자 적용/롤백. 도메인 등록·이미지 push·시크릿 값 주입만 수동이고, 그 사이 HTTPS·네트워크·서비스는 코드로 재현된다.
- **blast-radius 격리** — env 를 통째로 destroy 해도 DNS 루트·이미지·OIDC 는 foundation 에 남는다.
- **따라오는 의무**:
  - 원격 state(S3) 전환을 RDS 데이터 이관/컷오버 **전에** 완료(로컬 state 유실 방지).
  - 파이프라인 컷오버 절차 준수 — 스케줄러 DISABLED 로 검증 → ENABLED + CDK 제거 → 데이터 이관.
  - ECR 이미지 push 는 TF 밖(CI)이라 서비스 기동 전 이미지 선행(placeholder + `ignore_changes` 패턴).
- 세부 구성은 [infra/terraform/README.md](../../infra/terraform/README.md)가 SSOT. [[0005-db-as-contract]]·[[0001-monorepo-structure]]와 일관.
