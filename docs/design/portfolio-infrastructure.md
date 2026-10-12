# 포트폴리오 인프라 Terraform 편입

Refs: ALPHA-1266

기존 독립 포트폴리오 호스트와 `/api/portfolio/*` 연결을 `envs/dev` 관리 대상으로 편입한다. 기존 랜딩은 `src/apps/cloud/app-landing`과 `deploy-app-landing`이 계속 소유한다. 인프라 변경은 PR plan → dev merge → GitHub Terraform apply 순서로 적용하며 관리자 키·로컬 수동 변경은 사용하지 않는다.

`portfolio-imports.tf`는 기존 EC2, SG, RDS 접근 규칙, IAM, EIP, DNS, 배포 버킷, 런타임 비밀 메타데이터 15개를 가져온다. 인스턴스·EIP·버킷·비밀에는 삭제 방지를 둔다. 비밀 값은 Terraform 자원으로 만들지 않는다. CloudFront는 기존 `landing_site` 모듈 하나가 관리한다.

최초 편입 시 CI가 기존 CloudFront의 원본 헤더를 읽어 민감 변수로 전달한다. 로그 마스킹과 PR 댓글 치환을 모두 적용한다. 조회 역할에 Secrets Manager 값 읽기 권한을 추가하지 않는다. 기존 원본이 없거나 헤더가 잘못되면 계획 단계에서 실패한다. 새 환경의 무인 최초 생성용 구성이 아니라 현재 서비스의 안전한 편입 작업이다.

GitHub 애플리케이션 배포 역할에는 지정 배포 버킷, 지정 호스트의 SSM 명령, 배포 설정 파라미터 읽기만 추가한다. `edge-portfolio-ssm-operator` 정책은 해당 호스트의 셸 접속만 허용한다. 연결할 SSO/IAM 신원은 사용자 지정 대기 상태이며 IAM 사용자나 장기 키를 생성하지 않는다.

검증: 로컬 Terraform validate 통과. SSM 운영 정책을 추가하기 전 읽기 전용 plan은 15개 import, 2개 생성, 삭제·교체 0개였다. 이후 변경의 최종 plan·실제 적용은 GitHub 체크와 apply 결과를 기준으로 확인한다. 계획의 기존 dev 자원 드리프트는 PR에서 별도로 확인해야 한다. 로컬 AWS 관리자 프로필 사용은 사용자 지시 이후 중단했다.

단일 t3.small과 전용 PostgreSQL 볼륨으로 운영하며 고가용성·자동 DB 백업은 아직 없다. root EBS는 암호화하고 인스턴스 종료 시 보존한다. 보존은 백업을 대신하지 않는다.
