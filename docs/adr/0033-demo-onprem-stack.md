# ADR-0033: 데모 온프렘 terraform 스택 분리 — 실 클라우드와 state 격리

- 상태: 승인됨 (스택 열거 중 Redis 는 [0051](0051-byoc-deployment-topology.md) 결정 6이 대체; 데모 프록시 문면(단일 mock-broker 오리진)은 [0053](0053-widget-direct-serving-no-personalization.md) 구현이 부분 대체 — 설명 조회 /api/v1/* 는 별도 behavior 로 publication-api 직행, ALPHA-992)
- 날짜: 2026-07-21

## 맥락
[[0017-demo-topology-compose]]가 가상 온프렘을 **별도 EC2 + Docker Compose**로 정했다.
이 데모 인프라를 terraform으로 어떻게 담을지가 미결이었다. 핵심 사실:
- **프로덕션 온프렘은 우리가 terraform하지 않는다** — 고객이 compose/이미지로 받아 자기 환경에서
  실행한다([[0017-demo-topology-compose]]의 "Compose 파일 하나로 설치"). 우리 terraform이 다루는
  "온프렘"은 **시연용 가상 온프렘 하나**뿐이고, 그건 우리 AWS 안에 뜬다.
- 그래서 terraform의 진짜 축은 플레인(cloud/onprem)이 아니라 **목적·수명**이다:
  실 벤더 클라우드(`envs/dev`, prod-bound, ECS·SFN·RDS) vs 데모(throwaway, EC2+compose).
- 데모는 데모마다 세우고 부순다. 실 클라우드는 절대 그러면 안 된다.

## 결정
데모를 **별도 terraform 스택(`envs/demo-onprem`)**으로 두고 **state를 분리**한다(같은 S3 백엔드,
다른 key). apps처럼 cloud/onprem 디렉터리를 미러링하지 않는다 — 온프렘 프로덕션은 terraform 대상이
아니므로 대칭 분할이 성립하지 않는다.

- **`modules/demo-onprem`** (신규): EC2 + SG + IAM instance-profile + user-data. 재사용 모듈로 둔다.
- **`envs/demo-onprem`** (신규): demo-onprem 모듈 + `static-site`(가상 MTS 페이지) 조립.
  foundation 아웃풋(ECR·zone·CDN 인증서)만 remote state로 참조 — **dev 스택은 참조하지 않는다**(완전 격리).
- 잠근 결정([[0017-demo-topology-compose]] 구현):
  - **네트워크 단순** — EC2 1대, public subnet/IP. 신규 VPC를 만들지 않고 **default VPC** 사용
    (데모를 dev 네트워크와 격리 + dev 무수정). 실제 망분리(DMZ/내부망 서브넷) 충실 재현은 11월 데모용 후속.
  - **CD = SSM Run Command** — EC2 instance profile에 `AmazonSSMManagedInstanceCore`를 붙여
    SSM-manageable하게 만든다. 코드 배포(compose·이미지)는 GitHub Actions가 SSM으로 원격 실행
    (SSH·열 포트·정적 키 없음, 기존 OIDC 재사용).
  - **mTLS = 데모 단축** — pre-provisioned cert를 SSM SecureString에 둔다. **terraform은 그 값을
    관리하지 않는다**(SecureString 을 terraform 이 관리하면 평문이 state 에 저장·refresh 로 노출됨) —
    운영자가 CLI 로 파라미터를 생성하고, terraform 은 ARN 만 구성해 instance profile 이 읽게 한다.
  - **DB = compose 내장 PG+Redis** — terraform 밖([[0011-rls-to-physical-isolation]] 고객 격리 정신).
  - **부하 실험 겸용** — `instance_type`·볼륨(gp3/io2) 파라미터화. audit heavy-write·publication
    heavy-read를 단일 노드 상한까지 실험(온프렘 scale-up 모델이라 그 상한이 "한 증권사 install"의 값).

**이 PR에서 하지 않는 것(범위 경계)**:
- **terraform apply** — .tf만. 데모 스택은 apply CD 밖이라 **오프라인 `terraform validate` CI**
  (creds·default VPC 불필요)로 HCL·모듈·remote-state 참조를 검증한다. full plan/apply CI 편입은
  실제 apply 착수 시(default VPC/서브넷 확정과 함께). state 파일은 커밋하지 않는다.
- **온프렘 compose 파일·`deploy-demo-onprem.yml`** — `sync-agent`·`compliance-engine` 코드 완료 후.
  GHA 측 `ssm:SendCommand` 권한도 그 CD PR과 함께(EC2의 SSM 수신측만 이번에 준비).
- **CloudFront `/api/*` → EC2(mock-broker) 오리진 프록시** — mock-broker 컨테이너가 생기는 런타임과 함께.
  SG는 CloudFront 오리진 프리픽스 인바운드를 미리 열어 둔다.

## 대안
- **`envs/dev`에 데모를 끼워넣기(count 플래그)** — state 공유라 데모 destroy가 실 스택을 위협.
  수명·blast radius 분리 실패.
- **데모를 ECS로** — [[0017-demo-topology-compose]]는 의도적으로 compose(배포 산출물 그 자체 시연).
  온프렘(고객·에어갭)은 ECS를 못 돌린다. scale-out이 필요한 heavy 워크로드는 클라우드(pipeline)이고 거긴 이미 ECS.
- **cloud/onprem 디렉터리 미러** — 온프렘 프로덕션이 terraform 대상이 아니라 범주 오류.

## 결과
- 데모를 마음껏 apply/destroy해도 실 클라우드(`envs/dev`)는 안전.
- 데모 스택은 foundation에만 의존 — dev와 디커플.
- 실행 흐름: **terraform=서버·호스팅 인프라 / CD(SSM)=이미지·compose·콘텐츠** — 두 수명주기 분리.
- 후속: 온프렘 코드 완료 시 compose·`deploy-demo-onprem.yml`·CloudFront API 오리진·SSM 배포권한(ALPHA-445). (실현됨 — 배포 CD 는 ALPHA-542 로 구축, 마이그레이션 preflight·flyway force-recreate·기동 단언 포함.)
