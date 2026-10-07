# v2 분석 결과와 툴 감사

오늘 움직임과 전망을 발행본별로 저장한다. 이전 결과를 조회해 다음 분석에 전달하고, 저장된 행으로 화면 응답을 조립한다.

**현재 구조는 11개 테이블·101개 컬럼·11개 외래키·11개 기본키·8개 고유키다**(`validate.py` 기준). 최초 8개 설계에 [PR #970](https://github.com/alphaeveryday/edge/pull/970)의 `V202609282200` 마이그레이션이 수치 카드·이슈 상세 테이블 2개와 `issue_headline` 컬럼을 추가했고, 이후 `V202609301400`(`data_source`)·`V202610021500`(`analysis_execution_requests`)·`V202610022229`(`movement_analyses.withdrawn_at`)·`V202610071800`(`outlook_items.sentiment`, `source_links`)이 더했다. 대시보드의 `outlook_factor_metrics · 20행`은 이 실제 테이블에서 해당 분석에 저장된 20개 지표 행을 뜻하며, 테이블 수나 고정 카드 개수를 뜻하지 않는다.

[Obsidian ERD Editor 원본](analysis-storage.erd) · [draw.io 원본](erd.drawio). 원본에는 NULL 허용·기본값·복합키를, 아래 그림에는 테이블·컬럼·FK 개요를 표시한다. SQL CHECK와 조회 인덱스의 정확한 정의는 v2 마이그레이션을 기준으로 한다.

![v2 저장 관계](erd.svg)

- `movement_analyses`·`movement_items`: 오늘 움직임의 요약·선택 순서와 누적 설명.
- `outlook_analyses`·`outlook_items`: 전망 요약·본문·논점 변경 기록.
- `outlook_factors`·`outlook_conclusion_keywords`: 요인 판단과 결론의 도움·부담 키워드.
- `outlook_factor_metrics`·`outlook_issue_items`: 네 요인 수치 카드와 이슈 상세 문단. 이슈 제목은 `outlook_analyses.issue_headline`.
- `tool_runs`·`tool_definitions`: 호출 인수·반환값·수식·출처. 실행은 두 분석 종류 중 하나에만 소속.

실선은 실제 외래키다. `selected_item_ids`·`tool_run_ids` 배열의 존재·소속 검사는 저장 코드가 맡는다. 완료 결과 불변성·발행 조건도 별도 저장 로직이 필요하며, 이 테이블만으로 보장하지 않는다. 관측일만 있는 값은 날짜로, 시각이 있는 값은 타임스탬프로 저장한다.

## 2026-09-30 대조 결과

기준: `V202609281600__create_v2_analysis_storage.sql`, `V202609282200__add_v2_factor_details.sql`, 생성된 `physical-erd.dbml` 및 저장 코드. 기존 ERD의 실제 오류 9건을 수정했다.

- `movement_items.source_as_of`, `outlook_items.source_as_of`: NULL 허용으로 정정.
- `outlook_items.title_keyword`: NOT NULL로 정정.
- 기본값 `'{}'` 누락 6곳: `movement_items.tool_run_ids`, `outlook_items.tool_run_ids`, `outlook_conclusion_keywords.tool_run_ids`, `tool_definitions.source_names`, `tool_runs.arguments`, `tool_runs.context`.
- “8개 테이블”, “지표 카드 범위 밖”, 미적용 계획 설명을 현재 구현과 구분했다. 당일 강조 보존과 발행 검증 범위도 코드에 맞췄다.

`python docs/data-model/domains/analysis-v2/validate.py`는 101개 컬럼의 타입·NULL 허용·PK·기본값, FK 대상 컬럼, 고유키와 draw.io 컬럼을 대조한다. 전체 도메인 그림은 `python -X utf8 src/libs/schema/scripts/validate-doc-erd.py`로 검증한다. 두 검사를 PR CI에서도 실행한다. SQL 파서는 `validate.py`에 명시된 v2 마이그레이션을 대상으로 하므로 후속 변경 때 기준을 확장해야 한다.

이 검사는 **DDL·생성 스키마와 문서 일치 검증**이며, 실제 DB 카탈로그와 대조한 것이 아니다.

실제/가상 분석 분리: `V202609301400`으로 두 분석 테이블에 `data_source` 추가. 과거 값은 `unknown`이며 실제 분석의 이전 글·근거로 재사용하지 않습니다.
