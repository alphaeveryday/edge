# airflow — 유한 배치의 Airflow 실행 경로

유한 배치(SFN 5개)의 **실행 관리**를 레인별로 Airflow로 옮긴다. 업무 실행은 그대로 `data-pipeline`의 ECS 태스크 정의와 `data_pipeline.run` 명령이 맡는다. 상주 분 수집기와 SQS 소비자는 대상이 아니다.

현재 상태: 첫 레인인 장중 수급(`edge_investor_intraday`)을 **로컬에서 검증했다. 운영 배포와 전환은 아직 하지 않았다.** Airflow 실행 환경(MWAA 또는 자체 운영)도 아직 정하지 않았다.

## 구성

| 파일 | 역할 |
|---|---|
| `dags/edge_batch.py` | 레인 공통 연결 코드. `EdgeStep`(ECS 실행과 exit code 해석), 슬롯과 `pipeline_run_id` 파생 |
| `dags/edge_investor_intraday.py` | 장중 수급 DAG: `plan → collect → normalize → load → verdict` |
| `tests/` | terraform(슬롯·명령)과 대조하고, exit code·당일 수집·재처리 규칙을 고정한다 |
| `local/` | 로컬 비교 환경. ECS·SNS·SFN 대역, 배포된 ASL 해석기, 저장 입력 재생 |
| `requirements.txt` | Airflow 3.3.2, amazon provider 9.36.0 |

원장 쪽 변경은 `data-pipeline`에 있다.
- `ops_pipeline_run.orchestrator`(SFN|AIRFLOW)와 `orchestrator_run_ref` 컬럼
- Planner가 소유 충돌을 거부한다
- Reconciler가 Airflow 런을 원장 attempt로 대조한다
- wrapper에 `OPS_SKIP_IF_SUCCEEDED` 가드를 추가했다

## 실행 계약

- **run_id.** `plan`이 `plan-run`을 부를 때 `OPS_ORCHESTRATOR=AIRFLOW`, `OPS_SCHEDULED_TIME=<슬롯>`, `OPS_ORCHESTRATOR_RUN_REF=<dag_id>/<run_id>`를 넘긴다. Planner는 SFN 경로와 **같은 run_key·run_id**로 계획만 남기고 SFN은 시작하지 않는다. 스텝은 모두 `--run-id <그 run_id>`로 돈다.
- **런 상태.** SFN 런은 DescribeExecution이 `orchestration_status`를 채운다. Airflow 런은 Reconciler가 원장에서 투영한다. 판정은 DAG verdict와 같다. 전부 FULFILLED이고 최신 exit가 0이면 SUCCEEDED, 열린 작업이 있으면 RUNNING(hard deadline 뒤엔 FAILED), 나머지는 FAILED다. 비워 두면 콘솔 R02가 정상 완료 런을 "미귀결"로 올린다.
  - Airflow가 끝나며 `report` task(ops `reconcile`, `OPS_RUN_KEY`+`OPS_ORCHESTRATION_STATUS`)로 자기 판정을 원장에 보고한다. 원장 투영은 보고가 없을 때만 NULL·RUNNING을 채우고, 보고된 확정값은 덮지 않는다.
  - 주기 Reconciler가 락을 쥔 동안 온 보고는 0이 아니라 75로 끝나 재시도된다. 보고가 끝내 실패하면 `verdict`가 런을 실패로 닫는다.
- **Airflow run과 원장의 연결.** 두 개의 키로 잇는다.
  - `ops_pipeline_run.orchestrator_run_ref`는 Airflow run을 가리킨다.
  - `ops_task_attempt.ecs_task_arn`은 Airflow task 로그와 XCom `ecs_task_arn`의 ARN과 같다.
- **exit code.** 해석 규칙은 `EdgeStep` 독스트링에 있다. 요약하면 다음과 같다.
  - 0은 성공이다.
  - 정제·적재의 2는 부분 실패다. 하류는 계속 돌고, 런은 verdict에서 실패로 마감된다. **기존 SFN과 다르다.** 기존 SFN은 이 경우 적재를 건너뛰어, 유효 행이 다음 슬롯에야 분석(`available_at <= as_of`)에 보인다. 카탈로그 계약(`fulfilled_exit_codes=(0,2)`)을 따르는 의도적 변경이다.
  - 그 밖의 비0은 업무 실패다. Airflow가 재시도하지 않는다.
  - exit code가 없으면 인프라 실패로 보고 2회 재시도한다.
- **중복 실행.** 다섯 경우를 구분한다.
  - **같은 슬롯에 실행 주체가 둘:** Planner가 `INSERT … ON CONFLICT (run_key)`로 계획을 하나만 만든다. 뒤에 온 주체는 LAUNCH_CONFLICT로 실행하지 않는다.
  - **같은 스텝이 아직 실행 중:** 업무 스텝은 컨테이너 안에서 작업별 실행권(PostgreSQL 세션 advisory lock, 키=task_key)을 잡은 뒤에만 실행한다(`OPS_EXCLUSIVE_STEP`). 이미 잡혀 있으면 최대 600초 기다린다.
    - 프로세스가 죽으면 커넥션이 끊겨 lock이 스스로 풀린다.
    - 키가 task_key라 다른 슬롯의 같은 스텝도 겹치지 않는다. 같은 파티션을 병합하기 때문이다.
  - **이미 성공한 스텝(수집만):** 실행권 **안에서** 최신 attempt가 exit 0이면 실행 없이 0으로 끝난다(`OPS_SKIP_IF_SUCCEEDED`). 정제·적재는 외부 호출이 없고 멱등이라 성공 이력이 있어도 다시 돈다. 다른 슬롯이 같은 파티션을 갱신한 뒤 적재를 복구하려면 정제가 다시 돌아야만 하기 때문이다. provider의 `reattach`는 RUNNING ECS 태스크만 찾으므로, 끝난 태스크의 응답을 잃은 재시도는 새 태스크를 띄운다.
    - 단, 같은 run의 앞 단계(raw < normalize < feature)에 그 성공보다 나중에 만들어진 attempt가 있으면 입력이 바뀐 것이라 다시 실행한다. 예: 수집 실패 → 빈 입력 정제 성공 → 수집 복구. 순서는 DB 시계(`created_at`)로 판정한다.
    - 실행 전에 attempt 시작을 기록하지 못하면 실행하지 않는다(75). 흔적 없는 실행이 생기면 다음 재시도의 skip이 더 오래된 성공을 믿고, Reconciler는 실제로 돈 작업을 MISSED로 찍는다.
  - **의도적 재처리:** 실행권만 잡고 성공 skip은 끈다.
  - **상태를 확인할 수 없음:** 원장 미설정·접속 실패, 기대 작업 없음, 실행 이력 조회 실패, 시작 기록 실패 중 하나라도 해당하면 **실행하지 않고** exit 75로 끝난다. Airflow는 이를 인프라 실패처럼 재시도한다.
    - env가 없는 SFN·수동 경로는 종전대로 원장 장애에도 작업을 진행한다.
- **막지 못하는 것.**
  - KIS에는 멱등 키가 없어 **정확히 한 번 호출은 보장하지 않는다.** 수집이 외부 호출을 끝낸 뒤 attempt 종료를 기록하기 전에 죽으면, 최신 attempt의 exit가 없어 재시도가 다시 호출한다(최소 한 번).
  - 커넥션만 끊기고 프로세스가 계속 쓰는 경우(네트워크 분리)엔 lock이 풀린다. lock은 쓰기 fencing이 아니다.
  - SFN 경로와 수동 ECS 실행은 실행권을 잡지 않는다. 그래서 두 주체의 동시 실행은 Planner 소유 검사와 전환 절차의 종료 확인으로만 막는다.
- **당일 수집.** 장중 추정 API에는 날짜 인자가 없다. 그래서 `plan`과 `collect`는 슬롯 날짜가 오늘(KST)이 아니면 실패한다. backfill이나 과거 날짜로 trigger하면 원장에 아무것도 남지 않는다.
- **재처리.** 기존 슬롯의 raw를 다시 정제·적재하려면 conf `{"reprocess_slot": "<슬롯 ISO>"}`로 trigger한다. `plan`이 `OPS_REPROCESS=1`로 **계획이 있고 raw 단계가 끝난 슬롯인지** 확인한다. 없는 슬롯(오타)이나 수집 실패 슬롯은 거부해, 빈 입력 정제가 성공으로 끝나지 않게 한다. 이때 `collect`는 ECS를 띄우지 않는 성공 no-op이고, 정제·적재의 가드는 해제된다. skip으로 두면 하류까지 skip이 전파된다(3.3.2 로컬 관찰). 같은 DAG의 `max_active_runs=1`이 정기 run과 직렬화해 준다.

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
# DAG 계약 테스트(Airflow 이미지 안)
docker run --rm -v "$(git rev-parse --show-toplevel)":/repo:ro --entrypoint bash \
  edge-airflow-migration-lab:local -c "pip install -q pytest && cd /tmp && python -m pytest -q -p no:cacheprovider /repo/src/apps/cloud/airflow/tests"
```

- `EDGE_LAB_TODAY_KST`는 저장된 과거 슬롯을 재생하려고 "오늘"을 고정하는 로컬 전용 변수다. **운영 환경에는 두지 않는다.**
- ⚠️ 3.3.2 CLI의 `airflow tasks clear -s/-e`로는 수동 trigger run을 지정하지 못했다. logical date로도, run_after 창으로도 고르지 못했고, 매번 exit 0에 출력 없이 상태가 그대로였다(로컬에서 3회 관찰). 실패한 run을 복구할 때는 REST API `POST /api/v2/dags/{dag_id}/clearTaskInstances`에 `dag_run_id`와 `task_ids`, `include_downstream`을 지정해 호출한다.

## 운영 전환·롤백 절차(장중 수급, 아직 실행하지 않았다)

**불변 조건.** 한 레인의 스텝은 어느 순간에도 한 주체만 실행한다. 여러 슬롯이 같은 거래일 canonical 파티션을 CAS 없이 병합하기 때문이다(ALPHA-1057). 주체가 바뀌는 순간 기존 주체의 실행이 **실제로 끝났음을 확인하기 전에는 새 주체를 켜지 않는다.** DAG pause나 스케줄 DISABLED는 새 실행 생성을 멈출 뿐이고, 이미 뜬 ECS 태스크를 멈추지 않는다.

**종료 확인 쿼리·명령(전환·롤백 공통).**

```sql
-- ① 원장상 끝나지 않은 시도(이 레인)
SELECT r.run_key, r.orchestrator, et.task_key, a.ecs_task_arn, a.started_at
FROM ops_task_attempt a JOIN ops_expected_task et USING (expected_task_id)
JOIN ops_pipeline_run r USING (pipeline_run_id)
WHERE r.pipeline_type='investor-intraday' AND a.execution_status='RUNNING';
-- ② 실행권 보유 세션(Airflow 경로 스텝이 잡는 advisory lock)
SELECT pid, backend_start, state FROM pg_stat_activity
WHERE pid IN (SELECT pid FROM pg_locks WHERE locktype='advisory');
```

```bash
# ③ 실제 ECS 태스크(두 경로 공통 태스크 정의)
for f in kis bigkinds rds; do aws ecs list-tasks --cluster <cluster> --family <name>-$f --desired-status RUNNING; done
# ④ 기존 SFN 실행
aws stepfunctions list-executions --state-machine-arn <investor-intraday SFN> --status-filter RUNNING
```

- ①·③·④가 모두 비어 있으면 종료를 확인한 것이다.
- ②는 보조 증거다. advisory lock은 다른 기능(Reconciler)도 쓴다.
- 하나라도 확인할 수 없거나 남아 있으면 **새 주체를 켜지 않고 기다린다.**
  - SFN은 TimeoutSeconds가 1500이라 최대 25분이다.
  - Airflow 스텝은 lock 대기 600초와 dagrun_timeout 1500초를 합쳐도 25분 안에 끝나거나 실패한다.
  - ECS 태스크 자체에는 시간 상한이 없다. 25분이 지나도 남아 있으면 `aws ecs stop-task`로 멈춘 뒤, ①을 다시 확인한다.

### 전환(SFN → Airflow)

전제:
- 원장 마이그레이션(`V202609271500`·`V202609271510`)과 data-pipeline 이미지가 배포돼 있다.
- DAG는 **pause 상태**로 배포돼 있다.
- 아래 "활성화 전 AWS 확인"을 끝냈다.

1. **경계.** 마지막 SFN 슬롯은 전날 14:35다. 첫 Airflow 슬롯은 다음 거래일 09:35다. 장 마감 뒤(15:00 이후)에 진행한다.
2. **새 실행 생성 중단.** `investor_intraday_orchestrator = "AIRFLOW"`로 apply한다(envs/dev).
   - 이 레인의 EventBridge 스케줄 5개가 DISABLED가 된다.
   - Reconciler 슬롯 대조(`OPS_INVESTOR_INTRADAY_SCHED_HHMM`)는 유지된다. 그래서 Airflow 런도 같은 run_key로 대조되고, Airflow가 슬롯을 안 돌리면 PLANNER_MISSING이 열린다.
   - `investor_intraday_schedule_state = "DISABLED"`로 끄면 **안 된다.** 그 값은 슬롯 대조까지 끈다.
3. **종료 확인.** 위 ①·③·④를 확인한다. SFN RUNNING은 중단하지 않고 끝날 때까지 기다린다. 중단하면 그 슬롯의 병합이 반쪽으로 남는다.
4. **슬롯 소유 확인.** 전날 run_key 5개가 모두 `orchestrator='SFN'`이고, 다음 거래일 run_key가 아직 없는지 본다.
5. **DAG unpause.** catchup이 없어 다음 cron 슬롯부터 실행된다.
   - 로컬 확인: 일요일에 unpause하면 run 0개, 다음 실행 월요일 09:35였다.
6. **첫날 대사.**
   - 그날 run_key 5개가 모두 `orchestrator='AIRFLOW'`여야 한다.
   - SFN 실행 0건, LAUNCH_CONFLICT 0건이어야 한다.
   - `investor_flow_intraday` 행 수를 전주 같은 요일과 비교한다.

### 롤백(Airflow → SFN)

롤백 조건: Airflow 환경 장애로 슬롯 실행이 불가능할 때, 또는 원장·산출물 대사가 불일치할 때.

1. **새 실행 생성 중단.** DAG pause.
   - 이미 queued·running인 run은 멈추지 않는다.
   - 급하면 해당 run을 failed로 표시한다. 이때 ECS 태스크도 `on_kill`로 멈춘다. 단 Airflow가 죽어 있으면 이 경로가 없다.
2. **종료 확인.** ①·②·③을 확인한다.
   - Airflow가 죽어서 run 상태를 알 수 없으면 ECS(③)와 원장(①)으로만 판단한다.
   - 둘 다 비어야 3으로 간다.
3. **다음 스케줄 활성화 조건.** 종료가 확인된 뒤에만 `investor_intraday_orchestrator = "SFN"`으로 apply한다. 스케줄이 다시 켜지고, 다음 cron 슬롯부터 SFN이 실행한다.
4. **장중 긴급 롤백과 "한 거래일 안에서 섞지 않는다"의 관계.**
   - 평시 전환은 거래일 경계에서만 한다.
   - 긴급 롤백은 장중에도 할 수 있다. 이때 섞여도 되는 조건은 **2의 종료 확인 하나**다. 병합 위험은 같은 시점의 동시 실행에서 오고, 같은 날 주체가 차례로 바뀌는 것 자체에서 오지 않는다.
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

**활성화 전 AWS 확인(로컬에서 검증하지 않은 것)**

- Airflow 실행 역할의 권한:
  - `ecs:RunTask`·`DescribeTasks`·`ListTasks`·`StopTask`, `iam:PassRole`(execution·task 역할)
  - `sns:Publish`, CloudWatch Logs 읽기
- 실제 Fargate에서 확인할 것:
  - `startedBy`(provider의 uuid) 기반 reattach
  - `EcsTaskFailToStart` 재시도
  - 비0 exit의 `describe_tasks` exitCode
- 실제 CloudWatch에서 로그 스트림 `raw-ingest/data-pipeline/<task-id>`를 읽는지 확인한다.
- Airflow가 ECS 태스크를 기다리는 동안 worker slot을 점유하는 것이 허용되는지 판단한다. 필요하면 `deferrable=True`로 바꾸는 것을 검토한다.
- 휴장일 한 번: Planner SKIPPED와 수집 skip(exit 0)이 verdict 성공으로 끝나는지 확인한다.
