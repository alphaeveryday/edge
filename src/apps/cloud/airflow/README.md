# airflow — 유한 배치의 Airflow 실행 경로

유한 배치(SFN 5개)의 **실행 관리**를 레인별로 Airflow로 옮긴다. 업무 실행은 그대로 `data-pipeline`의 ECS 태스크 정의와 `data_pipeline.run` 명령이 맡는다. 상주 분 수집기와 SQS 소비자는 대상이 아니다.

현재 상태: 첫 레인인 장중 수급(`edge_investor_intraday`)을 로컬에서 검증했다. 실행 환경은 ECS on EC2 자체 운영으로 정했다(ALPHA-1119, 아래 "실행 환경"). **2026-09-29 밤, t4g.micro + 기존 RDS 로 실제 AWS 단기 검증을 한다**(아래 "실제 AWS 단기 검증"). 장중 수급 정기 DAG 활성화와 SFN → Airflow 전환은 하지 않았다(별도 승인).

## 구성

| 파일 | 역할 |
|---|---|
| `dags/edge_batch.py` | 레인 공통 연결 코드. `EdgeStep`(ECS 실행과 exit code 해석), 슬롯과 `pipeline_run_id` 파생 |
| `dags/edge_investor_intraday.py` | 장중 수급 DAG: `plan → collect → normalize → load → verdict` |
| `dags/edge_source_daily.py` | 원천 관측 DAG(ALPHA-1130, Airflow 전용 레인 `source-daily`): `plan → {매크로·업종·재무} 각각 collect → normalize → load → report → verdict`. 생성 시 pause — 아래 "원천 관측 레인" |
| `tests/` | DagBag 파싱, terraform(슬롯·명령) 대조, exit code·당일 수집·재처리·보고·활성화 규칙. CI `test-airflow.yml`이 공식 Airflow 이미지(다이제스트 고정) 안에서 실행한다 |
| `dags/edge_investor_intraday_verify.py` | 격리 검증 DAG — 운영 DAG 와 같은 `build_dag`·EdgeStep, 검증 전용 클러스터·태스크 정의, conf 장애 주입. `EDGE_VERIFY_CLUSTER` 가 있을 때만 등록 |
| `Dockerfile` · `deploy/entrypoint.sh` | 배포 이미지(공식 이미지 + DAG). entrypoint 가 메타DB 연결·UI 비밀번호 파일을 만든다 |
| `deploy/compose.local.yaml` | 배포 이미지의 로컬 리허설(ECS 태스크와 같은 세 컨테이너·localhost 공유, 마이그레이션 선행) |
| `verify/` | 실제 AWS 격리 검증 — `shim.py`(검증 태스크 진입점: 재생 입력·계수·장애), `Dockerfile`(배포된 업무 이미지 + shim), `run.py`(설정·trigger·증거 수집) |
| `local/` | 로컬 비교 환경. ECS·SNS·SFN 대역, 배포된 ASL 해석기, 저장 입력 재생 |
| `requirements.txt` · `requirements-test.txt` | Airflow 3.3.2, amazon provider 9.36.0 · pytest(테스트 전용) |

원장 쪽 변경은 `data-pipeline`에 있다.
- `ops_pipeline_run.orchestrator`(SFN|AIRFLOW)와 `orchestrator_run_ref` 컬럼
- Planner가 소유 충돌을 거부한다
- Reconciler가 Airflow 런을 원장 attempt로 대조하고, DAG가 보고한 판정을 기록한다
- wrapper에 실행권(`OPS_EXCLUSIVE_STEP`)·성공 skip(`OPS_SKIP_IF_SUCCEEDED`) 가드를 추가했다
- 실행권을 얻어도 같은 작업의 앞선 시도 종료가 원장에 확인되지 않으면 보류한다(exit 76, `EXECUTION_HOLD` 이슈)
- 추적 컬럼 `ops_task_attempt.orchestrator_attempt_ref`, `ops_pipeline_run.orchestration_reported_at`

## 실행 계약

- **run_id.** `plan`이 `plan-run`을 부를 때 `OPS_ORCHESTRATOR=AIRFLOW`, `OPS_SCHEDULED_TIME=<슬롯>`, `OPS_ORCHESTRATOR_RUN_REF=<dag_id>/<run_id>`를 넘긴다. Planner는 SFN 경로와 **같은 run_key·run_id**로 계획만 남기고 SFN은 시작하지 않는다. 스텝은 모두 `--run-id <그 run_id>`로 돈다.
- **런 상태(`orchestration_status`).** SFN 런은 DescribeExecution이 채운다. Airflow 런은 다음 규칙으로 채운다.
  - DAG가 끝나며 `report` task(ops `reconcile`, `OPS_RUN_KEY`+`OPS_ORCHESTRATION_STATUS`)로 자기 판정을 보고한다. 원장은 값과 보고 시각(`orchestration_reported_at`)을 함께 남긴다.
  - `plan`이 성공하지 않은 run(소유 충돌·재처리 불가·인프라)은 판정을 보고하지 않는다. 업무를 건드리지 않았으므로 그 슬롯의 기존 판정을 덮지 않는다.
  - 주기 Reconciler는 보고가 없거나, 보고 뒤에 새 업무 시도가 생겼을 때만 원장 증거로 다시 투영한다. 투영은 **결론이 난 증거로만** 한다. 전부 FULFILLED이고 최신 exit 0이면 SUCCEEDED, 그렇지 않으면 FAILED다. 열린 작업(미실행·종료 코드 미확인)이 있으면 값을 바꾸지 않는다.
  - 그래서 일시적 조회 실패를 종료로 단정하지 않는다. hard deadline 뒤에도 비어 있으면 콘솔 R02가 "마감 초과 미귀결"로 드러낸다.
  - 주기 Reconciler가 락을 쥔 동안 온 보고는 75로 끝나 재시도된다. 보고가 끝내 실패하면 `verdict`가 런을 실패로 닫는다.
- **활성화 시 지난 슬롯을 돌리지 않는다.** 3.3.2 `CronTriggerTimetable`은 `run_immediately=False`를 "다음 슬롯까지 대기"로 문서화했다. 그러나 코드는 슬롯 뒤 max(다음 발화까지 간격의 10%, 5분) 안이면 그 슬롯을 즉시 만든다. 하루 1회 cron이라 창이 2.4시간이다. 로컬 관찰: 11:25 슬롯이 11:45 unpause에 즉시 실행됐다. 그래서 `run_immediately=timedelta(0)`을 쓴다(테스트가 실제 timetable로 확인).
- **exit code.** 해석 규칙은 `EdgeStep`·`ecs_verdict` 독스트링과 아래 "재시도·재접속·보류 정책"에 있다. 요약하면 다음과 같다.
  - 0은 성공이다.
  - 정제·적재의 2는 부분 실패다. 현재 구현은 하류를 계속 돌리고 verdict에서 런을 실패로 마감한다. 기존 SFN은 원래 같은 규칙("exit 2면 계속")을 선언했지만 `runTask.sync`의 실패 경로가 exit를 싣지 않아 도달하지 못했고, #958(ALPHA-1113, 2026-09-28 dev 머지)이 그 경로를 고쳐 **지금은 두 경로가 같게 동작한다.** 정책 자체는 여전히 팀 결정 항목이다(아래 "활성화 전 결정").
  - 컨테이너가 스스로 끝낸 그 밖의 비0은 업무 실패다. Airflow가 재시도하지 않는다.
  - 75(업무 미시작)와 기동 실패만 재시도한다. exit code가 없거나 외부 종료(StopTask 등)로 끝난 것은 "결과 미상"이라 **보류**한다. "종료 코드 없음 = 인프라 실패 = 재시도"로 다루지 않는다.
- **중복 실행.** 다섯 경우를 구분한다.
  - **같은 슬롯에 실행 주체가 둘:** Planner가 `INSERT … ON CONFLICT (run_key)`로 계획을 하나만 만든다. 뒤에 온 주체는 LAUNCH_CONFLICT로 실행하지 않는다.
  - **같은 스텝이 아직 실행 중:** 업무 스텝은 컨테이너 안에서 작업별 실행권(PostgreSQL 세션 advisory lock, 키=task_key)을 잡은 뒤에만 실행한다(`OPS_EXCLUSIVE_STEP`). 이미 잡혀 있으면 최대 600초 기다린다.
    - 프로세스가 죽으면 커넥션이 끊겨 lock이 스스로 풀린다.
    - 키가 task_key라 다른 슬롯의 같은 스텝도 겹치지 않는다. 같은 파티션을 병합하기 때문이다.
    - **lock을 얻었다고 앞 실행이 끝난 것은 아니다.** lock 커넥션만 끊겨도 lock은 풀린다. 그래서 lock을 얻은 뒤 같은 task_key(모든 run·모든 주체)에 원장상 RUNNING인 시도나 운영자 해제 전 ECS 보류가 있으면 업무를 시작하지 않고 76으로 보류한다. 시작 기록은 **lock을 쥔 세션에서** 커밋한다. 커밋됐다면 그 순간 lock을 쥐고 있었으므로, 다음 실행은 lock을 얻어도 그 기록을 보고 멈춘다.
  - **이미 성공한 스텝(수집만):** 실행권 **안에서** 최신 attempt가 exit 0이면 실행 없이 0으로 끝난다(`OPS_SKIP_IF_SUCCEEDED`). 정제·적재는 외부 호출이 없고 멱등이라 성공 이력이 있어도 다시 돈다. 다른 슬롯이 같은 파티션을 갱신한 뒤 적재를 복구하려면 정제가 다시 돌아야만 하기 때문이다. provider의 `reattach`는 RUNNING ECS 태스크만 찾으므로, 끝난 태스크의 응답을 잃은 재시도는 새 태스크를 띄운다.
    - 단, 같은 run의 앞 단계(raw < normalize < feature)에 그 성공보다 나중에 만들어진 attempt가 있으면 입력이 바뀐 것이라 다시 실행한다. 예: 수집 실패 → 빈 입력 정제 성공 → 수집 복구. 순서는 DB 시계(`created_at`)로 판정한다.
    - 실행 전에 attempt 시작을 기록하지 못하면 실행하지 않는다(75). 흔적 없는 실행이 생기면 다음 재시도의 skip이 더 오래된 성공을 믿고, Reconciler는 실제로 돈 작업을 MISSED로 찍는다.
  - **의도적 재처리:** 실행권만 잡고 성공 skip은 끈다.
  - **상태를 확인할 수 없음:** 원장 미설정·접속 실패, 기대 작업 없음, 실행 이력 조회 실패, 시작 기록 실패 중 하나라도 해당하면 **실행하지 않고** exit 75로 끝난다. Airflow는 이를 인프라 실패처럼 재시도한다.
    - env가 없는 SFN·수동 경로는 종전대로 원장 장애에도 작업을 진행한다.
- **막지 못하는 것.**
  - KIS에는 멱등 키가 없어 **정확히 한 번 호출은 보장하지 않는다.** 수집이 외부 호출을 끝낸 뒤 attempt 종료를 기록하기 전에 죽으면 결과 미상 보류가 된다. 운영자가 다시 돌리면 다시 호출한다.
  - lock 커넥션만 끊기고 **이미 업무를 시작한** 프로세스는 계속 쓴다. 보류는 새 업무의 시작을 막을 뿐이고, 저장소가 오래된 작업자의 쓰기를 거부하지는 않는다(fencing·CAS 없음, ALPHA-1057).
  - SFN 경로와 수동 ECS 실행은 실행권·보류 확인을 하지 않는다. 그래서 두 주체의 동시 실행은 Planner 소유 검사와 전환 절차의 종료 확인으로만 막는다(아래 "보호 범위").
- **당일 수집.** 장중 추정 API에는 날짜 인자가 없다. 그래서 `plan`과 `collect`는 슬롯 날짜가 오늘(KST)이 아니면 실패한다. backfill이나 과거 날짜로 trigger하면 원장에 아무것도 남지 않는다.
- **재처리.** 기존 슬롯의 raw를 다시 정제·적재하려면 conf `{"reprocess_slot": "<슬롯 ISO>"}`로 trigger한다. `plan`이 `OPS_REPROCESS=1`로 **계획이 있고 raw 단계가 끝난 슬롯인지** 확인한다. 없는 슬롯(오타)이나 수집 실패 슬롯은 거부해, 빈 입력 정제가 성공으로 끝나지 않게 한다. 이때 `collect`는 ECS를 띄우지 않는 성공 no-op이고, 정제·적재의 가드는 해제된다. skip으로 두면 하류까지 skip이 전파된다(3.3.2 로컬 관찰). 같은 DAG의 `max_active_runs=1`이 정기 run과 직렬화해 준다.

## 재시도·재접속·보류 정책(초기 운영)

**선택.** 이번 첫 이관은 CAS·fencing을 선행 조건으로 두지 않는다. 대신 실행 상태가 불명확하면 자동 재실행을 멈추고, 기존 작업의 종료를 확인한 뒤에만 다시 돌린다. 자동 복구 일부를 포기하고, 장애 시 수집 공백을 받아들이는 결정이다. 자동 복구·동시 실행을 늘리려면 ALPHA-1057(쓰기 세대·CAS)을 먼저 해결해야 한다.

**provider 실제 경로(amazon 9.36.0 `EcsRunTaskOperator.execute`, 코드 확인).**
- `reattach=True`면 제출 전에 `startedBy=uuid(dag_id, task_id, run_id, map_index)`로 `ListTasks(desiredStatus=RUNNING)`만 조회한다. 같은 task instance의 모든 try가 같은 `startedBy`를 쓴다.
- `RunTask`에 멱등 토큰을 붙이지 않는다. 그래서 boto의 자동 재전송(연결 끊김·5xx)이 태스크를 하나 더 만들 수 있다.
- 대기 오류(`WaiterError`)가 나면 기본값(`stop_task_on_failure=True`)으로 **도는 태스크를 StopTask로 죽인다.** `on_kill`(run failed 표시·dagrun_timeout)도 StopTask를 부른다. StopTask는 종료 요청일 뿐 종료 확인이 아니다.

`EdgeStep`은 이 경로를 다음처럼 바꿨다. 재접속·조회는 `_try_reattach_task`에서, 제출은 `_start_task`에서, 판정은 `execute`에서 한다. `stop_task_on_failure=False`로 두고 `on_kill`도 StopTask를 부르지 않게 바꿨다(Airflow가 task를 끝내도 업무 컨테이너는 끝까지 돈다). `RunTask`에는 시도·제출별 `clientToken`을 붙인다.

| # | 경우(판별 근거) | 처리 | 새 ECS 태스크 |
|---|---|---|---|
| 1 | 제출 전 실패: 첫 시도의 ECS 조회 실패(3회) | 제출하지 않고 실패로 끝낸다. 이 run의 태스크는 있을 수 없다(`startedBy`가 run마다 다르다). 원장엔 미실행으로 남는다 | 없음 |
| 2 | ECS가 기동 실패를 확정: `RunTask` failures(배치 거부) | 같은 시도 안에서 새 토큰으로 최대 3회 다시 제출한다 | 확정 뒤에만 |
| 2′ | 컨테이너가 안 떴다: `stopCode=TaskFailedToStart` · 업무 미시작: exit 75 | Airflow 재시도. 다음 시도가 그 태스크를 보고 새로 띄운다 | 다음 시도 |
| 3 | 제출 여부 불명: `RunTask` 응답 유실(연결 끊김·타임아웃·5xx), Throttling(boto가 먼저 보낸 같은 토큰 요청이 태스크를 만들었을 수 있다) | 다시 제출하지 않는다. `startedBy`로 앞서 없던 태스크를 찾아(3회) 붙는다. 못 찾으면 **보류(ECS_STATE_UNKNOWN)** | 없음 |
| 3′ | 앞 시도가 있었는데(try≥2) ECS에 아무것도 안 보임 · 둘째 시도부터의 조회 실패 · 살아 있는 태스크가 둘 이상 | **보류(ECS_STATE_UNKNOWN).** 빈 조회는 "제출하지 않았다"는 증거가 아니다(조회 지연·기록 만료와 구분 불가) | 없음 |
| 4 | 실행 중 태스크 확인: `lastStatus≠STOPPED`(종료 중 포함) | 재접속해 끝날 때까지 기다린다. 대기가 실패하면 태스크는 두고 재시도해 다시 붙는다. 마지막 시도에서도 끝을 못 보면 **보류(ECS_STATE_UNKNOWN)** — 도는 태스크를 두고 "실패"로 닫지 않는다 | 없음 |
| 5 | 종료·업무 결과 확인: exit 0 / 부분 2 / 그 밖 비0(컨테이너가 스스로 종료) | 이 시도의 태스크면 그 결과로 끝낸다(0·2는 성공 처리, 비0은 재시도 없는 실패). 앞 시도의 태스크면 요청 종류로 가른다. **자동 재시도**(판정 조회 실패·대기 오류·worker 중단 뒤)는 그 결과를 쓴다 — 끝난 업무를 반복하지 않는다. **clear 뒤 첫 시도**(다시 돌리려는 요청)는 수집의 성공만 재사용하고(외부 재호출 없음), 나머지는 종료가 확인됐으므로 새로 띄운다 — 옛 결과를 쓰면 바뀐 입력(재수집 raw·보고할 보류)이 반영되지 않는다. 둘은 `try_number == max_tries - retries + 1`로 가른다(clear가 `max_tries`를 다시 잡는다, 3.3.2 코드 확인). 단 **실행 중인 task를 clear**하면 Airflow가 `max_tries`를 다시 잡지 않아 자동 재시도로 다뤄진다(끝난 결과를 재사용 — 안전 쪽). 다시 돌리려면 끝난 뒤 clear한다 | 자동 재시도: 없음. clear: 수집 성공 재사용 외에는 새로 |
| 6 | 종료됐지만 결과 불명: exit 없음, 외부 종료(StopTask·호스트 중단·Spot)의 비0 | **보류(RESULT_UNKNOWN).** 업무 도중 끊겼을 수 있다 | 없음 |
| — | 컨테이너가 원장 보류로 업무를 시작하지 않음: exit 76 | **보류(OPEN_ATTEMPT).** clear로 다시 돌리면 새 컨테이너가 원장 확인을 다시 거친다 | clear 때만 |

- 선행 스텝의 결말이 확인되지 않으면 하류 업무 스텝은 시작하지 않고 skip한다. 확인된 결말은 셋이다: ECS가 보고한 exit code(XCom `exit_code`), 보류(`hold`), 살아 있는 태스크가 없다는 표시(`no_ecs_task` — 재처리 no-op·당일 거부·제출 전 실패). 보류이거나 셋 다 없으면(마지막 시도 중 worker 사망·수동 failed 표시) 그 ECS 태스크가 아직 입력을 쓰고 있을 수 있다. 정제의 trigger rule(수집이 업무 실패여도 정제)은 결말이 확인된 실패에만 적용된다. `report`는 예외다(보류를 원장에 옮긴다).
- 보류는 `HoldExecution`(`AirflowFailException`)이다. Airflow가 자동 재시도하지 않는다. XCom `hold`에 종류·사유를 남기고, `report`가 그것을 원장에 옮긴다(`OPS_EXECUTION_HOLDS`). `verdict`는 "실행 보류"로 런을 실패시킨다. 결손·업무 실패와 섞지 않는다.
- 원장 표현은 기존 구조를 쓴다. 이슈 `EXECUTION_HOLD`(evidence.kind로 종류 구분)와, 시도가 없는 작업이면 `task_outcome=FAILED`·`outcome_reason=EXECUTION_HOLD`다. 그래서 hard deadline 뒤 MISSED(미실행)로 바뀌지 않는다. 이미 결론 난 작업(성공한 슬롯의 재처리)의 outcome은 덮지 않는다.

**새 실행을 막는 위치(책임).**

| 경계 | 무엇을 보나 | 막는 것 |
|---|---|---|
| Airflow, 제출 전(`EdgeStep`) | 같은 task instance(모든 try·clear)의 ECS 태스크 | 재시도·clear가 살아 있거나 결과를 모르는 태스크 옆에 새 태스크를 띄우는 것 |
| 컨테이너, 업무 시작 전(wrapper, lock 세션) | 같은 task_key(모든 run·슬롯·주체)의 원장 RUNNING 시도, OPEN `EXECUTION_HOLD`(ECS_STATE_UNKNOWN) | 다른 슬롯·재처리·수동 trigger가 끝나지 않은 실행과 같은 파티션을 쓰는 것. lock 커넥션을 잃은 A가 계속 도는 동안 B가 시작하는 것 |
| Airflow DAG | `max_active_runs=1` | 이 DAG의 두 run 동시 진행(정기·재처리) |

- 새 업무 시작이 다시 허용되는 길은 셋뿐이다. ① 그 시도가 스스로 종료를 기록한다. ② Reconciler가 ECS `STOPPED`를 확인해 시도를 닫는다. 컨테이너가 스스로 끝냈으면 그 exit로(비0이면 FAILED·`attempt_failed`) 닫는다. exit가 없거나 외부 종료(`stopCode`가 `EssentialContainerExited`가 아닌 비0, 예: StopTask 137)면 FAILED로 닫되 `outcome_reason=stopped_result_unknown`으로 업무 실패와 가른다(exit 값은 있으면 그대로 남긴다). 이 변경은 SFN 레인의 Reconciler에도 같이 적용된다. ③ 운영자가 종료를 확인하고 ECS 보류 이슈를 RESOLVED로 바꾼다. **시간이 지났다는 이유로는 풀리지 않는다.** 이슈 행 자체의 정리는 ⑥ 설명을 따른다.
- ECS 조회가 늦게 반영되거나 만료될 수 있다(멈춘 태스크는 최소 약 1시간 조회된다). 그래서 확정적인 종료 증거가 없으면 가용성보다 보류를 택한다.

**미확정 실행을 원장이 스스로 찾는다(DAG 의 마지막 task·callback 이 돌지 않아도).**
- **결말 없이 끝난 스텝(worker 사망·수동 failed).** 모든 시도는 시작할 때 XCom `edge_started`를 남긴다. 시작했는데 결말(`exit_code`·`hold`·`no_ecs_task`)이 없으면 verdict·report는 그 스텝을 `ECS_STATE_UNKNOWN` 보류로 읽는다. 확정된 업무 실패(exit 1)는 보류가 아니다. report가 원장에 옮긴다.
- **report 도 못 도는 경우(`dagrun_timeout`).** 주기 Reconciler(EventBridge 15분, 야간·휴장일 포함)가 슬롯 대조와 별개로 `sweep_airflow_runs`를 돈다.
  - ① 원장에 끝나지 않은 시도가 남은 Airflow 런(과거 슬롯 재처리 포함 — 슬롯 대조는 최근 예정일만 본다)을 대조한다. ECS 종료 증거로 닫고, 수명(`OPS_AIRFLOW_RUN_LIFETIME_SECONDS`, 기본 1800초 > dagrun_timeout)을 넘도록 안 끝난 시도는 `OPEN_ATTEMPT` 보류로 남긴다(종료 증거로 닫히면 자동 해결). 결과 미상으로 닫힌 시도는 `RESULT_UNKNOWN`으로 남긴다.
  - ② ECS에서 이 레인 명령(`--run-id <Airflow 런>`)으로 수명보다 오래 살아 있는데 원장에 시도가 없는 태스크(시간 초과 당시 PENDING)를 찾아 `ECS_STATE_UNKNOWN` 보류로 남긴다. 그 태스크가 늦게 떠도 게이트에서 자기 보류를 보고 76으로 멈춘다. ECS 조회 실패는 "없음"으로 읽지 않고 기록 없이 드러낸다.
  - 권한: Reconciler 역할에 `ecs:ListTasks`(같은 클러스터 한정)를 더했다.
- **늦게 도착한 옛 보고.** report는 자기 DAG run(`OPS_REPORT_RUN_REF`)을 함께 넘긴다. 이 런의 최신 업무 시도가 다른 DAG run의 것이면 판정을 기록하지 않는다(보류 기록은 사실이라 남긴다).
- **같은 증거에 같은 결론.** ECS 종료 증거 분류(`ecs_stop_evidence` ↔ `reconciler._ecs_stop_evidence`)는 한 규칙이고, 공용 사례표 `tests/ecs_stop_cases.json`으로 두 쪽 테스트가 대조한다. exit 0이면 업무 완료다. exit가 없거나, 128 이상(신호: SIGKILL·OOM 137, SIGTERM 143)이거나, 외부 종료 stopCode의 비0이면 결과 미상이다. 그 밖의 비0(stopCode 없음 포함)은 스스로 끝난 업무 결과다. stopCode가 없다는 것만으로 재시도·보류를 정하지 않는다.
- **남는 경계.** ②는 수명(30분) + 주기(최대 15분) 뒤에야 기록한다. 그 사이 PENDING이던 태스크가 먼저 떠서 원장 게이트를 통과하면(다른 끝나지 않은 시도가 없을 때) 늦게 한 번 실행된다(동시 실행은 아니다 — lock·게이트가 직렬화한다). 과거 슬롯 수집은 그 전에 `same_day_only`가 막지만 같은 날의 늦은 수집은 그 슬롯 run_id로 저장된다. 저장소 쪽 차단(fencing·CAS)이 없어서 남는 한계다.

**보호 범위 — 장중 수급 canonical 파티션(`canonical/…/investor_flow_intraday/market=KR/trade_date=…`)에 쓰는 경로.** 쓰는 코드는 `normalize-investor-estimate` 하나다. 적재(`load-investor-intraday`)는 그 파티션을 읽어 DB에 쓴다.

| 쓰기 경로 | 코드가 막는 것 | 운영 절차에 맡기는 것 |
|---|---|---|
| Airflow 재시도·clear | 제출 전 확인 + 컨테이너 게이트 | — |
| 같은 날 다른 슬롯(Airflow) | DAG `max_active_runs=1` + 컨테이너 lock·게이트 | — |
| 의도적 재처리(Airflow `reprocess_slot`) | 같은 것. ECS 보류가 OPEN이면 재처리도 76 | 해제는 운영자 확인 뒤 |
| 기존 SFN | Airflow 컨테이너는 SFN 시도가 RUNNING이면 보류한다(원장 기록이 있을 때) | SFN 쪽은 확인하지 않는다 → **한 번에 한 주체만** 켠다(전환 절차 ①~⑥) |
| 수동 ECS 실행(README 레시피, env 없음) | 없음 | 보류 중엔 실행하지 않는다. 실행해야 하면 `OPS_EXCLUSIVE_STEP=1`을 붙여 같은 게이트를 거친다(ECS 안에서만 — 태스크 ARN이 없으면 75로 실행하지 않는다) |
| 로컬·셸에서 직접 실행(`python -m data_pipeline.run`) | 없음 | 운영 파티션에 쓰지 않는다 |
| 이미 업무를 시작한 오래된 작업자 | 없음(fencing·CAS 없음) | 종료 확인 전 새 주체·재실행 금지. 저장소 차단은 ALPHA-1057 |

**보류 해제와 수동 복구(운영자).** 순서를 지킨다.
1. **새 실행 생성 중단.** DAG pause(스케줄만 멈춘다 — 도는 ECS를 멈추지 않는다). SFN 쪽이면 스케줄 DISABLED.
2. **이전 실행 식별.** 원장 `EXECUTION_HOLD` 이슈(⑥ — 컨테이너 보류는 evidence.blocking에 막은 시도의 ARN·run, Airflow 보류는 reason·run_key), Airflow 보류 task의 XCom `hold`·로그, `startedBy`로 ECS 조회, SFN 실행.
   ```bash
   aws ecs list-tasks --cluster <cluster> --started-by <startedBy> --desired-status RUNNING   # STOPPED 도
   aws ecs describe-tasks --cluster <cluster> --tasks <arn...>   # lastStatus·stopCode·exitCode
   ```
3. **같은 파티션의 기존 작업 종료 확인.** 아래 "종료 확인" ①~⑥ 전부. `lastStatus=STOPPED`가 종료 증거다. StopTask 요청이나 DAG pause는 증거가 아니다. ECS 기록이 이미 만료돼 조회되지 않으면 태스크 로그 스트림의 끝과 CloudTrail `StopTask`/`RunTask`로 확인한다. 확인할 수 없으면 여기서 멈추고 수집 공백을 받아들인다.
4. **완료된 산출물·원장 확인.** 그 슬롯의 raw·canonical manifest·원장 attempt(exit·records_out)·`investor_flow_intraday` 행.
5. **재실행 범위 결정.**
   - raw가 있고 정제·적재만 필요하면 → 재처리 run(conf `reprocess_slot`).
   - 수집이 필요하면 → 다시 수집하지 않고 공백을 받아들인다. 이 API는 그날 누적이라 다음 슬롯이 대부분 회수한다. 지난 슬롯을 다시 수집하면 현재 응답이 그 슬롯으로 저장된다. 날짜가 다른 슬롯은 코드가 거부하고(`same_day_only`), 같은 날 지난 슬롯은 이 절차로 금지한다.
6. **보류 해제와 재실행.**
   - 원장 RUNNING 시도: Reconciler가 ECS STOPPED로 닫는다(`OPS_RUN_KEY=<run_key>`로 reconcile 1회). 닫히지 않으면(ECS 기록 만료) 3의 증거를 적어 그 시도 한 행만 종료로 고친다:
     ```sql
     UPDATE ops_task_attempt SET execution_status='FAILED', finished_at=now(),
            failure_reason='OPERATOR_CONFIRMED_STOPPED: <증거 요약>'
      WHERE attempt_id = :'attempt_id' AND execution_status = 'RUNNING';
     ```
   - ECS 보류 이슈: 3을 확인한 뒤에만 RESOLVED로 바꾼다:
     ```sql
     UPDATE ops_reconciliation_issue SET status='RESOLVED', resolution_source='operator',
            resolution_reason='operator_confirmed_stopped: <증거 요약>', updated_at=now()
      WHERE dedupe_key = :'dedupe_key' AND status = 'OPEN';
     ```
   - 그다음 5에서 정한 run을 trigger한다. 같은 task instance의 clear로는 풀리지 않는다(ECS 기록이 결과 미상이거나 없으면 다시 보류한다).
- **clear를 쓸 수 있는 경우.** 앞 시도의 ECS 태스크가 아직 조회될 때(멈춘 뒤 약 1시간)만 clear가 그 결과를 보고 판단한다. 첫 시도가 **제출 전에** 실패한 경우(ECS 조회 실패·RunTask 4xx·배치 거부 반복)나 기록이 만료된 뒤에 clear하면, 둘째 시도는 아무것도 안 보여 ECS_STATE_UNKNOWN으로 보류하고 그 스텝을 레인 전체에서 막는다. 선행 스텝 결말 미확인으로 **skip된** 스텝을 clear해도 같다(그 스텝은 제출한 적이 없다). 이런 복구는 clear가 아니라 새 run(정제·적재는 재처리 run)으로 한다.
7. **정상 스케줄 재개.** DAG unpause. 다음 cron 슬롯부터 돈다(지난 슬롯을 돌리지 않는다).

금지: 잠금 행·원장 행을 **삭제**해 실행 가능하게 만들기, DAG pause를 기존 ECS 종료 확인으로 대신하기, 상태를 확인할 수 없는데 "충분히 기다렸다"는 이유로 재실행하기, 과거 슬롯을 다시 수집하기.

**대가(수집 공백).** 보류된 수집 슬롯은 비고, 마지막 14:35 슬롯은 다음 슬롯의 회수도 없다. 원장에 RUNNING 시도가 남거나 ECS 보류(ECS_STATE_UNKNOWN)가 열린 스텝은 **같은 task_key의 이후 모든 run**(다음 슬롯·다음 날·재처리)이 76으로 멈춘다. 운영자가 풀기 전까지다 — 파티션 겹침을 막는 대신 치르는 비용이다. 결과 미상(RESULT_UNKNOWN)은 종료가 확인된 것이라 다음 run을 막지 않는다.

## 실행 추적

| 알고 싶은 것 | 원장에서 | Airflow에서 |
|---|---|---|
| Airflow run이 처리한 업무 슬롯 | `ops_task_attempt.orchestrator_attempt_ref LIKE 'airflow:<dag_id>/<run_id>/%'` → expected_task → run. 최초 계획 run은 `ops_pipeline_run.orchestrator_run_ref` | 재처리 run은 conf `reprocess_slot` |
| 재시도·clear·재처리에서 어느 ECS 시도가 돌았나 | attempt 행마다 `ecs_task_arn`과 그 ECS를 띄운 `.../<task_id>/<try_number>` | 각 try의 XCom `ecs_task_arn`과 task 로그 |
| 실행 중 ECS에 재접속 | 새 attempt가 **생기지 않는다**. 원장의 참조는 처음 띄운 try를 가리킨다 | 그 try의 로그 "실행 중인 ECS 태스크에 재접속"과 XCom `ecs_reattached_arn` |
| 성공 이력으로 건너뜀 | `record_source='DUPLICATE_SKIP'` 행(그 컨테이너의 ARN·try 참조, exit 0). 이후 판단의 "최신 업무 시도"에서는 빠진다 | 그 try는 성공 |
| 실패한 attempt → Airflow | `orchestrator_attempt_ref`의 dag_id·run_id·task_id·try_number로 Airflow UI의 task 시도를 연다 | — |
| 보류 | `ops_reconciliation_issue` `EXECUTION_HOLD`(scope task, evidence.kind·blocking·run_key). 시도 없는 작업은 `outcome_reason=EXECUTION_HOLD` | 보류된 try의 XCom `hold`, 로그 "실행 보류(<종류>)", verdict "실행 보류" |

- Airflow 재시도 번호와 ECS 태스크는 1:1이 아니다. 재접속 try는 ECS를 새로 띄우지 않고, 응답 유실 재시도는 새 ECS를 띄운다. 그래서 원장의 참조는 "그 ECS를 띄운 try"만 뜻한다.
- SFN 런과 과거 행은 이 컬럼이 NULL이고, 종전대로 `sfn_execution_arn`·`sfn_state_name`으로 잇는다.

```sql
-- Airflow run 하나가 건드린 슬롯과 시도
SELECT r.run_key, et.task_key, a.record_source, a.exit_code, a.ecs_task_arn, a.orchestrator_attempt_ref
  FROM ops_task_attempt a JOIN ops_expected_task et USING (expected_task_id)
  JOIN ops_pipeline_run r USING (pipeline_run_id)
 WHERE a.orchestrator_attempt_ref LIKE 'airflow:edge_investor_intraday/' || :'airflow_run_id' || '/%'   -- psql -v airflow_run_id=...
 ORDER BY a.created_at;
```

## 로컬 검증

```bash
cd src/apps/cloud/airflow/local
# 입력: dev 레이크 읽기 전용 복사(inputs/dev-lake)와 배포된 ASL(inputs/deployed-asl.json). 둘 다 커밋하지 않는다.
docker compose build && EDGE_LAB_TODAY_KST=2026-09-22 docker compose up -d --wait
python3 lab.py scenario-legacy      # 기존 경로: plan-run → 대역 SFN(배포 ASL) → ECS 대역
python3 lab.py scenario-airflow     # Airflow 경로(8개 상황)
python3 lab.py scenario-partial     # 정제 exit 2의 두 경로 해석 대조(+ available_at 시점 가시성)
python3 lab.py scenario-guard       # 실행권·원장 장애·동시 계획(Airflow env 로 ECS 직접 실행)
python3 lab.py scenario-holiday     # 휴장일 계획·수집 skip, 같은 logical date 재trigger
python3 lab.py scenario-report      # DAG 판정 보고 → orchestration_status
python3 lab.py scenario-trace       # attempt ↔ Airflow try 참조, 응답 유실 뒤 DUPLICATE_SKIP
python3 lab.py scenario-lockloss    # 실행권 커넥션 상실 중 다른 실행 진입(경계 재현)
python3 lab.py scenario-hold        # 실행 상태 불명 시 보류(V1~V8, 16개 확인) — 결과: results/hold-summary.md
python3 lab.py scenario-unsettled   # 결말 없는 실행(수동 failed·worker 사망·DAG 시간 초과·늦은 보고·같은 증거) — 결과: results/hold-summary.md
# DAG 계약 테스트 — CI(test-airflow.yml)와 같은 이미지·명령
docker run --rm -v "$(git rev-parse --show-toplevel)":/repo:ro --entrypoint bash \
  apache/airflow@sha256:9df9c8be4096b9cc626bd7cb1f2b8c712eef66c59c615f8c7e6200871ca10bd1 \
  -c "pip install -q -r /repo/src/apps/cloud/airflow/requirements-test.txt && cd /tmp && python -m pytest -q -p no:cacheprovider /repo/src/apps/cloud/airflow/tests"
```

- `EDGE_LAB_TODAY_KST`는 저장된 과거 슬롯을 재생하려고 "오늘"을 고정하는 로컬 전용 변수다. **운영 환경에는 두지 않는다.**
- clear는 "재시도·재접속·보류 정책"의 제약을 받는다(앞 시도의 ECS 기록이 조회될 때만 판단 가능).
- ⚠️ 3.3.2 CLI의 `airflow tasks clear -s/-e`로는 수동 trigger run을 지정하지 못했다. logical date로도, run_after 창으로도 고르지 못했고, 매번 exit 0에 출력 없이 상태가 그대로였다(로컬에서 3회 관찰). 실패한 run을 복구할 때는 REST API `POST /api/v2/dags/{dag_id}/clearTaskInstances`에 `dag_run_id`와 `task_ids`, `include_downstream`을 지정해 호출한다.

## 실행 환경(ECS on EC2, ALPHA-1119)

Terraform: `infra/terraform/modules/airflow`(환경), `envs/dev/main.tf` `module "airflow_rds"`·`module "airflow"`, `foundation/ecr.tf`(`edge/airflow`). 배포: `.github/workflows/deploy-airflow.yml`.

### 구성과 선택 이유

| 결정 | 선택 | 이유 |
|---|---|---|
| 실행 방식 | 일반 ECS on EC2(ASG + Capacity Provider) | ECS Managed Instances 가 아니다(별도 관리 요금). MWAA 는 SCP 거부 |
| 클러스터 | 전용 `edge-dev-airflow` | worker 클러스터의 capacity provider 목록은 `aws_ecs_cluster_capacity_providers` 가 통째로 소유한다. EC2 용량을 더하면 기존 리소스를 고치게 된다. 클러스터는 무료다 |
| 호스트 | **검증 중: t4g.micro**(arm64, 2 vCPU·1 GiB) 1대, ASG `host_count`(1/0) | 로컬은 호스트 몫을 가정해 micro 를 불가로 봤다(아래 "사양 검증") — 실제 EC2 로 다시 잰다("실제 AWS 단기 검증"). 모자라면 같은 조건의 t4g.small. medium 은 small 이 모자란 근거가 없다 |
| AMI | ECS 최적화 AL2023 arm64 **고정**(`ami_id`) | SSM recommended 를 data 로 읽으면 사람이 고르지 않은 시각(머지 = apply)에 교체가 준비된다 |
| 서비스 | 서비스 1개·태스크 1개 안에 `api-server`·`scheduler`·`dag-processor`(awsvpc) | 세 컨테이너가 localhost 를 공유한다. LocalExecutor 의 task 프로세스가 Execution API 를 `http://localhost:8080/execution/` 로 부른다. 별도 서비스로 나누면 서비스 간 이름 해석(Service Connect 등)이 필요하다 |
| Executor | LocalExecutor, `parallelism=1`, DAG 파싱 프로세스 1 | 장중 수급은 직렬이다(`max_active_runs=1`). LocalExecutor 는 parallelism 만큼 워커를 미리 띄운다(4 면 약 120MiB). 검증 DAG 와 겹치면 차례로 돈다. Celery·Redis·Kubernetes 를 쓸 근거가 없다 |
| Triggerer | 없음 | `EdgeStep` 이 `deferrable=False` 로 고정돼 있다(defer 하면 재개가 provider 의 `execute_complete` 로 가서 판정을 건너뛴다). 다른 deferrable operator 도 없다 |
| 버전 | Airflow 3.3.2 · amazon provider 9.36.0 (CI 와 같은 다이제스트) | 로컬·CI 에서 검증한 조합 그대로 |
| 메타DB | **검증 중: 기존 업무 RDS 안의 전용 DB `airflow`·역할 `airflow_meta`** | 아래 "메타DB"·"실제 AWS 단기 검증". 재사용 확정은 그 결과 뒤 |
| DAG 배포 | 이미지에 굽는다(`Dockerfile`) | 세 구성요소와 마이그레이션 작업이 한 이미지 태그(커밋)를 쓴다. 서버에 파일을 복사하지 않는다 |
| UI 접근 | SSM 포트 포워딩만 | ALB·공인 IP 없음. 태스크 SG 는 호스트 SG 에서 온 8080 만 받는다 |
| 인증 | Airflow 3 기본 SimpleAuthManager, 사용자 `admin` 1명 | 비밀번호는 Secrets Manager. ⚠️ Airflow 문서는 SimpleAuthManager 를 개발·테스트용으로 분류한다. 여기서는 네트워크 경로가 SSM 뿐이고 사용자가 1명이라 받아들였다. 사용자가 늘면 FAB auth manager 로 바꾼다 |

**자원(태스크 정의).** 태스크 메모리 **1536MiB 하나**를 세 컨테이너와 그 자식(LocalExecutor task 프로세스)이 함께 쓴다(태스크 cgroup). 컨테이너별 hard 상한은 두지 않고 예약(`memoryReservation`)만 둔다 — api 300·scheduler 300·dag-processor 250, CPU unit 512·1024·256. 근거는 바로 아래 "사양 검증".

**헬스체크.** 파이썬을 띄우지 않는다. 각 컨테이너가 api-server `/api/v2/monitor/health` 의 자기 구성요소 status(`metadatabase`·`scheduler`·`dag_processor`)가 `healthy` 인지 본다. 응답 코드는 부분 장애에도 200 이라 본문을 본다. 판정 재료는 `airflow jobs check` 와 같은 heartbeat(메타DB job 표, 30초 임계)다. `jobs check` 는 한 번에 약 110MiB 인 프로세스라 60초마다 둘이 겹치면 순간 228MiB 가 늘었다(로컬 실측).
- ⚠️ 전제: 메타DB 하나에 scheduler·dag-processor 가 하나씩뿐이다(서비스 desired 1, 배포 min 0 / max 100). `jobs check --local` 과 달리 이 판정은 호스트를 가리지 않는다 — 같은 메타DB 에 다른 scheduler 가 붙으면(예: 수동으로 띄운 두 번째 태스크) 그쪽 heartbeat 가 이 태스크의 멈춤을 가린다.
- ⚠️ 대가: 헬스체크가 새 DB 연결을 열지 않는다. 메타DB 비밀번호 로테이션(토 09:00~12:00) 뒤에도 기존 풀 연결로 한동안 healthy 이고, 새 연결이 필요해지는 시점(풀 재활용 `sql_alchemy_pool_recycle` 기본 1800초 등)에 구성요소가 실패하며 그때 교체된다. 실제 AWS 검증 항목이다.

- 배포 때 추가 용량은 필요 없다. 서비스는 min 0 / max 100 으로 옛 태스크를 멈춘 뒤 새 태스크를 띄운다. scheduler 가 둘 뜨는 순간이 없고, 대가로 1~3분 Airflow 가 멈춘다. 그래서 `deploy-airflow` 는 평일 장중(KST 09:20~15:00)에 명시 허용 없이 배포하지 않는다.
- 마이그레이션 작업(`airflow db migrate`)은 **Fargate ARM64** one-off 다. 서비스 태스크가 호스트 메모리를 거의 쓰므로 같은 호스트에 자리를 요구하지 않는다.

### 사양 검증(로컬 합산 상한, 2026-09-29) — 실제 EC2 검증이 아니다

질문: "기존 RDS 재사용 + t4g.micro"를 첫 배치 운영 후보로 삼을 수 있는가. 도구는 `local/micro/`, 기준은 측정 전에 고정했다(`local/micro/criteria.json`). 판정 결과는 `local/results/micro/<실험>/verdict.json`에 있다(원시 샘플·로그는 로컬에만 보존).

**환경.**
- **합산 상한:** 세 구성요소를 한 컨테이너 = 한 cgroup 에 띄워 합산 상한 하나를 건다. 구성요소 셋, LocalExecutor task 프로세스, 헬스체크 exec 가 모두 그 안이다. ECS 태스크 수준 memory 와 같은 경계다.
- **상한 적용 확인:** cgroup 파일에서 `memory.max`·`memory.swap.max=0`(swap 금지)·`cpu.max=200000/100000`(2 CPU)을 확인했다. Docker VM 에 swap 1GiB 가 있어도 이 cgroup 은 swap 을 쓰지 못한다.
- **상한 밖에 둔 것:** 업무 처리(ECS 대역 fake-aws 의 서브프로세스 = 실제 `data_pipeline` 코드), 업무 DB, 메타DB. 운영에서 Fargate·RDS 인 것과 같은 경계다.
- **관측:** 상한 밖의 특권 관측기가 2초마다 cgroup(`memory.current·peak·events·stat`, `cpu.stat`), 프로세스별 PSS, 메타DB 연결(구성요소별 `application_name`), heartbeat·파싱 경과를 읽는다. Airflow 는 REST 로만 부른다 — CLI 를 exec 하면 측정 대상에 150MB 급 프로세스가 끼어든다.
- **입력:** 보존된 dev raw(2026-09-22, 5슬롯)를 재생한다. KIS·토스 등 외부 API 는 부르지 않는다.
- **ECS 대역인 것:** RunTask·ListTasks·DescribeTasks·StopTask 는 대역이다.
- **실제 코드인 것:** EdgeStep 의 제출·대기·판정·보류, 원장 wrapper·Planner·Reconciler, 정제·적재, Airflow 3.3.2·amazon 9.36.0(arm64 네이티브, 에뮬레이션 없음, Apple M5 Pro).
- **CPU 결과의 한계:** T4g CPU 크레딧(버스트·기준선)은 재현하지 않았다. CPU 는 이 개발기 코어 기준이라 Graviton2 의 수치가 아니다.

**스위트(실험마다 같다).**
- 기동 → 유휴 5분 → **B1** 정상 5회(각 정제 120초 외부 대기, 3번째 대기 중 API 30건 + DAG 파일 touch).
- **B2** 다섯 가지를 차례로 돌린다.
  - 적재 exit 1(확정 실패, 재시도 없음)
  - 수집 기동 실패 1회 → 재시도
  - 정제 대기 중 StopTask → 결과 미상 HOLD
  - 정제 대기 중 Airflow 재시작 → 재접속·결과 재사용
  - 재시작 뒤 정상
- 유휴 5분 → **B3** B1 반복 → 유휴 5분 → 재시작 2회.

**판정 기준(요지).**
- OOM·자발 재시작 0
- heartbeat ≤ 30초, 파싱 경과 ≤ 360초, touch 뒤 재파싱 ≤ 90초
- 지연 p95: 앞 task 종료→queued ≤ 15초, queued→시작 ≤ 15초, 시작→ECS 생성 ≤ 20초, ECS 종료→task 종료 ≤ 20초
- 시나리오 결과·원장 상태가 기대와 같다
- 업무 실행 중복 0, canonical 이 dev 와 같다
- 풀 대기·연결 오류 0
- B3 뒤 유휴 anon ≤ B1 뒤 × 1.10, UI 30건 2xx·p95 ≤ 2초

**설정.**
- **C0(#981 원안):** parallelism 4, 파싱 프로세스 2, 풀 3/5, 헬스체크 `airflow jobs check` CLI.
- **C1(조정):** parallelism 1, 파싱 1, 풀 2/3, 헬스체크 `/monitor/health`.
- C1 은 C0 측정(E1)의 원인별 분해로 정했다: 미리 뜬 워커 4개 119MiB, 헬스체크 CLI 순간 228MiB, 실제 연결 최대 6. 기능은 빼지 않았다 — serve-logs·API 워커 1·재시도·보류·보고 모두 그대로다.

| 실험 | 합산 상한 | 설정 | 결과 | 최대 memory.current / anon | 비고 |
|---|---|---|---|---|---|
| E1 | 2048 | C0 | 전 기준 통과(**오염** — 아래) | 1155 / 1122 MiB | small 참조. 유휴 anon 662→796→833→851(배치마다), 재시작 뒤 634 |
| E2 | 1024 | C0 | **실패** — 기동 중 api-server OOM(137) 재시작 3회, B1 첫 run failed | 1014 / 942 | 중단(판정 확정) |
| E3 | 1024 | C1 | **통과**(실패 기준 0, 상대 지연은 판정 불가 — 아래) | 968 / 875 | anon 이 768 을 넘은 샘플 54% |
| E4 | 768 | C1 | **실패** — dag-processor OOM(137) 2회, B1 첫 run 미완료 | 767 / 740 | memory.events max 118 |
| E6 | 768 | C1 + `MALLOC_ARENA_MAX=2` | **실패** — 유휴부터 상한 도달, B1 중 api-server OOM | 744 / 677 | 설정으로 구제되지 않음. 사전 계획(criteria)에 없던 추가 실험(E4 실패 뒤 "설정으로 해결되는가" 확인) |
| E5 | 1792 | C1 | **통과**(실패 기준 0, 상대 지연은 판정 불가) | 940 / 879 | 연결 최대 5(활성 1), heartbeat 최대 11.8초 |

- **통과 실험의 공통값(E3·E5).**
  - 지연 p95: 앞 task 종료→queued 1.0초, queued→시작 0.0초, 시작→ECS 생성 0.2초, ECS 종료→task 종료 5.7초(E1 과 같다).
  - 재시작 복구 run 은 heartbeat 타임아웃(300초, 운영 기본) 뒤 재시도가 끝난 ECS 결과를 재사용했다. 업무 실행 1회, ECS 태스크 1개였다.
  - HOLD 는 원장 `EXECUTION_HOLD`(RESULT_UNKNOWN)로 남았다.
  - B1·B3 의 canonical 행 sha 는 dev 와 같았다.
- **메모리 구성(유휴 PSS, C1).** api-server 220~280, dag-processor 180~210, scheduler 70~140, serve-logs 60~115, LocalExecutor 워커 1개 약 70MiB.
  - 유휴 anon 은 배치를 돌수록 늘다가(E5: 676→750→789→791) 증가 폭이 준다. 재시작하면 약 615 로 돌아간다.
  - 한 시간 남짓의 관찰이라 **며칠 단위의 증가는 확인하지 않았다**(실제 AWS 검증 항목).
- **micro 가 모자란 원인.** 기본 상주(유휴 anon 약 610~680MiB)만으로 768 의 대부분을 쓴다. 여기에 업무 실행·API 응답 뒤 늘어난 상주(약 +100~180)와 기동·파싱 순간 증가가 겹쳐 상한에 닿는다. 불필요한 프로세스(미리 뜬 워커·CLI 헬스체크)는 C1 에서 이미 뺐다.
  - 더 줄이려면 기능을 건드려야 한다. serve-logs(실행 중 로그 조회)를 빼는 안은 CloudWatch 실시간 로그로 대체되는지 확인해야 해서 이번에 하지 않았다.
- **호스트 몫 가정(미실측).**
  - t4g.micro·small 은 1·2 GiB 전체를 태스크에 줄 수 없다. 커널·systemd·journald, dockerd·containerd, ECS 에이전트, SSM 에이전트가 쓴다.
  - 이 합을 **200~350MiB 로 가정**하고 768(micro)·1792(small)를 검증했다. AWS 실측이 아니다.
  - 실제 값은 단기 검증에서 호스트 `free -m`, ECS `RegisteredResources`(MEMORY)로 확정한다.
  - 태스크 정의 1536 은 small 의 등록 메모리 안쪽을 노린 값이다. E3 의 C1 이 1024 에서 통과해 1536 은 그보다 넉넉하다.
- **CPU(개발기 코어 기준, 참고).**
  - C1 평균 0.03~0.04 코어(유휴·배치), C0 0.06~0.08 코어(CLI 헬스체크 몫).
  - 2 CPU 상한에서 스로틀은 거의 없다(C1 0~1%).
  - t4g 기준선(인스턴스 전체)은 micro 0.2·small 0.4 vCPU 다. 개발기와 Graviton2 의 코어 성능 차이를 몇 배로 잡아도 small 기준선 안쪽으로 보이지만, 실측이 아니다.
- **오염·무효 기록(판정에서 제외, 보존).**
  - E1 첫 시도: JWT 서명키 미설정으로 task 인증 실패 → 측정 환경 결함(운영 태스크 정의에는 있다). `invalid/`에 보존.
  - E1: 실행 중 마운트된 `healthcheck.sh`를 고쳤다(cli 분기 명령은 같다) → `E1.TAINTED`. 이후 실험은 시작·끝 코드 해시가 같아야 유효(`C0_valid_run`)로 본다.
  - E1 재실행(E1b)은 하지 않았다. 그래서 criteria 의 **상대 지연 기준(E1 대비 +10초)은 판정 불가**다. 절대 기준은 통과했고, E3·E5 의 지연 p95 수치(1.0·0.0·0.2·5.7초)는 오염된 E1 과 같다. small 참조는 E5 로 갈음했다.
  - 판정기 결함을 고쳤다: `p95 0.0` 을 거짓으로 읽음, 부모 cgroup OOM 을 Airflow 로 귀속, 중단 실험의 재시작 누락. 리뷰에서 누락을 통과로 읽는 경로도 막았다(heartbeat 한쪽 누락·빠진 파티션·로그 없음·주입 안 된 재시작·다른 슬롯의 보류·무효 참조). 재판정 뒤에도 E3·E5 의 실패 기준은 0 이다.
- **검증 도구의 남은 한계(리뷰 검증 라운드, 수정하지 않고 기록).**
  - **OOM 근거:** 컨테이너가 재시작하면 cgroup 이 새로 생겨 `memory.events` 의 oom·oom_kill 이 0 으로 돌아간다. 그래서 E2·E4·E6 의 OOM 근거는 이 카운터가 아니다. supervisor 로그의 `Killed` 137 과 재시작 수, 상한 도달 횟수(`memory.events max`)가 근거다. kill 없이 끝난 OOM 이 재시작 앞에 있었다면 판정기가 놓칠 수 있다.
  - **계획 대조 범위:** 계획 대조(`C0_matches_plan`)는 상한·parallelism·파싱·풀만 비교한다. 헬스체크 방식·malloc 설정은 `verdict.json` 의 `ran_with` 로 사람이 확인한다(E3·E5 는 light·기본).
  - **지연 짝짓기:** 지연 짝짓기는 시도 참조 env 가 있는 ECS 태스크만 센다. EdgeStep 은 모든 태스크에 붙인다. 종료 감지 표본 수는 강제하지 않는다(E3·E5 는 배치당 25·23·25).
- **판단.** micro 는 실제 AWS 단기 검증 후보가 아니다(호스트 몫을 뺀 768 에서 두 설정 모두 OOM). **small 을 실제 AWS 단기 검증 후보로 한다.** medium 이 필요하다는 근거는 없다(small 차감 조건 E5 통과). 로컬 통과는 EC2 운영 안정성의 검증이 아니다.

### 실제 AWS 단기 검증(t4g.micro + 기존 RDS) — 실행 전 고정(2026-09-29 19:50 KST)

로컬에서는 호스트 몫을 200~350MiB 로 **가정**해 micro 를 불가로 봤다. 여기서는 실제 EC2 에서 호스트 전체 사용량과 Airflow 동작을 잰다. 기준·반복 횟수·중단 조건·종료 시각은 `verify/criteria_aws.json` 에 고정했다(실행 도중 바꾸면 그 전 결과는 판정에 쓰지 않는다). 판정은 `verify/analyze_aws.py`(통과·실패·**판정 불가** 셋 — 표본 부족·계측 공백은 성공이 아니다).

**구성(이번 검증).**
- 일반 ECS on EC2 t4g.micro 1대(전용 클러스터, 업무 클러스터와 분리). 태스크 합산 메모리 1024(로컬 C1 통과값) — 등록 메모리보다 크면 배치되지 않고, 그것도 결과로 적는다(억지로 줄여 넣지 않는다).
- 메타DB: **기존 업무 RDS 인스턴스 안의 전용 DB `airflow`·전용 역할 `airflow_meta`**. 검증 원장: 같은 인스턴스의 전용 DB `edge_verify`·역할 `airflow_verify`(업무 DB 스키마만 복제, 행 없음). 새 RDS·사양 변경·전역 파라미터 변경 없음.
- 두 역할: 슈퍼유저·DB 생성·역할 생성 권한 없음, CONNECTION LIMIT 10, 역할 수준 `statement_timeout` 30초·`idle_in_transaction_session_timeout` 60초, 업무 DB 테이블 권한 0(관리 태스크 `privcheck` 로 확인), 새 DB 에 PUBLIC 접속 불가. 비밀번호는 Secrets Manager 에만(Terraform·로그·코드에 없음 — `run.py secrets` 가 만들고 찍지 않는다). 역할 생성 SQL 에는 평문 대신 SCRAM 검증자(`*_scram`)를 넣는다 — 문장이 실패해도 RDS 오류 로그(`log_min_error_statement=error`)에 평문이 남지 않게. 관리 태스크의 소유자 권한 문장(PUBLIC 접속 회수·DB 삭제)은 `SET ROLE` 로 소유 역할이 되어 실행한다(RDS 마스터는 슈퍼유저가 아니고 두 역할을 `INHERIT FALSE` 로만 받는다).
- DB·역할 생성·정리는 관리 태스크(`verify/dbadmin.sh`, postgres:16, 마스터 시크릿은 이 태스크의 execution 역할만 읽는다).
- 호스트 관측기(`host_observer`): 5초마다 호스트 메모리·모든 cgroup(태스크·ECS·SSM·docker)·상위 프로세스, 커널 로그(OOM), docker 이벤트(종료 코드)를 **호스트 디스크에 append** — 대상이 죽어도 남는다. SSM 으로 수거.
- 정기 DAG 는 모두 pause, `investor_intraday_orchestrator = "SFN"`·SFN 스케줄 그대로.

**수명주기(되살아나지 않게).** 호스트 수는 Terraform `host_count`(1/0)가 정한다 — 콘솔로 0 을 만들면 다음 dev 머지의 자동 apply 가 1 로 되돌리므로 내리는 것도 코드로 한다. 서비스 desired 는 CD 소유(`deploy-airflow` 는 desired 0 이면 마이그레이션까지 건너뛴다 — 지운 DB 에 붙지 않는다). 서비스 중단 알람은 `host_count=1` 일 때만. 검증 자원(버킷·태스크 정의·관리 역할·검증 시크릿)은 `verify_enabled=false` 로 걷는다.

**시간.** 시작은 19:30 공시 배치 종료 확인 뒤, 23:58~00:45(뉴스 00:10)에는 새 run 을 시작하지 않는다, **06:30 KST 전에 신규 실행 중지·정리 시작**(프리마켓 07:00·분 세션 07:45 전). 최대 24시간, 상시 운영으로 자동 연장하지 않는다.

**시나리오·반복(고정).** S0 기동·DAG 로딩 → S1 유휴 30분(기존 RDS 영향 확인 — 통과해야 다음으로) → B1 정상 5회(각 정제 120초 대기, 3번째 대기 중 API 30건·재파싱) → 유휴 15분 → B2(확정 실패·업무 미시작 재시도·StopTask HOLD·실행 중 Airflow 재시작 추적·응답 유실+조회 실패 HOLD·보류 중 차단·종료 확인 뒤 해제·해제 뒤 정상) → 유휴 15분 → B3 정상 5회 → 유휴 15분 → 재시작 2회(각 5분). 실제 AWS 와 모의 주입의 경계는 `criteria_aws.json` `real_vs_injected`.

**중단 기준.**
- Airflow: 태스크 OOM 1건, 주입하지 않은 서비스 태스크 교체 2회(30분 안), 10분 넘는 미배치, 연속 3개 task 에서 queued→시작 > 60초 또는 ECS 종료→task 종료 > 90초. 걸리면 증거를 보존하고 micro 를 멈춘다(재시작을 반복하지 않는다) → 같은 환경을 t4g.small 로 바꿔 같은 시나리오.
- 기존 RDS(14일 1분 지표의 장외 분포에서 정했다 — FreeableMemory 최소 559·p1 588MiB, Swap 최대 38MiB, CPU 최대 19.2%, 연결 최대 30, 지연 p99 5.8~6.7ms): FreeableMemory < 500MiB 3분, Swap > 60MiB 3분, CPU > 30% 5분, 지연 > 20ms 3분, 연결 > 42 2분, 창 안의 업무 SFN FAILED 1건. 걸리면 Airflow 서비스를 desired 0 으로 멈추고 원인을 본다.
- 장외만 검증했으므로 **장중 재사용은 미검증**으로 남긴다. SFN 이 아직 실행 주체라는 사실이 공유 RDS 영향의 안전을 뜻하지 않는다.

**예상 비용(서울 공개 단가).**

| 항목 | micro 검증(약 10시간) | + small 비교(약 5시간) |
|---|---|---|
| EC2(0.0104 / 0.0208 /h) | 0.10 | +0.10 |
| EBS gp3 30GiB(종료 시 삭제) | 0.04 | +0.02 |
| CPU 크레딧 초과(unlimited, 최악: 2 vCPU 상시 사용) | 0~0.72 | +0~0.29 |
| Fargate 검증 태스크(0.25 vCPU·0.5GB, 약 150회 × 2분) | 약 0.07 | +0.07 |
| NAT 처리량(검증 이미지 176MB × 약 150회 pull + Airflow 이미지·postgres 이미지) | 약 1.7~2.0 | +1.6~1.8 |
| 로그·시크릿(시간 비례)·S3·SSM | 약 0.3 | +0.1 |
| **합계** | **약 2.2~3.2 USD** | **약 4.1~5.5 USD** |

- 무료 혜택은 넣지 않았다(계정 적용 미확인). 검증 뒤 남는 월 비용: 앱 시크릿 0.40, ECR 이미지 약 0.1~0.2, 로그 보관 소액 → **약 0.6 USD/월**(호스트·검증 버킷·검증 시크릿·알람은 정리).

**정리 순서.** DAG pause → 도는 검증 ECS 태스크 STOPPED 확인 → 증거 수거(호스트 관측·배치·deployinfo·RDS)·검증 원장 백업·이미지 digest 기록 → 서비스 desired 0 → 관리 태스크 `teardown`(전용 DB·역할 삭제, 업무 데이터·계정은 건드리지 않는다) → Terraform `host_count = 0`·`verify_enabled = false` 머지(자동 apply 가 호스트·검증 자원·알람을 걷는다).

### 실제 AWS 단기 검증 결과(2026-09-29 20:05 ~ 09-30 12:00 KST)

**결론: t4g.micro 는 불가, t4g.small 에 태스크 1024MiB 도 불가.** 1408MiB 는 유휴·재시작만 확인했고 업무 실행은 검증하지 못했다(아래 도구 결함). 상시 운영 사양은 아직 정해지지 않았다.

| 구성 | 결과 | 근거(원자료 `local/results/aws/`) |
|---|---|---|
| micro · 1024 | **배치 불가** | ECS 등록 메모리 916MiB < 1024 |
| micro · 896 | **호스트 전역 OOM**(중단 기준) | Airflow 전 호스트 상주(dockerd·containerd·ECS 에이전트·SSM) 230~300MiB, 가용 약 610MiB. 기동 때 컨테이너마다 `airflow db check`(각 약 135MiB)가 겹쳐 가용 42MiB → 약 6분 스래싱(관측기도 멈춤) → 20:40 `global_oom` 이 api-server 를 죽였다. 태스크 cgroup 은 545MiB 로 상한 아래였다. swap 없음 |
| small · 1024 | **태스크 cgroup OOM**(중단 기준) | 등록 1846MiB. 유휴 태스크 약 770MiB(anon 730). B1 첫 run 에서 task 실행 프로세스(LocalExecutor 가 띄우는 task runner, 약 250~300MiB)가 더해져 상한 도달 → 23:16·23:22 api-server OOM 2회, 태스크 교체 |
| small · 1408 | 유휴·재시작만 확인 | 약 12시간 OOM 0, 태스크 최대 1011MiB(p50 930, 페이지 캐시 포함), 호스트 가용 최소 415MiB, 재시작 1회 정상. **B1~B3 는 무효** — 아래 도구 결함 |

- **로컬 측정과의 차이.** 로컬 C1 은 1024 에서 전 기준을 통과했지만, 실제 EC2 에서는 유휴만 약 770MiB 였다. 로컬 가정(호스트 몫 200~350MiB)은 맞았지만 태스크 자체가 더 컸다. 사양은 실제 환경 실측으로 정한다.
- **실제 장애 중 정확성(주입 아님).** small·1024 의 B1 첫 run 은 api-server OOM 두 번과 Airflow 태스크 교체를 겪고도 성공했다. plan·normalize 는 try 1 이 실패하고 try 2 가 성공했다. 업무 ECS 태스크는 5개, 스텝마다 업무 시작 1회(중복 0), 파티션 쓰기 1회, 원장 `AIRFLOW · SUCCEEDED` 였다(`aws-small/B1.json`). 이 한 건만으로 exactly-once 를 주장하지는 않는다.
- **기존 RDS 영향.** 전 구간 중단 기준 밖. Airflow 연결은 유휴 4개(총 20~22 → 24~26), FreeableMemory 약 −30MiB(최소 557MiB). 계획 밖으로 09-30 장중(09:00~11:48)에도 Airflow 가 유휴로 붙어 있었다 — 그 구간 CPU 최대 33.4%(5분 지속 아님), 연결 최대 29, 쓰기 지연 최대 8.6ms, 업무 SFN 실패 0. 장중 부하 중 Airflow 실행은 여전히 미검증이다.

**검증 중 드러난 결함(모두 수정)**
- 원격 로그 핸들러(watchtower)가 기동 때 `logs:CreateLogGroup` 을 부른다. 권한이 없어 dag-processor 가 exit 1 로 죽었고, 서비스가 약 90초마다 재기동했다. circuit breaker 가 이전 리비전으로 롤백해서 새 `task_memory` 리비전 대신 옛 리비전 태스크가 떴다(21:14~22:31). 태스크 역할에 그 그룹 하나만 허용했다(#993). 로컬 하네스는 원격 로그를 쓰지 않아 못 봤다.
- 판정기가 ECS AMI 의 systemd cgroup 경로(`ecstasks.slice/ecstasks-<id>.slice`)를 태스크로 읽지 못했다(#992).
- 검증 실행기가 슬롯을 오늘 날짜로 만들어서, 자정을 넘긴 배치는 `logical_date` 가 미래가 됐다. Airflow 는 그 시각까지 run 을 시작하지 않고 `dagrun_timeout`(900초)으로 실패시켰다 → small·1408 의 B1~B3 전부 무효. 이제 미래 슬롯을 거부한다. 수집은 `same_day_only` 라 **검증 창은 같은 날 14:35 뒤 ~ 자정 전**이다.
- 스위트가 06:30 종료 시각을 코드로 지키지 않아, 로컬 기기 수면 뒤 09-30 11:16 장중에 Airflow 재시작 1회를 했다(업무 영향 없음 — Airflow 는 격리 DAG 만 가진 별도 클러스터). 발견 즉시 desired 0 으로 멈췄다.
- ASG 첫 기동 "Authentication Failure"(새 인스턴스 프로파일 전파 지연 — 1분 뒤 자동 성공). terraform 은 이를 실패로 보고 ASG 를 taint 해서 `untaint` 뒤 apply 를 다시 돌렸다.

**비용(실측·추정).** micro 약 1시간·small 약 15시간, CPU 크레딧 초과는 micro 6.04(약 0.24 USD)·small 0. EC2·EBS·크레딧·이미지 pull·로그를 합쳐 **약 1 USD**(추정 — Cost Explorer 확정 전). 정리 뒤 잔여: 앱 시크릿·ECR 이미지·로그 보관, **약 0.6 USD/월**.

**정리 상태.** 서비스 desired 0 · 검증 ECS 태스크 0 · 검증 원장 백업(87개 표) · 검증 버킷 사본 로컬 보존 · 전용 DB·역할 삭제(`roles_left=0 dbs_left=0`) · `host_count = 0`·`verify_enabled = false`(#998). 정기 DAG 는 pause 그대로, SFN·`investor_intraday_orchestrator = "SFN"` 변경 없음.

**다시 한다면.**
- small 에 1408 로, 같은 날 15:00~23:30 창 안에서 B1~B3 를 다시 돈다.
- 스위트가 종료 시각을 코드로 지키게 한다.

### small·1408 후속 검증 — 실행 전 고정(2026-09-30 14:10 KST)

위 small·1408 의 B1~B3 는 실행기 결함으로 무효였다(결과는 그대로 둔다). 이번에는 그 결함과 종료 방식을 먼저 고치고, 업무 실행·복구만 다시 본다. 기준·순서·시각은 `verify/criteria_aws_1408.json`(실행 전 고정, `VERIFY_CRITERIA` 로 고른다).

**고친 것(실행 전).**
- 실행기: 검증일(`verify_day`)과 슬롯을 기준 파일로 고정하고, 다른 날·미래 슬롯·제출 마감(22:15) 뒤 제출을 **제출 전에** 거부한다(`run.py slotcheck` 가 자정 전후를 가상 시각으로 확인). run 이 제출 뒤 240초 안에 어떤 task 도 시작하지 않으면 `not_started` 로 즉시 멈추고 run·DAG 상태를 남긴다. 트리거마다 서비스 태스크 리비전·이미지 digest 를 기록해, 회차 도중 배포가 바뀌면 판정(A12)이 실패한다.
- 종료 장치(`infra/terraform/modules/airflow/verify_shutdown.tf`): 운영자 PC 와 무관하게 AWS 가 끝낸다.
  - 22:30 EventBridge Scheduler 가 verify-ops 태스크로 `shim.py verify-shutdown 900` 을 띄운다. 순서: Airflow 서비스 desired 0(새 제출 중단) → 15분 동안 검증 업무 태스크의 자연 종료 대기 → 남은 **검증 태스크만** StopTask → 호스트 ASG 0.
  - 멈춘 태스크는 결과 미상으로 보고서(검증 버킷 `shutdown/`)에 남긴다. 원장의 RUNNING 시도는 보류로 남는다.
  - 23:00 두 스케줄이 서비스 desired 0·ASG 0 을 직접 호출한다(종료 태스크가 실패한 경우 대비).
  - 분 세션 스케줄과 같은 방식(universal target `aws-sdk:ecs:runTask`)을 재사용했다.
  - 본 실험 전에 같은 역할·같은 대상으로 몇 분 뒤 시각의 시험 스케줄을 만들어 실제 동작(서비스 0·대기 태스크 중단·ASG 0)을 확인한다. 확인되지 않으면 본 실험을 하지 않는다.
- 검증 시크릿 `recovery_window_in_days = 0`: 어제 정리한 같은 이름이 복구 대기로 남아 생성이 막혔다(즉시 삭제로 풀었다).

**구성(고정).** t4g.small 1대 · 태스크 합산 1408MiB · parallelism 1·파싱 1·풀 2/3·API workers 1 · 이미지 `edge/airflow:7bb0c196…`(digest `sha256:9e1c1f86…`) · 검증 업무 이미지 = 배포된 data-pipeline(`sha256:8b8c2a60…`) + shim(이번 커밋으로 다시 빌드) · 기존 RDS 안의 전용 DB `airflow`·`edge_verify`와 최소 권한 역할 · 입력은 업무 레이크의 장중 수급 raw 1개를 읽기만 해서 검증 버킷에 복사 · 산출물은 검증 버킷·검증 DB 에만.

**시나리오(V 배치, 순서 고정).**

| 순서 | 슬롯 | 내용 |
|---|---|---|
| N1·N2·N3 | 09:35·10:05·11:25 | 정상 |
| R1 | 13:25 | 정상 + 정제 150초 대기 중 Airflow 서비스 강제 재배포 → 기존 ECS 추적 |
| F1 | 14:35 | 적재 exit 1 확정 실패 |
| H1 | 15:00 | 수집 제출 응답 유실 + 조회 실패 → ECS_STATE_UNKNOWN 보류 |
| X1 | 15:10 | 보류 중 새 run → 수집 exit 76 |
| 해제 | — | H1 태스크 STOPPED 확인 뒤에만 `verify-resolve-holds` |
| A1 | 15:20 | 해제 뒤 정상 |

앞뒤로 S1 유휴 20분(Airflow 연결 직후 기존 RDS·호스트 기준선)과 V 뒤 유휴 15분을 둔다. 판정 항목은 A1~A8·A11(1408 상한 도달 0)·A12(배포 고정)이다.

**시각(KST).**
- 시작은 16:30 이후다. 분 세션 stop 16:10·일봉 15:40 종료와 실행 중 SFN·ECS 0 을 확인한 뒤 시작한다.
- 19:25~19:55(공시 19:30)에는 새 run 을 시작하지 않는다.
- 제출 마감 22:15, 종료 장치 22:30, 강제 23:00, 정리 완료 23:30.
- 준비가 늦으면 다른 날로 미룬다. 그때는 `verify_day`·종료 시각을 새로 고정한다.

**예상 추가 비용.**
- 항목:
  - small 약 9시간: 0.19.
  - EBS: 0.03.
  - 검증 Fargate 약 50회 × 2분: 약 0.1.
  - 이미지 pull(검증 이미지 약 176MB × 50 + Airflow): NAT 약 0.6.
  - 로그·시크릿·스케줄: 약 0.1.
- 합계 **약 1 USD**, 상한 3 USD.

**정리 대상.**
- 검증 버킷·태스크 정의·역할·검증 시크릿·검증 SG·RDS SG 규칙.
- 종료 장치 스케줄 3개와 그 역할.
- 호스트(ASG 0).
- 전용 DB·역할(dbadmin teardown).
- 서비스 0.


### small·1408 후속 검증 결과(2026-09-30 16:20~18:51 KST)

**결론: 조건부.** 메모리·배치, 업무 실행·복구·보류, 기존 RDS 는 기준을 통과했다. 사전 기준 A5(종료 감지 지연)는 실패했고, A4(heartbeat)는 도구 결함으로 25분 공백이 생겨 판정 불가다. 그래서 "검증된 초기 운영 후보로 확인됨"이라고 쓰지 않는다. 가장 작은 다음 변경은 아래 A5 항목에 있다. 원자료는 `local/results/aws/aws-small-1408-v/`(판정 `verdict.json`)에 있다.

| 판정 | 결과 | 근거 |
|---|---|---|
| A1 배치 | 통과 | 등록 1846MiB, 태스크 1408, 세 컨테이너 HEALTHY |
| A2 OOM·재시작 | 통과 | 커널·docker·cgroup OOM 0, 주입(R1) 밖 태스크 교체 0, 호스트 관측 5초·최대 공백 8초 |
| A3 호스트 메모리 | 통과 | 가용 최소 169MiB(18:07), 업무 실행 중 p50 277MiB, swap 0 |
| A4 heartbeat | **판정 불가** | 17:40~18:05 API 조회 불가(아래 포워딩 결함). 그 사이 ECS 컨테이너 헬스체크는 HEALTHY, 태스크 교체 없음 |
| A5 지연 | **실패** | ECS 종료→task 종료 p95 32.5초(기준 30), 중앙값 29.5초. 나머지 셋은 통과(1.1·4.4·1.1초) |
| A6 시나리오 | 통과(교정 1건) | 재시작 추적·확정 실패·상태 불명 보류·보류 중 차단·해제 뒤 복구 |
| A7 업무 | 통과 | 정상 5회(N1~N3·R1·A1) 모두 스텝별 업무 시작 1·exit 0 종료 1·파티션 쓰기 1·ECS 5·원장 `AIRFLOW SUCCEEDED` 전 작업 FULFILLED |
| A8 RDS | 통과 | 중단 기준 미발동, Airflow 연결 4·검증 역할 0(유휴 시), DB 오류 로그 0, 업무 SFN 실패 0 |
| A11 상한 여유 | 통과 | 태스크 cgroup `memory.events max` 0, 최대 1139MiB(1408 의 81%) |
| A12 배포 고정 | 통과 | 모든 run 이 `edge-dev-airflow:10`·digest `9e1c1f86…`(재시작 뒤 새 태스크도 같은 리비전) |

**메모리(태스크 합산).**
- 유휴(S1) p50 903MiB → 업무 실행 중 p50 1064·최대 1139 → 실행 뒤 유휴 p50 984(+9%).
- 컨테이너별 최대 557·534·329MiB.
- 호스트 가용 최소 169MiB. 태스크 상한까지는 약 270MiB, 호스트 여유는 169MiB — 둘 중 호스트 쪽이 더 빠듯하다.
- CPU 평균 15.8%·최대 69.6%, 크레딧 초과 과금 0(지표 지연 가능 — 청구 확정 전).

**복구·보류(실제 AWS).**
- R1: 정제 대기 중 Airflow 서비스를 강제 재배포했다(17:33:51~17:37:09, 새 태스크). 기존 ECS 를 추적했다 — 정제 ECS 는 1개였고 새로 띄우지 않았다. 업무 시작 1회, 원장 SUCCEEDED.
- F1: 적재 exit 1(주입). run failed, 적재 try 1 번, 적재 업무 시작 0, 원장 `LOAD_INVESTOR_INTRADAY FAILED`.
- H1: 제출 응답 유실 + 조회 실패(주입). `ECS_STATE_UNKNOWN` 보류, 수집 ECS 1개(실제로 돌아 exit 0 으로 끝남).
- X1: 보류 중 새 run → 수집 exit 76, 업무 시작 0(새 업무를 시작하지 않았다).
- 해제: H1 의 태스크 셋(plan·수집·reconcile) STOPPED 를 확인한 뒤 `verify-resolve-holds`. A1 은 해제 뒤 정상으로 끝났다.
- 컨테이너 재기동과 업무 중복은 invocation 기록(태스크 ARN)과 업무 시작 기록으로 가른다. 정상 run 은 5개 ECS 가 모두 서로 다른 태스크였고, 스텝별 업무 시작은 1회였다.

**기존 RDS.**
- 연결 전(15:54~16:24) CPU 최대 37%. 15:40 일봉·16:00 섹터 롤업·16:10 분 세션 stop·#999 스키마 마이그레이션·재배포가 이 구간에 있다(업무 부하 변화).
- Airflow 유휴·업무 실행(16:20~18:48): FreeableMemory 575~601MiB, CPU 최대 8.2%, 연결 20~26, 쓰기 지연 최대 5.6ms.
- 이 관측이 말하는 범위는 두 가지다. **장외 이 시간대에 중단 기준을 넘지 않았다**, 그리고 **Airflow 연결은 유휴 4개**였다. Airflow 영향이 없다는 뜻이 아니고, 장중 영향은 검증하지 않았다.

**종료 장치.**
- 16:35:41 시험 스케줄이 발화했고, 종료 태스크가 16:36:22 에 떴다. 서비스 0, 대기 검증 태스크 중단(사유 `verify-shutdown: … 결과 미상`), ASG 0, 보고서 오류 0 을 확인했다.
- 노트북은 결과 조회만 했고 종료는 AWS 가 했다.
- 본 실험은 22:30 전에 끝나, 정리는 운영자가 직접 했다(18:49~). 스케줄은 정리 PR 로 제거한다.

**드러난 결함과 교정(실행 뒤 — 기준값은 그대로, `criteria_aws_1408.json` `_post_run_corrections`).**
- 포워딩: `run.py forward` 의 `pkill` 이 aws CLI 만 죽이고, 옛 태스크 IP 로 붙은 `session-manager-plugin` 이 포트를 계속 쥐었다. R1 재시작 뒤 25분간 API 를 조회하지 못했다(업무·Airflow 는 정상 진행). 운영자가 plugin 을 끝내 복구했다. 이제 포트 점유자를 직접 끝낸다.
- 증거 시각: ListTasks 는 멈춘 태스크를 약 1시간만 보여 준다. 배치 끝(18:31) 수집에서 N1~N3 의 ECS 가 0 으로 읽혔다. 컨테이너가 남긴 invocation 기록(태스크 ARN)과 합쳐 셌다. 실행기는 이제 invocations 를 함께 모은다.
- F1 사전 식 `writes==0` 은 틀린 식이었다. canonical 파티션은 정제가 쓰고, F1 의 정제는 성공한다. "적재 업무 시작 0"으로 교정했고 교정 전 결과(false)도 함께 남겼다.
- 관측 구간: 호스트 기동 전의 운영자 메모(16:24)를 실험 시작으로 읽어 A2·A11 이 판정 불가로 나왔다. 주석 표시는 구간에서 뺐다.

**A5 원인과 가장 작은 다음 변경.** provider `EcsRunTaskOperator` 는 태스크가 끝나면 로그 수집 스레드를 멈추고 `join` 한다. 그 스레드는 `awslogs_fetch_interval`(기본 30초)만큼 잔다. 그래서 스텝마다 종료 감지가 약 30초 늦는다(5스텝 run 이면 약 2.5분). 다음 변경은 EdgeStep 에 `awslogs_fetch_interval` 5~10초를 주는 것이다. DAG 코드 변경이라 A5 는 그 뒤 다시 잰다.

**원인 대조와 수정(2026-10-01, #1027).**
- **코드 대조**(provider 9.36.0 `utils/task_log_fetcher.py`): `run()` 이 `while not stopped: time.sleep(interval)` 이라 `stop()` 뒤에도 남은 sleep 을 끝까지 잔다. `execute()` 는 waiter(`DescribeTasks`, 6초 주기)가 STOPPED 를 본 뒤 `stop()`·`join()` 을 부르므로 종료 판정 = max(waiter 감지, 다음 로그 수집 깨어남)이다.
- **실행 로그 대조**(원격 태스크 로그 `/airflow/edge-dev-airflow/tasks`, V_1520): collect·load·report 의 "ECS Task stopped, check status" 가 모두 로그 수집 시작 **+90.7초**(30초 간격 세 번째 깨어남)였다. 업무 로그 줄도 +60.3~60.4초에 한꺼번에 찍혔다. waiter 6초 주기와는 맞지 않는다.
- **표본**: 종료 감지 21개 중 정상 스텝 19개는 6.0초 하나와 20.3~33.3초다. 음수 2개는 주입 시나리오다(R1 재시작 −40.6, H1 보류 −26.3). 업무 증거가 온전한 정상 run(A1)만 보면 26.7·29.6·29.9·31.8·31.9초(n=5)다. N1~N3 는 증거를 모을 때 ECS 조회 창(약 1시간)이 지나 표본이 없다.
- **수정**: EdgeStep `awslogs_fetch_interval` 10초. 공식 이미지에서 provider 수집 스레드를 실측했다(태스크 수명 34/37/40초). stop→join 완료는 30초에서 26/23/20초, 10초에서 6/3/0초, 5초에서 1/3/0초였다. 태스크당 GetLogEvents 는 30초 3회, 10초 5회, 5초 8~9회였다. 5초는 호출이 약 3배인데 waiter 6초가 상한을 정해 얻는 것이 적어 10초로 정했다. ECS 상태 조회·종료 판정(`ecs_verdict`)은 바꾸지 않았다.

**A4 증거 경로(#1027).** health 표본을 운영자 PC 대신 호스트 관측기가 15초마다 남긴다(`nsenter -n` 으로 api-server 네임스페이스의 `/api/v2/monitor/health`, `/var/log/edge-obs/health.log`). 종료 장치는 호스트를 내리기 전에 관측 기록을 검증 버킷 `obs/shutdown/` 으로 보낸다. 운영자 PC 가 잠들거나 포워딩이 끊겨도 A2~A4 증거가 남는다. import 오류 수는 인증 API 라 PC 표본(실험 처음·끝)으로 본다. 재검증 기준은 `verify/criteria_aws_1408_a4a5.json`, 배치는 `run.py batch <exp> L` 이다.

**실험 중 감시(PC 무관, 2026-10-01).** 지금까지 실험 중 중단 기준(OOM·호스트 메모리·기존 RDS·업무 SFN 실패)은 운영자 PC 의 실행기가 단계 사이에만 봤다. 22:30·23:00 종료 장치는 예정 시각 종료일 뿐 중단 기준 감시가 아니다. 그래서 감시를 AWS 안으로 옮겼다.
- `run.py watchdog <exp>`가 검증 ops 태스크로 `shim verify-watchdog <22:25> <rds_stop>`을 띄운다. 30~60초마다 아래를 본다.
  - 호스트: SSM으로 관측기 기록을 읽어 최근 90초 MemAvailable 최소(64MiB 미만이면 중단)와 OOM 흔적(커널·docker·태스크 cgroup)을 본다.
  - 기존 RDS: 기준 파일의 `rds_stop` 그대로, 지속 분까지 본다.
  - 업무: 창 안에 실패한 업무 SFN을 본다.
  - Airflow: 서비스 태스크가 교체됐는지 본다.
- 기준을 넘거나, 한 감시가 3회 연속 실패하면(감시 상실) 종료 장치와 같은 절차를 바로 부른다(서비스 0 → 검증 태스크 중단 → 관측 기록 전송 → 호스트 0).
- 심장박동은 검증 버킷 `watchdog/heartbeat.json`에 남는다(배치마다 지우는 `state/` 밖). 업무 스텝(shim)은 심장박동이 120초 넘게 묵었거나 중단이 선언됐으면 업무를 시작하지 않는다(exit 75). 감시가 죽으면 검증 부하도 멈춘다. 게이트는 `run.py watchdog`가 감시를 띄우기 전에 남기는 표지(`watchdog/required.json`)가 있을 때만 걸린다. 그래서 감시를 쓰지 않는 이전 기준(V·B1~B3)은 종전대로 돈다. `run.py batch`는 기준 파일이 감시를 요구하면 신선한 심장박동이 있어야 시작하고, 감시를 쓰지 않는 기준이면 남은 표지·심장박동을 지운 뒤 시작한다.
- 로컬 검증: `verify/test_watchdog.py` 11건(data-pipeline 환경). 각 규칙을 지우면 실패하는 것을 확인했다. 호스트 조회 스크립트는 이전 회차의 실제 관측 기록에 Amazon Linux 2023 컨테이너로 돌려 값을 얻었다.

**비용(추정).**
- small 약 4.5시간(두 호스트 합) ≈ 0.09 USD, EBS·검증 Fargate(약 45개 × 1~3분)·이미지 pull·로그 ≈ 0.4 USD.
- 합계 **약 0.5 USD**(상한 3). 정리 뒤 잔여는 앱 시크릿·ECR·로그 보관, 약 0.6 USD/월.

**원자료 보존.**
- `local/results/aws/aws-small-1408-v/`: marks·health·V.json·V_invocations.json·rds-*·host-obs·ledger-backup(90표)·verify-bucket-final(212개)·deployinfo·suite.log·suite-v.sh.
- 원자료(원장 행·태스크 ARN·env 포함)는 커밋하지 않고 운영자 PC 에만 둔다(이전 회차와 같다). 판정 요약은 위 표가 전부다. 보존하려면 폴더째 압축해 팀 저장소에 올린다.
- 재현: `VERIFY_CRITERIA=criteria_aws_1408.json python verify/analyze_aws.py aws-small-1408-v`.


### 메타DB

- **현재 Terraform(검증 구성)은 기존 업무 RDS(`edge-dev`) 안의 전용 DB·역할이다**(위 "실제 AWS 단기 검증"). 상시 운영에 쓸지는 그 검증 결과와 아래 평가로 정한다 — 재사용은 아직 확정이 아니다.
- scheduler 는 쉬지 않고 메타DB 를 조회한다. 로컬 유휴 실측은 초당 트랜잭션 약 28, 버퍼 적중 약 246, 쓰기 0.4행이고, DB 크기는 10MiB 다(업무 실행 중은 더 많다).

**기존 RDS 재사용 평가(2026-09-29, 읽기 전용 지표·기존 기록 — 실제 부하를 주지 않았다)**

| 항목 | 값 | 근거 |
|---|---|---|
| 과거 메모리 사고 | 2026-08-10 db.t4g.micro(1GiB)에서 하루 5회 다운. 커넥션 32→49 로 늘며 메모리가 먼저 끊겼다(연결 상한 79 는 도달 전) | ALPHA-919·924. 조치: db.t4g.small 상향, FreeableMemory 알람, RDS 이벤트 구독, 로테이션 창 고정 |
| 현재 사양 | db.t4g.small(2GiB), PostgreSQL 16, gp3 20GB(여유 15.1GB), `max_connections` = 메모리 파생(약 150~200) | describe-db-instances·파라미터 그룹(default.postgres16) |
| 여유 메모리 | 5분 최저 548MiB(09-18 09:20), p1 567 · p50 605. 상향 뒤 일 최저 575~604 로 평탄(감소 추세 없음) | CloudWatch 14일·60일 |
| 연결 | 5분 최대 35(09-22 09:45), p50 20, 장중 중앙값 22 | CloudWatch 14일 |
| CPU | 5분 최대 96.5%(09-22 09:25, 장 시작), p99 76%, p50 5.5%. 크레딧 잔고 최저 528/576, 초과 과금 0 | CloudWatch 14일 |
| 지연·스왑 | 쓰기 지연 최대 84ms(p99 9ms), 읽기 최대 12ms, Swap 최대 38MiB | CloudWatch 14일 |
| Airflow 가 더할 것(로컬 실측) | 연결 합계 3~5개(scheduler 1·dag-processor 1·api 1~2, 조정 설정 C1). 풀 설정값 합(3 × (2+3) = 15)이 아니다. 유휴 초당 약 28 트랜잭션 | local/micro E3·E5 |

- **판단: 현재 정보로는 재사용 가능·불가를 확정하지 않는다.**
  - 연결 수는 제약이 아니다(35 + 5 ≪ 상한).
  - 제약은 메모리와 장 시작 CPU 다. 여유 메모리 548MiB 에서 Postgres 백엔드 3~5개(백엔드당 수 MB + 조회 때 work_mem)와 Airflow 메타 페이지(수십 MB 이하)를 더하는 것은 수치상 작다. 그러나 이 DB 는 08-10 에 메모리로 죽은 이력이 있다. 추가량의 **실제 크기는 로컬 Postgres 로는 잴 수 없다**(RDS 의 FreeableMemory 는 OS·캐시 회수를 포함한다).
  - 장 시작(09:25 전후) CPU 96% 구간에 Airflow 09:35 슬롯의 조회가 더해진다.
- **DB·계정을 나눠도 자원은 격리되지 않는다.** 같은 인스턴스의 메모리·CPU·IOPS·`max_connections`·파라미터 그룹을 나눠 쓴다. 재부팅·유지보수·마이너 업그레이드·PITR(인스턴스 단위 복원)도 함께 겪는다. `modules/rds` 변경은 머지 즉시 재부팅이다.
- **재사용한다면(제안, 미적용).**
  - 전용 DB `airflow`·전용 역할 `airflow`(마스터 아님), `ALTER ROLE airflow CONNECTION LIMIT 10`·`SET statement_timeout = '30s'`·`SET idle_in_transaction_session_timeout = '60s'`.
  - 비밀번호는 Airflow 앱 시크릿에 둔다(RDS 관리형 로테이션을 타지 않는다).
  - Airflow 풀 2/3 유지.
- **관측 항목:** FreeableMemory 5분 최저, SwapUsage, DatabaseConnections(역할별 `pg_stat_activity`), CPUUtilization·CPUCreditBalance, Read/WriteLatency, 업무 레인 실패·지연.
- **중단 기준(하나라도):**
  - FreeableMemory 5분 최저 < 400MiB(현재 최저 548 에서 150MiB 이상 감소)
  - SwapUsage > 100MiB
  - Airflow 역할 연결 > 10
  - 장중 쓰기 지연 p99 > 50ms
  - 업무 레인에 DB 원인 실패가 1건이라도 생김
  - → Airflow 를 desired 0 으로 멈추고 별도 인스턴스로 옮긴다.
- **실제 검증 과제:** 장 마감 뒤 Airflow on/off 를 번갈아 FreeableMemory·연결·CPU 차이를 잰다. 이어서 장중 1일을 관측한다. 그 결과로 재사용을 정한다.

- 나눠도 공유하는 것: VPC·data 서브넷, 알람 SNS 토픽. 메타DB 장애는 Airflow 만 멈춘다. 반대로 업무 DB 장애는 여전히 Airflow 런의 원장 스텝(plan·report·wrapper)을 실패시킨다. 원장이 업무 DB 에 있기 때문이다.
- 백업: RDS 자동 백업 7일(PITR). 복구는 PITR 로 새 인스턴스를 만든 뒤 `module "airflow_rds"` 를 그 인스턴스로 바꾸고 `deploy-airflow` 를 다시 돌린다.
- 메타DB 를 잃으면 사라지는 것: DAG run·task 이력, XCom(보류 사유 포함), pause 상태. **업무 상태의 정본은 원장(업무 DB)이다.** 보류는 `report` 가 원장 `EXECUTION_HOLD` 로 옮기고, 결말 없는 실행은 주기 Reconciler 의 sweep 이 찾는다. 새 메타DB 에서 DAG 는 pause 로 다시 생긴다(`dags_are_paused_at_creation`). 잃은 이력 구간의 보류는 원장으로 판단한다.
- 비밀번호 로테이션: `modules/rds` 가 토 09:00~12:00 KST 에 돌린다. 연결 문자열은 태스크 기동 때 한 번 만들어진다. 로테이션 뒤에는 **새** 연결부터 실패한다 — 헬스체크는 새 연결을 열지 않으므로(위 "헬스체크") 풀 재활용 시점까지 healthy 일 수 있고, 구성요소가 새 연결에서 실패해야 태스크가 교체된다. 장이 없는 시간이지만 실제 AWS 검증 항목이다.

### 장애 범위와 호스트 교체

- **호스트 1대가 단일 장애점이다.** 호스트가 죽으면 Airflow 전체(스케줄·task 프로세스)가 멈춘다. 이미 뜬 업무 ECS 태스크(worker 클러스터, Fargate)는 멈추지 않고 끝까지 돈다.
- 복구 흐름: ASG 가 새 호스트를 띄운다 → ECS 가 서비스 태스크를 배치한다 → scheduler 가 heartbeat 끊긴 task 를 실패로 보고 재시도한다 → `EdgeStep` 이 `startedBy` 로 앞 ECS 태스크를 찾아 재접속하거나, 결말을 모르면 보류한다(위 "재시도·재접속·보류 정책").
- 멈춘 동안의 슬롯은 돌리지 않는다(`catchup=False`, 소급 수집 불가). 수집 공백으로 남는다. 이 레인의 응답은 그날 누적이라 다음 슬롯이 대부분 회수한다.
- 계획된 교체(AMI 갱신 등)는 장 마감 뒤에 한다.
  1. `envs/dev/main.tf` `ami_id` 를 바꿔 머지한다(launch template 만 바뀐다. 도는 호스트는 그대로다).
  2. 교체를 시작한다. 새 호스트를 먼저 띄우고, managed draining 이 태스크를 옮긴 뒤 옛 호스트를 끈다.
     ```bash
     aws autoscaling start-instance-refresh --auto-scaling-group-name edge-dev-airflow-host \
       --preferences '{"MinHealthyPercentage":100,"MaxHealthyPercentage":200}'
     aws autoscaling describe-instance-refreshes --auto-scaling-group-name edge-dev-airflow-host
     ```
  3. 확인: 서비스 태스크 1개 RUNNING·세 컨테이너 HEALTHY, UI 로그인, 과거 run·task 로그가 보이는지.

### 비밀값과 IAM

| 역할 | 권한 |
|---|---|
| 호스트(EC2) | ECS 에이전트(`AmazonEC2ContainerServiceforEC2Role`), SSM(`AmazonSSMManagedInstanceCore`). IMDSv2 강제, 태스크는 호스트 IMDS 차단(`ECS_AWSVPC_BLOCK_IMDS`) |
| execution | 이미지 pull·awslogs, 시크릿 2개 읽기(메타DB RDS 관리형 시크릿, `edge-dev-airflow/app`) |
| task(Airflow 프로세스) | 업무 태스크 정의 4종(kis·bigkinds·rds·ops)의 `RunTask`(worker 클러스터 한정), `ListTasks`·`DescribeTasks`·`StopTask`(같은 클러스터), `DescribeTaskDefinition`, 업무 역할 3개 `PassRole`(ecs-tasks 한정), 알람 토픽 `Publish`, 업무 컨테이너 로그 읽기, 자기 task 로그 쓰기. 업무 S3·DB 권한 없음. 검증 자원이 켜져 있으면 검증 태스크 정의·검증 클러스터 몫이 더해진다 |
| 배포(기존 `edge-dev-gha-schema-migrate` 역할에 정책 추가) | `edge/airflow` push, 마이그레이션 태스크 `RunTask`, 서비스 `UpdateService`, Airflow 역할 `PassRole`, 구성요소 로그 읽기 |

- `edge-dev-airflow/app` 시크릿의 값은 Terraform 이 만들지 않는다(state 에 평문을 남기지 않는다). 최초 1회 넣는다(아래 "최초 구축"). 키: `jwt_secret`(구성요소 간 Execution API 토큰 서명), `api_secret_key`, `admin_password`.
- 메타DB 연결 문자열은 `deploy/entrypoint.sh` 가 만든다. 비밀번호는 URL 인코딩해 파일(`/opt/airflow/sql_alchemy_conn`, 0600)에 쓰고, Airflow 는 `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_CMD` 로 읽는다. env 로 export 하면 entrypoint 를 거치지 않는 ECS 헬스체크(`docker exec`)가 연결을 못 보고 sqlite 로 떨어진다. 로컬 리허설에서 헬스체크가 실제로 그렇게 실패해서 고쳤다.

### 로그·상태 점검·알림

| 무엇 | 어디 | 보존 |
|---|---|---|
| 구성요소 stdout(api-server·scheduler·dag-processor·migrate) | CloudWatch `/ecs/edge-dev-airflow` | 30일 |
| task 로그(Airflow remote logging) | CloudWatch `/airflow/edge-dev-airflow/tasks` — UI 에서 그대로 보인다 | 30일 |
| 업무 컨테이너 로그 | 기존 `/ecs/edge-dev-data-pipeline`. `EdgeStep` 이 task 로그로 끌어온다 | 14일(기존) |
| 검증 태스크 로그 | `/ecs/edge-dev-airflow-verify` | 14일 |

- 헬스체크: 세 컨테이너 모두 `/api/v2/monitor/health` 의 자기 구성요소 status(위 "헬스체크", 60초 간격, 3회 실패면 ECS 가 태스크를 교체한다).
- 알림(모두 기존 파이프라인 SNS 토픽):
  - `edge-dev-airflow-service-down`: 서비스의 ECS CPU 지표가 10분 끊기면 울린다(태스크 없음·교체 실패·호스트 사망).
  - 메타DB 여유 메모리 알람·RDS 이벤트 구독: `modules/rds` 가 붙인다.
  - DAG 실패: 운영 DAG 의 `on_failure_callback`(기존).
- ⚠️ 서비스가 desired 0 인 동안(최초 구축 직후, 첫 배포 전)은 서비스 중단 알람이 울린다. 첫 배포로 풀린다.

### 최초 구축(한 번)

1. `foundation` apply(수동) — `edge/airflow` ECR 저장소.
2. 이 PR 머지 → `terraform-apply` 가 클러스터·호스트·서비스(desired 0)·검증 자원을 만든다(새 RDS 없음). 같은 머지에서 `deploy-airflow` 도 뜬다. 서비스가 desired 0 이면 배포를 건너뛴다.
3. 시크릿 값과 전용 DB·역할: `python3 verify/run.py secrets`(boto3 가 있는 인터프리터 — 예: `src/.venv/bin/python`)(없는 키만 만든다, 값은 찍지 않는다) → `python3 verify/run.py setup`(관리 태스크가 전용 DB·역할 생성, 검증 원장 스키마 복제, 권한 분리 확인).
4. `deploy-airflow` 를 workflow_dispatch `start_service=true` 로 실행한다(장 마감 뒤). 순서는 이미지 빌드 → 마이그레이션 태스크 exit 0 → 서비스 새 리비전·desired 1 → services-stable.
5. 확인: 세 컨테이너 HEALTHY, UI 로그인, DAG 두 개(운영·검증)가 **pause**, import error 0, 예제 DAG 없음, dag run 0.

### 배포·롤백

- 평시 배포: `src/apps/cloud/airflow/dags/**`·`Dockerfile`·`deploy/entrypoint.sh` 가 dev 에 머지되면 `deploy-airflow` 가 돈다. 이미지 태그는 커밋 SHA 다. 서비스 desired 가 0(운영자 정지)이면 리비전만 바꾸고 켜지 않는다 — 켜는 것은 `start_service=true` 뿐이다. 마이그레이션이 실패하면 서비스를 바꾸지 않는다. 서비스 교체가 실패하면 ECS circuit breaker 가 이전 리비전으로 되돌리고, 워크플로는 실패로 끝난다.
- 롤백: `deploy-airflow` workflow_dispatch `image_tag=<이전 커밋 SHA>`. 빌드 없이 같은 경로를 탄다.
- Airflow **버전을 내리는** 롤백은 이 경로로 하지 않는다. 새 버전의 `db migrate` 가 스키마를 올렸기 때문이다. 업그레이드 전에 메타DB 스냅샷을 찍고, 되돌릴 때는 그 스냅샷으로 복원한 뒤 옛 이미지를 배포한다.
- Terraform 이 태스크 정의(환경·자원·역할)를 바꾸면 **다음 배포부터** 반영된다. 서비스의 리비전과 desired 는 CD 가 소유한다(`ignore_changes`). 바로 반영하려면 workflow_dispatch 로 같은 태그를 다시 배포한다.

### UI 접근

```bash
iid=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names edge-dev-airflow-host \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)
tip=$(aws ecs describe-tasks --cluster edge-dev-airflow --tasks $(aws ecs list-tasks --cluster edge-dev-airflow \
  --service-name edge-dev-airflow --query 'taskArns[0]' --output text) \
  --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`].value' --output text)
aws ssm start-session --target "$iid" --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "host=$tip,portNumber=8080,localPortNumber=18080"     # → http://127.0.0.1:18080 (admin)
```

로컬에 Session Manager 플러그인이 필요하다. 태스크 IP 는 태스크가 바뀔 때마다 달라진다.

### 월 비용(서울 리전 공개 단가 2026-09-25 게시판, 730시간 상시 가동)

**늘어나는 비용 — 안별 비교(USD/월)**

| 항목 | 단가 | #981 원안: medium + 별도 RDS | #981 2차안: small + 별도 RDS | small + 기존 RDS | **현재 Terraform(검증 구성): micro + 기존 RDS**(로컬 가정으로는 불통과 — 실제 AWS 로 판정) |
|---|---|---|---|---|---|
| EC2 | medium 0.0416·small 0.0208·micro 0.0104 /h | 30.37 | 15.18 | 15.18 | 7.59 |
| EBS gp3 30GiB(ECS AMI 루트 최소) | 0.0912 /GB·월 | 2.74 | 2.74 | 2.74 | 2.74 |
| RDS db.t4g.micro + gp3 20GB | 0.025 /h · 0.131 /GB·월 | 20.87 | 20.87 | 0(기존 인스턴스) | 0 |
| Secrets Manager | 0.40 /개 | 0.80(2개) | 0.80 | 0.40(앱 1개 — DB 비밀번호 포함) | 0.40 |
| CloudWatch Logs 수집(추정 2~4GB) | 0.76 /GB | 1.5~3.0 | 1.5~3.0 | 1.5~3.0 | 1.5~3.0 |
| CloudWatch 알람 | 0.10 /개 | 0.20 | 0.20 | 0.10 | 0.10 |
| ECR·NAT 처리량 | 0.10 /GB·0.059 /GB | 0.5~1.1 | 0.5~1.1 | 0.5~1.1 | 0.5~1.1 |
| **합계** | | **약 57~59** | **약 42~44** | **약 20~23** | 약 13~15 |

- **CPU 크레딧:** 이 계정의 t4g 기본 크레딧 방식은 **unlimited** 다(`get-default-credit-specification` 확인). 평균 CPU 가 기준선을 넘으면 넘은 만큼 **0.04 USD/vCPU·시간**을 낸다(서울 가격표 `APN2-CPUCredits:t4g`).
  - 기준선(인스턴스 전체)은 micro 0.2·small 0.4 vCPU 다(AWS 버스터블 문서 값).
  - 예: small 에서 평균 0.5 vCPU 면 (0.5 − 0.4) × 730 × 0.04 ≈ 2.9 USD/월.
  - 로컬 C1 평균은 0.03~0.04 개발기 코어였다. Graviton2 에서의 값은 실제 AWS 검증에서 `CPUCreditUsage`·`CPUSurplusCreditsCharged`로 확인한다.
- **할인·무료 혜택:** 반영하지 않았다(계정 적용을 확인하지 않았다).
- **늘지 않는 것(0원):** 공인 IPv4 0개(private 서브넷·EIP 없음), ALB 없음, VPC 엔드포인트 추가 없음, SSM Session Manager 무료. ECS 관리 요금도 없다(ECS on EC2 는 EC2·EBS 만 과금한다, Managed Instances 아님).
- **기존 RDS 재사용 안의 숨은 비용:** 인스턴스 요금은 0이지만, 영향이 크면 `edge-dev` 를 상향해야 할 수 있다. db.t4g.small → medium 이면 +37.23 USD/월(0.051 → 0.102 /h, 서울 공개 단가). 그러면 별도 micro(20.87)보다 비싸다. 그래서 재사용은 위 "기존 RDS 재사용 평가"의 실제 검증 뒤에 정한다.

**일시적으로 늘어나는 것**
- 호스트 교체 때 두 번째 small: 약 15분 × 0.0208 ≈ 0.005/회.
- 배포마다 마이그레이션 Fargate ARM64 약 1분: 0.01 미만.
- 격리 검증 태스크(Fargate x86 0.25 vCPU·0.5GB, 수백 회 × 1~2분): 합계 약 0.1~0.5.

**이미 지출 중인 것(이번 작업과 무관):**
- NAT 게이트웨이 시간 요금(약 43.07).
- 업무 DB `edge-dev`.
- worker 클러스터의 업무 Fargate 태스크. 실행 주체가 SFN 이든 Airflow 든 같은 태스크가 돈다.
- 전환 뒤에는 이 레인의 SFN 상태 전이 요금이 사라진다(소액).

**실제 AWS 단기 검증 비용:** 위 "실제 AWS 단기 검증(t4g.micro + 기존 RDS)"의 예상 비용 표.

### 실제 AWS 검증(격리)

검증은 `edge_investor_intraday_verify` DAG 로 한다. 운영 DAG 와 **같은 `build_dag`·`EdgeStep`**(제출 전 확인·제출·추적·보류·재접속)을 쓰고, 격리 자원(`modules/airflow/verify.tf`)에만 닿는다.

- 원장: 검증 DB `edge_verify`(메타DB 인스턴스의 별도 DB. 업무 RDS 에는 SG 조차 없다). 스키마는 업무와 같은 `migrations-cloud` 다.
- 레이크: 검증 버킷(30일 만료). 업무 레이크는 재생 입력 한 파일을 **읽기만** 한다.
- KIS: 호출하지 않는다. 검증 태스크 정의에 KIS 시크릿이 없고, 수집 스텝은 shim 이 저장 응답을 재생한다.
- ECS: 검증 태스크는 Airflow 클러스터(Fargate)에서 돈다. 업무 Reconciler sweep 이 보는 worker 클러스터와 섞이지 않는다. 같은 슬롯이면 run_id 가 업무와 같아지므로 섞이면 안 된다.
- 업무 코드: 배포된 data-pipeline 이미지 그대로 쓴다. shim 은 소스 교체·계수·장애만 더한다.

로컬 확인(이 PR, 실제 AWS 아님):
- shim 스모크: 로컬 Postgres(SSL)·S3 대역(localstack)·Flyway 로 실제 `data_pipeline` 코드를 돌렸다.
- 결과:
  - 검증 DB 생성, 스키마 70개 적용, 종목 366개 등록.
  - plan(AIRFLOW) → 수집(재생 1,779행) → **같은 수집 재실행은 `DUPLICATE_SKIP`**(업무 실행 수 증가 0) → 정제 → 적재 장애 주입(exit 1, 실패 attempt) → 적재 1,779행.
  - 원장 조회·reset 정상.

검증 항목과 방법(승인 뒤 실행 — 결과는 이 절과 학습 문서 §32.12 에 기록한다):

| 항목 | 방법 | 실제 관측 / 모의 주입 |
|---|---|---|
| ECS 제출·시작·정상 종료·로그 조회 | 오늘 슬롯 trigger, 장애 없음 | 실제 |
| 확정된 업무 실패 vs 결과 미상 | 컨테이너 `{"exit": 1}` / 실행 중 `aws ecs stop-task`(StopTask 137) | 실패 exit 는 주입, 강제 종료는 실제 |
| 제출 응답 유실 → 중복 생성 없음 | conf `runtask_response_lost` | 주입(RunTask 는 실제로 태스크를 만든다) |
| 상태 조회 실패 → 새 작업 없이 보류 | `runtask_response_lost` + `list_tasks_error_after_submit` | 주입 |
| 구성요소 재시작 뒤 재접속 | 수집 `sleep_in_step` 중 `aws ecs update-service --force-new-deployment` | 재시작은 실제, 긴 스텝은 주입 |
| DAG timeout·worker 사망 뒤 원장 보존 | `sleep_in_step` > 900초로 dagrun_timeout, 뒤 검증 ops `reconcile`(sweep) | timeout 은 실제, 긴 스텝은 주입 |
| HOLD 에서 retry·clear·다른 run → 업무 중복 없음 | 보류 뒤 REST clear·새 run trigger | 실제 |
| 종료 확인·보류 해제 뒤 정상 복구 | README "보류 해제와 수동 복구" 절차 그대로 | 실제 |
| EC2 교체 뒤 메타데이터·DAG·로그·추적 복구 | 실행 중 `start-instance-refresh` | 실제 |

판정은 Airflow 상태 표시가 아니라 **네 가지 대조**로 한다: 실제 ECS 태스크 수(`run.py evidence`), 업무 실행 수(`state/business_starts` — 호출 전에 남는다), 원장 상태(`verify-ledger`), 산출물 쓰기(`state/partition_writes`). `clientToken` 이나 ECS 조회를 쓴다고 해서 정확히 한 번 실행이 보장되지는 않는다. KIS 에는 멱등 키가 없고, 저장소에는 fencing·CAS 가 없다(ALPHA-1057). 검증이 보이는 것은 "이 경로들에서 새 태스크를 띄우지 않았다"까지다.

```bash
cd src/apps/cloud/airflow
docker build --build-arg BASE=<배포된 data-pipeline 이미지 다이제스트> -t <ecr>/edge/airflow:verify verify && docker push <ecr>/edge/airflow:verify
EDGE_LAKE_BUCKET=<레이크(읽기)> python verify/run.py setup
AIRFLOW_PASSWORD=... python verify/run.py trigger --slot 10:05 --conf '{"faults": {"collect": {"runtask_response_lost": [1]}}}'
python verify/run.py evidence --slot 10:05 --dag-run <trigger 가 출력한 dag_run_id>
```

## 운영 전환·롤백 절차(장중 수급, 아직 실행하지 않았다)

**불변 조건.** 한 레인의 스텝은 어느 순간에도 한 주체만 실행한다. 여러 슬롯이 같은 거래일 canonical 파티션을 CAS 없이 병합하기 때문이다(ALPHA-1057). 주체가 바뀌는 순간 기존 주체의 실행이 **실제로 끝났음을 확인하기 전에는 새 주체를 켜지 않는다.** DAG pause나 스케줄 DISABLED는 새 실행 생성을 멈출 뿐이고, 이미 뜬 ECS 태스크를 멈추지 않는다.

**종료 확인(전환·롤백·보류 해제 공통).** 아래 여섯 가지를 **모두** 확인해야 끝난 것이다. 원장 기록은 프로세스 상태의 증거가 아니고, lock 부재도 쓰기 종료의 증거가 아니다(lock 커넥션만 끊겨도 풀린다).

```sql
-- ① 원장상 끝나지 않은 업무 시도(이 레인) — 어느 주체·어느 Airflow try 인지까지
SELECT r.run_key, r.orchestrator, et.task_key, a.ecs_task_arn, a.orchestrator_attempt_ref, a.started_at
  FROM ops_task_attempt a JOIN ops_expected_task et USING (expected_task_id)
  JOIN ops_pipeline_run r USING (pipeline_run_id)
 WHERE r.pipeline_type = 'investor-intraday' AND a.execution_status = 'RUNNING';
-- ② 이 레인 스텝의 실행권을 쥔 세션(StepLock 키 = sha256('ops-step:'||task_key) 앞 8바이트)
WITH k AS (
  SELECT t AS task_key,
         ('x' || substr(encode(sha256(convert_to('ops-step:' || t, 'UTF8')), 'hex'), 1, 16))::bit(64)::bigint AS key
    FROM unnest(ARRAY['INVESTOR_INTRADAY_COLLECTION_KIS', 'NORMALIZE_INVESTOR_INTRADAY',
                      'LOAD_INVESTOR_INTRADAY']) AS t)
SELECT k.task_key, l.pid, a.backend_start, a.state
  FROM k
  JOIN pg_locks l ON l.locktype = 'advisory' AND l.objsubid = 1
   AND l.classid::bigint = ((k.key >> 32) & 4294967295)
   AND l.objid::bigint = (k.key & 4294967295)
  LEFT JOIN pg_stat_activity a ON a.pid = l.pid;
-- ⑥ 풀리지 않은 보류(이 레인). kind 별 의미:
--    ECS_STATE_UNKNOWN — 해제 전까지 같은 작업의 새 업무를 막는다(종료 확인 대상). evidence: reason·run_key
--    OPEN_ATTEMPT      — 컨테이너가 보류한 기록(막는 것은 ①의 RUNNING 시도). evidence: run_id·blocking
--    RESULT_UNKNOWN    — 종료는 확인, 결과 미상(아무것도 막지 않는다). evidence: reason·run_key
SELECT i.dedupe_key, i.evidence->>'kind' AS kind, et.task_key, r.run_key, i.evidence->>'reason' AS reason,
       i.evidence->'blocking' AS blocking, i.first_seen_at, i.occurrence_count
  FROM ops_reconciliation_issue i JOIN ops_expected_task et ON et.expected_task_id = i.scope_key
  JOIN ops_pipeline_run r USING (pipeline_run_id)
 WHERE i.issue_type = 'EXECUTION_HOLD' AND i.status = 'OPEN' AND r.pipeline_type = 'investor-intraday';
```

```bash
# ③ 실제 ECS 태스크 — 원장이 RUNNING 을 못 남긴 컨테이너(PENDING 포함)와 종료 요청 뒤 아직 멈추는 중인 태스크
#    (desiredStatus=STOPPED, lastStatus≠STOPPED)도 본다. 태스크 정의(특히 rds)는 다른 레인과 같이 쓰므로 family 가
#    아니라 **이 레인의 명령·env** 로 거른다. 한 줄 = 태스크 하나(ARN·상태·명령·env 값). 출력이 비어야 한다.
q='tasks[?lastStatus!=`"STOPPED"`].[taskArn,lastStatus,join(`" "`,overrides.containerOverrides[0].command || `[]`),join(`" "`,overrides.containerOverrides[0].environment[].value || `[]`)]'
check3() {   # aws 호출이 하나라도 실패하면 1 — 빈 출력이 "조회 실패"를 "종료 확인"으로 바꾸지 않게 한다
  for f in kis bigkinds rds ops; do for d in RUNNING STOPPED; do
    arns=$(aws ecs list-tasks --cluster <cluster> --family <name>-$f --desired-status $d --query 'taskArns[]' --output text) || return 1
    { [ -z "$arns" ] || [ "$arns" = None ]; } && continue
    printf '%s\n' $arns | xargs -n 100 aws ecs describe-tasks --cluster <cluster> --output text --query "$q" --tasks || return 1
  done; done
}
if out=$(check3); then
  printf '%s\n' "$out" | grep -E 'investor-estimate|load-investor-intraday|investor-intraday' || echo "③ 이 레인의 살아 있는 태스크 없음"
else
  echo "③ 조회 실패 — 종료를 확인하지 못했다(새 주체를 켜지 않는다)" >&2
fi
# ④ 기존 SFN 실행
aws stepfunctions list-executions --state-machine-arn <investor-intraday SFN> --status-filter RUNNING
# ⑤ Airflow 의 진행·대기 run(REST, dag_run 상태)
curl -s "$AIRFLOW/api/v2/dags/edge_investor_intraday/dagRuns?state=running&state=queued" -H "Authorization: Bearer $TOKEN"
```

- ①·③·④·⑤가 비어 있고, ②에 행이 없고, ⑥에 **ECS_STATE_UNKNOWN**이 없으면 종료를 확인한 것이다. ⑥의 OPEN_ATTEMPT·RESULT_UNKNOWN은 새 실행을 막지 않지만, 그 슬롯의 산출물을 "보류 해제와 수동 복구" 4로 확인한 뒤 같은 SQL로 RESOLVED 처리한다(OPEN_ATTEMPT는 다음 Airflow 실행이 게이트를 통과하면 자동으로 닫힌다). ②의 키 계산은 로컬에서 Python `StepLock` 키와 같은 값인지 확인했다.
- ①에 행이 남았는데 ③에 그 ARN이 없으면, `describe-tasks`로 `lastStatus=STOPPED`를 확인하고 `OPS_RUN_KEY`로 reconcile을 한 번 돌려 원장을 닫는다. ⑥은 "보류 해제와 수동 복구" 6의 절차로만 푼다.
- 하나라도 확인할 수 없거나 남아 있으면 **새 주체를 켜지 않는다.** 기다리는 것은 확인을 기다리는 것이지, 시간이 지나면 끝난 것으로 보는 것이 아니다.
  - SFN은 TimeoutSeconds가 1500이라 보통 25분 안에 끝난다.
  - Airflow run은 `dagrun_timeout` 1500초다. 다만 ECS 태스크 자체에는 시간 상한이 없다.
  - 25분이 지나도 ③에 남아 있으면 `aws ecs stop-task`로 종료를 **요청**하고, `describe-tasks`가 `STOPPED`를 보일 때까지 ①·③을 다시 확인한다. 강제 종료한 태스크의 결과는 미상이다(보류 해제 절차 4·5로 산출물을 본다).
- **파티션 쓰기 종료**는 ③(프로세스 부재, `STOPPED`)으로만 확정한다. 원장 attempt 종료나 lock 해제는 쓰기 종료를 보증하지 않는다.
- **상태를 확인할 수 없으면 전환을 보류하고 수집 공백을 받아들인다.** 두 주체를 함께 켜 두는 것보다 그 슬롯을 비우는 쪽을 택한다.

### 전환 전 대조 — 최신 dev SFN(#958 뒤)과 Airflow 경로의 의미

2026-09-28 dev 의 `investor_intraday_pipeline.tf`(ASL)와 DAG 를 대조했다. 업무 동작은 바꾸지 않았다.

| 상황 | SFN(현행) | Airflow | 같은가 |
|---|---|---|---|
| 수집 비0(부분·실패) | NotifyRawPartial → 정제 계속, 런 FAILED | 정제 계속(`ALL_DONE_MIN_ONE_SUCCESS`), verdict 런 FAILED | 같다 |
| 정제 exit 2 | NotifyNormalizePartial → 적재 계속, 런 FAILED | 적재 계속(`partial_exit_codes=(2,)`), 런 FAILED | 같다(#958 이 SFN 을 고쳐 일치) |
| 적재 exit 2 | FeatureCheckResults 실패 → NotifyFailure, 런 FAILED | 성공 처리 뒤 verdict 런 FAILED | 결과 같음(적재 뒤 스텝 없음). 알림은 SFN SNS 1건 / DAG 실패 SNS 1건 |
| 기동 실패(TaskFailedToStart)·exit 75 | 재시도 없음(ASL Retry 0) → 런 FAILED | 재시도(다음 try 가 새 태스크) | **다르다(의도)** — 업무 미시작만 다시 띄운다 |
| 결과 미상(StopTask·신호 종료·조회 불가) | 런 FAILED | 보류(HOLD), 같은 task_key 이후 실행 차단(76) | **다르다(의도)** — 이번 초기 운영 정책 |
| 런 판정 기록 | Reconciler 가 DescribeExecution 으로 채움 | `report` 가 보고, 주기 Reconciler 가 원장 증거로 보완 | 같은 컬럼·같은 run_key |
| 원장 행 | `orchestrator='SFN'`, `sfn_execution_arn` | `orchestrator='AIRFLOW'`, `orchestrator_run_ref`·`orchestrator_attempt_ref` | 소비자(콘솔 R02 포함)는 둘 다 읽는다 |

- 부분 실패 적재 정책(선택 1/2)은 여전히 팀 결정 항목이다(아래 "활성화 전 결정" 1). 지금은 두 경로 모두 선택 2(유효 행 적재 + 런 FAILED)다.

### 전환(SFN → Airflow)

전제:
- 원장 마이그레이션(`V202609271100`·`1110`·`1120`)이 dev에 **적용 완료**(schema-migrate 초록 + `flyway_schema_history` 확인)이고 data-pipeline 이미지가 배포돼 있다.
- DAG는 **pause 상태**로 배포돼 있다.
- 아래 "활성화 전 결정·미해결 조건"이 모두 해결됐고 "실제 환경 검증"의 성공 기준을 충족했다.

1. **경계.** 마지막 SFN 슬롯은 전날 14:35다. 첫 Airflow 슬롯은 다음 거래일 09:35다. 장 마감 뒤(15:00 이후)에 진행한다.
2. **새 실행 생성 중단.** `infra/terraform/envs/dev/main.tf`의 `module "data_pipeline"` 인자 `investor_intraday_orchestrator = "SFN"`을 `"AIRFLOW"`로 바꿔 apply한다.
   - 이 레인의 EventBridge 스케줄 5개가 DISABLED가 된다.
   - Reconciler 슬롯 대조(`OPS_INVESTOR_INTRADAY_SCHED_HHMM`)는 유지된다. 그래서 Airflow 런도 같은 run_key로 대조되고, Airflow가 슬롯을 안 돌리면 PLANNER_MISSING이 열린다.
   - `investor_intraday_schedule_state = "DISABLED"`로 끄면 **안 된다.** 그 값은 슬롯 대조까지 끈다.
3. **종료 확인.** 위 ①~⑥을 모두 확인한다. SFN RUNNING은 중단하지 않고 끝날 때까지 기다린다. 중단하면 그 슬롯의 병합이 반쪽으로 남는다. 확인할 수 없는 항목이 하나라도 있으면 5로 가지 않는다.
4. **슬롯 소유 확인.** 전날 run_key 5개가 모두 `orchestrator='SFN'`이고, 다음 거래일 run_key가 아직 없는지 본다.
5. **DAG unpause.** catchup이 없고 `run_immediately=timedelta(0)`이라 **다음** cron 슬롯부터 실행된다. 지난 14:35 슬롯을 다시 부르지 않는다.
   - 로컬 확인: 수정 전에는 11:25 슬롯이 11:45 unpause에 즉시 실행됐다. 수정 뒤에는 slot+30분에 unpause해도 run 0개였다.
6. **첫날 대사.**
   - 그날 run_key 5개가 모두 `orchestrator='AIRFLOW'`여야 한다.
   - SFN 실행 0건, LAUNCH_CONFLICT 0건이어야 한다.
   - `investor_flow_intraday` 행 수를 전주 같은 요일과 비교한다.

### 롤백(Airflow → SFN)

롤백 조건: Airflow 환경 장애로 슬롯 실행이 불가능할 때, 또는 원장·산출물 대사가 불일치할 때.

1. **새 실행 생성 중단.** DAG pause.
   - 이미 queued·running인 run은 멈추지 않는다.
   - run을 failed로 표시해도 ECS 태스크는 멈추지 않는다(`EdgeStep.on_kill`이 StopTask를 부르지 않는다 — 업무 도중 끊으면 결과 미상이 된다). 멈춰야 하면 `aws ecs stop-task`로 따로 요청하고 ③에서 `STOPPED`를 확인한다.
2. **종료 확인.** ①~⑥을 모두 확인한다.
   - Airflow가 죽어서 ⑤를 볼 수 없으면 ECS(③)·원장(①)·lock(②)·보류(⑥)로 판단한다. 넷 다 비어야 3으로 간다.
   - 판단할 수 없으면 SFN을 켜지 않는다. 그 사이 슬롯은 공백으로 두고 5의 회수 대상으로 남긴다. 장중이어도 같다.
   - ⑥에 ECS_STATE_UNKNOWN이 있으면 SFN을 켜기 전에 "보류 해제와 수동 복구" 2~6을 먼저 끝낸다. SFN 경로는 보류를 확인하지 않으므로, 풀지 않은 채 켜면 확인되지 않은 실행과 같은 파티션을 쓸 수 있다.
   - Airflow가 죽어서 report가 돌지 못했으면 보류가 원장에 없을 수 있다. 보류는 Airflow에만(task 실패·XCom `hold`) 남는다. Airflow를 볼 수 없으면 ③으로 이 레인 명령의 태스크가 하나도 살아 있지 않은지 확인한다(PENDING 포함).
3. **다음 스케줄 활성화 조건.** 종료가 확인된 뒤에만 `envs/dev/main.tf`의 `investor_intraday_orchestrator`를 `"SFN"`으로 되돌려 apply한다. 스케줄이 다시 켜지고, 다음 cron 슬롯부터 SFN이 실행한다.
4. **장중 긴급 롤백과 "한 거래일 안에서 섞지 않는다"의 관계.**
   - 평시 전환은 거래일 경계에서만 한다.
   - 긴급 롤백은 장중에도 할 수 있다. 이때 섞여도 되는 조건은 **2의 종료 확인 하나**다. 병합 위험은 같은 시점의 동시 실행에서 오고, 같은 날 주체가 차례로 바뀌는 것 자체에서 오지 않는다. 종료를 확인할 수 없으면 롤백을 미루고 그 사이 슬롯을 비운다.
   - 이 레인의 API 응답은 그날 누적이라, 다음 SFN 슬롯이 앞 슬롯 값을 다시 받는다.
5. **미처리 슬롯 회수.**
   - Airflow가 계획만 하고 실행을 끝내지 못한 슬롯은 `orchestrator='AIRFLOW'`로 남는다. SFN이 같은 run_key를 계획하면 LAUNCH_CONFLICT로 거부된다.
   - 당일 슬롯은 다음 SFN 슬롯의 누적 응답이 회수한다. 마지막 슬롯(14:35)만 현행 수동 레시피(원래 run_id로 ECS 스텝 실행)로 회수한다.
6. **이력.** Airflow가 만든 원장 행과 산출물은 지우지 않는다. 같은 run_id·경로 계약이라 SFN 산출물과 구분 없이 소비된다.

### 안정화 뒤 제거(이 레인)

- `aws_scheduler_schedule.investor_intraday`
- `aws_sfn_state_machine.investor_intraday`와 타임아웃 알람
- `investor_intraday_pipeline.tf`의 ASL local들
- entry.py `_LANE_STATE_MACHINE_ARN_ENV`의 이 레인 행과 `OPS_INVESTOR_INTRADAY_STATE_MACHINE_ARN` 주입

Reconciler의 SFN history 경로는 다른 레인이 모두 옮겨 간 뒤에 제거한다.

## 원천 관측 레인(source-daily, ALPHA-1130)

분석 v2 원천(매크로 5계열·DART 재무 지표·KIS 지수업종)의 하루 1슬롯(09:10 KST) 배치다. **SFN 이 없는 첫 레인**이라
원장 계획·보고를 Airflow 만 한다(`ops.entry._AIRFLOW_ONLY_LANES` — SFN 주체로 plan-run 하면 거부). 계약·일정 근거는
`docs/design/etf-data-storage-plan.md` §10 과 DAG 도크스트링.

- 세 계열은 서로 기다리지 않는다(한 공급자 장애가 다른 원천 적재를 막지 않는다). 수집·정제 exit 2 는 받은 범위만 하류로 넘기고 런은 실패로 마감한다(장중 수급과 같은 선택 2).
- 백필은 같은 DAG 수동 trigger + params `macro_from/macro_to`·`financial_from/financial_to`. 업종은 현재값뿐이라 백필 인자가 없다. 한 run 1500초 — 긴 기간은 1년 단위로 나눈다. 청크는 1분 이상 간격으로, 그리고 대기열까지 포함해 **슬롯 날짜(KST) 안에 끝나게** trigger 한다(`plan` 이 당일 슬롯만 받아 자정을 넘긴 run 은 전체가 실패한다 — 다음 날 다시 trigger) — run_id 가 분 단위 슬롯에서 나와, 같은 분이면 두 번째 run 의 수집이 "다른 요청 범위"로 실패한다.
- 로컬 검증: DAG 계약(`tests/test_source_daily_dag.py`, 공식 이미지), DAG 명령 그대로의 원장 통합(`data-pipeline/tests/e2e/test_source_daily_lane_pg.py` — plan-run → 9스텝 → reconcile, 실 PostgreSQL·가짜 공급자 HTTP).

**활성화 전 인프라(이 레인 PR 범위 밖 — Airflow 환경 담당):**
1. `macro` 태스크 정의(`edge-{env}-data-pipeline-macro` = data-pipeline 모듈 `aws_ecs_task_definition.this["macro"]` — **배선 PR #1036, 미머지**): 업무 이미지 + DB env(`local.db_env`+password) + 키 env
   `DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__{ECOS,KOSIS,EIA,FRED}_API_KEY`(시크릿 `edge-{env}-data-pipeline/{ecos,kosis,eia,fred}/api-key` — 네 개 모두 **2026-10-01 수동 등록 완료**(TF 밖, `{"apikey":...}`, AWSCURRENT 1개씩), TF 는 data 로 참조만 한다. **FMP 는 쓰지 않는다** — 미 국채 10년은 FRED `DGS10`(#1039, 같은 계열 — 설계 §10.8 추기). ECOS 는 USD/KRW·국고채. 세 키 모두 **발급 완료(2026-09-30), 운영 시크릿 연결은 미완료** — 값은 문서·PR 에 쓰지 않는다). 카탈로그 `MACRO_COLLECTION.instrumented` 전환 순서(ALPHA-596·610 과 같은 두 단계, 지금은 False): ① 배선 PR — `tasks.tf` 에 `macro`(DB env + 키 env)를 넣고 **같은 PR 에서** `tests/test_ops_catalog.py` 의 `_WIRING_AHEAD_OF_FLAG` 에 `"MACRO_COLLECTION"` 을 더한다(안 더하면 같은 테스트의 역방향 단언 — DB env 가 배선됐는데 False — 이 실패한다). apply·배포. ② 플래그 PR — True 로 올리고 `_WIRING_AHEAD_OF_FLAG` 에서 지운다(안 지우면 만료 단언 `stale` 이 실패한다). 한 PR 에 묶지 않는 이유는 그 테스트 위 주석: 이미지가 태스크 정의보다 먼저 뜨면 DB env 없는 옛 리비전에서 True 가 돌아 LEDGER_GAP 이 영구로 열린다.
2. `bigkinds`·`dart`·`rds`·`ops` 태스크 정의는 기존 것을 쓴다(DART 키는 기존 `dart` 에 있다, 업종 마스터는 키가 없다). 새 이미지 배포가 필요하다(새 CLI 스텝·설정 섹션). **⚠️ Airflow 태스크 역할의 RunTask 허용 목록**(`infra/terraform/envs/dev/main.tf` `batch_task_definition_families`)에 지금 `kis`·`bigkinds`·`rds`·`ops` 만 있다 — `dart`(재무 수집)와 새 `macro` family 를 더하지 않으면 `financial_collect`·`macro_collect` 가 `AccessDeniedException` 으로 시작도 못 한다(Codex 봇 P1, 2026-09-30 확인). 활성화 전 필수, 인프라 PR 은 Airflow 담당.
3. 주기 결측 판정을 켜려면 `ops` 태스크 정의(주기 reconcile)에 `OPS_SOURCE_DAILY_SCHED_HHMM=09:10`·`OPS_SOURCE_DAILY_SCHED_WEEKEND=true`. 없으면 이 레인은 PLANNER_MISSING 판정 대상이 아니다(안전 기본값).
4. 컨테이너 egress 가 `financialmodelingprep.com`·`ecos.bok.or.kr`·`kosis.kr`·`api.eia.gov`·`opendart.fss.or.kr`·`new.real.download.dws.co.kr` 에 닿아야 한다. 2026-10-01 네트워크 층 확인: 업무 태스크 SG(`sg-047705118179af733`) egress 전체 허용, 서브넷 기본 경로 NAT — 호스트 차단 규칙은 보이지 않는다. **실제 도달은 첫 단건 실행에서 확인**(Airflow RunTask 가 다른 SG 를 쓰면 그 SG 도 본다). 호스트는 설정 기본값(`config/models.py` `*_base_url`)과 같다.
5. 마이그레이션 `V202609301200` — **적용 완료(2026-09-30 17:04 KST, #1003 머지 `86743919`, schema-migrate success)**. dev RDS 실측: `flyway_schema_history` 202609301200 success, 테이블 4 존재, 함수 5 모두 `SECURITY DEFINER`·`search_path=public, pg_temp`·소유자 `edge`·`edge_analysis_v2_writer` EXECUTE·PUBLIC EXECUTE 없음, writer 의 테이블 권한 없음. 코드 PR(#1010~#1013·이 PR)은 그 뒤 머지한다(스키마는 테이블 4·조회 함수 5·writer EXECUTE 부여 — 확장 단계만). 구 스키마 위에서 새 이미지가 돌면 `load-*` 만 실패(exit 1)하고 raw·artifact 는 남아 `--all` 로 이어 싣는다. 새 스키마 위의 기존 코드는 영향 없다(추가 객체뿐 — CI e2e 전체가 새 스키마 위에서 돈다).
6. USD/KRW 도 ECOS 다(FMP USDKRW 는 현재 구독에서 402) — `DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__ECOS_API_KEY` 없이는 원/달러·국고채가 오지 않는다. ECOS 샘플 키는 10건 상한이라 운영 키가 필요하다. 키가 빠진 계열은 부르지 않고 `missing_credentials` 로 실패한다(수집 exit 2).

**배포 상태(2026-10-01 04:50 KST 확인 — 코드 머지·이미지 준비와 실제 배포를 구분한다):**
- 코드: 분리 PR 9개 dev 머지 완료. 마지막 머지 `dc11b7c5`(#1015). 업무 이미지 `edge/pipeline:dc11b7c52a2a5efa9e0638703a006aadd06e877f`(=`data-pipeline-latest`, digest `sha256:f3d7ec00…`), 이미지 env `GIT_SHA=dc11b7c52a2a5efa9e0638703a006aadd06e877f`(ECR 설정 blob 에서 확인).
- 기존 업무: SFN 단발 태스크·주기 reconcile 은 실행마다 `data-pipeline-latest` 를 당겨 새 코드로 돈다(머지마다 다음 reconcile exit 0·로그 형태 불변 확인). 분 상주 서비스 9개는 머지 시각에 desired 0 이라 재배포가 건너뛰어졌고, 다음 세션 시작(평일 07:45) 때 새 이미지로 뜬다.
- Airflow: `edge/airflow:dc11b7c5…` 이미지는 **빌드·푸시만** 됐다. 서비스가 desired 0 이라 deploy-airflow 가 태스크 정의 등록·서비스 교체를 건너뛰었다(서비스 태스크 정의 `edge-dev-airflow:10` = 옛 이미지 `7bb0c196`). 즉 **`edge_source_daily` DAG 는 아직 Airflow 에 올라가지 않았다.** 서비스를 다음에 dev 이미지로 켜면 그때 pause 상태로 등록된다(`is_paused_upon_creation`) — 그 뒤 dag-processor 가 DAG 하나를 더 파싱한다.
- 새 수집 경로: `macro` 태스크 정의 없음, SFN·스케줄러 어디에도 새 CLI 스텝·`source-daily` 레인 참조 없음(10-01 조회), RunTask 허용 목록 미변경 — 실제 공급자 호출·적재는 한 번도 하지 않았다.

**첫 수집·적재·v2 소비 검증은 ALPHA-1136**(코드는 ALPHA-1130 으로 완료, 2026-10-01 기준 원천 관측 표 4개 모두 0행). 재무 표 재사용은 ALPHA-643 과 합의 대기 — 미결.

**자리표시자·미결정 값(첫 실행 전에 정한다):** `<…>` 는 채워 넣을 값이다. `R`(run_id)·날짜·계열·ETF 코드는 **예시**다.
- 미결정: 첫 실행 대상(매크로 계열·재무 ETF·접수일)과 실행 시각, 첫 실행에 쓸 이미지 태그(아래 "이미지").
- 정해진 것: 키 env 이름(설정 로더 `DATA_PIPELINE_` + `__` 중첩), 기본 대상 ETF `091160`(`sources.toml` `[source_observations].etf_ids`), DAG 슬롯 매일 09:10 KST(주말 포함).
- 키 값은 명령·로그·문서·PR 에 쓰지 않는다 — 태스크 정의의 시크릿 주입만.

**FRED 교체·실행 환경 배포 결과(2026-10-01 실측):**

| PR | 머지 | 결과 |
|---|---|---|
| #1038 스키마 | `123a7800` 13:46 | schema-migrate 성공, dev RDS `flyway_schema_history` 202610011500 success, CHECK 에 FRED 튜플 추가·FMP 튜플 유지 |
| #1039 FRED 코드 | `7ef0864b` 17:19 | 이미지 `edge/pipeline:7ef0864b…`·`GIT_SHA` 일치, 분 상주 재기동 생략(desired 0). 17:25 reconcile 새 이미지로 exit 0 |
| #1036 macro 배선 | `d7d4e111` 18:32 | apply 1 추가·2 변경·0 삭제. `edge-dev-data-pipeline-macro:1` — 시크릿 ECOS·KOSIS·EIA·FRED `:apikey::`·DB `:password::`, 실행 역할이 네 시크릿을 읽는다. 이미지 재빌드 `GIT_SHA=d7d4e111…`(digest `713b779e…`) |
| #1037 RunTask 허용 | `f32b30bd` 18:38 | apply 0·1·0. Airflow 태스크 역할 RunTask 에 `dart:*`·`macro:*` 추가 |

- #1039 는 Airflow 검증 중(사용자 지시 — 이미지만 바뀌고 검증 이미지는 이미 빌드됨) 머지했다. #1036·#1037 은 검증 정리(#1029) apply 뒤 머지했다. #1036 의 plan 은 검증 중에도 Airflow 자원을 건드리지 않았다(전체 plan 로그 확인).
- 그대로인 것: `MACRO_COLLECTION.instrumented=False`(`_WIRING_AHEAD_OF_FLAG` 유예), `edge_source_daily` 미등록·pause, `ops` 결측 판정 env 없음, `investor_intraday_orchestrator=SFN`, FMP 시크릿 보존. `macro` task-def 로 실행된 태스크 0, 원천 관측 표 4개 0행(18:40 조회).
- 18:40 reconcile 은 최종 이미지로 exit 0.
- **첫 단건 남은 조건(ALPHA-1136)**: 대상·기간·실행 시각 결정 → `macro`·`dart`·`bigkinds`·`rds` 태스크 정의로 CLI 단건(run_id 명시, `--all` 없음, 미 국채 10년 포함 가능 — FRED 배포됨) → raw·manifest·canonical·DB 판본·조회 함수·v2 대조 → instrumented 플래그 PR(유예 제거) → DAG 수동 trigger → 정기 활성화(결측 판정 env 포함).

**FRED 교체·실행 환경 PR 의 배포 시점(2026-10-01 조사 — 머지 전 판단 근거):**

| 단계 | 바뀌는 것 | 판단 |
|---|---|---|
| #1038 스키마 | schema-migrate → **공유 dev RDS 의 DDL**: `macro_observation` CHECK 교체(0행). `ACCESS EXCLUSIVE` 잠금을 `lock_timeout 3s` 안에서만 기다리고, 못 얻으면 Flyway 트랜잭션이 롤백돼 무변경으로 실패한다(로컬 재현: 다른 세션이 잠금을 쥔 동안 `canceling statement due to lock timeout`, CHECK·`flyway_schema_history` 무변경, 잠금 해제 뒤 재실행 성공). 이미지·서비스·IAM 불변 | 장중 가능 — 단 Airflow 측정 중에는 적용하지 않는다 |
| #1039 FRED 코드 | deploy-data-pipeline → 이미지 push(`data-pipeline-latest` 이동) + 분 상주 9개 중 desired>0 순차 재기동(서비스당 ~3분, 전체 ~30분). 태스크 정의·IAM·DB 불변 | **장외 권장**(아래) |
| #1036 macro 배선 | terraform-apply(`macro` task-def 신규·실행 역할 시크릿 정책·SFN 정책 — 추가만) + **deploy-data-pipeline 재실행**(카탈로그 주석·테스트 경로) | **장외 권장**, #1039 이미지가 돈 **뒤**(아니면 macro 태스크 ConfigError) |
| #1037 RunTask 허용 | terraform-apply(Airflow 태스크 역할 정책 — 추가만) | 장중 가능, **Airflow 검증 창(16:30~23:30)만 피한다** |

- **재배포 방식(실측)**: `MINUTE_SERVICES_DEPLOYED=true`, 9개 모두 minimumHealthy 100%·maximum 200%·헬스체크 없음·`stopTimeout` 120초 → 새 태스크가 RUNNING 되면 옛 태스크에 SIGTERM, 120초 뒤 SIGKILL. 세션 밖(평일 16:10~07:45·주말)은 desired 0 이라 재기동이 생략되고 이미지만 바뀐다.
- **종료 계약(코드)**: 9개 모두 SIGTERM 에 새 claim 을 멈추고 진행 중 tick 을 끝낸 뒤 세션 fence 를 반납한다(테스트 `test_sigterm_stops_without_new_claim`·`test_sigterm_releases_lease_for_immediate_takeover` 등 23개 dev 에서 통과). fence·claim token 은 **DB 쓰기만** 막는다 — KIS·DART·LLM 호출과 S3 쓰기는 commit 전에 일어나 막지 않는다(S3 는 내용 해시 키·IfNoneMatch 라 고아 객체만 남는다).
- **왜 장외인가**: price-worker 의 lease 근거는 "window 하나 75초"인데(검증기 주석상 하한 가드지 상한 아님), **10-01 실측 수집은 window 당 p50 112초·p90 183초**로 그 가정을 넘는다(lease 는 300초 — 가정을 넘었다는 것이 곧 lease 만료는 아니다). 그래서 다음이 **가능해진다**: ① tick 이 `stopTimeout` 120초를 넘겨 SIGKILL 되면 그 tick 의 window 는 lease(≤300초) 만료 뒤에야 재수집된다 ② tick 이 세션 lease(300초)를 넘기면 새 태스크가 fence 를 얻은 뒤에도 옛 태스크가 KIS 를 계속 부를 수 있고, 프로세스별 간격(가격 12.5 req/s ×2)만으로 앱키 한도 **18/s**(KIS 공식, ADR-0055)를 넘는다 — 공유 예산(ADR-0055)은 꺼져 있다. 10-01 에 실제 lease 만료·중복 실행이 있었는지, ②가 일어나는지는 **미확인**이다(만료는 claimed_by·commit 거부 대조, 중복 호출은 세는 로그가 없다).
- **과거 장중 재배포 4회(실측)**: 09-09 11:04·14:01, 09-17 13:00, 09-29 14:08(가격 워커 교체 시각). 가격 window 는 전부 VALID·결손 단위 0, 영향은 09-09 오전 2 window 가 2차 시도로 ~400초 늦은 정도. 09-17 은 교체 전부터 ~11분 밀려 있어 교체 영향을 분리할 수 없다. 업종·iNAV(재수집 불가 레인)는 4회 모두 결손 0(09-09 오후 교체 구간의 iNAV INCOMPLETE 1 window 는 그날 INCOMPLETE 3건 중 하나라 교체 탓인지 구분 불가). **ECS COMPLETED 가 아니라 window 상태로 본 결과**지만, 그때는 수집이 75초 가정 안이었다 — 오늘 조건의 근거는 아니다.
- **Airflow 검증과의 공유 지점**: 검증 이미지는 빌드 시점의 data-pipeline 다이제스트를 BASE 로 고정한다 → 빌드 직전 배포는 검증 대상을 바꾼다. 검증 중단 기준에 공유 RDS 지표와 "창 안 업무 SFN FAILED 1건"(19:30 공시 SFN 은 `data-pipeline-latest` 를 당긴다)이 있다. 그래서 이미지·terraform 을 바꾸는 #1039·#1036·#1037 은 검증 창(16:30~23:30)과 빌드 직전을 피한다. #1038 은 이미지·terraform 은 건드리지 않지만 공유 RDS 의 DDL 이라 검증 측정 중에는 적용하지 않는다.
- **권장 순서·시점**: #1038(측정 시작 전, 잠금 대기 상한 확인 뒤 — 2026-10-01 13:47 적용 완료) → #1039 → #1036 → #1037. #1039 부터는 **PR 마다 머지 직전에** 다음을 다시 본다: 분 상주 desired 0, 업무 ECS·SFN 실행 0, Airflow 검증의 **실제 종료·정리**(그날 dev 에 셋업 커밋이 있었고 지금 dev 가 `verify_enabled=false`·`host_count=0`, 그 정리 커밋의 terraform-apply 성공, Airflow 서비스·태스크·호스트 0), 진행 중인 deploy·terraform·schema 워크플로 0, 다음 배치까지 **실측 배포 소요** 이상(세션 밖 deploy-data-pipeline 최대 7.5분·terraform-apply 최대 3.9분 → #1039·#1036 15분, #1037 10분). 시각 경과(예: 23:30)는 검증 종료의 대체 조건이 아니고, 조건이 맞으면 시각을 더 기다리지 않는다. 쌓인 PR 이동 절차는 `docs/git-conventions.md`.
- **제안(미적용)**: deploy-data-pipeline 에 deploy-airflow 와 같은 평일 장중 차단(`allow_market_hours` 수동 허용)을 넣는 최소 수정. 지금은 장중 머지가 곧 장중 재기동이다.

**첫 수동 실행 인수인계(활성화 전, dev — 아직 실행하지 않았다):**
- **전제(환경 담당 확인 필요)**: ALPHA-1119 small·1408 검증이 끝나고 채택된 뒤. 그 검증은 장중 수급 **단일 배치**라 source_daily 까지 검증한 것이 아니다 — 이 DAG 를 올린 뒤 dag-processor 파싱 메모리와 첫 수동 실행의 호스트·태스크 메모리를 따로 본다.
- **호출 상한(강제)**: 첫 실행은 DAG 가 아니라 아래 "단건 실행" CLI(같은 이미지, `macro`·`dart` 태스크 정의로 ECS 단건 실행)로 한다. DAG 에는 계열·대상 제한 인자가 없어 수동 trigger 는 매크로 5계열·구성종목 전체를 부른다. 매크로는 `--series` 로 1~2계열로 줄인다. 재무는 ETF 하나(`DATA_PIPELINE_SOURCE_OBSERVATIONS__ETF_IDS='["<ETF 코드>"]'`)와 하루짜리 접수일 창(`--from`=`--to`)으로 줄인다. 이렇게 해도 목록 호출은 그 ETF의 구성종목 수만큼 나간다(종목 단위 제한 인자는 없다). 공급자 호출 수는 원장·수집 로그 `counts` 로 대조한다. DAG 수동 trigger 는 두 번째 실행부터다.
- **이미지**: `edge/pipeline:dc11b7c52a2a5efa9e0638703a006aadd06e877f`(원천 관측 코드 #1010~#1013 + Airflow 계획 경로 #1015 모두 포함, `GIT_SHA` 가 이미지에 구워져 있다 — taskdef 에 따로 넣지 않는다). 태스크 정의는 태그를 고정해 쓰면 첫 실행 재현이 쉽다(`data-pipeline-latest` 는 다음 머지로 바뀐다). 첫 실행의 raw manifest `code_version` 이 `dc11b7c5…` 인지 확인한다(`unknown` 이면 옛 이미지). 카탈로그 `MACRO_COLLECTION.instrumented=True` 전환 조건: `macro` taskdef 가 있고 ECOS·KOSIS·EIA 키 env 가 그 taskdef 에 들어간 배포 **뒤**의 이미지에서 플래그를 올린다(플래그가 먼저 가면 Reconciler 가 없는 시도를 결손으로 판정). 지금은 False 다.
- **DAG 첫 수동 trigger(단건 검증이 끝난 뒤 — 두 번째 실행부터, pause 유지)** — params 비움(정기 창: 매크로 어제−소급일~어제, 재무 접수일 오늘−14~오늘, 업종 오늘 거래일이면 3파일). 예상 공급자 호출: 매크로 **5**(계열당 1 — 창이 `max_window_days` 안), 업종 **3**(ZIP), 재무 **구성종목 수 ≈ 50**(`list.json` 회사당 1 페이지; 정기 창에 새 정기보고서가 있는 회사만 +재무제표 1~2·주식총수 1). 첫 실행이 8월 반기보고서를 실으려면 `financial_from=2026-08-01 financial_to=<오늘>` — 회사당 목록 1 + 반기 재무제표 **CFS·OFS 각 1**(`collect_financial` 은 둘 다 요청한다, 연결 없는 회사는 CFS 가 `empty`) + 주식총수 1 = 4, 구성종목 50이면 **≈ 200**(+corpCode.xml 1, 목록 2페이지 이상인 회사만 +1). 매크로 백필은 `macro_from/to` (`to`≤어제; 1500초 상한 안에서 1년 단위).
- **단건 실행(대상 run_id 를 명시한다 — 첫 검증에서 `--all` 을 쓰지 않는다)**: 같은 run_id `R` 로 세 단계를 잇는다(DAG 와 같은 형태). `--all` 은 이번 run 외의 미소비 정제 run 까지 집으므로 첫 검증의 대조 대상을 흐린다.
  ```bash
  R=manual_macro_20261001_1
  python -m data_pipeline.run ingest-raw-macro --series usd_krw --from 2026-09-15 --to 2026-09-26 --run-id $R
  python -m data_pipeline.run normalize-macro --run-id $R --input-run-id $R
  python -m data_pipeline.run load-macro --run-id $R --input-run-id $R
  ```
  데이터셋별 최소 범위와 예상 공급자 호출(단건):
  | 데이터셋 | 최소 대상·기간 | 예상 호출 |
  |---|---|---|
  | 매크로 | `--series usd_krw`(ECOS 1계열), `--from`~`--to` ≤ `max_window_days` | ECOS **1** |
  | 매크로(미 국채 10년) | `--series us_10y_yield`(FRED `DGS10`) — #1039 배포 뒤에만. 그 전 이미지에서는 FMP 계열이라 빼야 한다 | FRED **1** |
  | 재무 | ETF 1개(`<ETF 코드>`, 기본 `091160`), 접수일 하루(`--from`=`--to`=`<접수일>`) | corpCode.xml 1 + 목록 `<구성종목 수>`(회사당 1페이지) + 그날 정기보고서가 있는 회사만 재무제표 CFS·OFS 2 + 주식총수 1 — **추정**(3분기 정정이면 사업보고서 재무제표를 다시 받는다). 실제 수는 raw manifest `counts` 로 대조 |
  | 업종 | 인자 없음(오늘이 거래일일 때만) | ZIP **3**(코스피·코스닥·업종명) |

  재무는 `DATA_PIPELINE_SOURCE_OBSERVATIONS__ETF_IDS='["<ETF 코드>"]'` 와 하루짜리 `--from`=`--to` 로 `ingest-raw-financial-metric`·`normalize-financial-metric`·`load-financial-metric`, 업종은 `ingest-raw-sector`(기간 인자 없음, 거래일에만)·`normalize-sector`·`load-sector` 를 같은 형태로.
- **재요청 확인(재수집·중복 적재가 없는지)**: 끝난 `R` 로 세 줄을 그대로 한 번 더 돌린다. ① 수집 로그에 `run_id=R 는 이미 수집 완료 — 공급자 재호출 없음` 이 나오고 raw manifest 가 바뀌지 않는다(`shasum -a 256 manifest.json` 전후 동일). 요청 범위를 바꿔 같은 `R` 을 쓰면 "다른 요청 범위로 이미 수집됐다"로 거부돼야 정상. ② 정제는 완료 run 이라 manifest 를 덮지 않는다. ③ 적재 뒤 `SELECT count(*) FROM macro_observation WHERE raw_run_id='R'` 가 전후 같다(`ON CONFLICT DO NOTHING`).
- **교차 확인(run 뒤)**: ① raw manifest `operations_archive/raw_run_manifests/dataset=*/run_id=<run>/manifest.json` 의 `counts`(ok·empty·error)와 `code_version` ② canonical manifest 의 `rows`·`rejected`·`raw_manifest_sha256` 가 ①의 **저장된 manifest.json 바이트의 sha256**(`shasum -a 256 manifest.json` — manifest 안에 자기 해시 필드는 없다)과 같은지 ③ DB `SELECT count(*), max(received_at) FROM macro_observation WHERE raw_run_id='<run>'` 가 ②의 `rows` 와 같은지(재무는 `financial_metric` 과 `financial_report_version` 둘 다 — 판본 수 = canonical manifest `companion.rows`, `status` 별 건수로 UNCONFIRMED 응답 파악) ④ `SELECT * FROM source_observation_freshness()` 에 데이터셋 행이 생겼는지 ⑤ v2 어댑터 경로: `SELECT * FROM macro_observations_as_of(now(),'usd_krw',2)` · `SELECT period, eps, bps, bps_note, version_raw_run_id, latest_unconfirmed_at FROM financial_quarters_as_of(now(),'005930')` 를 `edge_analysis_v2_writer` 로(`SET ROLE`) 실행 — 테이블 직접 SELECT 는 거부돼야 정상. ⑥ v2 결과 대조: 같은 기준시각으로 v2 어댑터(`edge_analysis_v2.storage.source_inputs.macro_inputs`·`financial_inputs`)를 돌려 ⑤의 값과 같은지, 근사 Q4 EPS 가 `eps_derivation`·`approximate` 로 전달되고 카드 투영에서 근사 PER 이 빠지는지 본다.
- **재수집 없는 복구**(설계 §10.3 표 — 로컬 실측): ① 정제·적재가 실패했고 artifact 가 **살아 있으면**(30일 안) params `reprocess_slot=<그 슬롯 ISO>` 로 같은 DAG 재trigger(수집 건너뜀, 같은 run_id 로 정제는 완료분이면 no-op·적재는 멱등). 적재만 실패했으면 `load-* --all` 이 미소비 정제 run 을 싣는다. ② artifact 가 **만료된 뒤엔 `reprocess_slot` 으로 복구되지 않는다**(같은 run_id 정제는 no-op → 적재 exit 1). 같은 raw 를 **새 정제 run_id** 로 정제해 그 run 을 싣는다: `normalize-* --run-id <새 id> --input-run-id <raw run>` → `load-* --run-id <새 id> --input-run-id <새 id>`(같은 코드 판이면 같은 바이트라 멱등). 다른 코드 판이면 이미 실린 raw 는 적재가 거부된다(exit 1) — 규칙을 바꿔 다시 싣는 것은 새 수집으로만. ③ 적재 전에 만료된 옛 정제 run 은 `--all` 을 계속 실패시킨다(정리 도구 없음 — 후속 **ALPHA-1133**). 그래서 복구도 `--input-run-id <정제 run>` 으로 대상을 지정한다. 정기 DAG 는 `--input-run-id` 만 써서 영향이 없다.
- **멈추기**: DAG pause(다음 슬롯 안 돎) → 실행 중 run 은 Airflow 에서 task clear 하지 말고 ECS `stop-task` 뒤 원장 보류 해제 절차(아래 "보류 해제와 수동 복구"). 수집 스텝은 공급자 호출을 **다 마친 뒤** raw 객체와 manifest 를 쓴다(`write_raw_run`) — 수집 중 정지하면 그때까지 받은 응답도 남지 않고, 같은 run_id 재수집이 공급자를 처음부터 다시 부른다(정정이 그 사이 있었으면 같은 원문은 못 얻는다). 정지는 정제·적재 단계에서 하는 편이 싸다.

## 활성화 전 결정·미해결 조건

아래가 해결되기 전에는 **운영 활성화 불가**다. 코드 준비·로컬 검증과는 별개다. 2번은 이번에 대체 조건을 정했고, 나머지는 그대로 남아 있다.

1. **exit 2(부분 실패) 적재 정책 — 팀 결정.** 현재 구현은 선택 2다. 결정 전에는 활성화하지 않는다.

   | | 선택 1: 부분 실패 슬롯은 적재 중단(#958 전 SFN의 실제 동작) | 선택 2: 유효 행 적재 + 부분 실패 표시(현재 Airflow 구현, #958 뒤 SFN 동작) |
   |---|---|---|
   | 같은 as_of에서 분석에 보이는 데이터 | 그 슬롯의 새 행은 다음 슬롯 적재 전까지 안 보인다 | 게이트를 통과한 행이 그 슬롯에서 바로 보인다 |
   | `available_at`·계보 | 다음 슬롯의 더 늦은 fetch 시각이 남는다(실제 관측보다 늦게 기록). `data_version`은 다음 슬롯 run | 실제 fetch 시각. `data_version`은 그 슬롯 run |
   | 재시도·재처리 범위 | 재처리하면 정제·적재. 누적 응답이라 다음 슬롯이 대부분 회수(마지막 14:35는 회수 없음) | 같다. 이미 적재한 행은 수량이 같으면 갱신하지 않는다 |
   | 부분 데이터 사용의 업무 전제 | 슬롯 단위 전부 아니면 전무 | 행 단위로 유효하면 사용 가능. 탈락 행은 `failed_records`로 원장에 남는다 |
   | 운영 화면·경보 | 런 FAILED(SNS). 적재 작업은 PENDING | 런 FAILED(verdict, SNS). 정제 attempt FAILED·outcome FULFILLED, `failed_records`>0 |

   - 근거: 로컬 재현(벤더 응답에 거래일 결측 행 1개). 분석 엔진 `v_flow_intraday`(`available_at <= as_of`) 기준으로 셌다. 10:05 부분 실패 슬롯의 유효 행 354개가 as_of 10:30에 Airflow 경로에서만 보였다. 11:25 슬롯 뒤 수량은 두 경로가 같았다.
   - 검증 범위: 결측 행 1개 유형만 재현했다. 다른 탈락 사유와 탈락 비율이 클 때는 보지 않았다.
   - 선택 1로 정하면 `edge_investor_intraday.py`의 `normalize`·`load` `partial_exit_codes`에서 2를 뺀다(exit 2 → 업무 실패, 적재 upstream_failed).
2. **잠금 연결 상실 — CAS·fencing 대신 보수적 운영(이번에 정한 대체 조건).**
   - 배경: 실행권 lock을 쥔 **커넥션만** 끊기면 PostgreSQL이 lock을 즉시 푼다. 기존 작업은 이를 모른 채 계속 쓴다. 전에는 이 틈에 다른 실행이 들어와 같은 파티션을 동시에 병합할 수 있었다(`scenario-lockloss`: 1초 뒤 B 실행, attempt 겹침).
   - 정한 것: 초기 운영은 **실행 상태가 불명확하면 자동 복구를 중단한다.** 기존 작업의 종료를 확인한 뒤에만 재실행한다(위 "재시도·재접속·보류 정책").
   - 코드 가드가 다루는 것: lock을 얻은 실행도 같은 task_key에 원장상 끝나지 않은 시도나 ECS 보류가 있으면 업무를 시작하지 않는다(76). 로컬 재현: 같은 조건에서 B(다른 슬롯·같은 슬롯)는 76, 업무 실행 0(`scenario-hold` V1). Airflow는 결과를 모르는 태스크 옆에 새 태스크를 띄우지 않는다.
   - 운영 절차가 다루는 것: SFN·수동 실행과의 겹침(한 주체만), 보류 해제(종료 증거 확인 뒤), 전환·롤백 시점.
   - **아직 없는 것:** 파티션 저장소가 오래된 작업자의 쓰기를 거부하는 장치. lock 커넥션을 잃고도 이미 업무를 시작한 작업자는 끝까지 쓴다. 새 실행이 그 옆에서 시작하지 않을 뿐이다.
   - **ALPHA-1057 재검토 조건:** 자동 복구(보류 없는 재시도)나 동시 실행(스텝 병렬·`max_active_runs>1`)을 늘릴 때, 또는 SFN·수동 경로를 계속 함께 쓸 때 쓰기 세대·CAS를 먼저 해결한다.
3. **알려진 한계(팀 확인).**
   - **전날 이전 슬롯의 재처리는 주기 대조를 받지 않는다.** 주기 Reconciler는 가장 최근 예정일의 슬롯만 본다(ALPHA-565 사각). 그래서 과거 슬롯 재처리 run이 `report` 전에 죽으면 앞 판정이 남는다. 그 run_key를 `OPS_RUN_KEY`로 지정해 `reconcile`을 한 번 돌려야 한다.
   - **과거 슬롯 재처리 중 R02가 잠깐 켠다.** 새 업무 시도가 결론 나기 전 주기 대조가 돌면 판정이 NULL(미귀결)이 된다. 그 슬롯은 hard deadline이 이미 지났으므로 `report` 전까지 R02 P1이 뜬다. 멈춘 재처리를 숨기지 않기 위한 선택이다. 경보 소음으로 볼지 결정한다.
4. **결말 없이 끝난 실행의 보류 기록 — 해결(ALPHA-1088 후속, 아래 "미확정 실행을 원장이 스스로 찾는다").** worker 사망·수동 failed·`dagrun_timeout`으로 report·verdict·callback이 돌지 못해도 원장에 보류가 남는다. 남는 경계(탐지 지연)는 그 절에 적었다.
5. **run 시간 상한.** `dagrun_timeout`(1500초)과 Reconciler 수명 기준(`OPS_AIRFLOW_RUN_LIFETIME_SECONDS`, 기본 1800초)의 관계는 테스트가 고정한다. 실제 스텝 소요를 재고 두 값을 다시 볼지 결정한다(실제 환경 검증 항목).
6. **Airflow 실행 환경 — 정함(ALPHA-1119).** ECS on EC2 자체 운영(위 "실행 환경"). `EDGE_ECS_*`·`EDGE_ALARM_TOPIC_ARN`·`aws_default` 는 `modules/airflow` 태스크 정의가 주입하고, DAG 는 이미지로 배포한다. 남은 것은 실제 리소스 생성과 실제 AWS 검증이다.
7. **아래 "실제 환경 검증"의 성공 기준 충족.**

## 실제 환경 검증 계획(실행 환경이 정해진 뒤, dev)

> ALPHA-1119: 먼저 위 "실제 AWS 검증(격리)"을 한다. 아래 표의 항목 중 IAM·로그·ListTasks 반영 지연·종료 경위·재접속·응답 유실·보류 해제는 격리 검증 DAG 가 같은 EdgeStep 경로로 확인한다. 운영 데이터가 필요한 항목(정상 실행의 실제 KIS 수집, 운영 원장 대조)은 전환 승인 뒤 첫날 대사에서 본다.

로컬 대역으로 검증한 것을 실제 ECS·IAM·로그로 다시 확인한다. 전부 dev에서, DAG는 이 레인 전용 테스트 run(과거 슬롯 재처리 또는 업무 영향 없는 수동 슬롯)으로 한다.

| 항목 | 절차 | 성공 기준 |
|---|---|---|
| IAM | Airflow 실행 역할로 `ecs:RunTask`(taskdef 4종)·`DescribeTasks`·`ListTasks`·`StopTask`·`iam:PassRole`(execution·task 역할)·`sns:Publish`·`logs:GetLogEvents` | 각 호출 성공. AccessDenied 0 |
| 정상 실행 | 당일 슬롯 수동 trigger | ECS 4개(plan·collect·normalize·load)+report 1개. attempt 3건에 `orchestrator_attempt_ref`. `orchestration_status=SUCCEEDED`, `reported_at` 채워짐 |
| CloudWatch 로그 | `EDGE_ECS_LOG_GROUP` 설정 | Airflow task 로그에 컨테이너 로그가 보인다. plan은 `ops/…`, 나머지는 `raw-ingest/…` 스트림 |
| ECS 기동 실패와 재시도 | 태스크는 만들어지지만 컨테이너가 못 뜨게 한다(없는 이미지 태그·없는 시크릿 참조의 테스트 태스크 정의) | `stopCode=TaskFailedToStart` → Airflow 재시도 → 다음 시도가 새 태스크 1개 → 성공. 원장 attempt는 성공한 1건. (없는 서브넷은 RunTask 400이라 재시도 없는 설정 실패가 정상이다) |
| 실행 중 태스크 재접속 | 정제 실행 중 Airflow worker 프로세스 종료 | 재시도 try가 같은 `startedBy`로 RUNNING 태스크에 붙는다. 새 ECS·새 attempt 0. 로그 "재접속"·XCom `ecs_reattached_arn` |
| 응답 유실 뒤 재시도 | 수집 종료 직후 worker 종료 | 재시도가 끝난 태스크의 exit 0을 쓴다. 새 ECS 0, KIS 호출 증가 0(수집 로그) |
| RunTask 멱등 토큰 | 테스트 태스크 정의로 같은 `clientToken` RunTask 2회(CLI) | 같은 taskArn, 태스크 1개. 토큰 보존 시간을 기록한다 |
| ListTasks 반영 지연·보존 | RunTask 직후부터 `startedBy` 조회를 1초 간격으로 반복. 멈춘 태스크가 ListTasks·DescribeTasks에서 사라지는 시각 기록 | 보일 때까지 걸린 시간 < 추적 창(3회×10초). 멈춘 태스크 보존 ≥ 1시간. 넘으면 추적 횟수·보류 판단을 다시 본다 |
| 종료 경위 실측 | 정상 종료·`stop-task`·기동 실패(없는 서브넷)를 각각 `describe-tasks` | `stopCode`가 `EssentialContainerExited`·`UserInitiated`·`TaskFailedToStart`, 강제 종료 exit 137/143. `ecs_verdict` 판정표와 같다 |
| scheduler·worker 중단·복구 | 실행 중 scheduler 재시작(실행 환경 방식대로) | run이 이어서 끝나고 중복 ECS 0 |
| 보고 실패와 대조 복구 | report 직전 원장 접속 차단(보안그룹 임시 변경 등, dev 한정) | report 75 → 재시도. 소진되면 verdict 실패. 차단 해제 뒤 주기 Reconciler가 NULL을 투영으로 채운다 |
| 잠금 연결 상실 | 정제 중 lock 백엔드 종료(`pg_terminate_backend`) 뒤 다른 슬롯 재처리 trigger | 재처리 정제 exit 76, 업무 실행 0, `EXECUTION_HOLD`(OPEN_ATTEMPT). 기존 작업 종료 뒤 다음 run은 정상 실행 |
| 주기 점검(sweep)과 IAM | 머지 뒤 주기 reconcile 로그의 `airflow sweep` 요약·종료 코드. 활성화 뒤엔 DAG 시간 초과를 한 번 유도(테스트 run) | `ecs_listing: ok`, AccessDenied 0, 종료 코드 0. 시간 초과 run의 미확정 실행이 30~45분 안에 `EXECUTION_HOLD`로 남는다 |
| 보류 해제 절차 | 위 재처리 뒤 README "보류 해제와 수동 복구"를 그대로 따라 한다 | 종료 확인 ①~⑥ 결과와 해제 SQL이 이 문서대로 동작. 해제 전 clear·재처리는 계속 보류 |

## 머지 순서와 선행 조건

세 PR이 마이그레이션 버전 순서로 묶여 있다.
- #948 공유 호출 제어: `V202609271200__add_call_budget` → 머지 전 `V202609291230`으로 전방 이동(그사이 dev에 #960 `1600`·#965 `2100`·#970 `2200`이 먼저 머지돼 1200은 역행이 됐다. 미적용 파일이라 이름만 옮겼다)
- #949 이 레인 스키마: `V202609271100`·`1110`·`1120`
- #950 이 레인 코드: #949 머지 뒤 최신 dev로 갱신

Flyway는 이미 적용된 버전보다 낮은 새 버전을 거부한다(`outOfOrder` 미사용). 그래서 **나중에 머지하는 쪽이 높은 번호여야 한다.**

- **경로 B로 확정(2026-09-28).** dev `flyway_schema_history` 실측 최고 버전이 `202609202030`이고 #948은 미적용·미머지였다. #948을 서둘러 머지하지 않고, 어느 공유 환경에도 적용된 적 없던 #949의 세 파일 **이름만** `1500·1510·1520` → `1100·1110·1120`으로 낮췄다(내용 동일). 적용된 마이그레이션은 건드리지 않았다.
- #948은 손대지 않는다. 뒤에 머지될 때 1200 > 1120이라 그대로 적용된다. #948이 rebase할 때 ERD를 재생성한다.
- 검증: 실제 Flyway 10.21.0 + PostgreSQL 16, dev 세트 위에서 "dev → 1100대 → #948(1200) 나중" 적용, "dev → #948 먼저 → 1100대"는 `resolved migration not applied: 202609271100`으로 거부(`local/results/migration-matrix-948-949-950.txt`).
- **스키마 PR의 CI 통과는 적용 완료가 아니다.** CI는 임시 DB에 적용한다. dev 적용은 머지 뒤 `schema-migrate` 워크플로 결과와 `flyway_schema_history`로 따로 확인한다.
