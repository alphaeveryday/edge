ALPHA-1092는 단일 스레드 조회·JSON 조립·Claude SDK 툴 등록·호출 기록 저장까지 구현했다. 가상 뉴스 DB로 DeepSeek의 실제 툴 호출도 확인했다. 운영 DB 접속·페이지 탐색·앱의 초기 입력 연결은 남았다. 티켓 전체 완료가 아니다.

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

- 테스트 20개 통과, 제외된 테스트 없음. 조립 9개·조회 7개·SDK MCP 2개·실행 결과 검증 2개.
- `load_thread_summary`는 기존 테이블의 사건·단계·중복 분류를 보존해 조립 함수에 전달. 지정 스레드·기간·구성종목 범위에서 300기사 제한 없이 집계.
- 테스트는 실제 SQL 조인을 SQLite에서 실행. PostgreSQL 드라이버·타입·권한·쿼리 성능 검증을 대신하지 않음.
- 구성종목은 호출자가 전달. 전체 편입 조회 연결은 T03 작업이며 아직 완료되지 않음.
- 기존 로컬 v2와 운영 분석엔진은 수정하지 않음. 신규 클라우드 자원·DB 변경 없음.
- 다음: T02의 과거 조회 가능 범위 확인 → T03의 전체 구성종목 범위 연결 → 실제 조인 행으로 이 함수 호출 → 초기 입력·탐색 툴 동일 구조 확인.
- DeepSeek 실제 호출: 가상 DB의 `thread` 조회 → 기사 `0`·실행 ID 반환 일치 확인. 투자 문장 품질 검수는 아님.
- 첫 실행은 잘못된 스레드 ID로 빈 결과를 받음. 검사도 호출 ID만 확인해 잘못 통과시켰으므로 실패로 정정. 입력 ID를 JSON으로 명시하고 조회된 기사 ID까지 검사한 두 번째 실행 통과.
- `uv.lock`으로 새로 설치한 Python 3.13 환경에서도 테스트 20개와 세 번째 실제 호출 통과. 기존 환경만 의존하지 않음.

## 에이전트 연결과 기록

- `make_news_thread_server(connection, constituent_ids, bucket, start_at=..., analysis_at=...)`를 `ClaudeAgentOptions.mcp_servers["analysis"]`에 전달.
- 허용 툴: `mcp__analysis__get_news_thread`. 인자: `{"thread_id":"..."}`. 종목·기간은 서버 고정.
- `runs/{tool_run_id}.json`: 함수 ID·인자·조회 범위·출력·실행시각. `output`은 모델에 반환한 `{tool_run_id,result}`와 동일. raw 조회 행 복제 없음.
- `definitions/get_news_thread-v2.json`: 고정 출처·설명·최종 근거 사용 불가 표시. 기존 v1과 응답이 달라 버전 분리.
- 현재 고유 사건 3개 미리보기만 반환. `has_more_events`가 참이어도 다음 페이지 조회는 아직 미구현이며 툴 설명에 명시.
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
