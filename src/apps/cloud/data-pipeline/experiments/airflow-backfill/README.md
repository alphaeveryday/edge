# Airflow 날짜별 재처리·부분 복구 — 로컬 비교 실험 (첫 검증)

가격 경로 `원본 선택 → 정제(normalize-price) → 적재(load-price-daily) → 완료 확인`에서 날짜별 재처리와 일부 날짜 실패 복구를 세 방식으로 수행하고 결과를 대사했다. **로컬 CLI·로컬 Airflow 비교이며 AWS Step Functions 서비스와 비교한 것이 아니다.** 운영 전환 결정이 아니고, 운영 AWS 자원·데이터는 바꾸지 않았다(dev S3 원본과 SFN 이력은 읽기만 했다).

기준 코드: `388177ba`(실행 당시 dev HEAD, 2026-09-26). 결론 교정·조사 추가: 같은 날(ALPHA-1086), 원시 결과는 재실행 없이 그대로 사용. 결과: `results/r2/`. 중단된 첫 실행은 `results/r1-aborted-backfill-race/`에 그대로 남겼다(아래 "실패 기록").

## 무엇이 실제이고 무엇을 대체했나

| 실제 | 대체·고정 |
|---|---|
| `data_pipeline.run.main`(운영 ECS 진입점) → `normalize_price`·`load_price_daily`·원장 계측 `ops.entry.instrument` | S3 → `LocalStorage`(격리 볼륨), RDS → 격리 PostgreSQL 16 + `migrations-cloud` 전체 |
| 입력: dev 레이크의 **실제 KIS 가격 raw 7거래일**(2026-09-15~09-23, 런당 1파일, 1,648~2,054행) — `inputs.json`에 run_id·sha256 고정 | 종목 마스터(`entity`·`instrument`)는 **fixture**: raw에 나온 411종목을 XKRX로 등록 |
| 원장 계획: 실제 `planner.plan_run` — 운영과 **같은 run_id**가 결정적으로 재생성된다 | `StartExecution`만 stub(AWS 호출 없음) |
| C: Apache Airflow 3.3.2 standalone(LocalExecutor, 메타DB PostgreSQL 16), 실제 scheduler·backfill·`tasks clear` | 실패: 대상 날짜 raw 읽기를 **1회** 실패시키는 주입(`lab.py _arm_fault`, 업무 코드 무수정) |

A·B·C 모두 같은 이미지·같은 인터프리터(`/opt/dp`, Python 3.12.14)에서 같은 `lab.py step`을 부른다. Airflow 의존성과 섞지 않으려 업무 코드는 별도 venv다.

## 비교군

| | 정상 요청 | 복구 요청 | 재시도·동시성 |
|---|---|---|---|
| **A 현재 절차** | 날짜마다 정제 → (성공 시) 적재. 실패 시 `NotifyFailure run_id=…`(SNS 제목 대신 출력) — 현재 SFN 체인의 로컬 재현 | `statemachine.tf` NormalizeParallel 주석대로 **새 run-id**로 `normalize-price --run-id <새> --input-run-id <실패 run>`, 이어서 `load-price-daily --input-run-id <새>` | 0 · 1 |
| **B 스크립트 보완** | `backfill FROM TO`: 고정 입력 checksum 확인, **원장·manifest로 완료 확인된 날짜는 건너뜀**, 같은 run_id로 실행(원장 매칭), 끝에 `check-run` | 같은 명령 재실행 | 0 · 1 |
| **C Airflow DAG** | `select_input → normalize_price → load_price_daily → check_run`, 업무일=logical_date(15:40 KST)의 KST 날짜. `airflow backfill create --reprocess-behavior none` | `airflow tasks clear -s … -e … --only-failed --downstream` | 0 · 1 |

B·C의 공통 개선(고정 입력, 완료 확인 `check-run`, 원장 run_id 일치)은 같은 `lab.py` 코드다.

**동시성 1은 현재 가격 경로의 제약이지, 원리적으로 불가능하다는 뜻이 아니다.** 서로 다른 업무일의 런이 같은 산출물을 건드린다. KIS 일봉 raw는 런마다 과거 창을 겹쳐 담는다(09-15 런 59개 거래일, 09-18 이후 런은 08-25부터 19~22개). 그래서 `normalize_price._write_canonical`이 같은 `(market, trade_date)` 파티션을 읽고-합치고-덮어쓴다(CAS 없음, ALPHA-1057). 그리고 `load_price_daily`의 manifest 검증(`_manifest_partitions`·`manifest_rows`)은 manifest에 적힌 sha256과 현재 parquet 바이트가 다르면 적재를 거부한다. 두 날짜를 동시에 돌리면 한쪽 병합이 유실되거나, 먼저 끝난 정제의 적재가 뒤 정제의 덮어쓰기 때문에 실패한다. 날짜별 산출물이 겹치지 않거나(run별 staging 후 승격, 파티션 CAS·버전 병합) 적재가 manifest 시점 스냅샷을 읽는 구조라면 날짜 병렬은 가능하다. 이번 실험은 그 구조를 만들지 않았고 세 비교군 모두 직렬로 맞췄다.

## 결과 (r2: 정상 1회 + 실패·복구·재요청 3회씩, 순서 교대)

기대 결과는 `lab.py verify`가 **정제 코드를 쓰지 않고 raw에서 직접** 계산한다: 업무 키 `(market, ticker, trade_date)`마다 최신 `fetched_at` 행. canonical parquet과 `price_daily`를 키·값(시가·고가·저가·종가·거래량 / 종가·수정종가·거래량)으로 대사하고 누락·초과·중복을 센다. 업무 키 4,165개.

| | A | B | C |
|---|---|---|---|
| 1. 정상 실행 대사 | 일치(4,165/4,165, 7/7) | 일치 | 일치 |
| 실패 직후(09-17·09-23) | 누락 411(09-23분), 거래량 불일치 397(09-22분) | 동일 | 동일 |
| 2. 복구 후 업무 값 | 일치 | 일치 | 일치 |
| 복구 후 원장 완료 | **5/7** — 두 날은 FAILED로 남음 | 7/7 | 7/7 |
| 복구 호출(정제+적재) | 2+2 | 2+2 | 2+2 |
| 제출 명령 | 4(날짜당 2, run-id 직접 구성) | 1 | 1 |
| 3. 같은 범위 재요청 호출 | **7+7 전부 재실행** | 0 | 0 (Airflow가 7건 `already exists`) |
| 재요청 후 대사 | 일치, 중복 0 | 일치, 중복 0 | 일치, 중복 0 |
| `data_version` ≠ 순차 처리 | 복구 직후 3,347행이 원장에 없는 수동 run-id, 재요청 후 0 | 1,628행 | 1,628행 |
| 복구 경과시간(s, 3회) | 4.37 · 4.42 · 4.36 | 2.84 · 2.80 · 2.79 | 11.51 · 11.45 · 11.47 |
| 정상 요청(s) | 5.38 (실패 포함 3.4~3.9) | 5.47 (4.0~4.1) | 60.8 (37.8~57.5) |
| 재요청(s) | 6.8 | 1.0 | 18.9 |

- 경과시간은 **자동 실행 시간**이다(monotonic). 복구·재요청은 요청 제출 → 독립 대사 통과까지, 정상 요청은 요청 제출 → 요청 종료까지이며 뒤따르는 대사(약 0.6초)는 포함하지 않는다. 사람의 조사·판단 시간은 재지 않았다. A의 "실패 run을 알아내고 새 run-id를 만들고 적재 입력을 새 run-id로 바꾸는" 판단은 스크립트가 대신했다.
- 세 방식 모두 복구에서 실패한 두 날짜만 다시 돌렸다. A는 운영자가 올바르게 고른 경우이고, B·C는 도구가 골랐다.
- C의 경과시간 대부분은 scheduler의 run·task 배정 간격과 backfill 완료 표시 대기다. 업무 처리 자체는 날짜당 약 0.8초로 세 방식이 같다.
- 실행 이력: C는 `normalize_price` try_number 2로 재시도 이력을 구분한다. B는 호출 기록과 원장 outcome만 남는다. 로컬 실행에서는 세 방식 모두 원장 attempt가 **생성되지 않는다**(`ecs_task_arn 없음 — attempt 생성 안 함`, wrapper가 ECS ARN을 요구).

### 자원 (docker stats 약 2초 간격, `results/r2/summary.md`)

| | CPU% 평균(최대) | 메모리 MiB 평균(최대) |
|---|---|---|
| airflow 유휴 | 5.1 (12.1) | 1,170 (1,212) |
| airflow C 요청 중 | 58.1 (198.9) | 1,207 (1,323) |
| airflow-db(메타DB) | 3~4 | 58 (60) |
| runner A·B 요청 중(업무 프로세스 포함) | 102~115 (205) | 76~84 (114) |
| runner 유휴 | 0.5 | 3 |

C의 airflow 수치에는 LocalExecutor가 같은 컨테이너에서 띄운 업무 프로세스가 포함된다. `dags delete`·`reserialize` 같은 CLI 호출도 순간 CPU를 올린다(C-reset 평균 93%).

## 확인한 사실

### 1. 재처리 수요 — 조사한 범위에서만

dev `edge-dev-data-pipeline`의 조회된 실행 67회(07-17~09-25)는 SUCCEEDED 37·FAILED 27·ABORTED 3이다. 실패 state(한 실행에 여럿 가능)는 LoadPriceTriggers 10, CollectKrxEtf 10, CollectKisInvestor 5, AnalyzeOne 4, NormalizeEtfNav·CollectKisPrice·CollectBigKindsNews·AssembleEvents 각 1이다.

**이 실행 이력에서 NormalizePrice·LoadPriceDaily의 단계 실패로 인한 복구 수요는 관측하지 못했다.** 이번 실패 시나리오는 주입한 합성 실패다. 단계 실패가 없어도 원천 데이터 정정, 정제 규칙 변경, 수동 요청으로 재처리가 필요할 수 있으며, 그런 요청의 이력(티켓·수동 ECS 실행·운영 메모)은 조사하지 않았다. 재처리 수요가 없다고 결론 내리지 않는다.

### 2. 중간 날짜 실패는 대부분 가려진다

KIS 일봉 수집은 과거 창을 겹쳐 받는다(09-18 이후 런은 08-25부터). 그래서 09-17 실패는 다음 런이 대부분 덮는다. 다만 15:41 KST 수집의 당일 거래량은 잠정값이고, 다음 날 런이 확정값으로 바꾼다(2,355키가 `acml_vol`만 다름). 그 결과 D일 실패는 **D일 전량 누락 + D−1일 잠정 거래량 고착**을 남긴다. 마지막 날짜의 실패는 다음 런이 오기 전까지 가려지지 않는다.

### 3. 업무 값과 계보(`data_version`)는 따로 본다

- **업무 값:** 세 방식 모두 복구 후 4,165키의 값·누락·중복이 기대 결과와 일치했다.
- **`data_version`의 현재 계약:** 원천 데이터의 출처가 아니다. "그 행을 마지막으로 성공 확정한 NormalizePrice manifest의 run_id"다. `load_price_daily.run`은 `confirmed_data_version = input_run_id or run_id`를 쓰고, 값이 같아 UPDATE가 걸러진 행에도 이 값만 다시 찍는다(모듈 도크스트링: "현재 manifest에서 성공 재확정됐음을 downstream이 구분하도록"). 소비자 `load_price_triggers._db_closes`는 `price_daily.data_version = <자기 input_run_id>`인 행만 그 런의 현재 가격으로 읽는다. 즉 **소비 범위를 정하는 키**다.
- **관측:** B·C에서 늦게 복구한 옛 날짜가 겹치는 거래일 1,628행을 순차 처리와 다른 run_id로 다시 찍었다. 계약상으로는 정의된 동작("마지막 확정 run")이다. 따라서 이것만으로 오류라고 판정하지 않는다(**관측 차이**).
- **계약상 주의점(미검증):** 옛 날짜 복구 뒤, 그보다 늦은 런의 LoadPriceTriggers를 다시 돌리면 재확정된 행이 `data_version` 필터에서 빠진다. 이번 실험은 트리거 단계를 포함하지 않아 실제 영향은 확인하지 않았다. 다만 #912 이후 트리거의 현재 운영 판정은 manifest의 최신 거래일만 보고, 겹치는 과거 거래일 결손은 `historical_reconciliation_debt`로만 남긴다. 그래서 영향이 있다면 과거일 품질 지표 쪽일 가능성이 높다.
- **A의 수동 run-id:** A는 복구 직후 3,347행이 원장에 없는 run-id(`manual_…`)를 가리켰다. 그 run-id로 LoadPriceTriggers를 돌리지 않는 한 원래 run의 트리거 입력에서 빠진다. 재요청이 전 범위를 다시 돌린 뒤에야 순차 처리와 같아졌다.

### 4. B의 완료 판단이 보장하는 범위

`lab.py backfill`은 날짜마다 `_run_status(run_id)["ok"]`를 **먼저** 보고, 완료로 판단되면 `select_input`(원본 존재·sha256 검사)을 거치지 않고 건너뛴다. 완료 판단은 두 가지뿐이다. 원장 `ops_expected_task`의 NORMALIZE_PRICE·LOAD_PRICE_DAILY `task_outcome = FULFILLED`, 그리고 manifest의 `canonical_written = true`.

- **이 실험에서 의미하는 것:** 입력이 `inputs.json`으로 고정되어 run_id가 곧 입력 판본이고, 규칙·코드 판본도 한 번의 실행 동안 바뀌지 않는다. 그래서 "같은 run_id가 완료됐다 = 같은 입력·같은 규칙으로 결과가 있다"가 성립했다. 동일 범위 재요청의 불필요 재실행 0은 이 전제 위의 결과다.
- **보장하지 않는 것:**
  - 입력 정정: 같은 run_id의 raw가 바뀌어도 checksum 검사 전에 건너뛴다.
  - 규칙 변경: 코드·정책 판본을 완료 판단에 쓰지 않는다.
  - 산출물 소실: canonical parquet·`price_daily` 행이 사라져도 원장과 manifest가 남아 있으면 완료로 본다.
  - 원장만 FULFILLED인데 산출물이 다른 경우.
- **필요한 계약(이번에 구현하지 않음):** "같은 요청의 재실행"(입력·규칙 판본 동일 → 건너뛰기 허용)과 "새 입력·새 규칙 재처리"(판본이 다름 → 새 실행)를 구분해야 한다. 이를 위해서는 완료 기록에 입력 checksum·규칙 판본을 남기고, 요청에도 같은 판본을 명시한 뒤, 둘을 비교하고 나서 건너뛰는 계약이 필요하다. C의 재요청 건너뛰기는 같은 logical_date의 dag run이 이미 있는지(`--reprocess-behavior none` → `already exists`)만 본다. 원장·산출물도, 입력·규칙 판본도 보지 않으므로 같은 한계가 더 넓게 적용된다. Airflow의 run 상태는 이 판본 비교를 대신하지 않는다.

### 5. run-id 복구 지침 충돌과 권장 정리안

**두 지침:**
- `infra/terraform/modules/data-pipeline/statemachine.tf` NormalizeParallel 주석: `normalize-<step> --run-id <새 id> --input-run-id <실패한 run_id>`. 누가 언제 무엇을 승격했는지가 남는다는 근거를 든다.
- `ops/wrapper.py instrument` 주석: 수동·백필 런은 run_id가 `pipeline_run_id`와 안 맞아 계측이 사라지므로, 원장에 매칭하려면 `--run-id <pipeline_run_id>`를 쓰라고 한다.

**코드가 실제로 쓰는 방식:**

| 식별자 | 만드는 곳 | 뜻 |
|---|---|---|
| `pipeline_run_id` | `planner.plan_run` — `stable_domain_id("run", "<lane>:<슬롯 KST>")`. 운영 `plan-run`은 항상 `mode="incremental"` 슬롯 계획만 만든다 | 한 스케줄 슬롯의 계획. `ops_expected_task`가 여기에 달린다 |
| 업무 CLI `--run-id` | SFN이 `$.run_id`(= `pipeline_run_id`)를 넘김. 수동은 사람이 지정, 없으면 `make_run_id()` | 원장 매칭 키(`find_expected_task(run_id, task_key)`) + **자기 산출물 키**(`canonical_run_manifest_key(dataset, run_id)`, `quality_log_key(…, run_id)`) |
| `--input-run-id` | 정제: 읽을 raw 수집 런. 적재: 읽을 정제 manifest의 run_id | 입력 범위 |
| attempt | `ledger.record_attempt_start` — `(expected_task_id, ecs_task_arn)` 유일. ARN 없으면 생성 안 함 | 물리 실행 시도. 같은 run_id의 재시도는 ARN으로 구분된다 |
| manifest | 정제가 자기 `--run-id`로 기록. 시작 시 `canonical_written=false`로 먼저 무효화 | 그 실행이 승인한 canonical 범위 |
| `data_version` | 적재가 `input_run_id`(정제 manifest id)로 stamp | 마지막 성공 확정 manifest(§3) |

**같은 run-id 재사용의 영향:**
- 정제는 같은 manifest 키를 **덮어쓴다**. 시작 시 무효화하고 성공해야 완료로 다시 쓰므로 동일 입력 재시도에는 안전하다.
- 두 시도가 동시에 돌면 manifest·quality log가 서로 덮인다. wrapper는 `ops_attempt_id` 대조로 남의 실패 증거를 강등할 뿐, 쓰기 경합은 막지 않는다.
- canonical 병합과 DB PK upsert는 멱등이라 업무 값은 중복되지 않는다.

**권장 정리안(실험에서 도출, 운영 원문은 수정하지 않음):**

| 경우 | `--run-id` | 원장 | 근거 |
|---|---|---|---|
| 동일 입력으로 실패한 기존 실행 복구 | **원래 `pipeline_run_id`** | 같은 expected task에 새 attempt(ECS ARN)로 기록, outcome 갱신 | 입력·규칙이 같으므로 산출물 키를 덮어써도 의미가 같다. 원장 실패가 닫힌다(A 5/7 → B 7/7). **동시 실행 금지 전제** |
| 정정된 입력으로 새로 처리 | **새 run-id**, `--input-run-id` = 정정 raw 런 | 현재는 기록할 계획이 없다(원장 미매칭) → 별도 계획 경로 필요 | 원래 run의 manifest를 다른 입력으로 덮으면 "그 슬롯이 무엇을 처리했나"가 거짓이 된다 |
| 처리 규칙 변경으로 재계산 | **새 run-id** + 규칙 판본 기록 | 위와 같음 | 입력이 같아도 결과가 다르다. 원래 run의 산출물·계보를 보존해야 전후 비교가 된다 |
| 과거 입력 재현(검증·조사) | 새 run-id, **격리 저장소·DB**(이 실험처럼) | 운영 원장에 쓰지 않음 | 운영 canonical·`data_version`을 바꾸면 안 된다 |

`statemachine.tf`의 "새 id" 지침은 아래 세 경우에 맞고, `wrapper.py`의 "원래 id" 지침은 첫째 경우에 맞다. 두 문구가 각자 다른 경우를 전제로 하면서 그 구분을 적지 않은 것이 충돌의 원인이다. 모든 경우를 원래 run-id로 통일하면 입력·규칙이 다른 결과가 원래 슬롯의 산출물을 덮는다.

**별도 작업으로 제안하는 범위(이번에 하지 않음):**
1. 두 주석과 README에 경우별 run-id 표를 한 곳(SSOT)에 두고 나머지는 참조로 바꾼다.
2. 정정·규칙 변경 재처리를 원장에 남길 계획 경로를 만든다. 예: `plan_run` `mode`에 backfill·correction 추가. `_start_execution`은 이미 mode 차이를 CONFLICT로 구분하는 주석이 있다.
3. 동일 run-id 동시 실행을 막을 잠금을 둘지 검토한다.

### 6. LoadPriceTriggers 실패 — 원인과 현재 상태(읽기 전용 조사)

**자료:**
- SFN 실행 이력(보존본)과 S3 `operations_archive/data_quality_logs/dataset=price_movement_trigger/` 품질 로그 29건(08-20~09-24)을 읽었다.
- CloudWatch 로그 그룹 `/ecs/edge-dev-data-pipeline`은 **보존 14일**이라 08-28~09-07 컨테이너 로그는 남아 있지 않다.
- SFN 재실행·redrive·외부 API 호출은 하지 않았다.

| 실패 실행 | 당시 원인 | 당시 코드 근거 | 후속 수정 | 현재 잔존 | 복구 방식 | 미확인 |
|---|---|---|---|---|---|---|
| 정기 08-28·08-31·09-01·09-02·09-03·09-04·09-07(15:40), 비정기 09-01T18-33·09-01T22-15·09-02T00-37 — 10회 모두 ECS **exit 2** | 가격 manifest가 KIS 겹침 창의 과거 거래일 46개(06-23~08-27)를 담았다. 그 과거일에 ETF holdings가 없어 `missing_holdings` 1,748건, `missing_db_price` 2건이 났다. **현재 거래일의 트리거는 매번 정상 생성**(실패 목록에 당일 없음, `created` 0~30) | #881(08-27, `9b35a171`, ALPHA-1039)이 트리거 범위를 가격 manifest 전체로 바꿨다. 과거일 결손도 `exit_code = _PARTIAL_EXIT_CODE`였다 | #912(09-08 14:28, `b01458e1`, ALPHA-1062). 결손을 manifest 최신 거래일(`operational_trade_date`)이면 `current_operation`, 아니면 `historical_reconciliation_debt`로 나누고, exit 2는 현재일 결손에만 준다 | 09-08 이후 전 런 exit 0. 과거일 결손은 부채 지표로 계속 기록된다(09-18 이후 3일·114건). 현재 코드에서 같은 실패는 성립하지 않는다 | **업무 코드 수정**으로 해소. 실패 기간 중 재시도·redrive 기록은 없다. 다음 정기 실행도 같은 원인으로 exit 2를 반복했다 | 비정기 실행 3회(09-01 18:33·22:15, 09-02 00:37)를 누가 왜 시작했는지(가격 트리거 복구 목적인지), 수작업 소요시간 |

- **원인 분류:** 원천 입력의 창 구조(과거일 포함)와 업무 코드의 판정 범위 불일치다. 인프라·timeout·동시성 문제가 아니다. 반복된 실패는 **한 가지 원인**이다(10회 모두 같은 46개 날짜·같은 결손 건수).
- **복구 부담:** 이 실패는 당일 산출물을 잃지 않았고, 날짜 선택·부분 재실행으로 풀릴 성격이 아니었다. 다시 돌려도 같은 결과가 났다. 오케스트레이터가 줄일 수 있는 다단계 복구 부담은 확인되지 않았다.
- **판단:** 이미 업무 코드로 해결됐으므로 **추가 장애 실험을 권하지 않는다.** 남은 것은 과거일 holdings 부재라는 데이터 부채다. 이것도 재실행이 아니라 원천 보강·판정 정책의 문제다.

## 판단

**이번 가격 경로와 검증 조건에서는 B(스크립트 보완)로 충분했고, Airflow 도입은 보류한다.** 운영 코드에 B를 적용한 것은 아니다.

- **검증한 범위:** 고정 입력(실제 raw 7일), 날짜 직렬 실행, 주입한 raw 읽기 1회 실패 2건, 동일 범위 재요청.
- **검증하지 않은 범위:** worker 중단, 입력 정정, 원본 소실, 운영 동시 실행, 규칙 변경. B는 이 상황의 복구 도구로 검증되지 않았다(§4의 완료 판단 한계).
- **A→B(공통 개선):** 원장 완료 5/7→7/7, 제출 명령 4→1, run-id 수작업 제거, 재요청 불필요 재실행 14→0. B의 제어 코드(`backfill`·`check-run`·`_run_status`, 약 50줄)는 이 실험의 공통 기반(`reset`·`select_input`·`step`·`verify`)과 기존 업무 코드(`run.main`, 원장 wrapper, manifest) **위에** 얹은 추가분의 규모다. 독립 복구 도구의 전체 규모가 아니다.
- **B→C(Airflow 추가분):** 이번 조건에서 B 대비 추가로 해결한 문제는 제한적이었다. 날짜·작업별 상태와 시도 이력(try_number) UI·API, task 단위 clear, 중복 backfill 거절이 생겼다. 반면 정확성·복구 범위·호출 수는 같았고, 입력 선택·완료 확인은 B와 같은 코드가 필요했다.
- **C의 추가 부담(이번 로컬 구성 기준):**
  - 상주 메모리 약 1.2 GiB + 메타DB 58 MiB, 유휴 CPU 약 5%, 구성요소 네 개
  - DAG당 backfill 1개 제약과 비동기 완료 표시(실험 하네스가 이를 몰라 r1이 중단됐다)
  - `logical_date`→업무일 변환
- **경과시간 차이(C가 느림)의 범위:** standalone·LocalExecutor·기본 scheduler 설정에서 7일×4 task를 돌린 **로컬 관측**이다. Airflow 일반의 성능 열위나 SFN 대비 열위로 일반화하지 않는다.
- **재평가 조건:**
  1. 단계 실패·정정·규칙 변경으로 인한 반복 재처리가 실제 이력으로 확인될 때
  2. 여러 사람이 날짜별 실행 상태를 보고 인계해야 하는 요구가 생길 때
  3. 스크립트 보완이 판본 비교·의존성·재시도까지 떠안아 코드로 감당되지 않을 때

  LoadPriceTriggers는 §6에 따라 재평가 근거가 아니다.

## 한계

- 로컬 CLI·로컬 Airflow 비교다. SFN retry/redrive, Fargate 기동, AWS·MWAA 비용은 검증하지 않았다.
- 경과시간은 스크립트가 수행한 자동 실행 시간이다. 사용자의 작업시간 절감으로 쓰지 않는다. 사람 작업시간과 조사에 방문한 도구 수는 측정하지 않았다.
- 표본은 조건당 3회다. 분산·백분위 주장을 하지 않는다.
- 실패는 raw 읽기 1회 실패 한 종류, 재시도 0이다. 자동 재시도로 해결되는 경우와 다음 시나리오는 하지 않았다: worker 중단(A-R), 입력 정정(A-C), 원본 누락(A-M), 현재 날짜 동시 처리(A-L).
- 원장 attempt 계측은 로컬에서 세 방식 모두 비어 있다(ECS ARN 의존). Airflow 연동 시 attempt 식별 설계가 먼저 필요하다(학습 계획 §15.3).
- 종목 마스터는 fixture다. 적재 대상 판정(`unknown_instrument`) 경로는 검증 범위 밖이다.
- LoadPriceTriggers를 포함하지 않아 `data_version` 재확정이 트리거 소비에 미치는 영향은 확인하지 않았다(§3).

## dev 머지 시 배포 영향

**이 폴더는 `src/apps/cloud/data-pipeline/**` 아래라, dev에 머지하면 배포 워크플로가 돈다.** 업무 코드가 바뀌지 않는다는 사실은 배포가 없다는 근거가 아니다.

- **실행 조건:** `.github/workflows/deploy-data-pipeline.yml`은 `push: branches [dev]`와 경로 `src/apps/cloud/data-pipeline/**`(외 `src/libs/ontology/**`·`src/pyproject.toml`·`src/.dockerignore`)에서 실행된다. 2026-09-26 기준 PR 브랜치와 `origin/dev`의 워크플로 파일은 같다.
- **이미지 빌드·푸시:** 무조건 실행된다. 운영 Dockerfile로 `src/` 컨텍스트를 빌드해 ECR `edge/pipeline:<sha>`와 `data-pipeline-latest`로 푸시한다. SFN 단발 task는 다음 실행부터 이 태그를 당긴다. `src/.dockerignore`는 `experiments/`를 제외하지 않으므로, 이 폴더의 파일(결과 포함)이 이미지의 `COPY apps/cloud/data-pipeline` 층에 들어간다. wheel에는 들어가지 않는다(`packages = ["src/data_pipeline"]`).
- **상주 서비스 재배포:** `vars.MINUTE_SERVICES_DEPLOYED == 'true'`일 때 9개 서비스(price-worker·relay·price-consumer·news-consumer-realtime/backfill·news-worker·disclosure-worker·inav-worker·sector-index-worker)를 `force-new-deployment`한다. `desiredCount = 0`인 서비스는 건너뛴다. 2026-09-26 `gh variable list`로 저장소 변수 `MINUTE_SERVICES_DEPLOYED=true`(갱신 08-03)를 확인했다. 그러므로 장중(서비스 desired>0)에 머지하면 상주 서비스가 재기동된다.
- **확인하지 못한 것:** 머지 시점의 각 서비스 desiredCount, 환경(environment) 수준 변수 덮어쓰기 여부(job은 environment를 지정하지 않음), ECR·배포 역할의 현재 권한 상태.
- **처리:** 워크플로와 폴더 위치는 바꾸지 않았다. PR은 머지 보류다.

## 실행

Docker, dev 레이크 읽기 권한(입력 준비에만 필요), Python 3(표준 라이브러리)이 필요하다. 이 폴더에서:

```sh
# 입력 준비(읽기 전용 복사, inputs/raw 는 gitignore). inputs.json 의 sha256 과 대조된다
for d in 15 16 17 18 21 22 23; do aws s3 cp --recursive \
  s3://edge-dev-pipeline-lake/raw/source=kis/dataset=price_daily/market=KR/ingest_date=2026-09-$d/ \
  inputs/raw/source=kis/dataset=price_daily/market=KR/ingest_date=2026-09-$d/; done

docker compose -p airflow-lab build
python3 trial.py up
python3 trial.py run r3 --reps 3     # results/r3 (있으면 실패, 덮지 않는다)
python3 trial.py summary r3          # results/r3/summary.md
python3 trial.py down                # 컨테이너·볼륨 삭제
```

포트는 `127.0.0.1:55452`(업무 DB)·`127.0.0.1:58080`(Airflow UI/API, 인증 없음)만 연다. 결과 폴더: `results.json`(시나리오별 수치·대사·호출 기록), `commands.log`(모든 명령의 stdout/stderr 원문), `stats.jsonl`(자원 표본), `airflow-logs/`(마지막 C 시행의 task 로그), `versions.json`(git sha·Airflow·Python·pip freeze).

## 실패 기록

- 사후 보강(PR 전 리뷰, 원시 결과 재생성 없음): `trial.py`가 ① 최종 대사 불일치 시 비0 종료, ② `load-price-daily` exit 2를 실패로 집계(계속 진행 규칙은 `normalize-price`만), ③ 호출 기록 조회 실패를 0건으로 접지 않도록 고쳤다. r1·r2 원시 결과를 대조해 영향이 없음을 확인했다 — exit 2 호출 0건, 호출 기록 조회 실패 0건, 전 시나리오 최종 `business_ok=true`. r1·r2는 수정 전 코드로 만들어졌다.

- `r1-aborted-backfill-race`: C의 재요청 `backfill create`가 `AlreadyRunningBackfill`로 실패했다. dag run이 모두 끝나도 scheduler가 backfill `completed_at`을 찍기 전에는 새 backfill을 받지 않는다. 대기 조건에 `completed_at`을 추가해 r2를 새로 돌렸다. r1에서 끝난 A·B·C 정상, A·B 실패 시나리오의 대사 결과·호출 수는 r2와 같다.
