# Portfolio worker

독립 Python 런타임. 접수 API와 워커는 별도 프로세스이며 전용 PostgreSQL을 공유한다.
설계와 근거는 [설계 문서](../../../../docs/design/portfolio-worker.md)에 있다.

## 로컬 실행

프로젝트 루트에서 `PORTFOLIO_DB_PASSWORD`에 별도 DB 암호, `DEEPSEEK_API_KEY`에 모델 키를 설정한다.
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
pnpm --filter portfolio-ui test
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

AWS 운영 구성과 배포·롤백 절차는 [배포 기록](../../../../docs/design/portfolio-deployment.md)을 따른다. 전망 조회는 ETF 한 종목과 명시적인 목적이 필수다. 이메일은 1일 이내 발송을 안내하지만 자동 발송은 구현하지 않는다.
