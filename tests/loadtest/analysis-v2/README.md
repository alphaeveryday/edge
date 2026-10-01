# 분석 v2 실행 자동화 — 측정 기록

v2 전망·가격변동 실행 자동화의 기준 측정과 계약 재현 기록이다(ALPHA-1142). 기준 측정, 로컬 검증, dev 검증, 실제 스케줄 관측을 구분해 적는다.

## 구성과 재현

- `aws/`: 2026-10-02 dev 조회 원본. SFN 실행 3건(`v2-executions.json`, `exec-*.json`), 구간 분해(`cloud-phase-breakdown.json`), DB 조회 SQL과 결과(`q1.sql`, `q2.sql`, `dbq-q1.log`, `dbq-q2.parsed.json`), CloudWatch 원본(`cloudwatch.json`), 조회 스크립트(`dbq.sh`).
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
- 08:00 이전 마지막 원천 적재는 00:10 뉴스 배치다(00:20~00:22 종료). 장전 뉴스 배치는 08:10에 시작하므로 08:00까지 저장하는 전망에 들어갈 수 없다.

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
- '1,100초 제한과 충돌': 충돌하지 않는다. 실제 상한은 모델 대화 제한 300초(`run_model` 기본값)이고 관측 최대 226초다. 여유가 74초라 긴 꼬리 실패 위험은 남는다.
- 'dev 문서의 최대 2개': 로컬 대시보드 경로(건당 writer 2개) 기준이다. 클라우드 워커 경로는 건당 3개라 2개 동시 실행이 성립하지 않는다.

**미확인**

- DeepSeek 동시 호출 한도(실측은 동시 2까지).
- 실제 RDS에서 writer 세션이 늘 때의 메모리 변화(로컬 수치는 참고용).
- 37종 전체의 건당 시간 분포(표본은 10종).
- 가격변동 `analysis_at`의 의도된 기준(트리거 생성 시각으로 보이나 작성자 확인 전).
- Step Functions 내장 기능의 실제 동작 네 가지(아래 계획에 표시).

## 구현 전 계획 (2026-10-02, 이후 보완됨)

**전망** (신규 배치 SFN, 기존 단건 SFN을 중첩 호출)

- EventBridge Scheduler(평일) → 배치 SFN → Inline Map(37종, `MaxConcurrency`) → 항목별로 ① 오늘 발행본 조회 ② 없으면 단건 SFN 실행 ③ 실패 시 발행본 재조회 → 집계 → 실패가 하나라도 있으면 배치 실패.
- 중첩 호출을 택한 이유: 직접 ECS 실행은 상태 API가 단건 SFN 실행을 조회하는 가정을 깬다(queued가 404, 강제 종료가 영구 running). 단건 SFN의 종료 코드 판정과 타임아웃도 그대로 쓴다.
- 분석 ID: `MD5(종류:ETF:기준시각:배치 실행명:재시도 횟수)`. 32자리 소문자 16진수라 요청 검증을 통과하고, 시도마다 새 ID가 된다. 논리적 작업은 DB 행의 ETF와 기준시각으로 식별한다.
- 재시도는 항목당 1회. 완료분 건너뛰기와 최종 저장 확인은 `GET /v2/etfs/{code}/outlook?date=`로 한다(종료 코드보다 DB 발행본을 우선).
- 기준시각은 배치 시작 시각으로 고정한다. 기존 관례인 08:30은 08:00 마감에서 미래 시각이 된다.
- 마감은 배치 `TimeoutSeconds`로 건다. 알람은 장전 파이프라인과 같은 형태(실패, 시간 초과, 스케줄러 DLQ)를 쓴다.
- 구현 때 확인할 내장 기능: `$$.State.RetryCount`가 재시도마다 다시 평가되는지, `States.Hash` MD5 출력 형식, `apigateway:invoke`의 HTTP API IAM 인증, 부모 시간 초과 시 중첩 실행과 ECS 태스크 정리.

**가격변동** (v1 유지, v2 사본 경로 추가)

- price-consumer가 같은 트랜잭션에서 v2용 outbox 사건을 하나 더 쓴다(플래그) → relay → 새 SQS 큐와 DLQ → 디스패처(data-pipeline 이미지의 새 명령, 1건씩 순차) → 단건 SFN 시작과 완료 대기 → 성공 시 삭제.
- 기존 큐를 나눠 받지 않는다. v1 소비자와 `tenant_delivery` 경로는 그대로다. 되돌림은 플래그 끄기와 디스패처 0대다.
- 분석 ID: `MD5(movement:trigger_id:시도)`. 실행이 이미 있으면 상태를 보고 대기, 완료, 다음 시도로 갈린다. 시도 원장은 SFN 실행 이력이다.
- 순차 1건이면 전체 상한과 같은 ETF 직렬이 구조로 보장되고 역할 한도 변경이 필요 없다. 회수 사건은 v2로 보내지 않는다.

**처음부터 필요한 것과 미룰 것**

- 처음부터: 위 두 경로, 시도별 ID, 발행본 확인, 배치 실패 표시와 알람.
- 조건부: writer와 reader 역할 한도 상향. 전망 동시 2 이상 또는 가격변동 동시 2 이상을 택할 때만 필요하다. 필요 값은 `3 × 총 동시 실행 수 + 여유`다.
- 후속: 연결 점유 축소(짧게 쥐기, lease), 실패 원인별 재시도 분류, 휴장일 건너뛰기, 강제 종료된 `running` 행 정리, 전 종목 확대 용량 측정.
- 채택하지 않음: Distributed Map(37종은 Inline 한도 안), 풀이나 프록시, Airflow, FIFO 큐.

**사용자 결정 대기**

1. 전망 자료 기준: 00:40 시작과 순차 실행(추천, 설정 변경 없음, 예상 2.5~3.3시간) 또는 뉴스 장전 배치를 앞당기고 07시 전후 시작(동시 3~4, 역할 한도 상향과 DeepSeek 동시성 실측 필요).
2. 가격변동 v1 대체 범위: 병행 연결(추천) 또는 교체. 교체는 v2 결과를 테넌트로 내보내는 경로가 생긴 뒤에만 가능하다.
3. 가격변동 허용 지연: 순차 1건(추천, 관측 최대 버스트 25건이면 최악 약 1시간) 또는 동시 2(역할 한도 상향 필요).
