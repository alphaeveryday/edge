5요인 상세는 같은 전망 발행본의 스티커와 계산 결과로 조립한다. 수치 카드는 서버가 계산하고, 이슈 제목·설명은 에이전트가 작성한다. 원천 연결 전에는 목데이터를 사용한다.

- `save_factor_details(connection, analysis_id, metrics, issue)`: 실행 중인 전망에 상세 전체 저장. 호출자 트랜잭션에 참여하므로 본문 발행과 함께 커밋 가능.
- `metrics`: 차트·매크로·밸류·수급을 키로 하는 목록. 각 항목은 `key`, `value`, `observed_at`, `tool_run_ids`, 선택 `subject`.
- `issue`: `headline`, `items`. 항목은 `title_keyword`, `sentence`, `sentiment`, `tool_run_ids`.
- 수치는 유한 숫자, 방향은 상승·횡보·하락. 관측일은 날짜 그대로, 시각은 UTC 오프셋 필수. 미래 관측은 거부.
- 근거는 같은 분석에서 성공한 호출만 허용. 뉴스 근거는 `get_issue_evidence(include_body=false)` 호출. 수치 근거의 수식은 툴 정의에서 조회.
- 수치 없는 카드는 생략. 네 유형의 빈 목록은 허용. 알 수 없는 키·중복 키·다른 분석 근거는 거부.
- `read_factor_details(connection, analysis_id)`: 유형별 화면 JSON. 스티커는 `outlook_factors`에서 읽고 수치 화면 제목은 고정 문구로 조립. 이슈 제목은 저장값 사용.
- `outlook_factor_metrics`: 분석·유형·키별 한 행. 숫자/문자 중 하나, 관측일/관측시각 중 하나 저장. 카드 순서는 문서 정의 순서.
- `outlook_issue_items`: 이슈 문단별 한 행. `outlook_analyses.issue_headline`: 이슈 전체 제목.
- 신규 두 테이블에만 기존 v2 writer의 조회·삽입·수정·삭제 권한 추가. 감사 테이블 권한 유지.
