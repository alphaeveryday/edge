# v2 실행 접수 계약 — PR1

API와 가격 사건이 같은 접수 함수를 사용한다. 같은 요청의 재전달은 기존 실행을 찾고, 새 실행을 만들지 않는다. 이번 PR은 계약·공통 함수·저장 모듈·정확한 트리거 조회까지 구현한다. 운영 큐 소비자와 IAM 연결은 후속 PR이다.

## 입력

- 외부 API: 기존 `analysis_id`, `kind`, `etf_code`, `analysis_at` 그대로. 추가 필드 거절.
- 내부 가격 요청: 위 필드에 아래 `source` 추가. `kind=movement`만 허용.
- `analysis_id`: 원본 event_id·generation으로 결정. 원본 trigger_id는 변경하지 않음.
- 종목·시점·가격: 메시지 값을 믿고 조립하지 않고 `dataset_commit_outbox`와 `minute_price_trigger`를 대조.
- `analysis_at`: 트리거 생성 시각과 분봉 종료 시각 중 늦은 값. 재전달 시 현재 시각을 넣지 않음.
- `ExposureReverted`: 이번 PR에서는 분석 요청으로 접수하지 않음. 후속 workflow의 회수 처리로 연결.

```json
{
  "analysis_id": "06b1b994145a5b0c83e274d87eec9bda",
  "kind": "movement",
  "etf_code": "091160",
  "analysis_at": "2026-10-02T01:00:02+00:00",
  "source": {
    "event_id": "PriceTriggerFired:original-trigger:0",
    "event_type": "PriceTriggerFired",
    "trigger_id": "original-trigger",
    "generation": 1
  }
}
```

## 접수·복구

1. 입력을 검증하고 KST로 정규화. 같은 시각의 다른 UTC 표기도 같은 요청으로 취급. 장 시작 전 실행의 거래일이 전날로 밀리지 않도록 함.
2. DB에 요청 예약. 같은 ID의 다른 입력, 다른 요청에 사용한 사건 ID는 거절.
3. 이미 기록한 실행 ARN 또는 같은 이름의 실행 확인. 종료된 실행도 재실행하지 않음.
4. 실행이 없으면 고정 입력으로 `StartExecution` 호출.
5. ARN과 접수 시각을 DB에 저장한 뒤 반환. 이 반환이 있어야 후속 소비자가 ACK 가능.

| 상황 | 동작 |
|---|---|
| 동시에 같은 요청 접수 | 같은 DB 요청·실행명 사용 |
| AWS 응답 유실 | 같은 실행 조회. 확인되지 않으면 실패로 반환하고 재전달에서 재확인 |
| AWS 접수 후 DB 기록 실패 | 실패로 반환. 재전달에서 기존 실행 찾아 기록 복구 |
| 완료·실패한 실행 재전달 | 기존 ARN 반환. 재분석 금지 |
| 장기간 미확정 요청 | Step Functions 이름 보존 기간에 근접하면 신규 실행 금지. 운영자가 확인 |
| 예약 DB 장애 | 신규 실행 시작 금지 |

## 저장

`analysis_execution_requests` 한 테이블만 추가한다.

| 컬럼 | 의미 |
|---|---|
| analysis_id | 요청·분석 ID, 기본키 |
| source_event_id | 가격 사건 원본 ID. API는 null. 중복 불가 |
| input_json | 재사용할 고정 실행 입력 문자열. 최종 화면 JSON과 별개 |
| execution_arn | 예약한 실행 ARN. accepted_at이 있어야 접수 확인 완료 |
| created_at | 최초 예약 시각 |
| accepted_at | 실행 ARN 저장 시각 |

- DB 저장은 모델·화면 스키마에 영향을 주지 않음.
- 운영 API는 기존 읽기 계정으로 동작하므로 PR1에서 쓰기 권한을 확대하지 않음.
- PR1의 저장 경로는 로컬 DB 통합 테스트로 검증. 운영 연결은 후속 전용 접수 계정·IAM 작업에서 활성화.
- API의 기존 접수 경로도 공통 실행 함수로 변경하되, 저장 모듈 연결 전에는 기존 AWS·발행 기록 기반 중복 확인을 유지.

## 가격 선택

- 내부 가격 요청은 `trigger_id + generation + FIRE + ETF`가 모두 일치하는 원본 행 필수.
- 분석 시점이 원본으로부터 계산한 시점과 다르면 거절.
- 해당 트리거를 최신 관측으로 사용. 과거 관측은 해당 분봉보다 앞선 행만 최대 4개 사용.
- 더 늦은 트리거를 가져오거나 원본이 없을 때 최근 가격으로 대체하지 않음.
- 수동 API 분석은 기존 분석 시점 기준 조회 유지.

## 완료 검증

- 동시 접수, 입력 충돌, 응답 유실, 기록 실패 복구, 종료 실행 재전달.
- 실제 PostgreSQL에서 요청 예약의 고유성·불변 입력·ARN 기록 확인.
- 다른 종목·세대·REVERT·더 늦은 FIRE를 원본 가격으로 사용하지 않음.
- 외부 API·화면·모델 JSON 회귀 검사.

## SQS 소비 루프 — 연결 준비

`cloud.consumer.serve(sqs, queue_url, load_request, admission, stop)`가 기존 큐를
20초 long polling으로 한 건씩 읽는다. `load_request`는 원본 DB를 대조하는
`load_event_request`를 호출하고 연결을 닫아야 한다. `admission`에는 영속 요청
저장소가 반드시 연결되어야 한다.

1. 원본 메시지로 가격 요청 구성.
2. 공통 접수 함수로 DB 예약·SFN 접수·DB 기록 확인.
3. 정상 반환 후 해당 수신의 ReceiptHandle로 메시지 삭제.

- 원본 조회·접수·삭제 실패: 오류 유형만 기록. SQS 재배달에서 다시 확인.
- 같은 사건 재배달: 같은 실행을 재사용. 분석 완료까지 기다리지 않음.
- 조회·처리 실패: 5초 후 다음 수신 시도. 종료 요청은 대기 중에도 처리.
- 종료 요청: 새 접수를 시작하지 않음. 이미 시작한 접수는 기록·삭제까지 마침.
- 가격 복귀·미지원 사건: 삭제하지 않음. 이번 단계의 원본 조회기가 거절.
- 본문·인증정보·ReceiptHandle은 로그에 남기지 않음.

**아직 운영 큐에 연결하지 않는다.** 가격 복귀 회수, 공통 실행 제한, 접수 DB
권한을 연결하고 검증한 후 ECS의 실행 명령을 전환한다. 이 단계는 독립적으로
검증 가능한 소비 루프이며 실행 진입점·배포 설정은 추가하지 않는다.
기존 소비자는 큐 상태에 따른 0대 축소·재기동 설정을 유지한다.

## SQS·Step Functions 없는 호스트 (db 모드)

`ANALYSIS_QUEUE_TRANSPORT=db`(기본 `sqs`)면 같은 소비 루프가 SQS·SFN·ECS 없이 돈다
(`cloud/local.py`). 기본값에서는 위 계약이 그대로다.

1. 가격 사건: `dataset_commit_outbox`의 `price-explanation-realtime` 행을 직접 집는다. 집는 조건은 Relay와 같아 한 행을 둘이 동시에 쥐지 않는다. 접수를 마치면 `PUBLISHED`로 닫는다.
2. 접수: `analysis_execution_requests`에 예약만 한다. `execution_arn`은 `local:<analysis_id>`, `accepted_at`은 비워 둔다.
3. 실행: 같은 호스트에서 `cloud.worker`를 자식 프로세스로 띄운다. 동시 실행은 `ANALYSIS_SLOTS`건, 같은 ETF는 한 번에 한 건.
4. 종료: 자식 프로세스가 끝나면 종료 코드와 무관하게 `accepted_at`을 채운다. 이 모드에서 `accepted_at`은 "SFN 접수 시각"이 아니라 "실행 프로세스 종료 시각"이다.

| 상황 | 동작 |
|---|---|
| 접수 실패 반복 | 300초 뒤 다시 집힘. 16회를 넘기면 outbox 행을 `DEAD`로 닫고 `last_error=DB_MAX_RECEIVE` |
| 분석 프로세스 실패·강제 종료 | 다시 실행하지 않음. 요청당 프로세스 1회 |
| 1,100초 초과 | 프로세스를 중지하고 종료로 처리 |
| 호스트·소비자 재시작 | `accepted_at`이 빈 요청을 다시 실행. 완료된 분석은 워커가 반복하지 않음 |
| 전날(KST) 요청이 남음 | 실행하지 않고 닫음. 경고 로그에 `analysis_id` 기록 |
| 가격 복귀 | 기존과 같이 회수 기록 후 닫음. 분석 대기열과 무관하게 즉시 처리 |

| 장 마감 뒤 VM 정지 | `stop-minute-session`이 설명 사건과 끝나지 않은 요청이 빌 때까지 기다린 뒤에만 exit 0. 상한(기본 7,200초, `MINUTE_SESSION_ANALYSIS_DRAIN_TIMEOUT_SEC`)을 넘기면 exit 1. 성공하면 `batch_lane_success`에 (`minute_session_stop`, 그날 16:10 KST) 행을 쓰고, 감시가 그 행을 보고 VM을 내림 |

- 필요한 DB 권한: `V202611271200__allow_v2_outbox_claim.sql`(writer에 outbox 배달 상태 컬럼만). 이 테이블의 소유자는 data-pipeline이고, 이 권한은 단일 writer 원칙의 예외다([implementation.md](../implementation.md) §4에 기록).
- 자식 프로세스는 허용 목록의 환경변수만 물려받는다(`cloud/local.py` `CHILD_ENV`).

**대기 시간 산식.** 분석 1건은 약 588초다(AWS 실측, `analysis_autoscaling.tf` 주석. VM에서는 재지 않았다). 대기 N건을 슬롯 S개로 비우는 데 약 N × 588 ÷ S초가 걸린다. 슬롯 3이면 30건에 약 98분, 기본 상한 7,200초 안에는 약 36건이 끝난다. 2026-11-20에는 설명 사건이 분당 최대 63건 몰렸다. 그 규모면 슬롯 2~4로는 당일 안에 못 비우며, 그때는 상한에 걸려 exit 1이 난다. 슬롯 수와 "오래된 순" 실행 순서는 첫 장중에 재고 다시 정한다.

**`accepted_at`을 읽는 다른 쪽.** 운영 콘솔(`super-admin-api` `JdbcConsoleFactsRepository`의 `BOUNDARY_SQL`·`ANALYSIS_CHAIN_SQL`)은 `accepted_at`을 "실행이 시작된 시각"으로 읽는다. db 모드에서는 종료 시각이라 대기·실행 중인 요청이 "요청으로 이어지지 못함"으로, 실행 없이 닫은 전날 요청이 "요청됐는데 분석이 시작 안 됨"으로 집계된다. 콘솔은 OCI로 옮기지 않기로 해 그 쿼리는 고치지 않았다. db 모드의 DB를 콘솔로 보면 이 두 수치는 틀린다.
- 워커는 `ANALYSIS_EXECUTION_ARN` 없이 뜬다. 실행 수 제한과 ETF별 직렬화는 워커의 세션 잠금이 한 번 더 지킨다.

Refs: ALPHA-1143, ALPHA-1146
