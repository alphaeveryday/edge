# 그래프 도구 이전 진행 상황

작업 위치: `D:/Github/edge-graph-tools`, 브랜치 `feature/graph-tools` (origin/dev `9226762f`에서 분기).
뷰에 붙지 못한 입력은 [graph-tool-view-gaps.md](graph-tool-view-gaps.md)에 모은다.

## 지금

| 단위 | 상태 | 확인한 것 |
|---|---|---|
| 0. 토대 | 완료 | 그래프 조회·근거 저장·도구 등록/호출·`get_result_page`·가설 검사기·실행기. 엔진 테스트 459개 통과 |
| 1. 대상 찾기 — `resolve_securities` | 진행 중 | |

## 도구별 가설 판정

판정은 같은 상황을 3회 실행해 3회 모두 맞으면 성립이다. 가설 정의는 `src/edge_analysis_v2/quality/tool_intents.json`에 있다.

| 도구 | 선택 | 인자 | 해석 | 연결 | 비호출 | 답변 반영 |
|---|---|---|---|---|---|---|

## 열린 문제

- Jira 이슈 키가 없다. 브랜치 이름에 키가 없어 PR을 올리지 않고 로컬 커밋만 쌓는다.
- 실행기는 그래프 도구만 가진 에이전트를 돌린다. 엔진의 기존 DB 도구와 함께 있을 때의 선택은 아직 확인하지 않는다.
- `src/uv.lock`에 `neo4j` 추가와 무관한 `sys_platform != 'emscripten'` 마커 변경이 섞여 있다. 로컬 uv가 다시 쓴 것이다.
