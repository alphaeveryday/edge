# OCI 배치 cron

AWS 계정이 2026-11-29 에 닫혀 배치 레인을 OCI VM 의 cron 으로 옮긴다. Airflow DAG(`apps/engine/airflow/dags/`)가 하던 일을 셸 스크립트가 한다.
스텝은 지금과 같은 data-pipeline 이미지를 같은 명령으로 실행한다.

```
docker run --rm --env-file /etc/edge/batch.env --memory 2g <이미지> <DAG 와 같은 명령>
```

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
| | 감시: VM 켜고 끄기 | `vm1/watchdog.py vm …` | 아래 표 |
| VM-3 뉴스 | 뉴스 | `vm3/news.sh` | 매일 00:10 · 거래일 08:10 |

감시가 OCI CLI 로 켜고 끄는 시각:

| 대상 | 켬 | 끔 | 확인 |
|---|---|---|---|
| VM-2 장중 수집 | 거래일 07:50 | 평일 16:30 | 거래일 08:00 에 RUNNING 이 아니면 메일 |
| VM-3 뉴스 | 매일 00:00 · 거래일 07:50 | 매일 01:30 · 평일 16:30 | — |

거래일 = 평일이면서 KR 휴장일이 아닌 날. 휴장일에 VM 을 켜면 장중 컨테이너가 재시작을 되풀이하므로 07:50 켜기와 08:00 확인을 건너뛴다.
판정은 수집 스텝과 같은 코드(`data_pipeline/ops/trading_calendar.py`)와 같은 값(env 파일의 `OPS_KR_HOLIDAYS`)을 쓴다.
`OPS_KR_HOLIDAYS` 가 env 파일에 없으면 건너뛰지도 켜지도 않고 메일을 보낸다. 16:30 끄기는 평일마다 하고, 이미 꺼져 있으면 그대로 둔다.

정확한 줄은 `vm1/crontab` · `vm3/crontab` 에 있다. 슬롯 표는 `lib/edge_cron.py` 의 `SLOTS` 다.
crontab 과 `SLOTS` 는 같아야 한다(테스트가 대조한다).

## 한 레인이 도는 순서

1. cron 이 `<레인>.sh HH:MM` 을 부른다. `HH:MM` 이 오늘의 슬롯이고 run_id 가 여기서 나온다.
2. 슬롯을 확인한다. 슬롯 시각보다 10분 넘게 늦으면 시작하지 않고 메일을 보낸다(호스트 시간대가 틀린 경우를 잡는다).
3. 락(`/var/lock/edge-batch.lock`)을 최대 3600초 기다린다. 못 잡으면 한 스텝도 돌리지 않고 메일을 보낸다.
4. 스텝을 하나씩 돈다. 어느 스텝 뒤에 무엇을 돌릴지는 DAG 와 같다(각 스크립트 머리말).
5. 모든 스텝이 exit 0 이면 `batch_lane_success` 에 한 행(레인, 슬롯 시각, 끝난 시각)을 쓴다.
   하나라도 0 이 아니면 행을 쓰지 않고 스텝별 exit 코드와 run_id 를 메일로 보낸다.

감시는 기한(`SLOTS` 의 마지막 칸, 슬롯 뒤 90~150분)이 지났는데 행이 없는 슬롯을 한 번 메일로 알린다.
레인이 실패 메일을 스스로 보낸 슬롯도 행이 없으므로 감시 메일이 한 번 더 온다.

## 분석 스냅샷 정리와 레이크 감시

AWS 에서 따로 돌던 운영 작업 둘을 VM-1 로 옮겼다.

- **분석 스냅샷 정리**(04:30): 지난 분석 스냅샷 행을 지운다(`data_pipeline/ops/snapshot_retention.py`). 다른 레인과 같은 락을 쓴다.
  지우는 동안 DB 디스크 여유를 보고, 모자라면 기다리거나 멈춘다(exit 3 — 실패 메일이 온다).
  AWS 는 RDS 지표로 여유를 읽었다. 여기서는 `DB_DISK_PATH` 의 파일시스템 여유를 읽는다.
  레인이 그 경로를 컨테이너에 읽기 전용으로 붙인다. `DB_DISK_PATH` 가 없으면 레인이 시작하지 않고 메일을 보낸다.
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

## 설치

두 VM 공통:

- 저장소를 `/opt/edge` 에 둔다(crontab 의 `EDGE` 경로).
- 호스트 시간대를 `Asia/Seoul` 로 맞춘다. `CRON_TZ` 는 cronie 만 읽는다.
- docker, bash, flock(util-linux), python3 가 있어야 한다. VM-1 은 OCI CLI 와 인스턴스 주체 권한(VM-2·VM-3 시작·정지·조회)도 필요하다.
- `/etc/edge/batch.env` 를 만든다(권한 600). 이름 목록은 `vm1/batch.env.example` · `vm3/batch.env.example`.
- `/var/log/edge` 와 `/var/lock` 을 cron 사용자가 쓸 수 있어야 한다. VM-1 은 `/var/lib/edge`(감시가 알린 슬롯과 레이크 크기 기록)도 쓴다.
- VM-1 의 cron 사용자가 `EDGE_LAKE_DATA_DIR` 을 읽을 수 있어야 한다(`du`).
- KIS 토큰 캐시를 쓰면(`KIS_TOKEN_CACHE_DIR`) 그 디렉터리를 미리 만들고 컨테이너 사용자(uid 1001)가 쓸 수 있게 한다.
  스텝 컨테이너에 호스트와 같은 경로로 붙는다. 디렉터리가 없으면 레인이 시작하지 않고 메일을 보낸다.
- `crontab infra/oci/vm1/crontab`(VM-3 은 `vm3/crontab`).
- DB 에 `batch_lane_success` 표가 있어야 한다(`libs/schema/migrations-cloud/V202611270230__add_batch_lane_success.sql`).

## 실패한 슬롯 다시 돌리기

같은 날이면 같은 슬롯으로 다시 돌린다. 수집부터 전부 다시 한다.

```
EDGE_MANUAL=1 /opt/edge/infra/oci/vm1/etf_daily.sh 16:30
```

수집은 두고 정제·적재만 다시 하려면 실패 메일의 run_id 로 그 스텝을 직접 실행한다.

```
docker run --rm --env-file /etc/edge/batch.env --memory 2g <이미지> normalize-price --run-id <run_id> --input-run-id <run_id>
```

지난 날 슬롯은 스크립트로 다시 돌릴 수 없다. 날짜 인자가 없는 수집이 오늘 값을 지난 run_id 로 저장하기 때문이다.

## Airflow 와 달라진 것

- 운영 원장을 쓰지 않는다. DAG 의 `plan-run` · `reconcile` 스텝과 실행권(`OPS_EXCLUSIVE_STEP`), 성공 이력 skip(`OPS_SKIP_IF_SUCCEEDED`)이 없다.
  원장 env 가 없어도 스텝은 같은 일을 한다(`ops/wrapper.py` — 기대 작업이 없으면 계측 없이 실행).
  `OPS_KR_HOLIDAYS` 는 원장이 아니라 거래일 판정이 읽는 값이라 env 파일에 남긴다.
- 겹침 방지는 원장이 아니라 VM 마다 하나인 락이 한다. DAG 가 나란히 돌리던 스텝도 한 줄로 돈다.
- 자동 재시도가 없다. Airflow 는 컨테이너가 뜨지 못한 경우를 다시 시도했다. 여기서는 실패 메일을 보고 사람이 다시 돌린다.
- 시간 상한이 없다. 멈춘 컨테이너는 락을 계속 쥐고, 뒤 레인은 1시간 뒤 락 실패 메일을 보낸다.
- 시장 마감 수집이 15:40 에서 16:30 으로 옮겨 갔다. run_id 도 16:30 슬롯의 값이다.
- 뉴스 08:10 은 거래일만 돈다. Airflow 는 매일이었지만 VM-3 이 주말·휴장일 아침에 꺼져 있다. 그날 기사는 00:10 실행이 받는다(수집 창이 어제~오늘).
- 배당 레인의 "03:00~03:30 에 다른 배치가 없을 때만 시작" 검사가 없다. 락이 같은 일을 한다.
- 재처리 run(`reprocess_slot`)과 원천 관측 백필 인자(`macro_from` 등)가 없다. 위 "다시 돌리기"처럼 스텝을 직접 실행한다.
- 06:00 전망 배치는 꺼져 있어(#1654) 옮기지 않았다.

## 테스트

```
uv run --package data-pipeline pytest apps/engine/data-pipeline/tests/test_oci_cron_contract.py
```

레인 스크립트를 실제 bash 로 돌리고 docker 와 flock 만 가짜로 바꿔 run_id, 스텝 순서와 관문, 실패 통보, 빠진 슬롯 통보를 확인한다.
