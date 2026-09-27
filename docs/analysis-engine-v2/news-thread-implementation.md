ALPHA-1092는 스레드 검색·사건과 추가 기사 탐색·기사 발췌와 최종 참조·Claude SDK 툴 등록·호출 기록 저장까지 구현했다. 실제 PostgreSQL과 DeepSeek를 연결해 뉴스 입력부터 최종 기사 참조까지 실행했다. 운영 앱·대시보드 경로는 교체하지 않았다. 티켓 전체 완료 판정과 PR 검수는 남았다.

## 구현

- `summarize_news_thread`: 같은 단계의 서로 다른 사건·후속·정정 유지.
- 중복 기사: `document_id` 기준 집계. 전체 조회 범위의 고유 기사 제외 후 미리보기 제한 적용.
- 대표 기사: 가장 먼저 공개된 기사, 같은 시각은 기사 ID순. 기사 품질 판정 아님.
- 사건 정렬: 대표 기사 공개시각 내림차순, 동시각은 사건 ID순.
- 미확인 단계는 `null`. 제목이나 현재 단계로 새 사건·단계 생성 안 함.
- 원천 충돌·필수 필드 누락은 오류. 가용 행이 없으면 `None`.

## 입력 연결 조건

- 한 스레드의 지정 범위 전체 행을 전달. 잘린 조회 결과로 전체 중복 수를 표시하지 않음.
- 기사: `document_id`, `title`, `published_at`, `document_available_at`.
- 사건: `source_event_id`, `event_type_code`, `predicate_code`, `lifecycle_stage`, `event_available_at`.
- 연결: `thread_id`, `novelty_status`, `link_evaluated_at`.
- 세 가용시각 이름은 조인 컬럼 충돌을 피하기 위한 어댑터 별칭. DB 컬럼 추가 아님.
- 모든 시각은 UTC offset을 포함한 문자열. `end_at`은 서버가 고정한 분석시각 이하여야 함.
- 어댑터가 assertion·evidence 관계의 가용성과 과거 버전을 확인해야 함. 현재 행의 시각 비교만으로 과거 정정 전 상태를 복원하지 않음.

## 검증과 다음 작업

- 테스트 31개 통과, 제외된 테스트 없음. Python 3.12 잠금 환경 확인. 검색·사건별 추가 기사·대상·시점·페이지·SDK 응답 저장 검사 포함.
- `load_thread_summary`는 기존 테이블의 사건·단계·중복 분류를 보존해 조립 함수에 전달. 지정 스레드·기간·구성종목 범위에서 300기사 제한 없이 집계.
- 테스트는 실제 SQL 조인을 SQLite에서 실행. PostgreSQL 드라이버·타입·권한·쿼리 성능 검증을 대신하지 않음.
- 구성종목은 호출자가 전달. 전체 편입 조회 연결은 T03 작업이며 아직 완료되지 않음.
- 기존 로컬 v2와 운영 분석엔진은 수정하지 않음. 신규 클라우드 자원·DB 변경 없음.
- 남은 선행 의존성: T02의 과거 조회 가능 범위와 T03의 전체 구성종목 확보. 현재 시점 연결 성공을 과거 1주일 재현 성공으로 취급하지 않음.
- DeepSeek 실제 호출: 가상 DB의 `thread` 조회 → 기사 `0`·실행 ID 반환 일치 확인. 투자 문장 품질 검수는 아님.
- 첫 실행은 잘못된 스레드 ID로 빈 결과를 받음. 검사도 호출 ID만 확인해 잘못 통과시켰으므로 실패로 정정. 입력 ID를 JSON으로 명시하고 조회된 기사 ID까지 검사한 두 번째 실행 통과.
- `uv.lock`으로 새로 설치한 Python 3.13 환경에서도 테스트 20개와 세 번째 실제 호출 통과. 기존 환경만 의존하지 않음.

## 에이전트 연결과 기록

- `make_news_thread_server(connection, constituent_ids, bucket, start_at=..., analysis_at=...)`를 `ClaudeAgentOptions.mcp_servers["analysis"]`에 전달.
- 허용 툴: `mcp__analysis__get_news_thread`, `mcp__analysis__get_issue_evidence`, `mcp__analysis__search_news_threads`. 종목·조회 가능 기간은 서버 고정.
- `search_news_threads(start_at?, end_at?, query?, cursor?)`: 서버 범위 안에서 기간을 좁혀 검색. 제목 부분일치·최신순. 스레드와 미연결 기사를 합해 10개씩 반환. 커서는 동일 조건·트랜잭션에서만 유효.
- 검색어로 선택된 스레드의 중복 수·미리보기는 해당 기간 전체 기준. 초기 입력도 이 함수의 `result` 본문을 사용.
- `get_news_thread_articles(thread_id, source_event_id?, cursor?)`: 대표 기사 외 연결 기사 또는 중복 기사 제목을 10개씩 탐색. 최종 근거로는 사용 불가. 사건 필터 생략 시 해당 스레드 전체 범위.
- `get_issue_evidence(news_ids, include_body)`: 기사 ID 1~10개. true는 확보 발췌, false는 기사 ID·제목. false 호출만 최종 근거로 연결.
- 발췌가 없거나 cutoff 이후 관측됐으면 `lead_text:null`, `content_kind:unavailable`. 기사 자체가 범위 밖이면 전체 호출 오류. 성공한 일부 기사만 조용히 반환하지 않음.
- `runs/{tool_run_id}.json`: 함수 ID·인자·조회 범위·출력·실행시각. `output`은 모델에 반환한 `{tool_run_id,result}`와 동일. raw 조회 행 복제 없음.
- `definitions/get_news_thread-v2.json`: 고정 출처·설명·최종 근거 사용 불가 표시. 기존 v1과 응답이 달라 버전 분리.
- 고유 사건 3개씩 반환. `next_cursor`를 같은 `thread_id`와 함께 넘기면 다음 페이지. 커서는 마지막 사건 ID이며 동일 서버의 고정 범위·트랜잭션 안에서 사용.
- 페이지가 바뀌어도 중복 수는 전체 범위 기준. 범위 밖 커서는 오류.
- 저장 실패는 툴 오류로 반환. 성공한 호출 기록이 없는 실행 ID를 응답하지 않음.
- 기존 앱 에이전트·대시보드 실행 경로를 자동 교체하지 않았음. 이 SDK 서버로 수행한 독립 실행까지 검증.

로컬 검증:

```powershell
uv run --project src/apps/cloud/analysis-engine-v2 python -m pytest src/apps/cloud/analysis-engine-v2/tests -q
```

실제 모델 호출은 자동 단위 테스트에 포함하지 않음. 별도 실행:

```powershell
uv run --project src/apps/cloud/analysis-engine-v2 python src/apps/cloud/analysis-engine-v2/tests/run_thread_smoke.py --env-file <키가 있는 .env 경로> --output-dir <새 결과 폴더>
```

수동 호출용 시스템 프롬프트: `tests/thread_smoke_prompt.yaml`. 결과 폴더의 `input.json`, `result.json`, `runs/` 확인.

## 실제 DB 확인 — 2026-09-27

- 전용 read-only 계정·REPEATABLE READ·기존 SSM 터널 사용. 데이터·스키마 변경 없음.
- KODEX 반도체에서 확보된 21개 구성종목으로 조회. 기존 적재 상태는 `partial`이므로 전체 확보를 주장하지 않음.
- `thr_61595f4e35cf100a65c7240f9a`: 삼성 LPDDR6 관련 기사와 `FIRST_IN_THREAD`, 미확인 단계 `null` 반환.
- `thr_06e4f19d556b953a5f325a9881`: 9월 범위는 중복 기사 150개·고유 사건 0개. 원천 분류를 바꾸지 않음. 약 0.15초는 단일 실측이며 성능 보장 아님.
- 현재 시점 조회만 검증. 과거 링크 수정 이력의 재구성은 T02에서 별도 확인.
- 검색 1페이지 단일 실측 약 2.2초. 실제 기사 발췌 조회 성공. 본문 전체가 아니라 원천 `lead_text` 그대로 전달.
- 첫 실제 DB 에이전트 실행은 20회 툴 호출과 최종 참조 연결 성공. 문장 검수는 실패: 다른 기사에서 읽은 상장 시점의 근거 누락·부차적인 제품 수치·추상적인 영향 설명. 프롬프트 수정 후 재실행 중.

## CI와 배포

- `test-analysis-v2.yml`: v2 경로 변경 시 Python 3.12 잠금 환경의 테스트만 실행. AWS·DB·LLM 키 불필요.
- 최초 워크스페이스·잠금 파일 변경은 기존 Python CI에도 영향. 이후 v2 코드만 바꾸면 기존 앱 이미지 빌드를 유발하지 않음.
- DB 마이그레이션·새 AWS 자원·운영 스케줄 변경 없음.
