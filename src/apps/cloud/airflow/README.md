# airflow — 유한 배치의 Airflow 실행 경로

유한 배치(SFN 5개)의 **실행 관리**를 레인별로 Airflow로 옮긴다. 업무 실행은 그대로 `data-pipeline`의 ECS 태스크 정의와 `data_pipeline.run` 명령이 맡는다. 상주 분 수집기와 SQS 소비자는 대상이 아니다.

현재 상태: 첫 레인인 장중 수급(`edge_investor_intraday`)을 로컬에서 검증했다. 실행 환경은 **ECS on EC2 자체 운영**으로 정했고 Terraform·배포 워크플로·격리 검증 경로를 만들었다(ALPHA-1119, 아래 "실행 환경"). **실제 리소스 생성·배포·실제 AWS 검증·운영 전환은 아직 하지 않았다** — 비용·plan 승인 대기. MWAA 는 조직 SCP 가 거부한다(ALPHA-1086 조사).

## 구성

| 파일 | 역할 |
|---|---|
| `dags/edge_batch.py` | 레인 공통 연결 코드. `EdgeStep`(ECS 실행과 exit code 해석), 슬롯과 `pipeline_run_id` 파생 |
| `dags/edge_investor_intraday.py` | 장중 수급 DAG: `plan → collect → normalize → load → verdict` |
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
| 호스트 | t4g.medium(arm64, 2 vCPU·4 GiB) 1대, ASG min 1·max 2 | 로컬 실측(아래 표)으로 구성요소 합이 약 1.2 GB. t4g.small(2 GiB)은 헬스체크 CLI 가 겹칠 때 여유가 없다. max 2 는 호스트 교체 때만 쓴다 |
| AMI | ECS 최적화 AL2023 arm64 **고정**(`ami_id`) | SSM recommended 를 data 로 읽으면 사람이 고르지 않은 시각(머지 = apply)에 교체가 준비된다 |
| 서비스 | 서비스 1개·태스크 1개 안에 `api-server`·`scheduler`·`dag-processor`(awsvpc) | 세 컨테이너가 localhost 를 공유한다. LocalExecutor 의 task 프로세스가 Execution API 를 `http://localhost:8080/execution/` 로 부른다. 별도 서비스로 나누면 서비스 간 이름 해석(Service Connect 등)이 필요하다 |
| Executor | LocalExecutor, `parallelism=4` | 장중 수급은 직렬이다(`max_active_runs=1`). Celery·Redis·Kubernetes 를 쓸 근거가 없다 |
| Triggerer | 없음 | `EdgeStep` 이 `deferrable=False` 로 고정돼 있다(defer 하면 재개가 provider 의 `execute_complete` 로 가서 판정을 건너뛴다). 다른 deferrable operator 도 없다 |
| 버전 | Airflow 3.3.2 · amazon provider 9.36.0 (CI 와 같은 다이제스트) | 로컬·CI 에서 검증한 조합 그대로 |
| 메타DB | 별도 RDS `edge-dev-airflow`(db.t4g.micro, PostgreSQL 16, 백업 7일) | 아래 "메타DB" |
| DAG 배포 | 이미지에 굽는다(`Dockerfile`) | 세 구성요소와 마이그레이션 작업이 한 이미지 태그(커밋)를 쓴다. 서버에 파일을 복사하지 않는다 |
| UI 접근 | SSM 포트 포워딩만 | ALB·공인 IP 없음. 태스크 SG 는 호스트 SG 에서 온 8080 만 받는다 |
| 인증 | Airflow 3 기본 SimpleAuthManager, 사용자 `admin` 1명 | 비밀번호는 Secrets Manager. ⚠️ Airflow 문서는 SimpleAuthManager 를 개발·테스트용으로 분류한다. 여기서는 네트워크 경로가 SSM 뿐이고 사용자가 1명이라 받아들였다. 사용자가 늘면 FAB auth manager 로 바꾼다 |

**구성요소별 자원(태스크 정의 상한).** 로컬 리허설(`deploy/compose.local.yaml`, arm64) 실측 뒤 정했다.

| 컨테이너 | 상한 MiB | CPU unit | 실측(유휴) | 실측(task 1개 실행 중) |
|---|---|---|---|---|
| api-server(workers 1) | 900 | 512 | 230 MiB | — |
| scheduler(+LocalExecutor task 프로세스) | 1600 | 1024 | 270 MiB | 최대 464 MiB. task 1개 약 200 MiB → parallelism 4 면 약 1.1 GiB |
| dag-processor | 600 | 256 | 190 MiB | — |
| 합(태스크 메모리 3100) | | | 약 690 MiB | |

- 배포 때 추가 용량은 필요 없다. 서비스는 min 0 / max 100 으로 옛 태스크를 멈춘 뒤 새 태스크를 띄운다. scheduler 가 둘 뜨는 순간이 없고, 대가로 1~3분 Airflow 가 멈춘다. 그래서 `deploy-airflow` 는 평일 장중(KST 09:20~15:00)에 명시 허용 없이 배포하지 않는다.
- 마이그레이션 작업(`airflow db migrate`)은 **Fargate ARM64** one-off 다. 서비스 태스크가 호스트 메모리를 거의 쓰므로 같은 호스트에 자리를 요구하지 않는다.

### 메타DB

- 업무 DB `edge-dev` 와 **인스턴스를 나눴다.** `edge-dev` 는 2026-08-10 메모리 고갈로 하루 다섯 번 죽었다. 최근 2주(09-14~09-27) 여유 메모리는 최저 574 MB, CPU 는 최고 96% 였다(CloudWatch 일 단위). scheduler 는 매초 DB 를 폴링하고 구성요소마다 커넥션 풀을 쥔다(풀 3 + overflow 5). 같은 인스턴스에 두면 그 부하가 업무 레인 전체의 장애 범위에 들어간다. `app_rds` 를 분리한 것과 같은 이유다.
- 나눠도 공유하는 것: VPC·data 서브넷, 알람 SNS 토픽. 메타DB 장애는 Airflow 만 멈춘다. 반대로 업무 DB 장애는 여전히 Airflow 런의 원장 스텝(plan·report·wrapper)을 실패시킨다. 원장이 업무 DB 에 있기 때문이다.
- 백업: RDS 자동 백업 7일(PITR). 복구는 PITR 로 새 인스턴스를 만든 뒤 `module "airflow_rds"` 를 그 인스턴스로 바꾸고 `deploy-airflow` 를 다시 돌린다.
- 메타DB 를 잃으면 사라지는 것: DAG run·task 이력, XCom(보류 사유 포함), pause 상태. **업무 상태의 정본은 원장(업무 DB)이다.** 보류는 `report` 가 원장 `EXECUTION_HOLD` 로 옮기고, 결말 없는 실행은 주기 Reconciler 의 sweep 이 찾는다. 새 메타DB 에서 DAG 는 pause 로 다시 생긴다(`dags_are_paused_at_creation`). 잃은 이력 구간의 보류는 원장으로 판단한다.
- 비밀번호 로테이션: `modules/rds` 가 토 09:00~12:00 KST 에 돌린다. 연결 문자열은 태스크 기동 때 한 번 만들어진다. 로테이션 뒤에는 새 연결이 실패하고, 헬스체크가 태스크를 교체하며, 새 태스크가 새 비밀번호를 받는다. 장이 없는 시간이다.

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

- 헬스체크: api-server `curl /api/v2/monitor/health`, scheduler·dag-processor `airflow jobs check --local`(60초 간격, 3회 실패면 ECS 가 태스크를 교체한다).
- 알림(모두 기존 파이프라인 SNS 토픽):
  - `edge-dev-airflow-service-down`: 서비스의 ECS CPU 지표가 10분 끊기면 울린다(태스크 없음·교체 실패·호스트 사망).
  - 메타DB 여유 메모리 알람·RDS 이벤트 구독: `modules/rds` 가 붙인다.
  - DAG 실패: 운영 DAG 의 `on_failure_callback`(기존).
- ⚠️ 서비스가 desired 0 인 동안(최초 구축 직후, 첫 배포 전)은 서비스 중단 알람이 울린다. 첫 배포로 풀린다.

### 최초 구축(한 번)

1. `foundation` apply(수동) — `edge/airflow` ECR 저장소.
2. 이 PR 머지 → `terraform-apply` 가 메타DB·클러스터·호스트·서비스(desired 0)·검증 자원을 만든다. 같은 머지에서 `deploy-airflow` 도 뜬다. 서비스가 아직 없으면 "서비스 없음"으로 실패한다. 의도한 동작이다.
3. 앱 시크릿 값 넣기:
   ```bash
   aws secretsmanager put-secret-value --secret-id edge-dev-airflow/app --secret-string "$(python3 -c '
   import json, secrets; print(json.dumps({"jwt_secret": secrets.token_urlsafe(48),
     "api_secret_key": secrets.token_urlsafe(32), "admin_password": secrets.token_urlsafe(18)}))')"
   ```
4. `deploy-airflow` 를 workflow_dispatch 로 실행한다(장 마감 뒤). 순서는 이미지 빌드 → 마이그레이션 태스크 exit 0 → 서비스 새 리비전·desired 1 → services-stable.
5. 확인: 세 컨테이너 HEALTHY, UI 로그인, DAG 두 개(운영·검증)가 **pause**, import error 0, 예제 DAG 없음, dag run 0.

### 배포·롤백

- 평시 배포: `src/apps/cloud/airflow/dags/**`·`Dockerfile`·`deploy/entrypoint.sh` 가 dev 에 머지되면 `deploy-airflow` 가 돈다. 이미지 태그는 커밋 SHA 다. 마이그레이션이 실패하면 서비스를 바꾸지 않는다. 서비스 교체가 실패하면 ECS circuit breaker 가 이전 리비전으로 되돌리고, 워크플로는 실패로 끝난다.
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

### 월 비용(서울 리전, 2026-09-28 공개 단가, 730시간)

**이번에 늘어나는 비용**

| 항목 | 계산 | USD/월 |
|---|---|---|
| EC2 t4g.medium 온디맨드 | 0.0416 × 730 | 30.37 |
| EBS gp3 30 GiB(루트) | 30 × 0.0912 | 2.74 |
| RDS db.t4g.micro Single-AZ | 0.025 × 730 | 18.25 |
| RDS gp3 20 GiB | 20 × 0.131 | 2.62 |
| RDS 백업(7일, 할당량 이내) | 할당 스토리지만큼 무료 | 0 |
| Secrets Manager 2개(RDS 관리형·앱) | 2 × 0.40 | 0.80 |
| CloudWatch Logs 수집(추정 2~4 GB) | × 0.76/GB. 보관 비용은 소액 | 1.5~3.0 |
| CloudWatch 알람 2개(서비스 중단·메타DB 메모리) | 2 × 0.10 | 0.20 |
| ECR 저장(베이스 레이어 공유, 약 0.7 GB + 검증 이미지) | × 0.10/GB | ≈ 0.1 |
| NAT 처리량(이미지 pull 약 0.66 GB × 월 10회 + API) | × 0.059/GB | 0.4~1.0 |
| **합계** | | **약 57~59** |

**늘지 않는 것(0원)과 그 근거:** 공인 IPv4 0개(private 서브넷·EIP 없음), ALB 없음, VPC 엔드포인트 추가 없음, SSM Session Manager 무료. ECS 자체 관리 요금도 없다. ECS on EC2 는 EC2·EBS 만 과금한다(Managed Instances 가 아니다).

**일시적으로 늘어나는 것**

- 호스트 교체 때 두 번째 t4g.medium: 약 15분 × 0.0416 ≈ 0.01/회.
- 배포마다 마이그레이션 Fargate ARM64 약 1분: 0.01 미만.
- 격리 검증 기간의 검증 태스크(Fargate x86 0.25 vCPU·0.5 GB, 수백 회 × 1~2분): 약 0.1~0.5 합계. 검증 버킷 S3 는 소액이다(30일 만료).

**이미 지출 중인 것(이번 작업과 무관하게 계속)**

- NAT 게이트웨이 시간 요금: 0.059 × 730 ≈ 43.07. 기존 게이트웨이를 그대로 쓴다.
- 업무 DB `edge-dev`, worker 클러스터의 업무 Fargate 태스크: 실행 주체가 SFN 이든 Airflow 든 같은 태스크가 돈다.
- 전환 뒤에는 이 레인의 SFN 상태 전이 요금이 사라진다(하루 5회 × 수 전이, 소액).

**고려했으나 택하지 않은 절감안**

| 안 | 절감(USD/월) | 택하지 않은 이유 |
|---|---|---|
| 메타DB 를 `edge-dev` 에 같이(별도 DB·계정) | 약 21 | 위 "메타DB" — 메모리 고갈 이력이 있는 업무 DB 의 장애 범위를 넓힌다 |
| t4g.small | 약 15 | 로컬 실측 합 약 1.2 GB(parallelism 4 기준) + 헬스체크 CLI 가 겹친다. 등록 메모리 약 1.9 GB 로는 여유가 없다. 실제 AWS 실측 뒤 다시 본다 |
| Savings Plan·RI | 약 30% | 도입 초기라 약정하지 않는다 |

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
python verify/run.py evidence --slot 10:05
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
