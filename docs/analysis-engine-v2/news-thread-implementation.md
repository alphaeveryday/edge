ALPHA-1092의 첫 구현은 조회된 사건·기사 행을 스레드 미리보기 JSON으로 조립하는 함수다. DB 조회·페이지 탐색·초기 입력·에이전트 툴 연결은 아직 변경하지 않았다. 티켓 전체 완료가 아니다.

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

- 테스트 9개 통과, 제외된 테스트 없음. 이 작업 폴더의 새 조립 함수 테스트 범위.
- 기존 로컬 v2와 운영 분석엔진은 수정하지 않음. 신규 클라우드 자원·DB 변경 없음.
- 다음: T02의 과거 조회 가능 범위 확인 → T03의 전체 구성종목 범위 연결 → 실제 조인 행으로 이 함수 호출 → 초기 입력·탐색 툴 동일 구조 확인.
- 실제 LLM 호출과 문장 품질 검수는 아직 수행하지 않음.

로컬 검증:

```powershell
$env:PYTHONPATH='src/apps/cloud/analysis-engine-v2/src'
python -m pytest src/apps/cloud/analysis-engine-v2/tests -q
```
