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

Web analytics의 Top paths에서 `/portfolio/`로 시작하는 경로를 선택하면 랜딩(`/`)과 분리한다. 화면 이름을 `/portfolio/input/holdings`, `/portfolio/dashboard/overview` 등의 논리 경로로 보내며 www도 `etforca.com`으로 합쳐 집계한다. 쿼리·해시는 보내지 않는다. 경로가 안 보이면 프로젝트·조회 기간·호스트 필터와 실제 `$pageview` 적재를 확인한다. 브라우저 전송 테스트나 수집 API의 성공 응답만으로 집계 완료를 판정하지 않는다.

PostHog **Product analytics → New insight → Trends**에서 아래 이벤트와 속성을 사용한다. 화면 이벤트는 `screen`, 조작 이벤트는 `portfolio_action`과 고정된 `action` 값으로 구분한다.

| 확인할 내용 | 이벤트·집계 |
|---|---|
| 화면별 실제 방문 수 | `$pageview`의 Total count, Breakdown `screen` |
| 화면별 총 체류 시간 | `portfolio_screen_engagement`의 Sum `visible_seconds`, Breakdown `screen` |
| 방문 1회당 평균 체류 | 위 체류 합계 ÷ 같은 화면의 `$pageview` 수. 15초 단위 이벤트의 평균을 방문당 평균으로 사용하지 않는다 |
| 스크롤이 멈춘 구간 | `portfolio_scroll_pause`의 Total count 또는 Sum `pause_seconds`, Breakdown `section`, 필요시 `screen = result` 필터 |
| 분석 만족도 | `portfolio_feedback`, Filter `action = submitted`, Total count, Breakdown `rating` (`good` 좋아요 / `okay` 그냥 그래요 / `needs_improvement` 개선이 필요해요) |
| 정체 위치·도달 깊이 | 정체 이벤트의 `scroll_y`(뷰포트 상단 px), `scroll_depth_pct`(뷰포트 하단 기준 %), 체류 이벤트의 `max_scroll_depth_pct` |
| 보유 편집·저장·백테스트 흐름 | `portfolio_action`, Breakdown `action`: `holding_add_opened`, `holding_edit_opened`, `holdings_saved`, `holdings_save_failed`, `portfolio_saved`, `newsletter_requested`, `backtest_requested`, `backtest_completed`, `backtest_failed`, `instrument_search_empty`(입력 종목 검색이 0건으로 바뀐 횟수, 검색어는 수집하지 않음) |
| 입력 시작 퍼널 | `portfolio_action`: `input_search_opened`(입력 화면을 열 때마다 방문자가 처음 검색칸을 누른 1회, 코드가 옮긴 포커스는 세지 않음) → `input_holding_added`(종목 추가마다) → `screen = input_funding`. 일괄 입력은 `bulk_entry_opened`·`bulk_entry_added`·`bulk_entry_failed`. 모바일에서 접힌 요청사항을 방문자가 펼치면 `request_box_opened`(내용은 보내지 않음). 보유 입력 없이 '종목부터 추천받기'로 시작하면 `recommend_started`. `instrument_search_empty`는 검색 한 번(열고 고르거나 닫을 때까지)에 한 번만 센다. 검색어·금액은 보내지 않는다. 방문자 분류(시험·고객) 전에 일어난 행동은 분류 뒤 한 번 보낸다. 보유 저장(`holdings_saved`)이 있는 방문은 관리 목적이라 신규 분석 이탈에서 뺀다 |
| 화면 조작 재생 | Session replay에서 `/portfolio/` 방문을 선택. 화면 본문·입력값과 스크롤·클릭·이동을 재생 |

시험 브라우저는 `*.posthog.com` 이름 풀이가 막혀 운영 분석에 닿지 못한다(`playwright.config.ts`의 `blockAnalyticsHost`). 막기 전인 2026-10-21~22에는 시험 실행이 운영으로 새어 'Chrome/Windows, 화면 폭 390·1280'·`/portfolio/result`·`/portfolio/loading` 방문자 약 410명이 섞였다. 이 기간을 집계할 때는 이 조합을 빼고 본다.

`screen`은 입력 5단계(`input_holdings`, `input_funding`, `input_horizon`, `input_interests`, `input_question`), `loading`, `queued`, `running`, `result`, `failed`, 대시보드의 `dashboard_overview`, `dashboard_dividends`, `dashboard_returns`, `dashboard_backtest`이다. `section`은 `answers`, `current_portfolio`, `recommendation`, `changes`, `account_plans`, `execution`, `performance`, `conclusion`, `outlook`, `insights`, `evaluation`, `scenarios`, `followup`, `feedback`이며 그 밖의 위치는 화면 이름으로 집계한다.

만족도 팝업은 완료된 고객 보고서에서 과거 성과 다음 핵심 평가 구간에 도달하면 표시한다. 이모지 3지 선택이며 닫기·Escape를 지원한다. 같은 분석의 응답·닫기는 브라우저 로컬 저장소에 표시해 다시 묻지 않는다(저장소 차단 시 현재 페이지에서만 유지). `portfolio_feedback`의 `action`은 `shown`, `submitted`, `dismissed`이고, `rating`은 응답에만 들어간다. 관리자·중단된 보고서에는 팝업이 없다. SDK Surveys 대신 앱의 고정 선택 UI와 기존 이벤트 수집 경로를 사용한다.

팝업 위쪽에는 보유 ETF 전체의 데일리 뉴스레터 이메일 신청을, 아래쪽에는 평가 3지 선택을 둔다. 동의 안내와 함께 이메일 신청 버튼을 제공하며 별도 동의 체크박스는 두지 않는다. 자동 팝업은 읽던 초점을 빼앗지 않고, 직접 열었을 때만 초점을 이동한다. 고객이 추가 질문 입력을 시작하면 팝업을 닫고, 해당 화면에서는 다시 자동 표시하지 않는다. 팝업을 닫거나 평가한 뒤에도 ‘보유 종목 뉴스레터 받기’ 버튼으로 신청 화면을 다시 열 수 있으며 평가를 다시 요구하지 않는다. 서버의 발송 준비 상태에 따라 확인 메일 발송 안내와 준비 대기 안내를 구분한다.

이메일 확인 링크의 `#newsletter-confirm=...`는 새 문서 진입과 같은 탭의 fragment 이동 모두에서 즉시 주소에서 제거하고 별도 확인 화면을 표시한다. 고객이 버튼을 누를 때만 확인 API를 호출하며 이 화면에서는 PostHog·방문 기록을 시작하지 않는다. 이미 열려 있던 입력·결과 화면에서 전환하면 이전 화면의 방문 종료 기록은 보존하며, 확인 화면의 새 방문이나 클릭은 기록하지 않는다. 이메일·확인 토큰은 분석 이벤트에 포함하지 않는다. 이전 수신 거부를 해제해야 하는 고객에게는 기존 메일의 수신 설정에서 직접 허용하도록 안내한다.

체류는 탭이 보이는 시간이며 실제 독서·집중 시간을 뜻하지 않는다. 숨긴 화면은 방문으로도 세지 않는다. 스크롤·레이아웃 변화 없이 2초 이상 머문 구간을 다음 이동·숨김·종료 때 기록한다. 화면 가운데의 구간을 기준으로 하며, 펼치기 등으로 레이아웃이 바뀌면 위치를 갱신한다. 앱 전환·페이지 이탈의 마지막 이벤트는 즉시 Beacon으로 넘기지만 네트워크 차단이나 브라우저 강제 종료 시 전달을 보장하지 않는다.

사용자 요청에 따라 익명 재방문 연결용 `localStorage+cookie` 저장과 세션 리플레이를 사용한다. `ph_orca_portfolio`는 Secure·호스트 전용·365일 쿠키이며 고객 계정 ID와 연결하지 않는다. 서비스 인증 쿠키를 수집하거나 복사하지 않는다. 페이지 하단 ‘이용 통계 설정’에서 끄면 수집·녹화를 중지하고 분석 쿠키를 제거한다. 다시 켜면 현재 화면부터 수집을 재개하며 Do Not Track 설정을 존중한다.

리플레이는 카메라 영상이 아니라 DOM과 조작 기록이다. 사용자 요청에 따라 본문·입력값·이미지·차트의 전체 마스킹을 해제한다. 보유금액·종목·수량·이메일이 녹화에 포함될 수 있음을 이용 통계 설정에 안내한다. 명시적인 `.ph-no-capture` 영역과 SDK의 비밀번호 보호는 유지한다. 인증 쿠키·네트워크 본문·헤더·콘솔 로그는 수집하지 않으며 녹화 URL은 포폴 경로로 제한한다. 자동 클릭 분석·SDK 설문·기능 플래그·히트맵은 사용하지 않는다. 직접 보내는 분석 이벤트의 속성 허용 목록과 테스트 미리보기 수집 차단은 유지한다. 변경 후 새 녹화부터 적용되며 기존 마스킹 기록은 복원되지 않는다.

SDK 원격 설정은 리플레이 스크립트 로드에 필요하므로 전체 설정 조회를 차단하지 않는다. 개인정보 마스킹과 저장 설정은 [PostHog 리플레이 지침](https://posthog.com/docs/session-replay/privacy)과 [브라우저 저장 지침](https://posthog.com/docs/libraries/js/persistence)을 따른다. 새 AWS 자원은 추가하지 않으며 PostHog 사용량은 해당 프로젝트 요금에 반영된다.

## 권한

- GitHub는 OIDC 임시 자격증명을 쓴다. 배포 역할에는 지정 버킷·SSM 대상만 추가하고 AWS 관리 키를 저장하지 않는다. Terraform apply 역할은 기존 저장소의 dev 전용 신뢰 정책을 따른다.
- 호스트는 인스턴스 프로파일로 런타임 비밀과 배포 파일을 읽는다. SSH 포트·키 페어는 없다. 443은 CloudFront 원본 prefix list, 80은 ACME 인증서 발급용이다. IMDSv2 필수·hop limit 1을 유지한다.
- Exa 키는 기존 Secrets Manager `edge/portfolio/runtime` JSON의 `EXA_API_KEY` 필드로 관리한다. 배포 전에 값을 등록해야 하며, 배포 스크립트가 읽어 권한 `0600`의 `runtime.env`에 저장하고 워커에만 주입한다. 키 교체 후에는 `deploy-portfolio`를 재실행한다. 시크릿 값은 Git·Terraform state·배포 로그에 기록하지 않는다.
- 뉴스레터 모델 키는 같은 시크릿의 `DEEPSEEK_NEWSLESTTER_API_KEY` 필드로 관리한다(변수명 철자 그대로 사용). 배포 전에 등록해야 하며 누락·빈 값이면 배포를 거부한다. `runtime.env`를 거쳐 워커에만 주입하고, 뉴스레터 에이전트는 이 키만 사용한다. 포트폴리오 분석·후속 질문은 기존 `DEEPSEEK_API_KEY`를 사용한다.
- 관리자 대시보드는 `edge-portfolio-admin-access` 정책과 고정 포트 문서 `edge-portfolio-admin-tunnel`로만 접속한다. 기존 `edge-portfolio-ssm-operator` 셸 정책은 관리자 대시보드 사용자에게 부여하지 않는다. 사용자가 승인한 work 인증은 전용 edge-portfolio-admin-local 역할 발급에만 사용하며, SSM은 이 역할의 임시 권한으로 연결한다. 전용 역할과 정책 연결도 Terraform이 관리한다. SSM은 접속 수단이며 그 자체로 IAM 권한을 대체하지 않는다.
- 분석 워커는 비특권 사용자·읽기 전용 파일시스템·capability 제거로 실행한다. AWS 키, Docker 소켓, 전망 DB 비밀번호를 전달하지 않는다.
- 내부 전망 조회 서비스만 기존 읽기 전용 DB 계정을 가진다. 외부 포트가 없으며 임의 SQL·목록·배열 입력 없이 ETF 코드 하나와 목적을 받는다. 모델 도구는 확인된 코드만 허용하고 한 분석 최대 3개·중복 조회 금지·기준시각과 14일 신선도를 검사한다. DB 계정 자체는 기존 결과 API의 읽기 권한을 공유하며 단건 제한은 조회 서비스 계약에서 강제한다.

## 입력 저장

각 단계의 다음 버튼과 계좌 팝업 적용 시 저장한다. `portfolio_input_drafts`가 익명 세션 소유권을 보관하고 `portfolio_input_portfolio`, `funding`, `horizon`, `interests`, `notes`, `tax`가 각각 JSONB 입력을 저장한다. 위 이름의 후반부에도 모두 `portfolio_input_` 접두사가 붙는다. 하나의 트랜잭션으로 갱신하며 다른 세션의 초안은 읽거나 덮어쓸 수 없다. 세션별 최근 초안 10개만 보존하고 오래된 초안은 새 입력 시 정리한다. 최종 작업의 `input`에는 당시 입력과 초안 ID를 별도로 고정하므로 초안 정리가 기존 보고서에 영향을 주지 않는다. 서버에 매 키 입력을 전송하지 않는다. 브라우저에서는 같은 탭의 sessionStorage에 단계·입력·단위별 값을 보관해 새로고침과 화면 왕복 뒤 복원한다. 미리보기 키는 고객 키와 분리하며, 완료한 분석과 연결된 초안만 자동 정리한다. 저장소가 제한되면 안내하고 메모리의 재시도 키로 분석 접수를 계속한다. 기기 간 복원이나 영구 백업은 제공하지 않는다. 자세한 경계는 [UX 점검 기록](portfolio-ux-audit.md)을 따른다.

ETF 목록은 KRX·Nasdaq Trader의 2026-10-12 수집 스냅샷이다. 국내 1,172개, 미국 5,771개이며 ETF와 원화 현금만 입력 가능하다. `scripts/refresh_catalog.py`로 공개 종목 메타데이터를 갱신하고 리뷰·배포한다. 전망을 일괄 조회하는 기능은 아니다. 자동 목록 갱신 스케줄은 없다.

## 검증과 운영 한계

### 뉴스레터 예약 발송

기존 호스트에 `portfolio-newsletter.timer`를 설치해 Asia/Seoul 기준 매시 00·05·10분 등 5분 경계마다 SES 발송 준비 상태·이메일 확인 대기·일일 콘텐츠 준비를 확인한다. 매일 KST 08:00 이후 발행일별 종목 작업을 준비하고, 오후 15:30부터 준비된 일일 메일을 발송한다. 콘텐츠 준비나 SES 재시도가 늦어지면 후속 주기에 보낸다. 이메일 확인 메일에는 이 시간 제한을 적용하지 않는다. 별도 EC2·DB·Lambda·AWS 스케줄러는 추가하지 않는다. 분석은 기존 워커, 발송은 `newsletter` Compose profile의 128MiB 일회성 프로세스가 맡는다.

발송 프로세스만 host network로 인스턴스 역할을 사용한다. 분석 워커의 `AWS_EC2_METADATA_DISABLED=true`와 IMDSv2 hop limit 1은 유지한다. PostgreSQL은 추가로 호스트 루프백 `127.0.0.1:15432`에만 바인딩하며 `runtime_env.py`가 비밀번호를 인코딩해 `PORTFOLIO_NEWSLETTER_DATABASE_URL`을 만든다. 외부 DB 포트는 열지 않는다. 메일러에는 DeepSeek·Exa 키를 환경변수로 전달하지 않는다. 이는 IAM 격리를 뜻하지 않는다. 메일러는 모델·외부 도구를 실행하지 않는 신뢰된 코드이며 기존 호스트 역할을 공유하므로, 그 역할의 배포용 런타임 시크릿 읽기 권한도 갖는다.

SES 도메인 인증·발송 허용·운영 액세스가 모두 준비돼야 메일을 보낸다. 최근 준비 상태는 `portfolio_newsletter_runtime`에 보관하고 API는 10분 이상 지난 상태를 준비 완료로 표시하지 않는다. sandbox 해제 전 신청은 대기하며 팝업에서 준비 후 확인 메일을 보낸다고 안내한다. 확인 링크는 발송 후 48시간 유효하다.

SES 관리 수신 거부를 사용하는 일일 메일은 `ListManagementOptions`를 포함하므로, `ses:SendEmail` 권한에 발신 identity·configuration set뿐 아니라 해당 contact list ARN도 필요하다. 실제 호스트 역할로 AWS mailbox simulator에 발송해 이 권한 경로를 검증한다. 운영자 자격증명으로만 성공한 테스트는 호스트 발송 권한의 검증이 아니다.

발송 장애는 `journalctl -u portfolio-newsletter.service`와 전용 DB의 발송 상태로 확인한다. `sending` 상태로 프로세스가 중단됐거나 `uncertain`이면 SES 수락 여부를 확인하기 전 재발송하지 않는다. 기존 배포 절차가 메일 타이머를 멈추고 진행 중인 발송을 최대 330초 기다린 뒤 분석 워커를 비운다. 배포 실패 시 기존에 켜져 있던 타이머를 복구하고, 성공 시 새 릴리스·DB 준비가 끝난 뒤 활성화한다. SSM 실행 제한은 1,800초, GitHub 배포 제한은 45분으로 두 대기 구간과 빌드·검증 시간을 포함한다. 실제 수신함의 Gmail·Outlook 렌더링은 로컬 브라우저 미리보기와 별도로 검증해야 한다.

절세 정보 팝업은 카드형 단일 선택과 마지막 IRP·ISA·연금저축 체크박스를 사용한다. `tax_profile.recommended_accounts`는 전략 검토 대상으로 저장하며 실제 보유 계좌와 구분한다. 미입력 항목은 선택하지 않은 상태이며 ‘확인 필요’를 직접 고를 수 있다. 창을 취소하면 적용 전 정보를 유지하고 분석을 계속할 수 있다. 금액·비율을 오가도 각 방식의 입력을 별도로 보관하며, 다른 종목의 금액이 비어 있어도 이미 입력한 현금 금액은 표시한다.

2026-10-12 GitHub 배포 후 실제 분석·추가 질문·2회째 차단·이메일 접수 검증은 66.91초 분석으로 통과했다. 목적을 지정한 069500 단건 전망 조회도 `found`였다. 첫 실행은 질문 해석 단계에서 `ReadError`로 실패했다. SSM 진단에서 모든 컨테이너 정상·재시작 0회를 확인했으며, 모델 응답 읽기 오류는 분석 전체에서 한 번만 재시도하도록 보완했다. 재시도도 18회 호출 상한과 300초 제한 안에 포함한다.

운영 장애는 GitHub `diagnose-portfolio` 수동 워크플로로 확인한다. 기존 배포 역할과 지정 호스트의 SSM 권한만 사용하며 임의 명령 입력은 받지 않는다. 컨테이너 상태·재시작 횟수, 워커의 작업 ID와 오류 코드, 최근 10개 작업의 단계·상태만 출력한다. 포트폴리오 입력, 모델 응답, 이메일, 환경변수와 비밀값은 출력하지 않는다.

배포는 API와 분석 워커의 healthcheck를 모두 기다린다. 워커는 DB 초기화 후 작업 루프의 상태 파일을 갱신하고 종료 시 지운다. 상태 유효 시간은 660초로, 분석의 600초 제한보다 길게 잡아 정상 분석 중에는 실패로 판정하지 않는다.

배포는 새 이미지를 먼저 빌드하고 기존 워커에 종료 신호를 보낸 뒤 최대 630초 기다린다. 워커는 SIGTERM/SIGINT를 받으면 새 작업을 가져오지 않고 진행 중인 작업의 결과 저장을 마친 뒤 종료한다. 기존 API·DB·전망 서비스는 이 동안 유지하고, 워커가 멈춘 뒤 전체 서비스를 갱신한다. 강제 종료·호스트 장애의 중간 실행 재개는 제공하지 않으며 실행 중 최초 분석과 후속 질문 모두 시작 후 11분에 만료 처리한다. 대기열의 30분 만료는 유지한다. 이 종료 처리를 지원하지 않는 이전 워커에 대한 최초 교체에는 진행 작업 보호가 소급 적용되지 않는다.

로컬 검증: PostgreSQL 포함 백엔드 27개, 모바일·데스크톱 6개 통과. 테스트 생략 없음. Starlette TestClient 사용 중단 예정 경고 1건. React 빌드와 Terraform validate 통과. 실제 배포용 컨테이너의 기동·API 상태·7개 입력 테이블·ETF 카탈로그와 워커 자격증명 분리를 확인했다. 변경 전 실제 서비스에서 분석·추가 질문 1회·2회째 차단·이메일 접수까지 약 75초 분석으로 확인했다. 이번 변경의 GitHub CI·실제 배포 검증은 PR과 워크플로 결과로 기록한다.

전용 t3.small 한 대에 PostgreSQL을 함께 운영한다. DB는 전용 볼륨과 종료 시 보존되는 암호화 EBS에 저장하지만 고가용성과 자동 DB 백업은 아직 없다. 분석 화면의 “내 ETF 핵심 소식 이메일로 받기”와 피드백 팝업은 같은 구독 폼을 사용하며 `/analyses/{id}/newsletter`에 신청한다. 이메일 확인 뒤 기존 뉴스레터 자동 발송 경로로 연결하고, 기존 수동 리포트 접수 경로는 이 화면에서 사용하지 않는다. 단독 신청 화면의 미리보기는 실제 메일 렌더러로 만든 설명용 예시를 캡처한 이미지이며 Vite 정적 자산으로 함께 배포한다. 인용문은 [버핏의 2013년 주주서한](https://www.berkshirehathaway.com/2013ar/2013ar.pdf#page=20)의 경기장·전광판 비유를 번역한 것으로 출처와 번역 표시를 붙인다.

롤백은 GitHub에서 이전 서비스 커밋을 되돌려 dev에 반영하는 방식이다. 인프라 변경도 Terraform PR로 되돌린다. DB 볼륨을 삭제하는 `docker compose down -v`를 사용하지 않는다.

계좌 입력 근거: [국세청 연금계좌 세액공제](https://g.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7875&mi=6596), [ISA 법령](https://law.go.kr/LSW/lsLinkCommonInfo.do?lsJoLnkSeq=1027994797), [고용노동부 퇴직연금 위험자산 한도와 예외](https://www.moel.go.kr/news/enews/report/enewsView.do?news_seq=13711). 입력 정보만으로 자격·절세액을 확정하지 않으며 에이전트가 분석 기준일의 시행 규정을 다시 확인한다.
