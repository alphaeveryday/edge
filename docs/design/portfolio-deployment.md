# 포트폴리오 서비스 배포와 권한

추적: [ALPHA-1266](https://alphaeveryday.atlassian.net/browse/ALPHA-1266)

페이지 주소는 https://etforca.com/portfolio/ 이다. 기존 랜딩 코드는 `src/apps/cloud/app-landing`, React 분석 페이지는 `src/apps/cloud/portfolio-ui`, API와 워커는 `src/apps/cloud/portfolio-worker`에 둔다. 랜딩과 분석 페이지는 각각의 GitHub 배포가 소유한 S3 경로만 갱신한다.

## 배포 경계

- Terraform `envs/dev/portfolio.tf`가 전용 EC2·SG·EIP·DNS·IAM·배포 버킷·Secrets Manager 메타데이터를 소유한다. 기존 15개 자원은 import 블록으로 등록한다. 서버·디스크를 재생성하지 않는다.
- 기존 `module.landing_site`가 CloudFront `/api/portfolio/*` 원본과 경로를 관리한다. 최초 편입 때 기존 원본 비밀 헤더를 읽어 민감 변수로 전달한다. 값은 코드나 PR 댓글에 노출하지 않는다. 키 교체는 Secrets Manager 값과 이 헤더를 함께 변경하는 별도 운영 작업이다.
- PR의 `terraform-plan`, dev 머지 후 `terraform-apply`가 인프라를 검증·반영한다. 로컬 관리자 프로필·수동 CloudFront 변경·로컬 S3 업로드를 배포 경로로 사용하지 않는다.
- `deploy-portfolio`는 dev 코드만 사용한다. Terraform 성공 후 또는 서비스 코드 변경 시 실행하며, SSM 파라미터 `/edge/portfolio/deployment`에서 배포 대상을 읽는다. SSM으로 API·워커를 배포하고 `/portfolio/` 파일만 업로드한다. CloudFront 작업은 캐시 무효화뿐이다.
- 기존 `deploy-app-landing`은 루트 HTML·assets만 소유하므로 포트폴리오 배포와 파일이 충돌하지 않는다.

## 권한

- GitHub는 OIDC 임시 자격증명을 쓴다. 배포 역할에는 지정 버킷·SSM 대상만 추가하고 AWS 관리 키를 저장하지 않는다. Terraform apply 역할은 기존 저장소의 dev 전용 신뢰 정책을 따른다.
- 호스트는 인스턴스 프로파일로 런타임 비밀과 배포 파일을 읽는다. SSH 포트·키 페어는 없다. 443은 CloudFront 원본 prefix list, 80은 ACME 인증서 발급용이다. IMDSv2 필수·hop limit 1을 유지한다.
- `edge-portfolio-ssm-operator` 정책은 해당 호스트의 SSM 셸 세션만 허용한다. 사용자에게 지정받은 SSO/IAM 역할에 연결해야 하며 아직 지정되지 않았다. 정책 생성과 사용자 연결 완료는 구분한다. SSM은 접속 수단이며 그 자체로 IAM 권한을 대체하지 않는다.
- 분석 워커는 비특권 사용자·읽기 전용 파일시스템·capability 제거로 실행한다. AWS 키, Docker 소켓, 전망 DB 비밀번호를 전달하지 않는다.
- 내부 전망 조회 서비스만 기존 읽기 전용 DB 계정을 가진다. 외부 포트가 없으며 임의 SQL·목록·배열 입력 없이 ETF 코드 하나와 목적을 받는다. 모델 도구는 확인된 코드만 허용하고 한 분석 최대 3개·중복 조회 금지·기준시각과 14일 신선도를 검사한다. DB 계정 자체는 기존 결과 API의 읽기 권한을 공유하며 단건 제한은 조회 서비스 계약에서 강제한다.

## 입력 저장

각 단계의 다음 버튼과 계좌 팝업 적용 시 저장한다. `portfolio_input_drafts`가 익명 세션 소유권을 보관하고 `portfolio_input_portfolio`, `funding`, `horizon`, `interests`, `notes`, `tax`가 각각 JSONB 입력을 저장한다. 위 이름의 후반부에도 모두 `portfolio_input_` 접두사가 붙는다. 하나의 트랜잭션으로 갱신하며 다른 세션의 초안은 읽거나 덮어쓸 수 없다. 세션별 최근 초안 10개만 보존하고 오래된 초안은 새 입력 시 정리한다. 최종 작업의 `input`에는 당시 입력과 초안 ID를 별도로 고정하므로 초안 정리가 기존 보고서에 영향을 주지 않는다. 아직 제출하지 않은 필드의 매 키 입력 저장이나 초안 복원 UI는 제공하지 않는다.

ETF 목록은 KRX·Nasdaq Trader의 2026-10-12 수집 스냅샷이다. 국내 1,172개, 미국 5,771개이며 ETF와 원화 현금만 입력 가능하다. `scripts/refresh_catalog.py`로 공개 종목 메타데이터를 갱신하고 리뷰·배포한다. 전망을 일괄 조회하는 기능은 아니다. 자동 목록 갱신 스케줄은 없다.

## 검증과 운영 한계

로컬 검증: PostgreSQL 포함 백엔드 23개, 모바일·데스크톱 6개 통과. 테스트 생략 없음. Starlette TestClient 사용 중단 예정 경고 1건. React 빌드와 Terraform validate 통과. 실제 배포용 컨테이너의 기동·API 상태·7개 입력 테이블·ETF 카탈로그와 워커 자격증명 분리를 확인했다. 변경 전 실제 서비스에서 분석·추가 질문 1회·2회째 차단·이메일 접수까지 약 75초 분석으로 확인했다. 이번 변경의 GitHub CI·실제 배포 검증은 PR과 워크플로 결과로 기록한다.

전용 t3.small 한 대에 PostgreSQL을 함께 운영한다. DB는 전용 볼륨과 종료 시 보존되는 암호화 EBS에 저장하지만 고가용성과 자동 DB 백업은 아직 없다. 이메일은 신청만 저장하고 “1일 이내” 안내를 표시한다. 자동 발송은 구현하지 않았으므로 운영자가 별도 발송해야 한다.

롤백은 GitHub에서 이전 서비스 커밋을 되돌려 dev에 반영하는 방식이다. 인프라 변경도 Terraform PR로 되돌린다. DB 볼륨을 삭제하는 `docker compose down -v`를 사용하지 않는다.

계좌 입력 근거: [국세청 연금계좌 세액공제](https://g.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7875&mi=6596), [ISA 법령](https://law.go.kr/LSW/lsLinkCommonInfo.do?lsJoLnkSeq=1027994797), [고용노동부 퇴직연금 위험자산 한도와 예외](https://www.moel.go.kr/news/enews/report/enewsView.do?news_seq=13711). 입력 정보만으로 자격·절세액을 확정하지 않으며 에이전트가 분석 기준일의 시행 규정을 다시 확인한다.
