# 포트폴리오 서비스 배포와 권한

추적: [ALPHA-1266](https://alphaeveryday.atlassian.net/browse/ALPHA-1266)

페이지 주소는 https://etforca.com/portfolio/ 이다. 기존 랜딩 코드는 `apps/etforca/app-landing`, React 분석 페이지는 `apps/etforca/portfolio-ui`, API와 워커는 `apps/etforca/portfolio-worker`에 둔다. 랜딩과 분석 페이지는 각각의 GitHub 배포가 소유한 S3 경로만 갱신한다.

## 배포 경계

- Terraform `envs/dev/portfolio.tf`가 전용 EC2·SG·EIP·DNS·IAM·배포 버킷·Secrets Manager 메타데이터를 소유한다. 기존 15개 자원은 import 블록으로 등록한다. 서버·디스크를 재생성하지 않는다.
- 기존 `module.landing_site`가 CloudFront `/api/portfolio/*` 원본과 경로를 관리한다. 최초 편입 때 기존 원본 비밀 헤더를 읽어 민감 변수로 전달한다. 값은 코드나 PR 댓글에 노출하지 않는다. 키 교체는 Secrets Manager 값과 이 헤더를 함께 변경하는 별도 운영 작업이다.
- PR의 `terraform-plan`, dev 머지 후 `terraform-apply`가 인프라를 검증·반영한다. 로컬 관리자 프로필·수동 CloudFront 변경·로컬 S3 업로드를 배포 경로로 사용하지 않는다.
- `deploy-portfolio`는 dev 코드만 사용한다. Terraform 성공 후 또는 서비스 코드 변경 시 실행하며, SSM 파라미터 `/edge/portfolio/deployment`에서 배포 대상을 읽는다. SSM으로 API·워커를 배포하고 `/portfolio/` 파일만 업로드한다. CloudFront 작업은 캐시 무효화뿐이다.
- 기존 `deploy-app-landing`은 루트 HTML·assets만 소유하므로 포트폴리오 배포와 파일이 충돌하지 않는다.

## 고객 화면 이용 분석

포트폴리오 UI는 기존 [PostHog 웹 프로젝트 658097](https://us.posthog.com/project/658097)에 수집한다. `analytics.ts`의 공개 프로젝트 키는 랜딩과 같고, 개인 API 키나 추가 서버 환경변수는 필요 없다. `etforca.com/portfolio/`와 www 도메인의 같은 경로에서만 실행한다. 로컬 미리보기·관리자 진입점에서는 수집하지 않는다. SDK의 봇 제외는 유지한다.

Web analytics의 Top paths에서 `/portfolio/`를 선택하면 랜딩(`/`)과 분리한다. www 방문도 공개 주소인 `etforca.com`으로 합쳐 집계한다. 익명 방문·세션을 서버에서 생성할 수 있도록 `$cookieless_mode`를 보존하고 `$host`를 고정된 `etforca.com`으로 전송한다. 경로가 안 보이면 프로젝트·조회 기간·호스트 필터와 실제 `$pageview` 적재를 확인한다. 브라우저 전송 테스트나 수집 API의 성공 응답만으로 집계 완료를 판정하지 않는다.

PostHog **Product analytics → New insight → Trends**에서 아래 이벤트와 속성을 사용한다. 모든 포트폴리오 이벤트에 `screen`이 있으므로 랜딩과 분리할 수 있다.

| 확인할 내용 | 이벤트·집계 |
|---|---|
| 화면별 실제 방문 수 | `$pageview`의 Total count, Breakdown `screen` |
| 화면별 총 체류 시간 | `portfolio_screen_engagement`의 Sum `visible_seconds`, Breakdown `screen` |
| 방문 1회당 평균 체류 | 위 체류 합계 ÷ 같은 화면의 `$pageview` 수. 15초 단위 이벤트의 평균을 방문당 평균으로 사용하지 않는다 |
| 스크롤이 멈춘 구간 | `portfolio_scroll_pause`의 Total count 또는 Sum `pause_seconds`, Breakdown `section`, 필요시 `screen = result` 필터 |
| 분석 만족도 | `portfolio_feedback`, Filter `action = submitted`, Total count, Breakdown `rating` (`good` 좋아요 / `okay` 그냥 그래요 / `needs_improvement` 개선이 필요해요) |
| 정체 위치·도달 깊이 | 정체 이벤트의 `scroll_y`(뷰포트 상단 px), `scroll_depth_pct`(뷰포트 하단 기준 %), 체류 이벤트의 `max_scroll_depth_pct` |

`screen`은 입력 5단계(`input_holdings`, `input_funding`, `input_horizon`, `input_interests`, `input_question`), `loading`, `queued`, `running`, `result`, `failed`이다. `section`은 `answers`, `current_portfolio`, `recommendation`, `changes`, `account_plans`, `execution`, `performance`, `conclusion`, `outlook`, `insights`, `evaluation`, `scenarios`, `followup`, `feedback`이며 그 밖의 위치는 화면 이름으로 집계한다.

만족도 팝업은 완료된 고객 보고서에서 과거 성과 다음 핵심 평가 구간에 도달하면 표시한다. 이모지 3지 선택이며 닫기·Escape를 지원한다. 같은 분석의 응답·닫기는 브라우저 로컬 저장소에 표시해 다시 묻지 않는다(저장소 차단 시 현재 페이지에서만 유지). `portfolio_feedback`의 `action`은 `shown`, `submitted`, `dismissed`이고, `rating`은 응답에만 들어간다. 관리자·중단된 보고서에는 팝업이 없다. SDK Surveys 대신 앱의 고정 선택 UI와 기존 이벤트 수집 경로를 사용한다.

체류는 탭이 보이는 시간이며 실제 독서·집중 시간을 뜻하지 않는다. 숨긴 화면은 방문으로도 세지 않는다. 스크롤·레이아웃 변화 없이 2초 이상 머문 구간을 다음 이동·숨김·종료 때 기록한다. 화면 가운데의 구간을 기준으로 하며, 펼치기 등으로 레이아웃이 바뀌면 위치를 갱신한다. 앱 전환·페이지 이탈의 마지막 이벤트는 즉시 Beacon으로 넘기지만 네트워크 차단이나 브라우저 강제 종료 시 전달을 보장하지 않는다.

기존 `cookieless_mode: always` 정책을 유지한다. 자동 클릭·본문 수집, 세션 녹화, SDK 설문·기능 플래그는 끄고 이벤트·속성 허용 목록만 전송한다. 입력 내용·이메일·보유 종목·보고서 본문·분석 ID·URL 쿼리·해시는 보내지 않는다. 사용자 식별자를 연결하지 않으며 날짜를 넘긴 동일 사용자 추적은 하지 않는다. 결과별 배치가 달라지는 보고서는 구간별 이벤트로 비교한다. 프로젝트의 세션 녹화·히트맵 설정을 바꾸거나 저장된 대시보드를 자동 생성하지 않는다.

## 권한

- GitHub는 OIDC 임시 자격증명을 쓴다. 배포 역할에는 지정 버킷·SSM 대상만 추가하고 AWS 관리 키를 저장하지 않는다. Terraform apply 역할은 기존 저장소의 dev 전용 신뢰 정책을 따른다.
- 호스트는 인스턴스 프로파일로 런타임 비밀과 배포 파일을 읽는다. SSH 포트·키 페어는 없다. 443은 CloudFront 원본 prefix list, 80은 ACME 인증서 발급용이다. IMDSv2 필수·hop limit 1을 유지한다.
- Exa 키는 기존 Secrets Manager `edge/portfolio/runtime` JSON의 `EXA_API_KEY` 필드로 관리한다. 배포 전에 값을 등록해야 하며, 배포 스크립트가 읽어 권한 `0600`의 `runtime.env`에 저장하고 워커에만 주입한다. 키 교체 후에는 `deploy-portfolio`를 재실행한다. 시크릿 값은 Git·Terraform state·배포 로그에 기록하지 않는다.
- 관리자 대시보드는 `edge-portfolio-admin-access` 정책과 고정 포트 문서 `edge-portfolio-admin-tunnel`로만 접속한다. 기존 `edge-portfolio-ssm-operator` 셸 정책은 관리자 대시보드 사용자에게 부여하지 않는다. 사용자가 승인한 work 인증은 전용 edge-portfolio-admin-local 역할 발급에만 사용하며, SSM은 이 역할의 임시 권한으로 연결한다. 전용 역할과 정책 연결도 Terraform이 관리한다. SSM은 접속 수단이며 그 자체로 IAM 권한을 대체하지 않는다.
- 분석 워커는 비특권 사용자·읽기 전용 파일시스템·capability 제거로 실행한다. AWS 키, Docker 소켓, 전망 DB 비밀번호를 전달하지 않는다.
- 내부 전망 조회 서비스만 기존 읽기 전용 DB 계정을 가진다. 외부 포트가 없으며 임의 SQL·목록·배열 입력 없이 ETF 코드 하나와 목적을 받는다. 모델 도구는 확인된 코드만 허용하고 한 분석 최대 3개·중복 조회 금지·기준시각과 14일 신선도를 검사한다. DB 계정 자체는 기존 결과 API의 읽기 권한을 공유하며 단건 제한은 조회 서비스 계약에서 강제한다.

## 입력 저장

각 단계의 다음 버튼과 계좌 팝업 적용 시 저장한다. `portfolio_input_drafts`가 익명 세션 소유권을 보관하고 `portfolio_input_portfolio`, `funding`, `horizon`, `interests`, `notes`, `tax`가 각각 JSONB 입력을 저장한다. 위 이름의 후반부에도 모두 `portfolio_input_` 접두사가 붙는다. 하나의 트랜잭션으로 갱신하며 다른 세션의 초안은 읽거나 덮어쓸 수 없다. 세션별 최근 초안 10개만 보존하고 오래된 초안은 새 입력 시 정리한다. 최종 작업의 `input`에는 당시 입력과 초안 ID를 별도로 고정하므로 초안 정리가 기존 보고서에 영향을 주지 않는다. 아직 제출하지 않은 필드의 매 키 입력 저장이나 초안 복원 UI는 제공하지 않는다.

ETF 목록은 KRX·Nasdaq Trader의 2026-10-12 수집 스냅샷이다. 국내 1,172개, 미국 5,771개이며 ETF와 원화 현금만 입력 가능하다. `scripts/refresh_catalog.py`로 공개 종목 메타데이터를 갱신하고 리뷰·배포한다. 전망을 일괄 조회하는 기능은 아니다. 자동 목록 갱신 스케줄은 없다.

## 검증과 운영 한계

절세 정보 팝업은 카드형 단일 선택과 마지막 IRP·ISA·연금저축 체크박스를 사용한다. `tax_profile.recommended_accounts`는 전략 검토 대상으로 저장하며 실제 보유 계좌와 구분한다. 미확인 항목의 기본값은 ‘확인 필요’다. 금액 입력 방식을 다시 선택해도 기존 금액을 지우지 않으며, 다른 종목의 금액이 비어 있어도 이미 입력한 현금 금액은 표시한다.

2026-10-12 GitHub 배포 후 실제 분석·추가 질문·2회째 차단·이메일 접수 검증은 66.91초 분석으로 통과했다. 목적을 지정한 069500 단건 전망 조회도 `found`였다. 첫 실행은 질문 해석 단계에서 `ReadError`로 실패했다. SSM 진단에서 모든 컨테이너 정상·재시작 0회를 확인했으며, 모델 응답 읽기 오류는 분석 전체에서 한 번만 재시도하도록 보완했다. 재시도도 18회 호출 상한과 300초 제한 안에 포함한다.

운영 장애는 GitHub `diagnose-portfolio` 수동 워크플로로 확인한다. 기존 배포 역할과 지정 호스트의 SSM 권한만 사용하며 임의 명령 입력은 받지 않는다. 컨테이너 상태·재시작 횟수, 워커의 작업 ID와 오류 코드, 최근 10개 작업의 단계·상태만 출력한다. 포트폴리오 입력, 모델 응답, 이메일, 환경변수와 비밀값은 출력하지 않는다.

배포는 API와 분석 워커의 healthcheck를 모두 기다린다. 워커는 DB 초기화 후 작업 루프의 상태 파일을 갱신하고 종료 시 지운다. 상태 유효 시간은 660초로, 분석의 600초 제한보다 길게 잡아 정상 분석 중에는 실패로 판정하지 않는다.

배포는 새 이미지를 먼저 빌드하고 기존 워커에 종료 신호를 보낸 뒤 최대 630초 기다린다. 워커는 SIGTERM/SIGINT를 받으면 새 작업을 가져오지 않고 진행 중인 작업의 결과 저장을 마친 뒤 종료한다. 기존 API·DB·전망 서비스는 이 동안 유지하고, 워커가 멈춘 뒤 전체 서비스를 갱신한다. 강제 종료·호스트 장애의 중간 실행 재개는 제공하지 않으며 실행 중 최초 분석과 후속 질문 모두 시작 후 11분에 만료 처리한다. 대기열의 30분 만료는 유지한다. 이 종료 처리를 지원하지 않는 이전 워커에 대한 최초 교체에는 진행 작업 보호가 소급 적용되지 않는다.

로컬 검증: PostgreSQL 포함 백엔드 27개, 모바일·데스크톱 6개 통과. 테스트 생략 없음. Starlette TestClient 사용 중단 예정 경고 1건. React 빌드와 Terraform validate 통과. 실제 배포용 컨테이너의 기동·API 상태·7개 입력 테이블·ETF 카탈로그와 워커 자격증명 분리를 확인했다. 변경 전 실제 서비스에서 분석·추가 질문 1회·2회째 차단·이메일 접수까지 약 75초 분석으로 확인했다. 이번 변경의 GitHub CI·실제 배포 검증은 PR과 워크플로 결과로 기록한다.

전용 t3.small 한 대에 PostgreSQL을 함께 운영한다. DB는 전용 볼륨과 종료 시 보존되는 암호화 EBS에 저장하지만 고가용성과 자동 DB 백업은 아직 없다. 이메일은 신청만 저장하고 “1일 이내” 안내를 표시한다. 자동 발송은 구현하지 않았으므로 운영자가 별도 발송해야 한다.

롤백은 GitHub에서 이전 서비스 커밋을 되돌려 dev에 반영하는 방식이다. 인프라 변경도 Terraform PR로 되돌린다. DB 볼륨을 삭제하는 `docker compose down -v`를 사용하지 않는다.

계좌 입력 근거: [국세청 연금계좌 세액공제](https://g.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7875&mi=6596), [ISA 법령](https://law.go.kr/LSW/lsLinkCommonInfo.do?lsJoLnkSeq=1027994797), [고용노동부 퇴직연금 위험자산 한도와 예외](https://www.moel.go.kr/news/enews/report/enewsView.do?news_seq=13711). 입력 정보만으로 자격·절세액을 확정하지 않으며 에이전트가 분석 기준일의 시행 규정을 다시 확인한다.
