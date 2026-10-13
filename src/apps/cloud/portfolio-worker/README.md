# Portfolio worker

독립 Python 런타임. 접수 API와 워커는 별도 프로세스이며 전용 PostgreSQL을 공유한다.
설계와 근거는 [설계 문서](../../../../docs/design/portfolio-worker.md)에 있다.

## 로컬 실행

프로젝트 루트에서 `PORTFOLIO_DB_PASSWORD`에 별도 DB 암호, `DEEPSEEK_API_KEY`에 모델 키를 설정한다.
`PORTFOLIO_DATABASE_URL`은 `postgresql://portfolio:<퍼센트 인코딩한 암호>@db/portfolio`로 설정한다. DB 암호 원문과 접속 URL을 분리해 특수문자가 있어도 같은 암호로 접속한다.
비밀값을 커밋하거나 터미널 출력에 포함하지 않는다.

```powershell
docker compose -f docker-compose.portfolio.yml up -d --build
cd src
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
- `GET /api/portfolio/admin/overview`: 최근 2분 활동, 한국 날짜 기준 방문·분석 집계.
- `GET /api/portfolio/admin/records?kind=analyses|drafts|reports&days=7&page=1`: 관리자 목록, 페이지당 50건. 분석은 `state` 필터 지원.
- `GET /api/portfolio/admin/records/{kind}/{id}`: 관리자 전용 입력·결과 상세.

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

조회는 10초마다 갱신하며 실패 시 화면의 입력값을 지운다. 방문 기록은 90일 유지하고 같은 쿠키의 여러 탭은 한 방문자다. 실제 사람 수가 아니며 과거 방문 기록은 소급 생성하지 않는다. [설계·접속 경계](../../../../docs/design/portfolio-admin.md)를 참고한다.

AWS 운영 구성과 배포·롤백 절차는 [배포 기록](../../../../docs/design/portfolio-deployment.md)을 따른다. 전망 조회는 ETF 한 종목과 명시적인 목적이 필수다. 이메일은 1일 이내 발송을 안내하지만 자동 발송은 구현하지 않는다.
