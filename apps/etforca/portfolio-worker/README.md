# Portfolio worker

독립 Python 런타임. 접수 API와 워커는 별도 프로세스이며 전용 PostgreSQL을 공유한다.
설계와 근거는 [설계 문서](../../../docs/design/portfolio-worker.md)에 있다.

배포 이미지의 `data/deployment.json` 버전별 스키마 초기화 완료를 DB에 기록한다. API·관리자·워커·검증 CLI는 같은 버전에서 DDL을 반복하지 않아 실행 중인 분석 테이블을 다시 잠그지 않는다. 초기화와 버전 기록은 한 트랜잭션이며 실패하면 재시도한다. 배포 메타데이터가 없는 로컬 개발에서는 기존 초기화를 수행한다.

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

`/portfolio/?view=dashboard`는 같은 브라우저 세션의 보유 자산·배당 전망·자동 일별 기록·조합 백테스트·최근 AI 분석을 관리한다.
메인 화면은 자산 요약, 자산 구성·월별 배당, 보유 목록 순서다. 차트와 진행 단계 모션은 `prefers-reduced-motion`을 따른다.
자산 구성은 투자 지역·테마 도넛으로 전환한다. 상장 시장과 투자 지역을 구분하며 확인되지 않은 이름은 미분류로 남긴다. 보유 목록은 비중순 표(모바일에서는 카드)로 주식수·평단가·최근 종가·평가액·손익률·배당률·예상 연배당금·관측 배당주기를 제공한다. 해외 가격은 현재 환율의 원화와 원통화를 함께 표시한다.
배당 분석은 향후 12개월 예상 현금을 재투자 없이 합산한다. 연간 배당 내역은 계좌·연도별 종목×12개월 표와 차트를 제공하며 예상과 저장된 실수령을 분리한다. 이력이 부족하거나 조회 범위 밖인 달은 0원과 구분한다. 주기는 완전한 최근 1년 지급 이력에서 계산하며 주 단위·불규칙 지급을 월배당으로 단정하지 않는다.
수익 기록은 자동 저장된 일별 평가손익을 보여준다. 과거 수동 월 기록과 계산 기준·출처 영역은 대시보드에서 숨기되 원본 기록과 분석 보고서의 출처는 보존한다.
고객·관리자는 [Orca UI](../../../docs/design/portfolio-design-system.md)의 공통 토큰과 조작부를 사용한다. `/portfolio/?preview=design-system`에서 실제 컴포넌트와 상태를 확인한다. `pnpm --filter portfolio-ui check:design`은 색상 원천·공통 조작부·대비를 검사하며 빌드에도 포함된다. 종목 삭제와 미저장 수정 취소는 공통 확인 팝업을 사용한다. 분석 결과에서 입력한 포트폴리오 저장은 추가 확인 없이 진행한다.
저장 팝업은 예시 데이터로 촬영한 `portfolio-ui/src/dashboard-preview.png`를 보여준다. 뉴스레터는 이메일과 신청 버튼으로 접수하며 기존 이메일 확인 절차를 유지한다.
뉴스레터는 이메일 확인 후 매일 15:30 KST부터 발송한다. 매일 최신 저장 포트폴리오를 우선 사용하고, 저장 문서가 없으면 신청한 분석의 입력 종목을 사용한다. 보유 종목이 없으면 TIGER 미국S&P500(360750)·KODEX 반도체(091160) 50:50 기본 구성을 사용하며 메일에도 기본 구성임을 표시한다. 기본 구성은 사용자의 실제 보유 문서에 저장하지 않는다.
분석 결과의 저장 버튼은 입력했던 보유 종목을 가져오며 추천안을 실제 매매로 반영하지 않는다.
`portfolio_workbooks`는 소유자별 문서와 버전을 전용 DB에 저장한다. `GET/POST /api/portfolio/workbook`은 기존 세션 소유권과 POST origin 검사를 사용하며 오래된 저장 버전은 409로 거절한다.
`GET /api/portfolio/analyses/{job_id}/workbook`은 소유한 분석의 입력을 변환한다.
`AI 포폴 분석`은 `GET /api/portfolio/analyses/latest`로 최근 요청한 분석을 복원하고 기존 `Analysis`/`ReportView`/`ProgressView` 화면을 재사용한다. 새 분석을 시작해도 서버의 이전 결과는 삭제하지 않는다.
분석 접수 캐시는 소유자·입력(초안 ID 제외)·한국 날짜·Python 코드/프롬프트 해시가 같을 때 성공 결과 또는 진행 중 작업을 재사용한다. 실패/기한 만료 작업과 다른 고객 결과는 재사용하지 않는다. `portfolio_analysis_requests`는 재사용 요청의 멱등 키를 보관한다. 캐시 적중은 새 작업/LLM 호출/체험 횟수를 소비하지 않으며 최근 요청 순서만 갱신한다. 내부 모델 검증 요청은 캐시를 사용하지 않는다.
로그인·기기 간 동기화는 제공하지 않는다. 세션 쿠키가 없어지면 기존 저장본에 접근할 수 없으므로 브라우저 데이터를 지우지 않는다.
종목 검색·계좌·수량·선택 평균매수가만 입력한다. 시세·환율·비중은 자동 계산한다. 비중으로 분석한 고객은 저장 전에 총 투자금을 입력한다. 금액으로 가져온 보유분은 첫 확보 시세로 추정 수량을 고정해 추적하며 화면에 금액 기준 추적임을 표시한다. 실제 수량을 입력하면 이를 대체한다.

`dashboard_quotes.py`는 종목별 공유 시세를 수집하고 `dashboard_work.py`는 저장된 자료로 포트폴리오를 평가한다. 수집·평가·백테스트는 기존 워커의 대시보드 이벤트 루프에서 각각 진행하며 분석·뉴스레터 큐가 비기를 기다리지 않는다. 저장과 화면 조회는 외부 가격 공급자를 호출하지 않는다. 저장 시 보유 문서와 캐시 기반 평가를 같은 트랜잭션으로 갱신하고 `GET/POST /api/portfolio/workbook`에 `tracking`을 함께 반환한다. 부가 기록 조회가 실패하면 이 필드를 생략하고 보유 문서의 조회·저장 성공을 유지한다. UI는 이때 기존 개별 조회로 복구한다.

`portfolio_quote_refresh`는 종목·최초 필요일·다음 수집 시각·임대를 저장한다. 같은 종목은 고객 간 한 번만 수집하고 새 종목 추가는 기존 종목의 수집 일정을 초기화하지 않는다. 완료·실패 후 1분 뒤 다시 시도하며 보유자가 없거나 잔고가 0인 종목은 수집을 중단한다. 수집 임대는 가격 발행 때도 확인하고, 늦게 끝난 요청·더 오래된 실제 관측일·수정 가격 전용 대체 자료가 더 최신 실제 종가를 덮어쓰지 못하게 한다. 수집 완료 알림은 이미 기다리는 포트폴리오의 순서를 유지한다. 수집은 210초, 백테스트 작업은 240초로 제한하며 느린 원천이나 대기 작업이 있으면 갱신이 지연될 수 있다. 종료 신호 후 새 작업을 시작하지 않는다. 새 AWS 자원이나 LLM 호출은 없다.

거래일 달력은 시장·연도 범위별로 재사용하며 API 시작 때 현재 및 최근 1년 범위를 준비한다. 화면 읽기는 이미 계산된 평가를 반환한다. 설계는 [단일 저장소에서 읽기·쓰기 모델을 분리하는 방식](https://learn.microsoft.com/en-us/azure/architecture/patterns/cqrs)을 따른다. 완료 종가를 사용하는 현재 서비스의 수집·서빙 분리이며 실시간 체결가 서비스로 변경한 것은 아니다.

종가 캐시는 한국·미국 시장의 완료 거래일 기준으로 구분한다. 보유 정보를 저장하면 유효한 종목별 캐시로 다시 계산하며, 현재 거래일의 분할 공시는 읽되 미완료 가격은 평가에 쓰지 않는다. 분할이 확인됐으나 분할 후 첫 종가가 없으면 새 평가를 보류하고 기존 평가가 있으면 이전 값으로 표시한다. 가격 복구가 수정 종가만 제공할 때 이를 실제 평가 가격으로 대체하지 않는다. 수량·평균매수가의 명시적인 수정은 각각 별도 기준일로 보존한다.

평가용 가격은 최근 35일과 실제 수량·평균매수가 기준일 중 더 이른 시점부터 수집한다. 캐시에 복구 범위를 기록해 더 오래된 보유 기준이나 백테스트 요청에는 필요한 범위로 다시 조회한다. 최근 가격만 확보된 종목은 전체 이력 확보 전까지 완전한 배당 이력이나 백테스트 자료로 취급하지 않는다. 보유 기간 중 거래일이 누락되면 분할이 없었다고 가정하지 않고 해당 종목을 미확인 처리하고 다음 1분 주기에 재조회한다. 잔고가 0인 종목이나 수정 전 기준일은 다른 종목의 조회 범위를 늘리지 않는다.

`market_preload.py`는 전체 카탈로그와 환율을 `portfolio_market_preload`에 멱등 등록한다. 최근 가격을 먼저 채운 뒤 전체 이력을 수집하고 한국은 17시, 미국은 07시, 환율은 UTC 날짜가 바뀐 뒤인 09시 30분 이후 일별 갱신한다. 한국 시간 07:00–07:50, 09:30–14:30, 17:00–19:00, 21:00–23:00에만 새 수집을 시작해 기존 배치 예약 시간을 피한다. 다른 배치의 지연 종료까지 감시하는 잠금은 아니다. 고객 분석·백테스트 대기나 실행, 뉴스레터 원고 실행 중에는 선적재를 쉰다. 공급자 오류는 지수 간격으로 재시도하며 호출 제한은 전체 선적재를 15분간 중단한다.

선적재는 기존 워커의 수집 슬롯 하나와 재사용 가능한 자식 프로세스를 사용한다. 응답 제한이나 취소 시 프로세스를 종료하고 슬롯을 반환하며, 원본 가격 이후 보정만 실패하면 확보한 가격과 실패 상태를 보존한다. 최신 부분 이력은 호환되는 과거 자료와 병합하며 분배금 조정 비율을 과거 수정 가격에 반영한다. 분할 등으로 원가격 기준이 달라지면 서로 다른 기준을 섞지 않고 전체 이력을 다시 받는다. 부분 응답에서 빠졌거나 0으로 돌아온 기존 배당·분할은 보존하고 전체 이력 조회로 정정한다. 거래가 없는 배당 전용 행은 가격 결측으로 취급하지 않는다. 조정 기준을 확인할 수 없으면 실제 가격·현금 배당은 유지하되 전체 수정 가격 이력의 확보 완료를 취소한다. 가격이 없는 고객 종목·환율에는 갱신 한 주기(1분)의 우선순위를 주되 오래 기다린 기존 종목 갱신이 밀리지 않게 한다. 분배금·분할의 빈칸은 `actions_complete`로 따로 기록해 가격·백테스트 자료를 숨기지 않고 후속 이력 수집을 유지한다. 선적재도 기존 무료 대체 자료원을 사용하며 실제 가격·분할 정보는 확보한 원본을 보존한다. 고객 보유 종목은 1분 갱신 주기를 유지해 늦게 올라오는 분할 공시를 확인한다. 새 종목 추가가 다른 종목의 갱신 시각을 초기화하지 않으며 저장·조회는 캐시로 즉시 응답한다.

`GET /api/portfolio/workbook/tracking`은 소유자의 시세·월별 예상 분배금·일별 평가액·이전 수동 기록 보관본을 반환한다. `portfolio_dashboard_market`, `portfolio_dashboard_days`, `portfolio_dashboard_prices`가 시세 상태·일별 기록·공용 공개 가격 캐시를 저장한다. 보유 문서 버전이 달라지면 오래된 계산은 발행하지 않는다. 종목 삭제·분석 재가져오기 후에도 자동 기록은 남고, 없어지는 수동 배당·월 기록은 원래 종목명과 함께 `portfolio_workbook_archives`에 보관한다.

화면은 저장한 보유분의 새 계산을 기다리는 동안 3초, 준비된 뒤에는 1분 간격으로 갱신 상태를 조회한다. 저장이나 조회 실패로 기존 평가액·손익·차트를 지우지 않는다. 평가 행의 `instrument_id`로 같은 종목인지 확인하며, 이전 저장본은 기존 `bases.signature`와 현금 행 형태로 호환한다. 자료가 없는 새 종목은 제외하고 ‘확인된 자산’을 표시한다. 이전 값을 표시하는 동안 수정창에는 최신 저장 입력을 사용한다. 이전 미국 종목의 원화 단가는 보존된 평가액의 환율을 사용한다. 오래된 당월 분배 예상을 새 확인일로 되살리지 않는다. 갱신 실패로 유지한 평가는 `missing`에 남기고 새 일별 기록으로 발행하지 않는다. 빈 포트폴리오와 시드머니 입력 대기 화면은 빠른 조회를 하지 않는다. 숨겨진 화면에서는 조회하지 않으며 이전 주기 요청이 끝나기 전에 다음 주기 요청을 겹치지 않는다.

대시보드 루프는 같은 프로세스의 별도 스레드·이벤트 루프에서 실행해 동기 DB 대기가 분석을 막지 않는다. 작업 접수/실패 기록 중 예외도 이 루프에서 로그를 남기고 재시도하므로 진행 중 분석을 취소하지 않는다. 가격 수집 subprocess는 두 루프가 합쳐 최대 1개이며 워커 메모리 한도는 768 MB다. 2026-10-20 두 분석의 가격 수집 중 512 MB cgroup OOM 종료가 확인되어 동시 프로세스 수와 메모리 한도를 함께 조정했다. 대기 취소와 프로세스 종료 시에도 이 슬롯을 반환한다.

선적재는 대기·실행 중인 고객 시세 요청에 슬롯을 양보한다. 최적화의 공급자 응답 제한은 공유 슬롯 대기 이후에 시작하며 전체 가격 수집 제한은 유지한다. 계좌 한도가 확인된 경우 실행 계산은 전체 자산 대비 허용 최대 비중을 내림해 제공한다. 최종 비중의 과거 성과를 읽지 않은 예상 수익률은 표시하지 않되, 검증을 통과한 추천 비중은 유지한다.

분석 조회는 소유권을 확인한 뒤 원래 `input`을 함께 반환한다. 실패 화면은 이 입력으로 재시도하거나 편집 초안을 복원한다. 접수 응답을 잃은 경우 브라우저의 pending 요청·멱등 키로 접수를 재확인한다. 실패 작업은 고객의 일일 3회 체험 횟수에서 제외하되 전체 일일 작업·큐 한도는 유지한다.

운영자가 테스트 방문자임을 확인한 경우 `admin_store.mark_test_visitor(owner, label, posthog_distinct_id)`를 사용한다. `portfolio_test_visitors`에 분류를 보관하고 기존·이후 작업의 `test_label`에 반영한다. `mark_tests`는 개별 작업만 분류하며 혼합 방문자의 고객 작업을 변경하지 않는다. 원본·녹화·보유 문서는 삭제하지 않는다. `/session`의 `is_test`를 PostHog 이벤트·녹화에 넣고, 고객 보고서와 관리자 집계는 동일한 방문자 제외 기준을 사용한다. 과거 PostHog 이벤트는 등록된 distinct ID의 테스트 제외 필터를 별도로 적용해야 하며 프로젝트/인사이트 쓰기 권한이 필요하다.

문서 버전이 같아도 다른 워커가 갱신을 이어받으면 이전 작업은 결과와 갱신 예약을 덮어쓰지 않는다. 백테스트와 분석은 기존 전체 대기·실행 중 20건 접수 한도를 공유한다. 백테스트의 소유자별 하루 50건 제한 외에 새 일일 전체 예산 제한은 추가하지 않는다.

`POST /api/portfolio/backtests`는 카탈로그 종목·합계 100% 비중·선택 기간을 `portfolio_backtests`에 접수한다. `GET /api/portfolio/backtests/{id}`는 소유한 작업만 반환한다. 지정 시작일에 비중을 적용하고 분배금 전액 재투자·환율 반영·세금/거래비용 제외로 계산한다. 기간을 생략하면 최대 공통 기간을 사용한다. 자료가 없는 종목은 제외 내역을 표시하고 남은 비중을 정규화한다. 수익 기록의 자산 추이는 입력 수량의 평가액이므로 입출금과 수량 변경도 포함하며, 이를 투자 수익률로 표시하지 않는다.

시세는 최근 완료된 종가다. 해외 종목 평가손익은 현재 환율 기준 가격 손익이며 매입 이후 환차익을 포함하지 않는다. 분할은 등록 이후 관측된 분할 비율로 수량과 평균매수가를 보정한다. 배당 전망은 완전한 최근 12개월 분배 이력을 같은 월로 옮긴 세전 추정이며 확정 지급일을 만들어내지 않는다. 미확인 자료는 0원과 구분한다. 원천은 기존 Yahoo/대체 수집기이며 [가격·분배·분할 인터페이스](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html)와 [가격 복구](https://ranaroussi.github.io/yfinance/advanced/price_repair.html)를 따른다.

평가손익 요약과 일별 수익기록에는 확인된 손익을 합산하고 금액 뒤에 소수점 둘째 자리 수익률을 표시한다. 일부 종목의 원가가 없으면 ‘확인된 평가손익’과 제외 종목 수를 표시하며, 수익률 분모에도 손익이 확인된 종목의 원가만 포함한다. 원가 합계가 0 이하이면 수익률을 만들지 않는다. 과거 기록에 현재 입력한 원가를 소급해 채우지 않는다. 평균매수가가 없는 보유 종목에는 입력 버튼을 표시한다.

배당 달력은 현재 월의 남은 기간부터 내년 같은 월까지 표시해 향후 1년 안의 예상 분배금을 빠뜨리지 않는다. 따라서 월 표시 칸은 양 끝의 부분 월을 포함한 13개다. 백테스트의 가격 유효성은 요청한 과거 종료일 기준으로 판단하며, 휴장일인 시작·종료일에는 직전 관측 가격을 사용한다.

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
- `GET /api/portfolio/analyses/latest`: 소유자의 최근 분석 또는 null.
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
