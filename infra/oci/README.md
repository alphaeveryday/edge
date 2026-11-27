# OCI 배치 cron

AWS 계정이 2026-11-29 에 닫혀 배치 레인을 OCI VM 의 cron 으로 옮긴다. Airflow DAG(`apps/engine/airflow/dags/`)가 하던 일을 셸 스크립트가 한다.
스텝은 지금과 같은 data-pipeline 이미지를 같은 명령으로 실행한다.

```
timeout -s KILL <상한 초> docker run --rm --name <이름> --env-file /etc/edge/pipeline.env --memory 2g <이미지> <DAG 와 같은 명령>
```

메모리 상한은 VM-1 이 2g, VM-3(뉴스)이 4g 다. 로컬 태거(tag-news)는 최대 2.0~2.2GB 를 썼고 assemble-events 는 AWS 에서 4096MB 였다.

KIS 토큰 캐시 디렉터리(`KIS_TOKEN_CACHE_DIR`)를 쓰면 `-v <디렉터리>:<디렉터리>` 가 붙는다.

## 무엇이 어디서 언제 도는가 (KST)

| VM | 레인 | 스크립트 | 시각 |
|---|---|---|---|
| VM-1 서비스 | 배당 | `vm1/dividend_daily.sh` | 매일 03:00 |
| | 분석 스냅샷 정리 | `vm1/snapshot_retention.sh` | 매일 04:30 |
| | 원천 관측 | `vm1/source_daily.sh` | 매일 05:20 |
| | 장전 유니버스 | `vm1/premarket.sh` | 평일 07:00 |
| | 장중 수급 | `vm1/investor_intraday.sh` | 평일 09:35 · 10:05 · 11:25 · 13:25 · 14:35 |
| | 시장 마감 수집 | `vm1/etf_daily.sh` | 평일 16:30 |
| | 공시 보충 | `vm1/disclosure.sh` | 평일 19:30 |
| | 감시: 빠진 슬롯 | `vm1/watchdog.py slots` | 15분마다 |
| | 감시: 레이크 하루 증가 | `vm1/watchdog.py lake` | 매일 06:00 |
| | 감시: 휴장일 목록 | `vm1/watchdog.py holidays` | 매달 1일 09:00 |
| | 감시: VM 켜고 끄기 | `vm1/watchdog.py vm …` | 아래 표 |
| VM-3 뉴스 | 뉴스 | `vm3/news.sh` | 매일 00:10 · 거래일 08:10 |
| VM-2 장중 수집 | 분 세션 시작·종료 | `vm2/session_start.sh` · `vm2/session_stop.sh` | 거래일 08:05 · 16:10 |

정확한 줄은 `vm1/crontab` · `vm3/crontab` 에 있다. 슬롯 표는 `lib/edge_cron.py` 의 `SLOTS` 다.
crontab 과 `SLOTS` 는 같아야 한다(테스트가 대조한다).
`SLOTS` 에는 VM-2 의 분 세션 종료(`minute_session_stop` 16:10, 거래일)도 있다. 그 행은 `stop-minute-session` 이 직접 쓰고,
감시는 19:00 까지 행이 없으면 다른 슬롯과 같이 알린다.

## VM 켜고 끄기

감시가 OCI CLI(인스턴스 주체 인증)로 한다. 끄기는 모두 `SOFTSTOP` 이다(OS 종료를 기다린다).

| 대상 | 켬 | 끔 |
|---|---|---|
| VM-2 장중 수집 | 거래일 07:50 | 분 세션 종료의 성공 행이 생긴 뒤. 16:30 부터 10분마다 보고 19:00 에 포기 |
| VM-3 뉴스(밤) | 매일 00:00 | 00:10 뉴스 레인의 성공 행이 생긴 뒤. 01:30 부터 10분마다 보고 03:00 에 포기 |
| VM-3 뉴스(낮) | 거래일 07:50 | 평일 16:30 |

- **거래일** = 평일이면서 KR 휴장일이 아닌 날. 휴장일에 VM 을 켜면 장중 컨테이너가 재시작을 되풀이하므로 07:50 켜기와 08:00 확인을 건너뛴다.
  판정은 수집 스텝과 같은 코드(`data_pipeline/ops/trading_calendar.py`)와 같은 값(`pipeline.env` 의 `OPS_KR_HOLIDAYS`)을 쓴다.
  `OPS_KR_HOLIDAYS` 가 없거나 비어 있으면 건너뛰지도 켜지도 않고 메일을 보낸다.
  빠진 슬롯 감시는 이때도 계속 돈다. 거래일 전용 슬롯(뉴스 08:10, 분 세션 종료)을 거래일로 치고 보고, 설정 오류는 하루에 한 번 알린다.
  목록에 올해 날짜가 하나도 없으면 매달 1일에, 12월에는 내년 날짜가 없어도 메일을 보낸다(그해 휴장일을 거래일로 보게 된다).
- **거래일 08:00**: VM-2 가 RUNNING 이 아니면 메일을 보낸다.
- **성공 행을 보고 끄기**: 일이 도는 중에 끄지 않으려는 것이다. 포기 시각에도 행이 없으면 메일을 보내고 VM 을 켜 둔다. 사람이 확인하고 끈다.
  - VM-2 는 장 마감 뒤 밀린 가격 설명 분석을 돌린다. VM-2 의 분 세션 종료가 큐와 분석이 다 빠지고 품질 검사가 통과한 뒤
    `batch_lane_success` 에 `minute_session_stop` · 그날 16:10 행을 쓴다
    (`data_pipeline/minute/session_ops.py` 의 `STOP_SUCCESS_LANE` · `STOP_SUCCESS_SLOT_KST` — #1665).
    품질 검사 실패, 대기 상한 초과, 행 기록 실패, 그날 세션 없음이면 행이 없다. 그러면 19:00 에 메일이 오고 VM-2 가 켜진 채 남는다.
  - VM-3 밤은 뉴스 레인이 쓰는 `news` · 00:10 행을 본다. 레인이 실패하면 행이 없으므로 03:00 에 메일이 오고 VM-3 이 켜진 채 남는다.
- 이미 꺼진 VM 은 건드리지 않는다(휴장일).

## 한 레인이 도는 순서

1. cron 이 `<레인>.sh HH:MM` 을 부른다. `HH:MM` 이 오늘의 슬롯이고 run_id 가 여기서 나온다.
2. 슬롯을 확인한다. 슬롯 시각보다 10분 넘게 늦으면 시작하지 않고 메일을 보낸다(호스트 시간대가 틀린 경우를 잡는다).
3. 락(`/var/lock/edge-batch.lock`)을 최대 3600초 기다린다. 못 잡으면 한 스텝도 돌리지 않고 메일을 보낸다.
   장전 레인만 07:20 까지 기다린다. `build-minute-universe` 가 거래일 07:30 뒤에는 교체를 스스로 거부하고,
   레인 전체는 실측 최대 376초다(중앙 217초, SFN 최근 40회). 07:30 뒤의 판단은 그 스텝이 한다 — 유니버스가 안 바뀐 날은 그 뒤에도 성공한다.
4. 스텝을 하나씩 돈다. 어느 스텝 뒤에 무엇을 돌릴지는 DAG 와 같다(각 스크립트 머리말).
   스텝이 시간 상한(기본 90분)을 넘거나 신호로 죽으면 컨테이너를 지우고 레인을 멈춘다. 뒤 스텝은 돌리지 않는다.
   컨테이너를 지우지 못하면 실패 메일에 그렇게 적는다 — 그 컨테이너가 아직 돌 수 있으니 `docker ps` 로 확인한다.
5. 모든 스텝이 exit 0 이면 `batch_lane_success` 에 한 행(레인, 슬롯 시각, 끝난 시각)을 쓴다.
   하나라도 0 이 아니면 행을 쓰지 않고 스텝별 exit 코드와 run_id 를 메일로 보낸다. 어느 쪽이든 끝나면 락이 풀린다.

감시는 기한(`SLOTS` 의 마지막 칸, 슬롯 뒤 90~170분)이 지났는데 행이 없는 슬롯을 한 번 메일로 알린다.
레인이 실패 메일을 스스로 보낸 슬롯도 행이 없으므로 감시 메일이 한 번 더 온다.

## 분석 스냅샷 정리와 레이크 감시

AWS 에서 따로 돌던 운영 작업 둘을 VM-1 로 옮겼다.

- **분석 스냅샷 정리**(04:30): 지난 분석 스냅샷 행을 지운다(`data_pipeline/ops/snapshot_retention.py`). 다른 레인과 같은 락을 쓴다.
  지우는 동안 DB 디스크 여유를 보고, 모자라면 기다리거나 멈춘다(exit 3 — 실패 메일이 온다).
  AWS 는 RDS 지표로 여유를 읽었다. 여기서는 `DB_DISK_PATH` 의 파일시스템 여유를 읽는다.
  레인이 그 경로를 컨테이너에 읽기 전용으로 붙인다. `DB_DISK_PATH` 가 없으면 레인이 시작하지 않고 메일을 보낸다.
  06:15 에 끊는다(실패 메일이 온다). 05:20 원천 관측 레인이 락을 06:20 까지만 기다리므로, 더 돌면 그날 원천 관측이 빠진다.
  못 지운 행은 다음 날 정리가 지운다.
- **레이크 하루 증가**(06:00): `EDGE_LAKE_DATA_DIR` 의 크기를 `du` 로 재서 지난 측정보다 5GiB 넘게 늘었으면 메일을 보낸다.
  AWS 의 레이크 증가 알람과 같은 기준이다(`infra/terraform/envs/dev/storage-growth.tf`). 첫 실행은 기준값만 적는다.

## MinIO 수명 규칙

AWS 레이크 버킷의 수명 규칙 둘(`infra/terraform/modules/pipeline/storage.tf`)을 MinIO 버킷에 한 번 건다.
둘 다 30일 뒤 만료다. 이 명령은 저장소에서 실행해 보지 못했다 — 걸고 나서 `ls` 로 확인한다.

```
mc ilm rule add --prefix "analysis-v2/runs/" --expire-days 30 <별칭>/<레이크 버킷>
mc ilm rule add --prefix "operations_archive/canonical_run_artifacts/" --expire-days 30 <별칭>/<레이크 버킷>
mc ilm rule ls <별칭>/<레이크 버킷>
```

## run_id

DAG 와 같은 규칙이다(`lib/edge_cron.py` 의 `run_id`).

- 장전 레인: 슬롯의 UTC 시각 `YYYYMMDDTHHMMSSZ`
- 나머지: `run_` + sha256(`alphamale-etf-daily-v1` + `\x01` + `<레인>:<YYYY-MM-DDTHH:MM KST>`) 앞 26자

오늘 슬롯의 run_id 는 슬롯 시각이 지난 뒤 `EDGE_MANUAL=1 python3 lib/edge_cron.py run-id etf-daily 16:30` 으로 본다.

## 설정 파일

`/etc/edge` 아래에 둔다(권한 600). 읽는 쪽이 갈린다 — 스텝 컨테이너는 `pipeline.env` 만 받는다.

| 파일 | 읽는 쪽 | 내용 | 이름 목록 |
|---|---|---|---|
| `batch.env` | 호스트 스크립트 | 이미지, 명령 인자, 메모리·시간 상한, OCI CLI 경로, 인스턴스 OCID | `vm1/batch.env.example` · `vm3/batch.env.example` |
| `pipeline.env` | 스텝 컨테이너(`docker --env-file`) | 레이크·DB 접속, 수집 키, 휴장일 | `vm1/pipeline.env.example` · `vm3/pipeline.env.example` |
| `mail.env` | `lib/edge_cron.py` 만 | 통보 메일 계정 | `vm1/mail.env.example` · `vm3/mail.env.example` |
| `retention.env` | 분석 스냅샷 정리 스텝만(VM-1) | 정리 계정의 DB 접속값, DB 디스크 경로 | `vm1/retention.env.example` |

호스트 스크립트가 `pipeline.env` 에서 읽는 값은 둘이다: `KIS_TOKEN_CACHE_DIR`(마운트), `OPS_KR_HOLIDAYS`(감시의 거래일 판정).

스텝 시간 상한은 `batch.env` 에서 바꾼다. 스텝 하나는 `EDGE_TIMEOUT_SEC_<레인>_<스텝>`(예: `EDGE_TIMEOUT_SEC_NEWS_TAG`), 전체는 `EDGE_STEP_TIMEOUT_SEC`.
기본 90분은 DAG 의 레인 전체 상한(25~45분)보다 길다. 그래서 어느 스텝도 따로 올려 두지 않았다.

## 설치

두 VM 공통:

- 저장소를 `/opt/edge` 에 둔다(crontab 의 `EDGE` 경로).
- 호스트 시간대를 `Asia/Seoul` 로 맞춘다. `CRON_TZ` 는 cronie 만 읽는다.
- docker, bash, flock(util-linux), timeout(coreutils), python3 가 crontab 의 `PATH` 안에 있어야 한다.
- VM-1 은 OCI CLI 와 인스턴스 주체 권한(VM-2·VM-3 시작·정지·조회)도 필요하다. CLI 의 절대 경로를 `batch.env` 의 `OCI_CLI` 에 적는다.
  실행할 수 없는 경로면 감시가 VM 을 건드리지 않고 메일을 보낸다.
- 위 설정 파일을 만든다.
- `/var/log/edge` 와 `/var/lock` 을 cron 사용자가 쓸 수 있어야 한다. VM-1 은 `/var/lib/edge`(감시가 알린 슬롯과 레이크 크기 기록)도 쓴다.
- VM-1 의 cron 사용자가 `EDGE_LAKE_DATA_DIR` 을 읽을 수 있어야 한다(`du`).
- KIS 토큰 캐시를 쓰면(`KIS_TOKEN_CACHE_DIR`) 그 디렉터리를 미리 만들고 컨테이너 사용자(uid 1001)가 쓸 수 있게 한다.
  스텝 컨테이너에 호스트와 같은 경로로 붙는다. 디렉터리가 없으면 레인이 시작하지 않고 메일을 보낸다.
- `crontab infra/oci/vm1/crontab`(VM-3 은 `vm3/crontab`).
  VM-1 의 crontab 은 VM-2 에 #1665 의 이미지와 `vm2/crontab` 을 올린 뒤에 설치한다. VM-2 를 끄는 신호(`minute_session_stop` 행)를
  그 코드가 쓴다 — 먼저 설치하면 VM-2 가 꺼지지 않고 거래일마다 19:00 에 메일이 온다.
- DB 에 `batch_lane_success` 표가 있어야 한다(`libs/schema/migrations-cloud/V202611270230__add_batch_lane_success.sql`).

## 실패한 슬롯 다시 돌리기

같은 날이면 같은 슬롯으로 다시 돌린다. 수집부터 전부 다시 한다.

```
EDGE_MANUAL=1 /opt/edge/infra/oci/vm1/etf_daily.sh 16:30
```

수집은 두고 정제·적재만 다시 하려면 실패 메일의 run_id 로 그 스텝을 직접 실행한다.

```
docker run --rm --env-file /etc/edge/pipeline.env --memory 2g <이미지> normalize-price --run-id <run_id> --input-run-id <run_id>
```

지난 날 슬롯은 스크립트로 다시 돌릴 수 없다. 날짜 인자가 없는 수집이 오늘 값을 지난 run_id 로 저장하기 때문이다.

## Airflow 와 달라진 것

- 운영 원장을 쓰지 않는다. DAG 의 `plan-run` · `reconcile` 스텝과 실행권(`OPS_EXCLUSIVE_STEP`), 성공 이력 skip(`OPS_SKIP_IF_SUCCEEDED`)이 없다.
  원장 env 가 없어도 스텝은 같은 일을 한다(`ops/wrapper.py` — 기대 작업이 없으면 계측 없이 실행).
  `OPS_KR_HOLIDAYS` 는 원장이 아니라 거래일 판정이 읽는 값이라 `pipeline.env` 에 남긴다.
- 겹침 방지는 원장이 아니라 VM 마다 하나인 락이 한다. DAG 가 나란히 돌리던 스텝도 한 줄로 돈다.
- 자동 재시도가 없다. Airflow 는 컨테이너가 뜨지 못한 경우를 다시 시도했다. 여기서는 실패 메일을 보고 사람이 다시 돌린다.
- 시간 상한이 레인 전체가 아니라 스텝마다 걸린다(기본 90분). Airflow 는 상한을 넘겨도 컨테이너를 두었다. 여기서는 지운다 — 두면 락을 계속 쥔다.
- 신호로 죽은 스텝(exit 124 이상) 뒤로는 레인 전체를 멈춘다. DAG 는 그 가지의 하류만 멈췄다.
- 슬롯 시각보다 10분 넘게 늦으면 시작하지 않는다. 손으로 다시 돌릴 때는 `EDGE_MANUAL=1` 을 준다.
- 시장 마감 수집이 15:40 에서 16:30 으로 옮겨 갔다. run_id 도 16:30 슬롯의 값이다.
- 뉴스 08:10 은 거래일만 돈다. Airflow 는 매일이었지만 VM-3 이 주말·휴장일 아침에 꺼져 있다. 그날 기사는 00:10 실행이 받는다(수집 창이 어제~오늘).
- 배당 레인의 "03:00~03:30 에 다른 배치가 없을 때만 시작" 검사가 없다. 락이 같은 일을 한다.
- 재처리 run(`reprocess_slot`)과 원천 관측 백필 인자(`macro_from` 등)가 없다. 위 "다시 돌리기"처럼 스텝을 직접 실행한다.
- 06:00 전망 배치는 꺼져 있어(#1654) 옮기지 않았다.

## 알려진 한계

- 감시를 지켜보는 것이 없다. VM-1 이 죽거나 cron 이 멈추면 아무 메일도 오지 않는다.
- VM 에서 새 이미지를 받아 오는 절차가 없다. 레인은 `EDGE_PIPELINE_IMAGE` 에 적힌 태그를 그대로 쓰고, 이미 받은 태그는 다시 받지 않는다.
  VM-2·VM-3 의 `compose up -d` 도 새 이미지를 받지 않는다. 배포할 때 사람이 `docker pull` 을 해야 한다.
- VM-3 의 낮 끄기(16:30)는 실시간 태거가 다 끝났는지 보지 않는다. 그것을 알려 주는 신호가 없다.
- 뉴스 00:10 레인이 실패하면 03:00 에 메일이 오고 VM-3 이 켜진 채 남는다.
- 감시 기한(슬롯 뒤 90~170분)은 추정값이다. 첫 주 실행 시간을 보고 `SLOTS` 에서 줄인다.
- DB 가 죽어 있으면 감시가 실행될 때마다 조회 실패 메일이 간다.

## 테스트

```
uv run --package data-pipeline pytest apps/engine/data-pipeline/tests/test_oci_cron_contract.py
```

레인 스크립트를 실제 bash 로 돌리고 docker·flock·timeout 만 가짜로 바꿔 run_id, 스텝 순서와 관문, 시간 상한, 실패 통보, 빠진 슬롯 통보, VM 끄기 규칙을 확인한다.
`infra/oci/` 가 바뀐 PR 은 CI 가 이 테스트를 돌린다(`.github/scripts/select_python_jobs.py`).
