# airflow — 유한 배치의 Airflow 실행 경로

유한 배치(SFN 5개)의 **실행 관리**를 레인별로 Airflow로 옮긴다. 업무 실행은 그대로 `data-pipeline`의 ECS 태스크 정의와 `data_pipeline.run` 명령이 맡는다. 상주 분 수집기와 SQS 소비자는 대상이 아니다.

현재 상태: 첫 레인인 장중 수급(`edge_investor_intraday`)을 **로컬에서 검증했다. 운영 배포와 전환은 아직 하지 않았다.** Airflow 실행 환경(MWAA 또는 자체 운영)도 아직 정하지 않았다.

## 구성

| 파일 | 역할 |
|---|---|
| `dags/edge_batch.py` | 레인 공통 연결 코드. `EdgeStep`(ECS 실행과 exit code 해석), 슬롯과 `pipeline_run_id` 파생 |
| `dags/edge_investor_intraday.py` | 장중 수급 DAG: `plan → collect → normalize → load → verdict` |
| `tests/` | DagBag 파싱, terraform(슬롯·명령) 대조, exit code·당일 수집·재처리·보고·활성화 규칙. CI `test-airflow.yml`이 공식 Airflow 이미지(다이제스트 고정) 안에서 실행한다 |
| `local/` | 로컬 비교 환경. ECS·SNS·SFN 대역, 배포된 ASL 해석기, 저장 입력 재생 |
| `requirements.txt` · `requirements-test.txt` | Airflow 3.3.2, amazon provider 9.36.0 · pytest(테스트 전용) |

원장 쪽 변경은 `data-pipeline`에 있다.
- `ops_pipeline_run.orchestrator`(SFN|AIRFLOW)와 `orchestrator_run_ref` 컬럼
- Planner가 소유 충돌을 거부한다
- Reconciler가 Airflow 런을 원장 attempt로 대조하고, DAG가 보고한 판정을 기록한다
- wrapper에 실행권(`OPS_EXCLUSIVE_STEP`)·성공 skip(`OPS_SKIP_IF_SUCCEEDED`) 가드를 추가했다
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
- **exit code.** 해석 규칙은 `EdgeStep` 독스트링에 있다. 요약하면 다음과 같다.
  - 0은 성공이다.
  - 정제·적재의 2는 부분 실패다. 현재 구현은 하류를 계속 돌리고 verdict에서 런을 실패로 마감한다. **기존 SFN과 다르고, 팀 결정 전이다**(아래 "활성화 전 결정").
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

## 실행 추적

| 알고 싶은 것 | 원장에서 | Airflow에서 |
|---|---|---|
| Airflow run이 처리한 업무 슬롯 | `ops_task_attempt.orchestrator_attempt_ref LIKE 'airflow:<dag_id>/<run_id>/%'` → expected_task → run. 최초 계획 run은 `ops_pipeline_run.orchestrator_run_ref` | 재처리 run은 conf `reprocess_slot` |
| 재시도·clear·재처리에서 어느 ECS 시도가 돌았나 | attempt 행마다 `ecs_task_arn`과 그 ECS를 띄운 `.../<task_id>/<try_number>` | 각 try의 XCom `ecs_task_arn`과 task 로그 |
| 실행 중 ECS에 재접속 | 새 attempt가 **생기지 않는다**. 원장의 참조는 처음 띄운 try를 가리킨다 | 그 try의 로그 "실행 중인 ECS 태스크에 재접속"과 XCom `ecs_reattached_arn` |
| 성공 이력으로 건너뜀 | `record_source='DUPLICATE_SKIP'` 행(그 컨테이너의 ARN·try 참조, exit 0). 이후 판단의 "최신 업무 시도"에서는 빠진다 | 그 try는 성공 |
| 실패한 attempt → Airflow | `orchestrator_attempt_ref`의 dag_id·run_id·task_id·try_number로 Airflow UI의 task 시도를 연다 | — |

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
# DAG 계약 테스트 — CI(test-airflow.yml)와 같은 이미지·명령
docker run --rm -v "$(git rev-parse --show-toplevel)":/repo:ro --entrypoint bash \
  apache/airflow@sha256:9df9c8be4096b9cc626bd7cb1f2b8c712eef66c59c615f8c7e6200871ca10bd1 \
  -c "pip install -q -r /repo/src/apps/cloud/airflow/requirements-test.txt && cd /tmp && python -m pytest -q -p no:cacheprovider /repo/src/apps/cloud/airflow/tests"
```

- `EDGE_LAB_TODAY_KST`는 저장된 과거 슬롯을 재생하려고 "오늘"을 고정하는 로컬 전용 변수다. **운영 환경에는 두지 않는다.**
- ⚠️ 3.3.2 CLI의 `airflow tasks clear -s/-e`로는 수동 trigger run을 지정하지 못했다. logical date로도, run_after 창으로도 고르지 못했고, 매번 exit 0에 출력 없이 상태가 그대로였다(로컬에서 3회 관찰). 실패한 run을 복구할 때는 REST API `POST /api/v2/dags/{dag_id}/clearTaskInstances`에 `dag_run_id`와 `task_ids`, `include_downstream`을 지정해 호출한다.

## 운영 전환·롤백 절차(장중 수급, 아직 실행하지 않았다)

**불변 조건.** 한 레인의 스텝은 어느 순간에도 한 주체만 실행한다. 여러 슬롯이 같은 거래일 canonical 파티션을 CAS 없이 병합하기 때문이다(ALPHA-1057). 주체가 바뀌는 순간 기존 주체의 실행이 **실제로 끝났음을 확인하기 전에는 새 주체를 켜지 않는다.** DAG pause나 스케줄 DISABLED는 새 실행 생성을 멈출 뿐이고, 이미 뜬 ECS 태스크를 멈추지 않는다.

**종료 확인(전환·롤백 공통).** 아래 다섯 가지를 **모두** 확인해야 끝난 것이다. 원장 기록은 프로세스 상태의 증거가 아니고, lock 부재도 쓰기 종료의 증거가 아니다(아래 "잠금 연결 상실").

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
```

```bash
# ③ 실제 ECS 태스크(두 경로 공통 태스크 정의) — 원장이 RUNNING 을 못 남긴 컨테이너도 여기서 보인다
for f in kis bigkinds rds; do aws ecs list-tasks --cluster <cluster> --family <name>-$f --desired-status RUNNING; done
# ④ 기존 SFN 실행
aws stepfunctions list-executions --state-machine-arn <investor-intraday SFN> --status-filter RUNNING
# ⑤ Airflow 의 진행·대기 run(REST, dag_run 상태)
curl -s "$AIRFLOW/api/v2/dags/edge_investor_intraday/dagRuns?state=running&state=queued" -H "Authorization: Bearer $TOKEN"
```

- ①·③·④·⑤가 모두 비어 있고 ②에 행이 없으면 종료를 확인한 것이다. ②의 키 계산은 로컬에서 Python `StepLock` 키와 같은 값인지 확인했다.
- 하나라도 확인할 수 없거나 남아 있으면 **새 주체를 켜지 않고 기다린다.**
  - SFN은 TimeoutSeconds가 1500이라 최대 25분이다.
  - Airflow run은 `dagrun_timeout` 1500초다. 다만 ECS 태스크 자체에는 시간 상한이 없다.
  - 25분이 지나도 ③에 남아 있으면 `aws ecs stop-task`로 멈춘 뒤, ①·③을 다시 확인한다.
- **파티션 쓰기 종료**는 ③(프로세스 부재)으로만 확정한다. 원장 attempt 종료나 lock 해제는 쓰기 종료를 보증하지 않는다.

### 전환(SFN → Airflow)

전제:
- 원장 마이그레이션(`V202609271500`·`1510`·`1520`)이 dev에 **적용 완료**(schema-migrate 초록 + `flyway_schema_history` 확인)이고 data-pipeline 이미지가 배포돼 있다.
- DAG는 **pause 상태**로 배포돼 있다.
- 아래 "활성화 전 결정·미해결 조건"이 모두 해결됐고 "실제 환경 검증"의 성공 기준을 충족했다.

1. **경계.** 마지막 SFN 슬롯은 전날 14:35다. 첫 Airflow 슬롯은 다음 거래일 09:35다. 장 마감 뒤(15:00 이후)에 진행한다.
2. **새 실행 생성 중단.** `infra/terraform/envs/dev/main.tf`의 `module "data_pipeline"` 인자 `investor_intraday_orchestrator = "SFN"`을 `"AIRFLOW"`로 바꿔 apply한다.
   - 이 레인의 EventBridge 스케줄 5개가 DISABLED가 된다.
   - Reconciler 슬롯 대조(`OPS_INVESTOR_INTRADAY_SCHED_HHMM`)는 유지된다. 그래서 Airflow 런도 같은 run_key로 대조되고, Airflow가 슬롯을 안 돌리면 PLANNER_MISSING이 열린다.
   - `investor_intraday_schedule_state = "DISABLED"`로 끄면 **안 된다.** 그 값은 슬롯 대조까지 끈다.
3. **종료 확인.** 위 ①~⑤를 모두 확인한다. SFN RUNNING은 중단하지 않고 끝날 때까지 기다린다. 중단하면 그 슬롯의 병합이 반쪽으로 남는다. 확인할 수 없는 항목이 하나라도 있으면 5로 가지 않는다.
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
   - 급하면 해당 run을 failed로 표시한다. 이때 ECS 태스크도 `on_kill`로 멈춘다. 단 Airflow가 죽어 있으면 이 경로가 없다.
2. **종료 확인.** ①~⑤를 모두 확인한다.
   - Airflow가 죽어서 ⑤를 볼 수 없으면 ECS(③)·원장(①)·lock(②)으로 판단한다. 셋 다 비어야 3으로 간다.
   - 판단할 수 없으면 SFN을 켜지 않는다. 그 사이 슬롯은 5의 회수 대상이 된다.
3. **다음 스케줄 활성화 조건.** 종료가 확인된 뒤에만 `envs/dev/main.tf`의 `investor_intraday_orchestrator`를 `"SFN"`으로 되돌려 apply한다. 스케줄이 다시 켜지고, 다음 cron 슬롯부터 SFN이 실행한다.
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

## 활성화 전 결정·미해결 조건

아래가 해결되기 전에는 **운영 활성화 불가**다. 코드 준비·로컬 검증과는 별개다.

1. **exit 2(부분 실패) 적재 정책 — 팀 결정.** 현재 구현은 선택 2다. 결정 전에는 활성화하지 않는다.

   | | 선택 1: 부분 실패 슬롯은 적재 중단(기존 SFN 동작) | 선택 2: 유효 행 적재 + 부분 실패 표시(현재 구현) |
   |---|---|---|
   | 같은 as_of에서 분석에 보이는 데이터 | 그 슬롯의 새 행은 다음 슬롯 적재 전까지 안 보인다 | 게이트를 통과한 행이 그 슬롯에서 바로 보인다 |
   | `available_at`·계보 | 다음 슬롯의 더 늦은 fetch 시각이 남는다(실제 관측보다 늦게 기록). `data_version`은 다음 슬롯 run | 실제 fetch 시각. `data_version`은 그 슬롯 run |
   | 재시도·재처리 범위 | 재처리하면 정제·적재. 누적 응답이라 다음 슬롯이 대부분 회수(마지막 14:35는 회수 없음) | 같다. 이미 적재한 행은 수량이 같으면 갱신하지 않는다 |
   | 부분 데이터 사용의 업무 전제 | 슬롯 단위 전부 아니면 전무 | 행 단위로 유효하면 사용 가능. 탈락 행은 `failed_records`로 원장에 남는다 |
   | 운영 화면·경보 | 런 FAILED(SNS). 적재 작업은 PENDING | 런 FAILED(verdict, SNS). 정제 attempt FAILED·outcome FULFILLED, `failed_records`>0 |

   - 근거: 로컬 재현(벤더 응답에 거래일 결측 행 1개). 분석 엔진 `v_flow_intraday`(`available_at <= as_of`) 기준으로 셌다. 10:05 부분 실패 슬롯의 유효 행 354개가 as_of 10:30에 Airflow 경로에서만 보였다. 11:25 슬롯 뒤 수량은 두 경로가 같았다.
   - 검증 범위: 결측 행 1개 유형만 재현했다. 다른 탈락 사유와 탈락 비율이 클 때는 보지 않았다.
   - 선택 1로 정하면 `edge_investor_intraday.py`의 `normalize`·`load` `partial_exit_codes`에서 2를 뺀다(exit 2 → 업무 실패, 적재 upstream_failed).
2. **잠금 연결 상실 — fencing 없음.** 실행권(세션 advisory lock)을 쥔 **커넥션만** 끊기면 PostgreSQL이 lock을 즉시 푼다.
   - 기존 작업은 이를 감지하지 못하고 계속 실행한다. 외부 호출·S3 파티션 쓰기·적재가 모두 계속된다. 감지는 작업 끝 lock 해제 때의 경고뿐이다.
   - 그 사이 다른 실행이 실행권을 얻어 같은 스텝을 시작할 수 있다.
   - 로컬 재현(`scenario-lockloss`): A가 lock을 쥔 채 30초 작업하는 중 A의 lock 백엔드를 `pg_terminate_backend`로 끊었다. 1초 뒤 B가 실행권을 얻어 실행했고, A는 25초 뒤 exit 0으로 끝났다. 원장 attempt 두 건이 시간상 겹친다.
   - 이때 canonical 병합은 CAS가 없다(ALPHA-1057). 겹친 두 실행이 같은 거래일 파티션을 읽고-합치고-덮어써 한쪽 병합이 유실될 수 있다. manifest checksum이 어긋나면 적재도 실패할 수 있다. lock 키가 task_key라 **다른 슬롯**의 같은 스텝끼리도 이 경합이 가능하다.
   - 활성화 전 선택지(이번 PR 범위 밖): 쓰기 직전 lock 커넥션 생존 확인(창을 줄일 뿐 없애지는 못함), 또는 파티션 쓰기의 세대·CAS(ALPHA-1057)로 오래된 작업자의 덮어쓰기를 거부한다.
3. **알려진 한계(팀 확인).**
   - **전날 이전 슬롯의 재처리는 주기 대조를 받지 않는다.** 주기 Reconciler는 가장 최근 예정일의 슬롯만 본다(ALPHA-565 사각). 그래서 과거 슬롯 재처리 run이 `report` 전에 죽으면 앞 판정이 남는다. 그 run_key를 `OPS_RUN_KEY`로 지정해 `reconcile`을 한 번 돌려야 한다.
   - **과거 슬롯 재처리 중 R02가 잠깐 켠다.** 새 업무 시도가 결론 나기 전 주기 대조가 돌면 판정이 NULL(미귀결)이 된다. 그 슬롯은 hard deadline이 이미 지났으므로 `report` 전까지 R02 P1이 뜬다. 멈춘 재처리를 숨기지 않기 위한 선택이다. 경보 소음으로 볼지 결정한다.
4. **Airflow 실행 환경.** MWAA(3.3.1까지 지원) 또는 자체 운영. 그에 따른 `EDGE_ECS_*`·`EDGE_ALARM_TOPIC_ARN`·`aws_default` 연결과 DAG 배포 경로를 정한다.
5. **아래 "실제 환경 검증"의 성공 기준 충족.**

## 실제 환경 검증 계획(실행 환경이 정해진 뒤, dev)

로컬 대역으로 검증한 것을 실제 ECS·IAM·로그로 다시 확인한다. 전부 dev에서, DAG는 이 레인 전용 테스트 run(과거 슬롯 재처리 또는 업무 영향 없는 수동 슬롯)으로 한다.

| 항목 | 절차 | 성공 기준 |
|---|---|---|
| IAM | Airflow 실행 역할로 `ecs:RunTask`(taskdef 4종)·`DescribeTasks`·`ListTasks`·`StopTask`·`iam:PassRole`(execution·task 역할)·`sns:Publish`·`logs:GetLogEvents` | 각 호출 성공. AccessDenied 0 |
| 정상 실행 | 당일 슬롯 수동 trigger | ECS 4개(plan·collect·normalize·load)+report 1개. attempt 3건에 `orchestrator_attempt_ref`. `orchestration_status=SUCCEEDED`, `reported_at` 채워짐 |
| CloudWatch 로그 | `EDGE_ECS_LOG_GROUP` 설정 | Airflow task 로그에 컨테이너 로그가 보인다. plan은 `ops/…`, 나머지는 `raw-ingest/…` 스트림 |
| ECS 기동 실패와 재시도 | 존재하지 않는 서브넷 등으로 한 번 기동 실패 유도(테스트 환경 변수) | `EcsTaskFailToStart` → Airflow 재시도 → 성공. 원장 attempt는 성공한 1건 |
| 실행 중 태스크 재접속 | 정제 실행 중 Airflow worker 프로세스 종료 | 재시도 try가 같은 `startedBy`로 RUNNING 태스크에 붙는다. 새 ECS·새 attempt 0. 로그 "재접속"·XCom `ecs_reattached_arn` |
| 응답 유실 뒤 재시도 | 수집 종료 직후 worker 종료 | 새 ECS 1개가 `DUPLICATE_SKIP` 행을 남기고 exit 0. KIS 호출 증가 0(수집 로그) |
| scheduler·worker 중단·복구 | 실행 중 scheduler 재시작(실행 환경 방식대로) | run이 이어서 끝나고 중복 ECS 0 |
| 보고 실패와 대조 복구 | report 직전 원장 접속 차단(보안그룹 임시 변경 등, dev 한정) | report 75 → 재시도. 소진되면 verdict 실패. 차단 해제 뒤 주기 Reconciler가 NULL을 투영으로 채운다 |
| 잠금 연결 상실 | 정제 중 lock 백엔드 종료(`pg_terminate_backend`) | **현재 보장 없음**(위 2번) — 재현해 겹침을 기록하는 것까지다. 해결 전 활성화 불가 |

## 머지 순서와 선행 조건

세 PR이 마이그레이션 버전 순서로 묶여 있다.
- #948 공유 호출 제어: `V202609271200__add_call_budget`
- #949 이 레인 스키마: `V202609271500`·`1510`·`1520`
- #950 이 레인 코드: #949와 같은 스키마 커밋을 포함

Flyway는 이미 적용된 버전보다 낮은 새 버전을 거부한다(`outOfOrder` 미사용). 그래서 **나중에 머지하는 쪽이 높은 번호여야 한다.**

- **경로 A — #948 → #949 → #950(현재 번호 그대로).**
  - #948이 준비돼 먼저 머지되는 경우다. **#948을 순서 때문에 서둘러 머지하지 않는다.**
  - #949 rebase → `physical-erd.dbml` 재생성(두 PR이 모두 고친다) → 머지 → dev `schema-migrate` 초록 확인 → `flyway_schema_history`에 1500·1510·1520 적용 확인.
  - 그 뒤 #950을 rebase(스키마 커밋은 사라진다)해 머지한다.
- **경로 B — #949·#950을 #948보다 먼저 머지해야 할 때.**
  - **#949(과 #950의 같은 커밋)의 파일 이름만** 1200보다 낮고 dev 최고 버전(`202609202030`)보다 높게 바꾼다: `V202609271100__add_pipeline_run_orchestrator`, `V202609271110__validate_pipeline_run_orchestrator`, `V202609271120__add_orchestrator_trace`. 내용은 그대로다.
  - 미적용 파일의 PR 내부 리네임이라 allowlist가 필요 없다. dev에 들어가면 이 번호로 적용된다.
  - #948은 손대지 않는다. 뒤에 머지될 때 1200 > 1120이라 그대로 적용되고, ERD만 rebase 뒤 재생성한다.
- 두 경로 모두 로컬 PostgreSQL 16 + Flyway 10.21.0으로 결합 검증했다(`local/results/migration-matrix-948-949-950.txt`).
  - 번호를 안 바꾼 채 Airflow를 먼저 넣으면 #948이 "resolved migration not applied: 202609271200"으로 거부된다.
- **스키마 PR의 CI 통과는 적용 완료가 아니다.** CI는 임시 DB에 적용한다. dev 적용은 머지 뒤 `schema-migrate` 워크플로 결과와 `flyway_schema_history`로 따로 확인한다.
