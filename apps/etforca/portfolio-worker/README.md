# Portfolio worker

독립 Python 런타임. 접수 API와 워커는 별도 프로세스이며 전용 PostgreSQL을 공유한다.
설계와 근거는 [설계 문서](../../../docs/design/portfolio-worker.md)에 있다.

## 로컬 실행

프로젝트 루트에서 `PORTFOLIO_DB_PASSWORD`에 별도 DB 암호, `DEEPSEEK_API_KEY`에 포트폴리오 분석 모델 키, `DEEPSEEK_NEWSLESTTER_API_KEY`에 뉴스레터 전용 모델 키, `EXA_API_KEY`에 Exa 키를 설정한다. 뉴스레터는 분석 키로 대체하지 않는다.
`PORTFOLIO_DATABASE_URL`은 `postgresql://portfolio:<퍼센트 인코딩한 암호>@db/portfolio`로 설정한다. DB 암호 원문과 접속 URL을 분리해 특수문자가 있어도 같은 암호로 접속한다.
비밀값을 커밋하거나 터미널 출력에 포함하지 않는다.

```powershell
docker compose -f docker-compose.portfolio.yml up -d --build
pnpm install --filter portfolio-ui
pnpm --filter portfolio-ui dev
```

`http://localhost:5180/portfolio/`에서 사용한다. API는 localhost:8092, PostgreSQL은 localhost:55439다.
기존 Edge 서비스의 DB·포트를 사용하지 않는다. 개발 화면 origin 기본값은 `http://localhost:5180`이다.

HTTPS 배포 시 `PORTFOLIO_ORIGIN`과 `PORTFOLIO_COOKIE_SECURE=1`을 함께 지정한다.

Python만 실행할 때는 이 폴더에서 `uv sync` 후 아래 명령을 각각 실행한다.
`PORTFOLIO_DATABASE_URL`에는 전용 DB 접속 문자열을 설정한다.

```powershell
uv run uvicorn edge_portfolio.api:app --host 127.0.0.1 --port 8092
uv run python -m edge_portfolio.worker
```

## 포트폴리오 대시보드와 화면 테스트

`/portfolio/?view=dashboard`는 같은 브라우저 세션의 보유 자산·배당·월 기록을 관리한다.
분석 결과의 저장 버튼은 입력했던 보유 종목을 가져오며 추천안을 실제 매매로 반영하지 않는다.
`portfolio_workbooks`는 소유자별 문서와 버전을 전용 DB에 저장한다. `GET/POST /api/portfolio/workbook`은 기존 세션 소유권과 POST origin 검사를 사용하며 오래된 저장 버전은 409로 거절한다.
`GET /api/portfolio/analyses/{job_id}/workbook`은 소유한 분석의 입력을 변환한다.
로그인·기기 간 동기화는 제공하지 않는다. 세션 쿠키가 없어지면 기존 저장본에 접근할 수 없으므로 브라우저 데이터를 지우지 않는다.
가격과 환율은 수동 기록이며 실시간 시세가 아니다. 입출금이 있는 월의 수익률은 증권사 값을 입력하고, 누락 월을 연결해 누적 수익률을 꾸미지 않는다.

배포 사이트에서 `/portfolio/?preview=result`로 분석 없이 결과·저장 팝업을 확인한다.
상단 선택기에서 `input`, `queued`, `running`, `result`, `partial`, `failed`, `dashboard`, `empty`, `newsletter` 화면을 전환한다.
테스트 응답은 실제 컴포넌트를 사용하되 분석·메일·고객 API 및 PostHog/presence를 호출하지 않는다.
테스트 저장은 `edge-preview-*` 로컬 영역만 사용한다. 버전은 빌드 커밋이며 초기화 버튼으로 테스트 기록을 지운다.
이 모드는 UI 검증용이며 실제 모델 응답 품질이나 메일 도착을 검증하지 않는다.

## 검증

```powershell
uv run pytest -q
# 실제 모델 과금과 공개 Exa 호출이 발생하는 명시적 검증
uv run python scripts/smoke.py
```

DB 통합 테스트는 `PORTFOLIO_DATABASE_URL`이 없으면 건너뛴다. 완료 검증은 전용 DB를 설정하고 전부 실행한다.
테스트마다 고유 스키마를 만들고 지우며 운영 테이블을 비우지 않는다.
smoke는 합성 보유 입력을 쓰며 `artifacts/`에 결과와 응답·근거 관측을 저장한다. 모델 내부 사고와 키는 저장하지 않는다.

React 빌드와 모바일·데스크톱 화면 검증은 `src`에서 실행한다.

```powershell
pnpm --filter portfolio-ui build
pnpm --filter portfolio-ui exec playwright install chromium
pnpm --filter portfolio-ui test:e2e
```

실제 API·워커·개발 화면을 실행한 뒤 UI 폴더에서 `node scripts/live-browser.mjs`를 실행하면
합성 포트폴리오 제출, 새로고침 복원, 실제 분석·추가 질문, 두 번째 질문 차단, 이메일 접수를 검증한다.
이 명령은 실제 모델 비용이 발생하며 결과는 무시되는 `test-results/` 폴더에 저장한다.

## API

- `GET /api/portfolio/session`: HttpOnly 방문자 쿠키 설정.
- `GET /api/portfolio/catalog`: 국내·미국 ETF 종목 메타데이터 스냅샷.
- `POST /api/portfolio/drafts/{id}`: 단계별 입력을 소유권이 있는 초안의 분리 테이블에 저장.
- `POST /api/portfolio/analyses`: `Idempotency-Key` UUID, holdings/question/horizon → 202 작업.
- `GET /api/portfolio/analyses/{id}`: 소유한 작업의 queued/running/succeeded/failed 상태.
- `POST /api/portfolio/analyses/{id}/chat`: question과 멱등 키. 무료 1회 초과는 409 CHAT_LIMIT.
- `POST /api/portfolio/analyses/{id}/report-request`: email. 접수만 저장, 발송은 미구현.
- `POST /api/portfolio/presence`: 방문자 쿠키로 현재 활동 기록. 화면이 보일 때 30초 주기.
- 아래 관리자 GET API는 SSM 전용 프로세스에만 존재하며 공개 API에서는 404다.
- `GET /api/portfolio/admin/overview`: 최근 2분 활동, 한국 날짜 기준 방문·분석 집계. `today_unique_emails`·`total_unique_emails`는 공백 제거·소문자 기준 오늘/누적 이메일 고유 수, `total_report_requests`는 누적 신청 레코드 수다. 사람 수나 반복 클릭 횟수가 아니다.
- `GET /api/portfolio/admin/records?kind=analyses|drafts|reports&days=7&page=1`: 관리자 목록, 페이지당 50건. 분석은 `state` 필터 지원.
- `GET /api/portfolio/admin/records/{kind}/{id}`: 관리자 전용 입력·결과 상세.
- 관리자 `/overview`는 고객 기준으로 고정한다. `/records`의 `scope=all|customers|tests` 기본값은 customers이며 테스트 분석 탭은 tests를 사용한다. 방문자·초안은 분석이 모두 명시적 테스트인 방문자를 제외하며 고객 분석이 있는 방문자는 보존한다. `admin_store.delete_test_reports()`는 운영자 전용으로 테스트 분석에 연결된 이메일 신청만 삭제한다.
- `GET /api/portfolio/admin/verification-runs`: 검증 버전 목록. `/{run_id}`는 20개 유형과 실행 이력, `/{run_id}/{case_id}/{attempt}`는 해당 회차의 실제 입력·답변이다.

배포 시 실제 Git 커밋과 20개 유형을 등록하며, 아직 실행하지 않은 유형은 미실행으로 표시한다. 모델 호출을 자동 시작하지 않는다. 해당 배포 이미지의 워커 환경에서 `python -m edge_portfolio.verification --version <40자리 배포 커밋>`으로 실제 모델 검증을 시작한다. 이미지의 버전 메타데이터와 다른 커밋은 거부한다. 고객 작업은 만들지 않으며 모델·가격·검색 공급자는 실제 연결을 사용한다. 같은 버전을 다시 실행하면 통과한 유형은 유지하고 실패·중단 유형은 새 회차에 저장한다. 외부 모델 비용이 발생하며 자동검사 통과 후에도 설명 품질은 관리자에서 직접 검토해야 한다.

## 관리자 화면

SSM 터널을 연 뒤 `http://localhost:18080/portfolio/admin.html`에서 확인한다. 공개 도메인의 관리자 HTML·API는 차단하며 별도 공용 비밀번호는 사용하지 않는다. AWS IAM이 SSM 접속을 인증한다.

Windows에서 AWS CLI v2·Session Manager plugin을 설치하고, 기존 work 인증에서 전용 역할을 발급받는 프로필로 실행한다. `~/.aws/config`의 다른 프로필을 유지하고 아래 항목을 추가한다:

```ini
[profile edge-portfolio-admin]
role_arn = arn:aws:iam::393229433969:role/edge-portfolio-admin-local
source_profile = work
role_session_name = portfolio-admin
duration_seconds = 3600
region = ap-northeast-2
```

관리자 역할에는 `edge-portfolio-admin-access`만 연결되며, 기존 work 사용자만 역할을 발급받을 수 있다:

```powershell
./scripts/open-admin.ps1 -Profile edge-portfolio-admin
```

접속 후 브라우저가 자동으로 열리며 Ctrl+C로 터널을 종료한다. 스크립트는 전용 역할 설정과 실제 STS 신원을 모두 검사하며 IAM 사용자나 다른 역할의 직접 접속을 거부한다. SSO 계정 생성은 필요 없다. 정책에 광범위한 셸·임의 포트 권한을 더하지 않는다.

UI 빌드는 공개 `dist`와 내부 `dist-admin`으로 분리한다. 관리자 프로세스는 호스트 루프백 18080에만 게시한다. 로컬 실행은 `PORTFOLIO_DATABASE_URL` 및 `PORTFOLIO_ADMIN_UI_DIR`(빌드된 dist-admin 경로)을 지정한 뒤 `uv run uvicorn edge_portfolio.admin_app:app --host 127.0.0.1 --port 18080`을 사용한다.

조회는 10초마다 갱신하며 실패 시 화면의 입력값을 지운다. 방문 기록은 90일 유지하고 같은 쿠키의 여러 탭은 한 방문자다. 실제 사람 수가 아니며 과거 방문 기록은 소급 생성하지 않는다. [설계·접속 경계](../../../docs/design/portfolio-admin.md)를 참고한다.

AWS 운영 구성과 배포·롤백 절차는 [배포 기록](../../../docs/design/portfolio-deployment.md)을 따른다. 전망 조회는 ETF 한 종목과 명시적인 목적이 필수다. 이메일은 1일 이내 발송을 안내하지만 자동 발송은 구현하지 않는다.
