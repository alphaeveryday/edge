# 분석 v2 실행 자동화 — 측정 기록

v2 전망·가격변동 실행 자동화의 기준 측정과 계약 재현 기록이다(ALPHA-1142). 기준 측정, 로컬 검증, dev 검증, 실제 스케줄 관측을 구분해 적는다.

## 구성과 재현

- `aws/`: 2026-10-02 dev 조회 원본. SFN 실행 3건(`v2-executions.json`, `exec-*.json`), 구간 분해(`cloud-phase-breakdown.json`), DB 조회 SQL과 결과(`q1.sql`, `q2.sql`, `dbq-q1.txt`, `dbq-q2.parsed.json`), CloudWatch 원본(`cloudwatch.json`), 조회 스크립트(`dbq.sh`).
- `local/`: 탐침 `probe.py`, 격리 DB `docker-compose.yml`, 원시 결과 `results-20261002.jsonl` 44건, 요약 `results-summary-20261002.json`.
- 측정 시점 코드: `4311a19b`. 배포 이미지 `analysis-v2-cb9fa258`(작업 정의 9번)과 v2 소스 차이 0줄. 대상 목록 37종, 해시 `dc2373347520956f073a75214de38db1a8af9ebcd22a452efe73480f30f9225c`.

```sh
cd tests/loadtest/analysis-v2/local
EDGE_REPO=<저장소 루트> docker compose -p v2probe up -d postgres
EDGE_REPO=<저장소 루트> docker compose -p v2probe --profile migration run --rm flyway migrate
uv run --python 3.12 --with 'psycopg[binary]>=3.1' --with claude-agent-sdk==0.2.160 --with 'PyYAML>=6' --with 'jsonschema>=4' --with boto3 python probe.py sweep --kind outlook --c 2 --llm 6
uv run ... python probe.py scenarios
docker compose -p v2probe down
```

비밀번호 `local_only`는 로컬 컨테이너 전용이다. 실제 자격증명은 이 폴더에 없다.

## 기준 측정 (2026-10-02)

**연결 상태** (코드 관찰과 AWS dev 조회)

- v2 호출자는 없다. 스케줄러와 Airflow DAG 어디에도 v2 호출이 없고, app-api는 호출 권한만 있고 코드가 없다. v2 실행 이력은 수동 3건이 전부다.
- v2 결과 소비자도 없다. tenant-sync-api와 super-admin은 v1 표(`explanation_*`, `tenant_delivery`)만 읽는다. v2 결과는 v2 조회 API로만 나간다.
- v1 가격변동 경로는 가동 중이다. price-consumer → outbox → relay → SQS `price-explanation-realtime` → analysis-consumer(자동 확장 0~4) → `explanation_result` → `tenant_delivery`. 10월 1일에도 소비자가 처리했다.
- v1에는 전망 생산자가 없다. v1 장마감 analyze 단계는 SFN에서 제거된 상태다. 전망은 v2 신규 기능이며 대체할 v1이 없다.
- 트리거의 `entity_id`가 ETF 6자리 코드다. v2가 `minute_price_trigger`를 같은 값으로 조회한다.

**실측** (dev, 표본이 작으므로 분포 추정에 쓰지 않는다)

- 클라우드 실행 3건(전망 1, 가격변동 2, 모두 091160, 성공): SFN 전체 257초, 170초, 121초. 구간은 ECS 기동 21~28초, 컨테이너 시작 2초, 원천 읽기와 시작 처리 1초 이하(전망 1건), LLM과 도구 56~204초, 저장과 관측 내보내기 1초 이하, ECS 종료 25~27초. 도구 실행 합계는 0.1~0.8초다.
- DB 이력의 실데이터 분석(첫 도구 시작부터 발행까지, 로컬 실행 포함): 전망 완료 11건 135~226초, 중앙값 161초, 실패 2건. 가격변동 완료 13건 56~112초, 중앙값 79초, 실패 0건. 대상 ETF 10종.
- 전망 실패 2건은 모델이 낸 근거 ID 검증 실패 1건과 SDK 최종 응답 실패 1건이다.
- 클라우드 1건 실행 중 `DatabaseConnections`는 20에서 23으로 3 늘었다(2건 관측). `FreeableMemory` 변화는 6MB 이내다.
- RDS 7일: 연결 20~32, `FreeableMemory` 578~651MB, `max_connections` 181. 인스턴스는 db.t4g.small이다.
- 역할 연결 한도(pg_roles 조회): writer 5, reader 3, api_reader 5.
- 가격변동 트리거 큐 발행량(회수 포함, 8거래일): 하루 5~47건, 5분 최대 25건(9월 22일 09:00). 가장 오래 대기한 메시지는 하루 최대 13분~2.1시간.
- 뉴스 배치는 00:10(00:20~00:22 종료)과 08:10에 돈다. 08:10 배치는 08:00까지 저장하는 전망에 들어갈 수 없다. 이 관찰은 뉴스 배치 스케줄에 한정되며 다른 입력의 최신성을 말하지 않는다.

**로컬 실험** (PG 16.14 컨테이너 2GB, 실제 마이그레이션 77개, 실제 `worker.run`과 저장 코드. LLM, 원천 조회, S3는 대역)

- 배포된 한도(writer 5, reader 3)에서 동시 1건은 성공한다. 동시 2건은 13회 중 11회가 1건만 성공, 2회가 0건 성공이며 2건 모두 성공한 경우는 없다. 동시 4건과 8건에서는 0~1건만 성공한다. 오류는 `too many connections for role`이다.
- 한도를 로컬에서만 풀면 동시 37건이 모두 성공한다(writer 세션 111개, 컨테이너 메모리 약 120MiB → 341MiB). 전망 37건과 가격변동 37건을 함께 돌리면 `max_connections` 181에 걸려 74건 중 18건이 실패한다.
- 재시도 계약: 실패한 시도는 원인과 무관하게 분석 ID를 소진한다. 워커가 시작하자마자 관측 manifest를 `IfNoneMatch`로 선점하기 때문이다. 완료 ID를 워커로 다시 실행해도 같은 이유로 실패한다. 저장 결과 재사용은 API 계층에만 있다.
- 강제 종료된 실행은 `running` 행을 남기고 락은 풀린다. 새 ID 재시도는 성공한다.
- 연결 한도나 ETF 락으로 진 실행은 DB 행을 남기지 않는다. 실패 사유는 SFN 실패와 S3 manifest에만 남는다.
- 같은 ETF의 전망과 가격변동 병행은 ETF 락이 아니라 writer 한도 때문에 한쪽이 실패한다. 한도를 풀면 둘 다 성공한다.

**기각하거나 정정한 가설**

- '커넥션 풀 대기로 느리다': 기각. v2에는 풀이 없고 `psycopg.connect` 직접 연결이다. 연결 생성은 로컬 최대 76ms다.
- '연결을 오래 쥐어 메모리가 고갈된다': 현재 요구 범위에서는 근거 없음. 건당 writer 3개를 LLM 대기 내내 쥐는 것은 사실이나, 지금 동시 실행을 막는 것은 메모리가 아니라 역할 한도 5다.
- '건당 약 20분': 실측과 맞지 않는다. 건당 2~4분이다. 10월 1일 로컬에서 10종 전망을 2개씩 돌린 묶음이 약 19분 걸렸는데, 이 수치의 출처일 수 있다(추정).
- '1,100초 제한과 충돌': 충돌하지 않는다. 실제 상한은 모델 대화 제한 300초(`run_model` 기본값)다. 관측 최대 226초는 첫 도구 시작부터 발행까지라 구간이 달라서 여유를 초 단위로 단정하지 않는다. 긴 꼬리 실패 위험은 남는다.
- 'dev 문서의 최대 2개': 로컬 대시보드 경로(건당 writer 2개) 기준이다. 클라우드 워커 경로는 건당 3개라 2개 동시 실행이 성립하지 않는다.

**미확인**

- DeepSeek 동시 호출 한도(실측은 동시 2까지).
- 실제 RDS에서 writer 세션이 늘 때의 메모리 변화(로컬 수치는 참고용).
- 37종 전체의 건당 시간 분포(표본은 10종).
- 가격변동 `analysis_at`의 의도된 기준(트리거 생성 시각으로 보이나 작성자 확인 전).
- Step Functions 내장 기능의 실제 동작 네 가지(아래 계획에 표시).

## 전망 배치 (ALPHA-1142)

EventBridge Scheduler → 배치 워크플로(`edge-dev-analysis-v2-outlook-batch`) → Inline Map → 기존 단건 워크플로(`edge-dev-analysis-v2`) 중첩 호출. 정의는 `infra/terraform/modules/analysis-v2/outlook_batch.asl.json`, 자원은 같은 폴더의 `outlook_batch.tf`다. 분석 로직, 요청 계약, 기준시각과 결측 규칙은 바꾸지 않았다.

### 실행 계약

- **업무 기준시각**: 스케줄의 예정 시각이 `analysis_at`이 된다. 실제 시작이 늦어지거나 스케줄러가 재전달해도 같은 값이다. 수동 재실행은 같은 문자열을 입력으로 준다. `Z`로 끝나는 UTC 표기만 받는다(분석 ID가 이 문자열에서 나온다).
- **분석 ID**: `MD5(outlook:ETF:analysis_at:시도 번호)`. 시도마다 새 ID이고, 같은 작업의 같은 시도는 어느 배치에서 계산해도 같은 ID다. 논리적 작업은 종류, ETF, 기준시각이다.
- **완료분 재사용**: 시도 0부터 해당 ID의 저장 상태를 조회 API(`GET /v2/analyses/outlook/{id}`)로 읽는다. 완료면 재사용, 실패면 다음 시도, 없으면 실행한다. 날짜별 최신 발행본은 보지 않으므로 같은 날 다른 기준시각의 작업을 건너뛰지 않는다.
- **재시도**: 항목당 시도 2회(재시도 1회). 소진되면 그 항목은 실패다. `max_attempts`를 올려 재실행하면 실패 항목의 다음 시도만 돈다.
- **저장 뒤 실패**: 단건 워크플로가 실패로 끝나도 같은 ID의 저장 상태를 다시 읽어 판정한다. 종료 코드보다 DB가 정본이다.
- **총량**: 시작 전에 단건 워크플로의 실행 중 개수를 본다. 하나라도 돌고 있으면 30초 간격으로 기다린다. 다른 배치, API 수동 시작과 겹치지 않기 위해서다. 같은 작업을 다른 배치가 이미 돌리고 있으면 끝나기를 기다렸다가 그 결과를 쓴다.
- **마감**: 기준시각과 같은 UTC 날짜의 23:00Z(= 08:00 KST)를 절대 시각으로 계산한다. 마감이 지나면 새 분석을 시작하지 않는다. 마감 뒤에 발행된 결과는 완료가 아니라 `late`로 센다. 정의의 `TimeoutSeconds`(8시간)는 마감 장치가 아니라 안전망이다.
- **결과와 알람**: 전 항목이 마감 전 완료일 때만 성공한다. 그 밖에는 `OutlookBatch.Incomplete` 또는 `OutlookBatch.DeadlineExceeded`로 실패하고 cause에 건수와 미완료 항목이 실린다. 실패와 시간 초과 알람, 스케줄 전달 실패 DLQ 알람이 기존 알람 토픽으로 간다.

### 알려진 한계

- 총량 확인과 시작 사이에 끼어든 실행은 막지 못한다. 그때는 writer 역할 연결 한도가 한쪽을 거절한다(모델 호출 전). 배치 쪽이 거절되면 그 항목의 시도 하나를 쓴다.
- 로컬 대시보드 실행은 단건 워크플로를 거치지 않아 총량 확인에 보이지 않는다. 역할 한도만이 막는다.
- 진행 중인 분석은 마감이 지나도 중단하지 않는다. 끝난 뒤 `late`로 드러난다.
- 강제 종료된 시도의 `running` 행은 그대로 남는다(조회 API는 실패로 판정한다).
- 공휴일에도 평일 스케줄대로 돈다.
- 기준시각은 스케줄 예정 시각과 같다. 기준시각을 시작 시각보다 앞당기려면 입력을 계산하는 단계가 따로 필요하다.

### 검증 상태

| 단계 | 상태 | 근거 |
|---|---|---|
| 정의 문법 | 통과 | AWS `ValidateStateMachineDefinition` |
| 계약 테스트(대역) | 15건 통과, 변이 8건 전부 검출 | `infra/terraform/modules/analysis-v2/tests/test_outlook_batch.py` |
| Terraform | `validate` 통과 | 37종 목록이 `sources.toml` 파싱 결과와 순서까지 일치(해시 동일) |
| dev 소량 통합 | 대기 | 배포 뒤 |
| dev 37종 전체 실측 | 대기 | 배포 뒤 |
| 실제 스케줄 발화 관측 | 대기 | 시작 시각 확정 뒤 |

계약 테스트는 상태 전이, 내장 함수, Retry와 Catch를 AWS의 실제 해석기(TestState API)로 평가한다. 대역은 조회 API, 실행 중 목록, 단건 워크플로 호출 셋이다. IAM 권한, 실제 API 응답 형식, 부모 중단 시 자식 정리는 dev 검증에서 확인한다. 자격증명이 필요해 CI에서는 돌지 않는다.

```sh
AWS_PROFILE=edge uv run --with boto3 python -m unittest discover -s infra/terraform/modules/analysis-v2/tests -v
```

## 가격변동 연결 (다음 작업, 미구현)

구현 전 검토 메모다. 확정된 설계가 아니다.

- v1 소비자와 `tenant_delivery` 경로는 유지하고, v2에는 트리거 사본을 별도 경로로 보내는 방향을 검토했다. v2 결과를 테넌트로 내보내는 경로는 아직 없다.
- 전망과 가격변동이 writer 역할 한도 5를 함께 쓴다. 각각 순차로 돌아도 합치면 2건이 겹칠 수 있다. 전망 배치의 총량 확인은 단건 워크플로의 실행 중 개수를 보므로 가격변동도 같은 워크플로를 거치면 서로를 본다. 다만 확인과 시작 사이의 틈은 남는다.
- Standard SQS는 발생 순서를 보장하지 않는다. 순차 소비와 발생 순서 처리는 다르다. 회수(`ExposureReverted`) 의미, 재전달, 순서 뒤바뀜을 정리한 뒤 활성화한다.
- 트리거 큐 발행량(5분 최대 25건)은 회수를 포함한 수치다. 분석 요청 수로 그대로 쓰지 않는다. 허용 지연은 정해지지 않았다.
