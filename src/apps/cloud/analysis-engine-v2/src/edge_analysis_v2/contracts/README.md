# 최종 화면 출력 계약 감사

`screen-output.schema.json`은 **에이전트 응답이 아닌 DB 조립 완료 응답**의 JSON Schema Draft 2020-12 계약이다. `agent/output_schema.py`와 독립적으로 정의했다. 운영 조립 코드의 상수·예시 출력에서 기대값을 역생성하지 않는다.

`manifest.json`에는 계약 버전·옵시디언 원문 경로·SHA-256만 기록한다. 원문 사본은 저장하지 않는다. 줄바꿈 차이는 정규화한다. `--contract-vault`로 지정한 현재 ETF ORCA 폴더의 문서가 달라지면 화면 JSON이 맞더라도 감사 전체를 통과로 표시하지 않는다. 원문 일치는 문장 의미의 자동 증명이 아니다. 문서를 바꾸면 관련 schema와 독립 테스트를 검토하고 계약 버전·원문 해시를 함께 갱신한다.

## 계약의 범위

| 출력 | 원문 기준 | 코드 검사 |
|---|---|---|
| 오늘 움직임 전체·요약·상세 | 오늘 움직임 §1, §1.1 | 0~5개, 필드·enum, 근거 ID 배열, 서버 시각 메타, 빈 선정과 null 요약, ID 중복 |
| 전망 전체·요약·본문 | 전망 §2, 화면 1~2 | 필수 필드, 최대 15논점, 불릿 객체, 날짜·업데이트 연결, 최초 발행 강조 금지 |
| 5요인·결론 | 전망 화면 3~4 | 5개 고정 순서, 스티커, 키워드·근거, 선택적 변경 조건 |
| 수치 요인 상세 | 5요인 상세 §1~2 | 고정 제목·지표 키·타입·범위, 관측일/시각, 지표 순서·중복, 부모 스티커/기준시각, 미래 관측 금지 |
| 이슈 상세 | 5요인 상세 §3 | 제목·문단·감정·근거와 부모 스티커 |

`publication`은 문서의 서버 메타를 `{etf_code, analysis_at, published_at}`으로 명시하며 전망은 `forecast_period`를 추가한다. DB의 nullable 컬럼이 화면에서도 nullable이라는 뜻은 아니다. 예를 들어 `movement_items.source_as_of=NULL`은 최종 화면의 자료시각 계약에 실패한다.

`subject`는 보통 선택적이지만 원자재·정책 일정 카드는 실제 대상 이름이 필요하다. 결측 지표는 배열에서 제외하며 실제 0은 허용한다. T01에서 보류한 5년 PER 밴드가 반환되면 실패한다. 요약 카드 UI에 분석 제목을 쓰라는 사용자 요청은 표시 규칙이며 `summary_card.title == detail.title`이라는 저장 계약을 만들지 않는다.

문장 품질·스티커 판단, 원천 자료 재계산/최신성, 전체 변경 이력 재연산, 고객 UI의 표시 방식과 T01 미정 정책은 이 감사로 합격 처리하지 않는다. 근거 ID 배열의 형식과 업데이트 연결은 검사하지만 실행 레코드의 성공·소속 재검증은 기존 저장/근거 검사 영역이다.

## 사용

대시보드의 **출력 계약 감사** 탭에서 출력 유형을 고르면 왼쪽 계약과 오른쪽 실제 응답을 비교할 수 있다. `DB에서 다시 감사`는 모델 호출 없이 새 읽기 전용 DB 스냅샷을 조회한다. API는 `GET /api/contract-audit/{movement|outlook}/{analysis_id}`다.

- `passed`: 정의된 자동 검사가 통과했고 현재 원문이 검토본과 일치.
- `failed`: 구조/의미 규칙 위반, 조립 실패 또는 원문 변경 감지.
- `partial`: 원문 경로 미설정·조회 불가 또는 필요한 문맥을 검사하지 못함.
- DB 접속 불가: HTTP 503. 완료 발행본 없음: HTTP 404. 둘 다 계약 통과로 처리하지 않는다.

모든 응답은 실제 `assemble_screen` 경로를 사용한다. 전체·기능별 응답은 하나의 repeatable-read/read-only 트랜잭션에서 비교하며 감사는 저장본을 수정하지 않는다. JSON 숫자는 서버에서 직렬화해 관리자 비교 화면의 JavaScript 정밀도 손실을 피한다.

## 회귀 검사

모듈 디렉터리에서 실행한다.

```powershell
python -m pytest tests/test_screen_contracts.py tests/test_cloud_review.py -q
$env:V2_TEST_DSN='postgresql://v2_local:local_only@127.0.0.1:55439/analysis_v2'
$env:V2_FACTOR_TEST_DSN='postgresql://v2_local:local_only@127.0.0.1:55440/analysis_v2'
python -m pytest tests integration_tests -q
```

통합 테스트는 기존 Flyway 마이그레이션이 적용된 로컬 전용 PostgreSQL을 요구하고 원격 DB를 거부한다. 고정 모델 대역으로 저장한 뒤 **새 연결에서 실제 조립 결과**를 검사한다. SQL 제약에는 맞지만 화면 계약에 틀린 값을 DB에 넣어 실패 검출까지 확인한다. 실패 검출을 위한 변형은 로컬 테스트 DB에서만 수행한다.
