# 구조화 분석 도구 표면 전환 설계

## 목표

동적 분석기의 LLM이 SQL이나 저장소 뷰 이름을 만들지 못하게 하고, 시점이 고정된
ObjectSet·뉴스 사건·검증 도구만 사용하게 한다. 분석 산출물은 다음 분석의 입력이 아니라
실행 감사 로그로만 남긴다.

## 확정된 경계

- LLM 입력 표면에서 `analysis_evidence_bundle`, `explanation_result`,
  `explanation_run`, `hypothesis_trial`을 제거한다.
- `v_analysis_evidence_bundle`, `v_explanation_result`, `v_explanation_run`,
  `v_hypothesis_trial` 같은 자동 뷰를 만들거나 열거하지 않는다.
- 위 산출물의 신규 분석 기록은 S3 run/trace 구조화 로그가 정본이다.
- 기존 Cloud DB 테이블과 과거 데이터는 첫 단계에서 삭제하지 않는다. 먼저 writer와 reader를
  끊고 사용량 0을 증명한 뒤 별도 contract PR에서 제거한다.
- LLM은 SQL을 받거나 반환하지 않는다. 저장소 SQL은 구조화 도구의 내부 adapter 구현이다.
- Cube `*_latest`는 과거 인과 분석의 PIT를 보장하지 않으므로 이 경로에 사용하지 않는다.

최신 `dev`는 `hypothesis_trial`을 DB 정본으로 취급하지만 이는 사용자의 최신 요구와
충돌한다. 이 설계는 최신 요구를 선택한다. 한 번에 스키마까지 삭제하지 않고
expand/migrate/contract 순서로 전환해 롤백 가능성을 유지한다.

## PR 순서와 독립성

### PR 1 — 출력 조회 표면 격리

RDB 카탈로그에서 모든 테이블을 자동 공개하는 정책을 입력 전용 명시 정책으로 바꾼다.
분석 산출물은 자동 DuckDB 뷰, SQL allowlist, LLM schema/coverage에서 제외한다. 산출물 writer는
구조화 로그 전환이 완료될 때까지 별도 정책으로 다루되, LLM reader와 섞지 않는다.

이 PR의 핵심 불변식은 다음과 같다.

> 분석 산출물은 분석 입력 관계로 노출되지 않는다.

### PR 2 — ObjectSet 런타임 기반

ObjectSet 계약을 analysis-engine이 의존할 수 있는 경량 패키지 경계로 옮긴다. 생성, 필터,
관계 이동, affordance 열거, 객체 관측을 구조화 도구로 제공한다. `as_of`와 lineage는 handle에
붙으며 LLM이 변경할 수 없다. 기존 SQL 경로는 shadow 비교를 위해 아직 남긴다.

### PR 3 — 뉴스 thread/event/argument 도구

`event_thread → source_event → event_argument → evidence`를 일급 handle로 제공한다.
사건 타입별 허용 role, cardinality, object kind, measure/unit을 조회하는 schema 도구를 추가한다.
미해소 argument는 버리지 않고 `resolved=false`와 원문 surface로 반환한다.

### PR 4 — LLM 구조화 도구 컷오버

P2·P3·P5에서 자유 SQL 인자와 SQL 응답 schema를 제거한다. ObjectSet, 뉴스 탐색, 검증 도구만
주입한다. 검증 수치와 판정은 verifier ledger에서만 최종 설명으로 전달된다. 기존 경로와
shadow replay를 수행하되 사용자 결과에는 신규 경로만 사용한다.

### PR 5 — SQL 레거시 제거

신규 경로의 canary가 통과한 뒤 `sql_surface`, `statics/sqltool.py`, SQL 재시도 prompt와
allowlist 등 LLM 자유 SQL 경로를 삭제한다. 활성 소비자 0과 이미지 내 참조 0을 확인하기 전에는
스키마나 과거 데이터를 삭제하지 않는다.

## 구조화 도구 계약

LLM에 제공하는 최소 도구는 다음과 같다.

- ObjectSet: `create`, `filter`, `describe`, `list_affordances`, `follow`, `inspect`
- 뉴스: `find_threads`, `get_thread`, `list_events`, `get_event_arguments`,
  `describe_event_schema`, `follow_argument`, `get_event_evidence`
- 검증: 기존 결정적 cohort·return·placebo·permutation·fit 도구의 구조화 인자형

모든 응답은 `as_of`, dataset/version, object or event reference, unresolved/PIT gap,
lineage reference를 포함한다. 내부 SQL 문자열은 LLM tool schema와 정상 trace에 포함하지 않는다.

## 실패 처리

- 금지된 산출물 관계 요청은 빈 결과가 아니라 정책 오류로 실패한다.
- LLM이 `sql`, `query`, `view_name`을 반환하면 schema 검증에서 거부한다.
- PIT가 없는 출처는 시점 안전한 것처럼 처리하지 않고 `pit_gap`으로 반환한다.
- argument가 schema에 없거나 cardinality를 위반하면 hypothesis 근거로 승격하지 않는다.
- verifier에 없는 숫자나 판정은 최종 설명 조립 단계에서 거부한다.
- 근거 부족은 `undetermined` 또는 구조화된 `data_request`로 끝낸다.

## 검증 주장

- C1: 분석 산출물은 LLM 조회 표면에 노출되지 않는다.
- C2: LLM은 SQL을 생성하거나 실행할 수 없다.
- C3: ObjectSet의 모든 홉은 동일한 `as_of`와 재현 가능한 lineage를 유지한다.
- C4: 뉴스 관계는 실제 동일 사건의 argument와 선언된 role schema에만 근거한다.
- C5: 최종 설명의 수치와 판정은 verifier 결과까지 역추적된다.
- C6: 동일 입력 snapshot/version replay는 동일한 구조화 결과를 만든다.

검증은 주장별로 가장 작은 독립적 반증 수단을 먼저 사용한다. 금지 관계 음성 테스트,
미래 행 주입 PIT 테스트, 잘못된 role·SQL 필드 mutation, 고정 뉴스 fixture, 기존/신규 shadow
differential, 실제 배포 이미지와 AWS canary trace 순으로 fidelity를 높인다.

## 사용자 승인 증거

각 PR은 다음 표를 PR 본문과 결과 보고에 포함한다.

| 주장 | 반증 방법 | 실행 증거 | 결과 | 남은 공백 |
|---|---|---|---|---|
| 해당 PR의 claim | 독립 oracle 또는 mutation | 명령·fixture·trace ID | PASS/FAIL | 미실행 가정 |

Codex의 판정은 `VERIFIED`, `PARTIALLY VERIFIED`, `NOT VERIFIED` 중 하나로 표시한다.
로컬 테스트만으로 AWS 배포를 검증했다고 주장하지 않으며, 사용자가 diff와 증거를 검토한 뒤
최종 승인한다.

## 롤아웃과 롤백

PR 1은 차단 정책만 추가하므로 이전 task definition으로 즉시 롤백할 수 있다. PR 2~4는
feature flag와 shadow 실행을 사용한다. PR 5는 canary 기간 동안 레거시 사용량 0이 확인된 뒤
진행한다. DB drop은 PR 5와도 분리할 수 있으며, 별도 승인이 없으면 수행하지 않는다.
