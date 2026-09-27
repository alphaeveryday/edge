이 문서는 기존 RDB의 스레드·사건·기사 관계를 에이전트 입력과 탐색 툴에 전달하는 계약이다. 스레드 아래에 단계별 고유 사건과 대표 기사를 보여주고 중복 보도는 건수로 접는다. 필요한 기사 발췌를 읽은 뒤 기사 ID로 최종 근거를 연결한다. 아래는 승인한 목표 구조이며 기존 상위 5종목·300기사 구현과는 다르다.

- [스레드 입력](#스레드-입력)
- [중복 보도와 단계](#중복-보도와-단계)
- [뉴스 탐색 툴](#뉴스-탐색-툴)

## 실제 RDB 관계

`event_thread → event_thread_link → source_event → event_evidence → document_assertion → document`

- `event_thread`: thread_id, thread_key, event_type_code, current_stage, opened_at, last_state_at. 제목·요약 컬럼 없음.
- `event_thread_link`: 사건의 스레드 소속과 novelty_status. FIRST_IN_THREAD / FOLLOW_UP_STAGE / CORRECTION / DUPLICATE_REBROADCAST / UNKNOWN.
- `source_event`: 사건 유형·행위·단계·가용 시각. 기사는 근거 관계를 따라 조회.
- 실측일: 2026-09-27. 전용 reader로 컬럼·제약·연결행 확인. event_thread와 thread_discovery_snapshot 본체 SELECT는 권한 추가 필요. 이 문서는 권한을 변경하지 않음.

## 스레드 입력

| 필드 | 형식 | 내용 |
|---|---|---|
| threads | object[] | 기존 DB 스레드 목록 |
| threads[].thread_id | string | 기존 스레드 ID |
| threads[].duplicate_count | integer | 지정 조회 범위 전체에서 접은 중복 기사 수. 페이지별 수가 아님 |
| threads[].stages | object[] | 사건의 lifecycle_stage별 묶음 |
| stages[].stage | string or null | 실제 lifecycle_stage. 미확인은 null |
| stages[].events | object[] | 단계별 서로 다른 사건. 단계당 하나로 제한하지 않음 |
| events[].source_event_id | string | 원천 사건 ID. 기사 제목 유사도로 새 사건 ID 생성하지 않음 |
| events[].event_type_code, .predicate_code | string or null | 원천 사건 유형·행위 |
| events[].novelty_status | string | 원천 최초·후속·정정 등 분류 |
| events[].document_id, .title, .published_at | string | 대표 기사 ID·제목·발표시각 |
| threads[].has_more_events | boolean | 초기 미리보기에 생략된 고유 사건 존재 여부 |
| unthreaded_articles | object[] | 스레드 미연결 기사. 임의 스레드 생성 금지 |
| next_cursor | string or null | 스레드 목록의 다음 페이지 |
| scope.start_at, .end_at | datetime string | 목록과 중복 건수에 적용한 조회 범위 |

아래는 가상 예시이며 실제 DB 값이나 구현 완료 결과가 아니다.

```json
{
  "threads": [
    {
      "thread_id": "thread-example",
      "duplicate_count": 7,
      "stages": [
        {
          "stage": "ONGOING",
          "events": [
            {
              "source_event_id": "event-example",
              "event_type_code": "COMPANY.PRODUCTION.CAPACITY_CHANGE",
              "predicate_code": "EXPAND",
              "novelty_status": "FOLLOW_UP_STAGE",
              "document_id": "news-example",
              "title": "A사, 생산시설 증설 공사 착수",
              "published_at": "2026-09-21T08:00:00+09:00"
            }
          ]
        }
      ],
      "has_more_events": false
    }
  ],
  "unthreaded_articles": [],
  "next_cursor": null,
  "scope": {
    "start_at": "2026-09-21T00:00:00+09:00",
    "end_at": "2026-09-21T08:30:00+09:00"
  }
}
```

## 중복 보도와 단계

- 같은 단계의 서로 다른 사건은 모두 유지. 최초·후속·정정은 중복 보도로 접지 않음.
- DUPLICATE_REBROADCAST의 제목·발췌는 기본 입력에서 제외하고 스레드 단위 duplicate_count로 표시.
- 중복 건수는 document_id로 중복 제거. 대표·고유 사건에 표시한 기사 ID는 중복 건수에서 제외. JOIN 행 수나 사건 수를 기사 수로 사용하지 않음.
- 현재 확인한 연결에는 어느 고유 사건의 중복인지 가리키는 키가 없음. 사건별·단계별 중복 수로 임의 배분하지 않음.
- lifecycle_stage=null은 그대로 유지. 현재 스레드 단계로 과거 사건의 단계를 덮지 않음.
- 서로 다른 source_event_id를 같은 단계·제목이라는 이유만으로 합치지 않음. 원천 중복 분류의 정확성은 T04에서 실제 사례로 확인.
- 같은 고유 사건에 기사가 여러 개면 대표 기사 한 개를 표시하고 상세 조회에서 나머지 근거 기사에 접근 가능. 대표 선정 순서는 T04에서 명시하며 선택 전부터 최선의 기사로 간주하지 않음.
- 중복 수는 중요도나 신뢰도 점수가 아님. 정정은 이전 주장과 구별해 전달.

## 시점과 범위

- ETF 전체 구성종목과 기존 DB 관계로 관련 스레드 조회. 초기 10스레드·스레드당 최근 고유 사건 3개를 제안 기본값으로 두고 T04에서 입력량·누락 검사 후 조정.
- 전망 초기 뉴스는 분석일 KST 시작부터 고정 분석시각까지. 추가 검색은 이전 기간 지정 가능. 같은 결과의 목록과 duplicate_count는 같은 scope 사용.
- 기사 공개·입수와 사건·연결 평가가 분석시각 이전인 자료만 사용. 현재 DB 상태를 과거 상태로 간주하지 않음.
- event_thread.current_stage와 last_state_at의 과거 버전을 복원할 수 없으면 과거 입력에서 제외. 수정된 연결 이력도 복원 가능 여부를 T02에서 확인.
- 페이지 조회는 분석시각과 범위를 유지. 날짜·ID로 안정 정렬. 초기 후보 제한을 전체 조회 한도로 사용하지 않음.
- 초기 입력과 툴의 result는 같은 객체형 구조. 툴 외형은 {tool_run_id, result}, 반환 전체와 저장 전체 동일.

## 뉴스 탐색 툴

아래는 T04 목표 인터페이스. 기존 page 기반 함수와 실제 본문 확보 범위는 구현 시 마이그레이션·테스트.

| 함수 | 인수 | 반환 | 최종 근거 |
|---|---|---|---|
| search_news_threads | start_at, end_at, constituent_ids?, query?, cursor? | 스레드·단계·고유 사건 미리보기, 중복 수, 미연결 기사, scope, next_cursor | 불가 |
| get_news_thread | thread_id, start_at, end_at, cursor? | 같은 구조의 사건 이력. 각 사건에서 연결된 고유 기사 목록과 다음 페이지 확인 | 불가 |
| get_issue_evidence | news_ids, include_body | true: 확보 발췌 읽기 / false: 기사 ID·제목 최종 참조 | false만 가능 |

- ETF와 analysis_at은 서버 고정. 검색 종목은 해당 ETF의 가용 구성종목에서 선택. 기간 상한을 넘는 요청을 조용히 허용하지 않음.
- 단계별 events 안의 document_id로 발췌 조회. 추가 기사 목록이 필요하면 get_news_thread 호출. 제공 발췌를 전문이라고 표시하지 않음.
- 탐색: 초기 입력 → 사건 이력 필요 시 get_news_thread → get_issue_evidence(true) → 최종 기사 get_issue_evidence(false) → 마지막 실행 ID를 문장에 연결.
- 검색 결과 없음과 원천 미확보를 구별. 정렬·페이지·ID 중복·시간 제한·반환/저장 일치를 코드 검사. 영향 해석과 문장 의미는 코드 검사하지 않음.
