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
- ECS 실행(대역): 28 (가드 env 켜짐 6) · 스텝 진입 {'plan-run': 5, 'ingest-raw-investor-estimate': 5, 'normalize-investor-estimate': 6, 'load-investor-intraday': 5, 'reconcile': 6}
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
- ECS 실행(대역): 37 (가드 env 켜짐 6) · 스텝 진입 {'plan-run': 7, 'ingest-raw-investor-estimate': 5, 'normalize-investor-estimate': 8, 'load-investor-intraday': 7, 'reconcile': 9}
- SNS: ['[investor-intraday] FAILED — airflow lab__2026-09-22T10:05', '[investor-intraday] FAILED — airflow lab__s4_pastdate']
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[0,0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[1,0,0] |
| 2026-09-22T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T13:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T14:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### airflow-final

- canonical vs dev: 2026-09-22 1779행 바이트 동일; 2026-09-23 1739행 바이트 동일
- DB 행 3518 · 값 sha `d5ff3270a090` · 행+data_version sha `9ec7be717939`
- 외부 호출(재생 종목 수): 3206 / 수집 실행 9
- ECS 실행(대역): 62 (가드 env 켜짐 11) · 스텝 진입 {'plan-run': 12, 'ingest-raw-investor-estimate': 10, 'normalize-investor-estimate': 13, 'load-investor-intraday': 12, 'reconcile': 14}
- SNS: ['[investor-intraday] FAILED — airflow lab__2026-09-22T10:05', '[investor-intraday] FAILED — airflow lab__s4_pastdate', '[investor-intraday] FAILED — airflow lab__2026-09-23T09:35']
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[0,0] |
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
- ECS 실행(대역): 12 (가드 env 켜짐 9) · 스텝 진입 {'plan-run': 3, 'ingest-raw-investor-estimate': 3, 'normalize-investor-estimate': 3, 'load-investor-intraday': 3}
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
- ECS 실행(대역): 73 (가드 env 켜짐 11) · 스텝 진입 {'plan-run': 13, 'ingest-raw-investor-estimate': 10, 'normalize-investor-estimate': 13, 'load-investor-intraday': 12, 'reconcile': 24}
- SNS: ['[investor-intraday] FAILED — airflow lab__2026-09-22T10:05', '[investor-intraday] FAILED — airflow lab__s4_pastdate', '[investor-intraday] FAILED — airflow lab__2026-09-23T09:35']
- 원장 이슈: [['LAUNCH_CONFLICT', 'orchestrator_conflict:investor-intraday:2026-09-23T13:25:SFN', 'OPEN']]

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[0,0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[1,0,0] |
| 2026-09-22T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T13:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T14:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T09:35 | AIRFLOW | LAUNCHED | 수집 FAILED[1] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T13:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-23T14:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### guard

- canonical vs dev: 2026-09-22 1413행 **불일치**
- DB 행 0 · 값 sha `4f53cda18c2b` · 행+data_version sha `4f53cda18c2b`
- 외부 호출(재생 종목 수): 1413 / 수집 실행 4
- ECS 실행(대역): 31 (가드 env 켜짐 15) · 스텝 진입 {'plan-run': 12, 'ingest-raw-investor-estimate': 6, 'normalize-investor-estimate': 11, 'reconcile': 2}
- SNS: []
- 원장 이슈: [['LAUNCH_CONFLICT', 'orchestrator_conflict:investor-intraday:2026-09-23T13:25:SFN', 'OPEN'], ['LAUNCH_CONFLICT', 'orchestrator_conflict:investor-intraday:2026-09-23T14:35:SFN', 'OPEN']]

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 PENDING[-] · 정제 FULFILLED[0,0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 PENDING[-] · 정제 FULFILLED[0] |
| 2026-09-22T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 PENDING[-] · 정제 FULFILLED[1,0] |
| 2026-09-22T13:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[1,0] · 적재 PENDING[-] · 정제 FULFILLED[0,0] |
| 2026-09-22T14:35 | AIRFLOW | LAUNCHED | 수집 FAILED[1] · 적재 PENDING[-] · 정제 PENDING[-] |
| 2026-09-23T13:25 | AIRFLOW | LAUNCHED | 수집 PENDING[-] · 적재 PENDING[-] · 정제 PENDING[-] |
| 2026-09-23T14:35 | AIRFLOW | LAUNCHED | 수집 PENDING[-] · 적재 PENDING[-] · 정제 PENDING[-] |

### hold-v1

- canonical vs dev: 2026-09-22 687행 **불일치**
- DB 행 0 · 값 sha `4f53cda18c2b` · 행+data_version sha `4f53cda18c2b`
- 외부 호출(재생 종목 수): 687 / 수집 실행 2
- ECS 실행(대역): 12 (가드 env 켜짐 2) · 스텝 진입 {'plan-run': 2, 'ingest-raw-investor-estimate': 2, 'normalize-investor-estimate': 7, 'reconcile': 1}
- SNS: []
- 원장 이슈: [['EXECUTION_HOLD', 'execution_hold:NORMALIZE_INVESTOR_INTRADAY', 'RESOLVED'], ['EXECUTION_HOLD', 'execution_hold:NORMALIZE_INVESTOR_INTRADAY', 'RESOLVED']]

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 PENDING[-] · 정제 FULFILLED[0,0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 PENDING[-] · 정제 FAILED[0,137] |

### hold-v2-v7

- canonical vs dev: 2026-09-22 687행 **불일치**
- DB 행 687 · 값 sha `4157be0d8a39` · 행+data_version sha `4a61c852dbf0`
- 외부 호출(재생 종목 수): 687 / 수집 실행 2
- ECS 실행(대역): 17 (가드 env 켜짐 2) · 스텝 진입 {'plan-run': 4, 'ingest-raw-investor-estimate': 2, 'normalize-investor-estimate': 4, 'load-investor-intraday': 2, 'reconcile': 5}
- SNS: ['[investor-intraday] FAILED — airflow lab__2026-09-22T10:05', '[investor-intraday] FAILED — airflow lab__2026-09-22T10:05', '[investor-intraday] FAILED — airflow lab__v7_reprocess']
- 원장 이슈: [['EXECUTION_HOLD', 'execution_hold:NORMALIZE_INVESTOR_INTRADAY', 'RESOLVED'], ['EXECUTION_HOLD', 'execution_hold:run_95a67a8127a39125cf91eb6cf8:NORMALIZE_INVESTOR_INTRADAY', 'RESOLVED'], ['MISSED', 'missed:run_95a67a8127a39125cf91eb6cf8:LOAD_INVESTOR_INTRADAY', 'OPEN']]

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0,0] |

### hold-v3-v5

- canonical vs dev: 
- DB 행 0 · 값 sha `4f53cda18c2b` · 행+data_version sha `4f53cda18c2b`
- 외부 호출(재생 종목 수): 0 / 수집 실행 0
- ECS 실행(대역): 12 (가드 env 켜짐 3) · 스텝 진입 {'plan-run': 3, 'normalize-investor-estimate': 1, 'load-investor-intraday': 1, 'reconcile': 4, 'ingest-raw-investor-estimate': 3}
- SNS: ['[investor-intraday] FAILED — airflow lab__2026-09-22T11:25', '[investor-intraday] FAILED — airflow lab__2026-09-22T11:25', '[investor-intraday] FAILED — airflow lab__2026-09-22T13:25', '[investor-intraday] FAILED — airflow lab__2026-09-22T14:35']
- 원장 이슈: [['EXECUTION_HOLD', 'execution_hold:INVESTOR_INTRADAY_COLLECTION_KIS', 'OPEN'], ['EXECUTION_HOLD', 'execution_hold:run_6bbdd052c8b01e07a08fa69bfb:INVESTOR_INTRADAY_COLLECTION_KIS', 'OPEN'], ['MISSED', 'missed:run_2eed4fa22525b9d4caa51ea0d6:NORMALIZE_INVESTOR_INTRADAY', 'OPEN'], ['MISSED', 'missed:run_6bbdd052c8b01e07a08fa69bfb:INVESTOR_INTRADAY_COLLECTION_KIS', 'OPEN'], ['MISSED', 'missed:run_f9cd53180e6ecb3b675f9b6078:NORMALIZE_INVESTOR_INTRADAY', 'OPEN']]

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T11:25 | AIRFLOW | LAUNCHED | 수집 MISSED[-] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T13:25 | AIRFLOW | LAUNCHED | 수집 FAILED[-] · 적재 BLOCKED[-] · 정제 MISSED[-] |
| 2026-09-22T14:35 | AIRFLOW | LAUNCHED | 수집 FAILED[-] · 적재 BLOCKED[-] · 정제 MISSED[-] |

### hold-v6-v8

- canonical vs dev: 2026-09-22 1048행 **불일치**
- DB 행 1048 · 값 sha `86d01d93ffb7` · 행+data_version sha `249f496fdcf5`
- 외부 호출(재생 종목 수): 1048 / 수집 실행 3
- ECS 실행(대역): 19 (가드 env 켜짐 4) · 스텝 진입 {'plan-run': 4, 'ingest-raw-investor-estimate': 3, 'normalize-investor-estimate': 5, 'load-investor-intraday': 3, 'reconcile': 3}
- SNS: []
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[0,0,0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 PENDING[-] · 정제 FULFILLED[0] |
| 2026-09-22T11:25 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |

### holiday

- canonical vs dev: 
- DB 행 0 · 값 sha `4f53cda18c2b` · 행+data_version sha `4f53cda18c2b`
- 외부 호출(재생 종목 수): 0 / 수집 실행 0
- ECS 실행(대역): 0 (가드 env 켜짐 0) · 스텝 진입 {'plan-run': 1, 'ingest-raw-investor-estimate': 1, 'normalize-investor-estimate': 1, 'load-investor-intraday': 1}
- SNS: []
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 None[-] · 적재 None[-] · 정제 None[-] |

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
- ECS 실행(대역): 46 (가드 env 켜짐 0) · 스텝 진입 {'plan-run': 12, 'ingest-raw-investor-estimate': 9, 'normalize-investor-estimate': 12, 'load-investor-intraday': 11, 'reconcile': 1}
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

### lockloss

- canonical vs dev: 2026-09-22 687행 **불일치**
- DB 행 0 · 값 sha `4f53cda18c2b` · 행+data_version sha `4f53cda18c2b`
- 외부 호출(재생 종목 수): 354 / 수집 실행 1
- ECS 실행(대역): 4 (가드 env 켜짐 1) · 스텝 진입 {'plan-run': 1, 'ingest-raw-investor-estimate': 1, 'normalize-investor-estimate': 2}
- SNS: []
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 PENDING[-] · 정제 FULFILLED[0,0] |

### report

- canonical vs dev: 2026-09-22 687행 **불일치**
- DB 행 687 · 값 sha `4157be0d8a39` · 행+data_version sha `4a61c852dbf0`
- 외부 호출(재생 종목 수): 687 / 수집 실행 2
- ECS 실행(대역): 16 (가드 env 켜짐 2) · 스텝 진입 {'plan-run': 4, 'ingest-raw-investor-estimate': 2, 'normalize-investor-estimate': 3, 'load-investor-intraday': 3, 'reconcile': 4}
- SNS: ['[investor-intraday] FAILED — airflow lab__2026-09-22T10:05', '[investor-intraday] FAILED — airflow lab__report_reprocess', '[investor-intraday] FAILED — airflow lab__report_missing']
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
| 2026-09-22T10:05 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0] · 적재 FULFILLED[0,0] · 정제 FULFILLED[2,2] |

### trace

- canonical vs dev: 2026-09-22 333행 **불일치**
- DB 행 333 · 값 sha `15b907d6e8ec` · 행+data_version sha `5f592eca8f0a`
- 외부 호출(재생 종목 수): 333 / 수집 실행 1
- ECS 실행(대역): 6 (가드 env 켜짐 2) · 스텝 진입 {'plan-run': 1, 'ingest-raw-investor-estimate': 2, 'normalize-investor-estimate': 1, 'load-investor-intraday': 1, 'reconcile': 1}
- SNS: []
- 원장 이슈: []

| run_key | 주체 | launch | 작업 outcome[attempt exit] |
|---|---|---|---|
| 2026-09-22T09:35 | AIRFLOW | LAUNCHED | 수집 FULFILLED[0,0] · 적재 FULFILLED[0] · 정제 FULFILLED[0] |
