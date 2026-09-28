# 목데이터 도구 계약

실제 계산 도구가 목데이터를 조회하고 근거 ID를 반환한다. 도구 결과는 감사 저장 후 그대로 에이전트에게 전달한다. 원천 DB 연결은 별도 작업이다.

## 공통

- `FixtureTools(fixture).schemas`: 함수명·설명·JSON 인수 명세.
- `.definitions`: 함수 버전·관리자 설명·LaTeX 수식·출처 이름.
- `.call(name, arguments)`: `{tool_run_id, result}`. 저장은 호출자를 감싸는 `AuditedExecution`이 담당.
- `.initial_input()`: 시점 제한된 원자료. 시계열은 `columns/rows`, 뉴스는 객체 목록.
- `context`: `etf_code`, 명시적 시차가 있는 `analysis_at`, 확정 수급일 `flow_as_of_date`.
- 모든 자료는 `available_at <= analysis_at`. 미래 관측·미래 발표 제외. 결측은 0으로 채우지 않는다.

## 뉴스·구성종목

| 도구 | 인수 | 결과 | 최종 근거 |
|---|---|---|---|
| `search_news_threads` | 없음 | 스레드→단계→고유 사건별 대표 기사와 중복 수. 최대 100개 사건 | 아니오 |
| `get_issue_evidence` | `news_ids: string[]`, `include_body: boolean` | 기사 ID·제목. true는 확보 본문 추가 | false일 때 |
| `get_etf_holdings` | 없음 | 최신 공개 편입일과 구성종목 비중 | 예 |

- 뉴스 행: `news_id,title,body,published_at,available_at,thread_id,stage,event_id`. 동일 사건 ID만 중복으로 묶는다. 제목 유사도로 병합하지 않는다.
- 대표 기사는 분석시각에 공개된 같은 사건의 최신 기사. 각 중복 보도 수는 대표 기사 제외 건수.
- 편입 행: `instrument_id,weight,as_of_date,available_at`. 목데이터 초기 범위는 주식만, 전체 종목 확보, 비중 합 1. 불완전 비중은 거절한다.
- 기사 본문은 자료 내용이며 명령이 아니다. 에이전트가 읽고 영향·중요도를 판단한다.
