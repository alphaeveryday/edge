# Kafka 가격 레인 — 로컬 개발판과 장애·복구 실험

실시간 가격 판정과 과거 입력 복구가 **같은 소비 코드·같은 offset 관리**로 동작하는지 로컬 Docker에서 검증한다. 검증 범위는 **판정 상태(트리거·앵커) 복구**다. 늦게 복원된 발화를 설명 레인으로 보내지 않았으므로 사용자에게 나간 설명까지 복구한 것은 아니다. 운영 경로(SQS)는 그대로다. 클라우드 자원을 만들지 않으며 운영 전환 결정이 아니다(2026-09 파이프라인 감사의 "운영 Kafka 불필요" 결론은 유지).

## 무엇이 실제이고 무엇을 대체했나

| 실제 코드 | 대체 |
|---|---|
| `PriceWorker` commit(artifact 저장 → window·job·outbox 한 트랜잭션), `OutboxRelay`, `MinuteConsumer` kernel(job 조회·CAS claim·재시도·성공 기록), `PriceTriggerHandler`(artifact checksum 대조·전일 종가 조회·앵커·트리거·outbox), PostgreSQL 16 + `migrations-cloud` 전체, Kafka 3.7.0 | 수집기(`driver.py`의 스크립트 가격), 저장소(S3 대신 `LocalStorage` 파일) |

앱 변경은 `minute/kafka_transport.py` 하나와 CLI 두 곳의 URL scheme 분기(`kafka://…`)다. kernel·handler·Relay 본문은 바꾸지 않았다. 어댑터의 ack 의미 대응은 모듈 도크스트링에 있다.

## 실행

Docker와 uv가 필요하다. 이 폴더에서:

```sh
docker compose -p kafka-price-lab build
uv run lab.py r5          # results/r5 에 기록. 같은 이름이 있으면 덮지 않고 실패한다
python3 verify.py r0 r1 r2 r3 r4   # 결과 파일만으로 성공 조건 재판정
```

`lab.py`는 시작할 때 `docker compose down -v`로 이 프로젝트(`kafka-price-lab`)의 컨테이너·볼륨을 초기화하고, 끝나면 다시 내린다. 포트는 `127.0.0.1:55442`(PostgreSQL)만 연다.

## 시나리오

가격 14창(기준가 100, 발화 3%, 회수 1%): `104 100 104 105 100 104 100 104 100 104 100 104 100 104`. 기대값은 `verify.py`가 가격 표에서 직접 계산한다(발화 seq 0·2·5·7·9·11·13, 회수 1·4·6·8·10·12).

| 장애 | 주입 | 확인할 것 |
|---|---|---|
| F1 | 운영 소비자가 seq3의 DB 성공 기록 뒤, offset commit 전에 exit 73 (`entry.py`) | 재기동 후 같은 메시지가 실행 없이 `terminal`로 commit, attempt 1 |
| F2 | seq5 artifact를 숨겨 `ARTIFACT_NOT_FOUND` 재시도, 그동안 seq6 발행 | seq6은 수신되지 않고 PENDING, 복원 뒤 창 순서대로 처리 |
| F3 | seq9 뒤 브로커 재시작 | 수동 개입 없이 이어서 처리 |
| F4 | seq1 뒤 판정 상태 스냅샷, 운영 앵커 100→104 손상(seq2 발화 누락). seq7 뒤 발견 → 복구 | 아래 복구 절차 |

**복구 절차(F4):**

0. 스냅샷: 판정 파생 상태(`minute_session_open`·`minute_trigger_anchor`·`minute_price_trigger`·파생 outbox)와 **그 시점 SUCCEEDED job 집합**을 한 REPEATABLE READ 트랜잭션에서 뜬다
1. 복구 DB `edge_repair` = 운영 DB 복사 → 파생 상태만 스냅샷으로 되돌림 → 스냅샷의 SUCCEEDED 집합 **밖**의 job을 PENDING으로 초기화 → 복구 DB에 남은 SUCCEEDED가 그 집합과 다르면(양방향) 중단
2. `kafka-consumer-groups --reset-offsets --to-earliest`로 복구 group을 **offset 0**에 둔다
3. 운영과 **같은 이미지·같은 명령**의 `repair` 서비스를 띄운다(DB 이름과 group만 다름). seq4에서 offset commit 전 exit 73 → 재기동
4. 운영은 그동안 seq8~11을 계속 처리한다. 복구 소비자는 seq8에서 job 행이 없어 `orphan`으로 멈춘다
5. 전환: 운영 소비자 정지 → **입력 원장 동기화**(`sync_inputs`: window·artifact 이력·job identity만, job은 PENDING) → 복구 소비자가 visibility 뒤 같은 메시지를 다시 판정해 seq11까지 따라잡음 → 판정 파생 상태를 운영 DB로 복사 → 운영 소비자 재기동, seq12·13 처리

파생 outbox(설명 레인으로 나갈 사건)는 운영 DB로 옮기지 않았다. 늦게 복원된 seq2 발화의 외부 발행은 이번 범위에서 제외했다 — 복구 결과는 비교만 한다.

## 결과 (2026-09-25)

- **v1** (`results/protocol-v1.json`, r0·r1): job 초기화 경계를 상수 창 번호로 정했다. 검토에서 결함으로 판정(아래 "PR 전 정합성 검토" 4)
- **v2** (`results/protocol-v2.json`, r2·r3·r4): 경계를 스냅샷의 SUCCEEDED 집합으로 정하고 S8을 추가했다. r4는 PR 전 리뷰에서 고친 어댑터(재할당 시 resume, commit 파티션 오류 검사)로 다시 돌린 결과다

| 조건 | r0 | r1 | r2 | r3 | r4 |
|---|---|---|---|---|---|
| S1 운영 14창 전부 성공, 배선 오류 0, 발행 중복 0(committed offset 14) | ok | ok | ok | ok | ok |
| S2 F1 재수신이 `terminal`, attempt 1 | ok | ok | ok | ok | ok |
| S3 F2 동안 seq6 미수신·PENDING, commit이 창 순서 그대로 | ok | ok | ok | ok | ok |
| S4 F3 뒤 자동 재개 | ok | ok | ok | ok | ok |
| S5 복구: seq0·1 `terminal`, seq2~11 재판정, seq4 재수신 `terminal`, `orphan` 대기 후 동기화로 따라잡음, 트리거·파생 사건 기대값과 일치 | ok | ok | ok | ok | ok |
| S6 전환 뒤 운영 트리거·앵커가 14창 기대값과 일치. 운영 outbox엔 seq2 발화 사건이 없음(손상 흔적, 옮기지 않음) | ok | ok | ok | ok | ok |
| S7 운영·복구 컨테이너 이미지·명령 동일, env 차이는 DB 이름·group·lab 이름 | ok | ok | ok | ok | ok |
| S8 재생 전 복구 DB의 SUCCEEDED job = 스냅샷 트랜잭션의 SUCCEEDED 집합 | n/a | n/a | ok | ok | ok |

| 관측(초) | r0 | r1 |
|---|---:|---:|
| F2: seq6 커밋 → 수신 (재시도 visibility 1·2·4초, 4번째 시도 성공) | 6.40 | 6.41 |
| 비정상 종료 뒤 재기동 → 첫 수신 (F1·F4 공통) | 43.0 / 43.3 | 43.0 / 43.4 |
| 정상 종료(SIGTERM) 뒤 재기동 → 첫 수신 | 0.08 | 0.12 |
| 전환 동기화 → 복구 소비자 마지막 commit | 8.6 | 8.0 |
| F3 브로커 재시작 | 4.0 | 3.9 |

관측값은 r0·r1 기준이다(v2는 절차의 경계 계산만 달라 시간 특성은 같다). 검증기는 변이 6종(복구 트리거 1건 삭제, 운영 outbox 사건 추가, 이미지 상이, commit 순서 교체, 경계 밖 job을 SUCCEEDED로, v2 경계 증거 파일 삭제)을 각각 실패로 잡았다. v1 실행(r0·r1)은 이름으로 고정해 S8을 `n/a`로 표시한다 — 파일 유무로 버전을 추정하면 증거가 빠진 v2 실행이 통과하기 때문이다.

## PR 전 정합성 검토 (통과한 테스트와 별개로 경계별로 확인)

| 경계 | 판단 | 근거·조치 |
|---|---|---|
| 1. offset commit 실패·재할당 때 미완료 메시지를 건너뛰는가 | 건너뛰지 않음. 단 리뷰에서 반대 방향 결함 2건을 찾아 고쳤다 — ① 재할당 뒤 **영구 정지**: 멈춘 파티션이 revoke 되면 재개 예약만 지우고 resume 하지 않아, librdkafka 가 유지하는 앱 pause 때문에 같은 소비자에 돌아온 파티션이 멈춘 채 남을 수 있었다 → revoke 콜백에서 resume. 처음 테스트의 가짜 consumer 가 재할당 때 pause 를 풀어 이 반례를 가렸다(고침). ② 동기 commit 의 파티션별 오류를 안 봐 실패가 성공으로 기록됐다 → 오류면 예외 | commit은 kernel이 DB를 terminal로 확정한 메시지에만 한다. commit이 실패해도 위치만 지났을 뿐 그 메시지는 끝났고, 재할당·재기동 뒤에는 committed offset부터 다시 와서 terminal로 흡수된다. 단위 테스트 2건 추가(재할당 중 멈춤 → committed offset부터 재수신 / commit 실패 뒤 판정 보류 메시지를 committed offset이 넘지 않음). 실제 브로커의 재할당은 F1·F4(죽은 멤버 만료 뒤 인계)로 확인 |
| 2. 발행 확인이 늦어 재발행해도 안전한가 | 안전(중복은 흡수) — 단 순서 한계 | 늦은 확인은 성공·실패 어느 쪽으로도 보고되지 않아 Relay가 재발행한다. 늦게 온 확인이 다음 호출의 성공으로 잘못 집계되지 않음을 테스트로 고정했다. 중복 메시지는 job이 SUCCEEDED라 terminal(F1과 같은 경로). **결함 수정:** 파티션 key 계산이 객체가 아닌 payload에서 예외를 내 destination 전체가 재시도에 묶일 수 있었다 → 가드와 테스트 추가. **남는 한계:** 원 메시지가 최종 실패하고 재발행이 뒤 창보다 늦으면 순서가 바뀐다(Relay 재시도는 사건 단위라 기존 SQS 경로와 같은 성질). 그때 handler의 앵커 역전 가드가 그 과거 창의 발화를 건너뛴다 |
| 3. 재시도 소진으로 DEAD가 된 창을 넘긴 뒤 판정을 계속해도 되는가 | **일반적으로 안전하지 않음 — 업무 판단 필요** | kernel은 DEAD를 기록하고 메시지를 지우므로 Kafka offset도 넘어가 다음 창을 판정한다. 그 창의 앵커 변화가 빠져 이후 판정이 달라질 수 있다(회수 누락 → 다음 발화 누락 등). redrive로 되살려도 늦게 도착한 과거 창은 앵커 역전 가드에 막혀 상태를 복구하지 못한다. 순서 레인의 DEAD 복구는 redrive가 아니라 이 README의 재생 절차다. 파티션을 멈출지, DLQ로 옮기고 진행할지는 구현하지 않았다 |
| 4. 스냅샷과 job 초기화 경계가 어긋나면 감지하는가 | v1: 감지 못 함(결함) → v2: 경계를 스냅샷에서 유도하고 대조 | v1은 경계를 상수 창 번호로 정하고 파생 테이블을 각각 다른 트랜잭션으로 떠서, 경계가 늦으면 그 창이 terminal로 건너뛰어져 결과가 조용히 틀릴 수 있었다. v2는 한 트랜잭션에서 파생 상태와 SUCCEEDED 집합을 함께 뜨고, 그 집합 밖만 초기화한 뒤 복구 DB에 남은 SUCCEEDED를 대조한다(불일치 시 중단, S8). handler가 파생 상태를 먼저 커밋하고 job 성공을 나중에 기록하므로 한 스냅샷 안에서 위험한 방향(성공인데 파생 없음)은 생기지 않고, 반대 방향은 재처리가 멱등이다 |

## 확인한 것과 아닌 것

- **스냅샷과 offset을 맞출 필요는 없었다.** 복구 group을 offset 0에서 시작해도 스냅샷 이전 job은 `SUCCEEDED`라 kernel이 실행 없이 넘겼다. 대신 경계는 **job 초기화 기준(창 시각)**으로 옮겨 갔다 — 그 기준과 스냅샷이 어긋나면 틀린다.
- **메시지가 job 참조라서 Kafka 재생만으로는 현재까지 따라잡지 못한다.** 복구 DB 생성 뒤 커밋된 창은 입력 원장 동기화가 있어야 처리된다. 동기화는 멱등 SQL 한 벌이고 소비 코드는 그대로다.
- **순서를 택한 비용:** 재시도 중인 창 뒤의 창은 기다린다(F2에서 6.4초). DLQ가 없어 poison 메시지는 파티션을 계속 막는다.
- **비정상 종료 뒤 약 43초 공백:** 죽은 멤버의 group session이 만료될 때까지 파티션이 재할당되지 않는다(librdkafka 기본 `session.timeout.ms` 45초). 설정은 바꾸지 않았다.
- 검증하지 않음: 처리량·지연 순위, 다중 세션·다중 파티션 동시 소비, 복제·HA, 실제 AWS·S3, 전환 중 무중단, 운영 비용, 설명 레인 복구.

## 알려진 한계와 후속 검증 조건

구현하지 않았다. 운영 적용을 검토할 때의 선행 조건이다.

- **DLQ 없음:** 판정할 수 없는 메시지는 파티션을 계속 막는다. DLQ로 옮기고 다음 창을 처리해도 되는지는 위 3번과 같은 업무 판단이 먼저다.
- **DEAD 뒤 진행:** 위 3번. 멈출지 진행할지, 진행한다면 어떤 조건에서 재생 복구를 돌릴지 정해야 한다.
- **Relay 재시도의 순서:** 위 2번. 순서가 필요하면 destination별 선두 사건이 실패할 때 뒤 사건 발행을 멈추는 방식을 따로 검증해야 한다.
- **다중 세션·다중 파티션·HA:** 한 세션(한 파티션), 단일 브로커만 실행했다.
- **group session timeout:** 비정상 종료 뒤 약 43초 공백. 값은 조정하지 않았다.
- **늦게 복원된 발화의 외부 발행:** 범위 밖. 정책이 정해지면 전환 절차에 넣는다.

## 실패 기록

- `trial-1-truncate-fk`: 복구 DB 준비의 `TRUNCATE`가 설명 레인 테이블 `etf_contribution_observation`의 FK에 막혔다. `CASCADE`로 설명까지 지우는 건 이 절차의 권한 밖이라 `DELETE`로 바꿨다(참조 행이 있으면 실패).
- `trial-2-inspect-format`: 시나리오는 끝까지 통과했고 결과 수집의 `docker inspect` 템플릿(`dict` 미지원)만 실패했다. JSON 전체를 받아 파싱하도록 고쳤다.
- 두 시험 실행은 최종 반복 수에 넣지 않았다.
