# 장중 수급 레인 로컬 비교 결과(자동 생성: compare.py)

## 경로 간 대사

| 비교 | canonical 행 sha | DB 업무 값(수량) sha | DB +available_at sha | DB +data_version sha |
|---|---|---|---|---|
| legacy-s8 vs airflow-s8 | 같음 | 같음 | 다름 | 다름 |
| legacy-partial vs airflow-partial | 같음 | 같음 | 다름 | 다름 |

### airflow-day1

- canonical vs dev: 2026-09-22 1779행 바이트 동일
- DB 행 1779 · 값 sha `6f22d020c442` · 행+data_version sha `30dd93655712`
- 외부 호출(재생 종목 수): 1779 / 수집 실행 5
- ECS 실행(대역): 23 (가드 env 켜짐 23) · 스텝 진입 {'plan-run': 5, 'ingest-raw-investor-estimate': 6, 'normalize-investor-estimate': 6, 'load-investor-intraday': 5}
- SNS: ['[investor-intraday] FAILED — airflow lab__2026-09-22T10:05']
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[1,0] |
| 2026-09-22T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T13:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T14:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### airflow-day1-after

- canonical vs dev: 2026-09-22 1779행 바이트 동일
- DB 행 1779 · 값 sha `6f22d020c442` · 행+data_version sha `30dd93655712`
- 외부 호출(재생 종목 수): 1779 / 수집 실행 5
- ECS 실행(대역): 30 (가드 env 켜짐 27) · 스텝 진입 {'plan-run': 7, 'ingest-raw-investor-estimate': 7, 'normalize-investor-estimate': 8, 'load-investor-intraday': 7}
- SNS: ['[investor-intraday] FAILED — airflow lab__2026-09-22T10:05', '[investor-intraday] FAILED — airflow lab__s4_pastdate']
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[1,0,0] |
| 2026-09-22T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T13:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T14:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### airflow-final

- canonical vs dev: 2026-09-22 1779행 바이트 동일; 2026-09-23 1739행 바이트 동일
- DB 행 3518 · 값 sha `d5ff3270a090` · 행+data_version sha `9ec7be717939`
- 외부 호출(재생 종목 수): 3206 / 수집 실행 9
- ECS 실행(대역): 50 (가드 env 켜짐 47) · 스텝 진입 {'plan-run': 12, 'ingest-raw-investor-estimate': 12, 'normalize-investor-estimate': 13, 'load-investor-intraday': 12}
- SNS: ['[investor-intraday] FAILED — airflow lab__2026-09-22T10:05', '[investor-intraday] FAILED — airflow lab__s4_pastdate', '[investor-intraday] FAILED — airflow lab__2026-09-23T09:35']
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[1,0,0] |
| 2026-09-22T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T13:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T14:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T09:35 | AIRFLOW | LAUNCHED | 수집 FAILED[1] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T13:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T14:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### airflow-partial

- canonical vs dev: 2026-09-22 1048행 **불일치**
- DB 행 1048 · 값 sha `2391206e4fbd` · 행+data_version sha `b3f37c711226`
- 외부 호출(재생 종목 수): 1048 / 수집 실행 3
- ECS 실행(대역): 12 (가드 env 켜짐 12) · 스텝 진입 {'plan-run': 3, 'ingest-raw-investor-estimate': 3, 'normalize-investor-estimate': 3, 'load-investor-intraday': 3}
- SNS: ['[investor-intraday] FAILED — airflow lab__2026-09-22T10:05']
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[2] |
| 2026-09-22T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### airflow-s8

- canonical vs dev: 2026-09-22 1779행 바이트 동일; 2026-09-23 1739행 바이트 동일
- DB 행 3518 · 값 sha `d5ff3270a090` · 행+data_version sha `9ec7be717939`
- 외부 호출(재생 종목 수): 3206 / 수집 실행 9
- ECS 실행(대역): 61 (가드 env 켜짐 47) · 스텝 진입 {'plan-run': 13, 'ingest-raw-investor-estimate': 12, 'normalize-investor-estimate': 13, 'load-investor-intraday': 12, 'reconcile': 10}
- SNS: ['[investor-intraday] FAILED — airflow lab__2026-09-22T10:05', '[investor-intraday] FAILED — airflow lab__s4_pastdate', '[investor-intraday] FAILED — airflow lab__2026-09-23T09:35']
- 원장 이슈: [['LAUNCH_CONFLICT', 'orchestrator_conflict:investor-intraday:2026-09-23T13:25:SFN', 'OPEN']]

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[1,0,0] |
| 2026-09-22T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T13:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T14:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T09:35 | AIRFLOW | LAUNCHED | 수집 FAILED[1] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T13:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T14:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### legacy-day1

- canonical vs dev: 2026-09-22 1779행 바이트 동일
- DB 행 1779 · 값 sha `238c4602e127` · 행+data_version sha `7acf815820b5`
- 외부 호출(재생 종목 수): 1418 / 수집 실행 4
- ECS 실행(대역): 21 (가드 env 켜짐 0) · 스텝 진입 {'plan-run': 5, 'ingest-raw-investor-estimate': 4, 'normalize-investor-estimate': 6, 'load-investor-intraday': 5}
- SNS: ['[edge-dev-data-pipeline-investor-intraday] FAILED — run run_95a67a8127a39125cf91eb6cf8', '[edge-dev-data-pipeline-investor-intraday] raw 부분 실패 — run run_6bbdd052c8b01e07a08fa69bfb']
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[1,0] |
| 2026-09-22T11:25 | SFN | LAUNCHED | 수집 PENDING[-] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T13:25 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T14:35 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### legacy-day1-after

- canonical vs dev: 2026-09-22 1779행 바이트 동일
- DB 행 1779 · 값 sha `238c4602e127` · 행+data_version sha `7acf815820b5`
- 외부 호출(재생 종목 수): 1418 / 수집 실행 4
- ECS 실행(대역): 24 (가드 env 켜짐 0) · 스텝 진입 {'plan-run': 6, 'ingest-raw-investor-estimate': 4, 'normalize-investor-estimate': 7, 'load-investor-intraday': 6}
- SNS: ['[edge-dev-data-pipeline-investor-intraday] FAILED — run run_95a67a8127a39125cf91eb6cf8', '[edge-dev-data-pipeline-investor-intraday] raw 부분 실패 — run run_6bbdd052c8b01e07a08fa69bfb']
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[1,0,0] |
| 2026-09-22T11:25 | SFN | LAUNCHED | 수집 PENDING[-] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T13:25 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T14:35 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### legacy-final

- canonical vs dev: 2026-09-22 1779행 바이트 동일; 2026-09-23 1739행 바이트 동일
- DB 행 3518 · 값 sha `e3f9c042d5e2` · 행+data_version sha `26e7307c9e7c`
- 외부 호출(재생 종목 수): 2845 / 수집 실행 8
- ECS 실행(대역): 44 (가드 env 켜짐 0) · 스텝 진입 {'plan-run': 11, 'ingest-raw-investor-estimate': 9, 'normalize-investor-estimate': 12, 'load-investor-intraday': 11}
- SNS: ['[edge-dev-data-pipeline-investor-intraday] FAILED — run run_95a67a8127a39125cf91eb6cf8', '[edge-dev-data-pipeline-investor-intraday] raw 부분 실패 — run run_6bbdd052c8b01e07a08fa69bfb', '[edge-dev-data-pipeline-investor-intraday] raw 부분 실패 — run run_87436804990dd896f63f68881f']
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[1,0,0] |
| 2026-09-22T11:25 | SFN | LAUNCHED | 수집 PENDING[-] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T13:25 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T14:35 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T09:35 | SFN | LAUNCHED | 수집 FAILED[1] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T10:05 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T11:25 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T13:25 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T14:35 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### legacy-partial

- canonical vs dev: 2026-09-22 1048행 **불일치**
- DB 행 1048 · 값 sha `86d01d93ffb7` · 행+data_version sha `249f496fdcf5`
- 외부 호출(재생 종목 수): 1048 / 수집 실행 3
- ECS 실행(대역): 11 (가드 env 켜짐 0) · 스텝 진입 {'plan-run': 3, 'ingest-raw-investor-estimate': 3, 'normalize-investor-estimate': 3, 'load-investor-intraday': 2}
- SNS: ['[edge-dev-data-pipeline-investor-intraday] FAILED — run run_95a67a8127a39125cf91eb6cf8']
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 PENDING[-] · 정제 FULFILLED[2] |
| 2026-09-22T11:25 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### legacy-s8

- canonical vs dev: 2026-09-22 1779행 바이트 동일; 2026-09-23 1739행 바이트 동일
- DB 행 3518 · 값 sha `e3f9c042d5e2` · 행+data_version sha `26e7307c9e7c`
- 외부 호출(재생 종목 수): 2845 / 수집 실행 8
- ECS 실행(대역): 45 (가드 env 켜짐 1) · 스텝 진입 {'plan-run': 12, 'ingest-raw-investor-estimate': 9, 'normalize-investor-estimate': 12, 'load-investor-intraday': 11}
- SNS: ['[edge-dev-data-pipeline-investor-intraday] FAILED — run run_95a67a8127a39125cf91eb6cf8', '[edge-dev-data-pipeline-investor-intraday] raw 부분 실패 — run run_6bbdd052c8b01e07a08fa69bfb', '[edge-dev-data-pipeline-investor-intraday] raw 부분 실패 — run run_87436804990dd896f63f68881f', '[investor-intraday] FAILED — airflow lab__s8a']
- 원장 이슈: [['LAUNCH_CONFLICT', 'orchestrator_conflict:investor-intraday:2026-09-23T11:25:AIRFLOW', 'OPEN']]

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[1,0,0] |
| 2026-09-22T11:25 | SFN | LAUNCHED | 수집 PENDING[-] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T13:25 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T14:35 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T09:35 | SFN | LAUNCHED | 수집 FAILED[1] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T10:05 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T11:25 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T13:25 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T14:35 | SFN | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
