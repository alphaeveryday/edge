# ADR-0063: ETF Orca 동기화를 Spring Batch 예약 태스크로 실행

- 상태: 승인됨
- 날짜: 2026-10-21

## 맥락
app-api 는 파이프라인 RDS 발행본을 앱 DB 로 옮기는 동기화를 API 프로세스 안에서 돌렸다. `@Scheduled`(10분)와 ShedLock(`pipeline-sync`)으로 여러 태스크 중 한 대만 실행했다. 이 구조에는 세 가지 문제가 있었다.

- API 프로세스의 파이프라인 DB 풀과 접속 정보 보유
- 실패가 스케줄러 로그에만 남는 구조(ETF 단위 실패는 로그만 남기고 성공 종료)
- application 계층의 adapter 직접 참조로 인한 계층 방향 위반 163건(멀티모듈 전환 트랙, ALPHA-1177)

동기화는 batch 하위 모듈의 Spring Batch 잡(`syncJob`)으로 이식됐다(#1484·#1485). 남은 결정은 그 잡을 언제·어디서 실행하느냐다.

## 결정
- **실행 형태:** EventBridge Scheduler 가 ECS RunTask 로 Fargate 태스크 1개를 띄운다. 태스크는 잡을 한 번 실행하고 종료한다. 상주 프로세스는 없다.
- **주기:** `rate(10 minutes)`. 기존 app-api 동기화 주기를 그대로 옮긴다. 원천별 갱신 주기에 맞춘 조정은 별도로 정한다.
- **클러스터:** worker 클러스터(`edge-dev-worker`). 0.5 vCPU·1GB(app-api 와 같은 JVM 크기).
- **이미지 리비전:** 배포 워크플로(`deploy-app-batch.yml`)가 새 이미지로 task 정의 리비전만 등록한다. 스케줄은 task 정의를 패밀리 이름으로 불러 RunTask 의 "리비전 미지정 시 최신 ACTIVE" 규칙으로 배포본을 실행한다. Terraform 의 baseline 이미지는 배포가 매번 옮기는 `latest` 태그다(ECR 수명 주기 보존). env·시크릿 변경은 TF 가 새 리비전으로 반영하고 이후 배포가 그 리비전을 복제한다.
- **잠금:** ShedLock 을 쓰지 않는다. 실행마다 새 JobInstance(`RunIdIncrementer`)이고, 중복 실행은 쓰기의 멱등성에 맡긴다.
- **메타 테이블:** `BATCH_*` 는 앱 DB 스키마 소유자인 app-api Flyway(V22)가 만든다. batch 는 스키마를 만들지 않는다.
- **실패 신호:**
  - 실행 실패: 컨테이너 종료 코드 ≠ 0 또는 기동 실패를 EventBridge 규칙이 운영 알람 토픽으로 보낸다. 잡은 반영 0건·필수 단계 실패에서 FAILED 로 끝난다.
  - 제출 실패: RunTask 제출이 재시도를 소진하면 DLQ 로 가고, 도착 수 알람이 울린다.
  - 미실행: 잡 완료 로그(COMPLETED)가 30분 동안 0회면 알람이 울린다(결측은 0 으로 채워 평가). RunTask 가 200 응답의 `failures` 로 태스크를 못 띄우는 경우처럼 위 두 경로에 안 잡히는 누락을 덮는다. 스케줄이 켜져 있을 때만 만든다.

## 대안
- **app-api 안에서 Spring Batch 실행(`@Scheduled` + ShedLock):** 배포 단위가 분리되지 않고 파이프라인 접속 정보가 API 에 남는다.
- **상주 batch 앱 + `@Scheduled`:** 여러 대면 ShedLock 이 다시 필요하고, 10분 중 대부분을 놀면서 상시 과금된다.
- **Step Functions 오케스트레이션(analysis-v2 선례):** 단일 태스크 1회 실행이라 상태 머신이 더하는 것이 없다.
- **리비전 고정 ARN 을 배포 때마다 스케줄에 갱신:** 배포 역할에 스케줄 수정 권한이 늘고, 배포와 스케줄 사이 경합이 생긴다.

## 결과
- **비용:** 실행할 때만 과금된다. 실행마다 JVM 기동 시간이 든다(실측은 첫 dev 실행에서).
- **중복 실행:** 순차 중복은 결과를 바꾸지 않는다. 모든 쓰기가 "바뀐 행 한정 upsert"와 "원천 밖 행 삭제"이기 때문이다(`SyncJobTest` 재실행 단언). 두 실행이 겹쳐 순위 단계에 동시에 들어가면, 늦은 쪽이 `etf_rank` PK 충돌로 실패해 알람이 오탐될 수 있다. 데이터는 먼저 커밋한 쪽의 결과로 온전하다.
- **TF 소유 설정 변경:** env·시크릿·cpu·memory·역할을 바꾸면 TF 가 baseline(`latest`)으로 새 리비전을 만들어 최신 ACTIVE 가 된다. `latest` 가 마지막 배포 이미지라 실행 이미지는 그대로다.
- **롤아웃 순서:**
  1. foundation apply(`edge/app-batch` ECR)
  2. 머지와 dev apply(스케줄 DISABLED, app-api task 정의의 파이프라인 env 제거)
  3. app-api 배포(동기화 꺼짐)
  4. batch 첫 배포와 수동 RunTask 확인
  5. 스케줄 활성화
- **후속:** app-api 의 sync 코드 제거 PR 에서 app-api 의 파이프라인 시크릿 권한과 파이프라인 RDS 인바운드 규칙을 뺀다.
