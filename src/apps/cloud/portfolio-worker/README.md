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
- `POST /api/portfolio/admin/login`, `/logout`: 일반 방문자와 분리된 관리자 인증.
- `GET /api/portfolio/admin/overview`: 최근 2분 활동, 한국 날짜 기준 방문·분석 집계.
- `GET /api/portfolio/admin/records?kind=analyses|drafts|reports&days=7&page=1`: 관리자 목록, 페이지당 50건. 분석은 `state` 필터 지원.
- `GET /api/portfolio/admin/records/{kind}/{id}`: 관리자 전용 입력·결과 상세.

## 관리자 화면

`/portfolio/admin.html`에서 별도 계정 `admin`으로 로그인한다. 운영 비밀번호는 `edge/portfolio/admin` Secrets Manager에 있으며 Terraform은 비밀 메타데이터와 최소 IAM 권한을 소유한다. dev GitHub 배포가 최초 값이 없을 때만 32바이트 난수 비밀번호를 만들며 기존 값은 바꾸지 않는다. 일반 배포 출력이나 Terraform state에 비밀번호를 넣지 않는다.

API에만 `PORTFOLIO_ADMIN_PASSWORD_SHA256`를 전달한다. 로그인 세션은 8시간이며 로그아웃·비밀번호 해시 변경 시 무효다. 로그인 시도는 서비스 전체에서 15분당 20회로 제한한다. 로컬 검증은 테스트 전용 비밀번호의 SHA-256을 해당 API 환경변수에 넣는다. 운영 비밀번호를 로컬 개발에 재사용하지 않는다.

조회는 10초마다 갱신한다. 방문자 기록은 90일 유지하며 같은 쿠키의 여러 탭은 한 방문자다. 실제 사람 수가 아니며 이전 방문 기록은 소급 생성하지 않는다. 설계와 검증 기준은 [관리자 설계](../../../../docs/design/portfolio-admin.md)에 있다.

AWS 운영 구성과 배포·롤백 절차는 [배포 기록](../../../../docs/design/portfolio-deployment.md)을 따른다. 전망 조회는 ETF 한 종목과 명시적인 목적이 필수다. 이메일은 1일 이내 발송을 안내하지만 자동 발송은 구현하지 않는다.
