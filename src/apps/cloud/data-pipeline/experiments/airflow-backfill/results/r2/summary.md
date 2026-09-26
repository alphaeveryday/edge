| 비교군 | 시나리오 | rep | 요청(s) | 복구(s) | 재요청(s) | 제출 명령 | 요청 호출 | 복구 호출 | 재요청 호출 | 요청 후 정확 | 복구 후 정확 | 재요청 후 정확 | 복구 후 원장 완료 | data_version≠순차 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | N | - | 5.38 | - | - | - | load-price-daily:ok=7 normalize-price:ok=7 | - | - | O(7/7) | - | - | - | 0 |
| B | N | - | 5.47 | - | - | - | load-price-daily:ok=7 normalize-price:ok=7 | - | - | O(7/7) | - | - | - | 0 |
| C | N | - | 60.8 | - | - | - | load-price-daily:ok=7 normalize-price:ok=7 | - | - | O(7/7) | - | - | - | 0 |
| A | F | 0 | 3.94 | 4.37 | 6.85 | 4 | load-price-daily:ok=5 normalize-price:fail=2 normalize-price:ok=5 | load-price-daily:ok=2 normalize-price:ok=2 | load-price-daily:ok=7 normalize-price:ok=7 | X(5/7) | O(5/7) | O(7/7) | 5 | 0 |
| B | F | 0 | 4.04 | 2.84 | 0.95 | 1 | load-price-daily:ok=5 normalize-price:fail=2 normalize-price:ok=5 | load-price-daily:ok=2 normalize-price:ok=2 | 0 | X(5/7) | O(7/7) | O(7/7) | 7 | 1628 |
| C | F | 0 | 57.54 | 11.51 | 18.93 | 1 | load-price-daily:ok=5 normalize-price:fail=2 normalize-price:ok=5 | load-price-daily:ok=2 normalize-price:ok=2 | 0 | X(5/7) | O(7/7) | O(7/7) | 7 | 1628 |
| B | F | 1 | 4.1 | 2.8 | 0.97 | 1 | load-price-daily:ok=5 normalize-price:fail=2 normalize-price:ok=5 | load-price-daily:ok=2 normalize-price:ok=2 | 0 | X(5/7) | O(7/7) | O(7/7) | 7 | 1628 |
| C | F | 1 | 44.12 | 11.45 | 18.9 | 1 | load-price-daily:ok=5 normalize-price:fail=2 normalize-price:ok=5 | load-price-daily:ok=2 normalize-price:ok=2 | 0 | X(5/7) | O(7/7) | O(7/7) | 7 | 1628 |
| A | F | 1 | 3.43 | 4.42 | 6.81 | 4 | load-price-daily:ok=5 normalize-price:fail=2 normalize-price:ok=5 | load-price-daily:ok=2 normalize-price:ok=2 | load-price-daily:ok=7 normalize-price:ok=7 | X(5/7) | O(5/7) | O(7/7) | 5 | 0 |
| C | F | 2 | 37.82 | 11.47 | 18.85 | 1 | load-price-daily:ok=5 normalize-price:fail=2 normalize-price:ok=5 | load-price-daily:ok=2 normalize-price:ok=2 | 0 | X(5/7) | O(7/7) | O(7/7) | 7 | 1628 |
| A | F | 2 | 3.9 | 4.36 | 6.82 | 4 | load-price-daily:ok=5 normalize-price:fail=2 normalize-price:ok=5 | load-price-daily:ok=2 normalize-price:ok=2 | load-price-daily:ok=7 normalize-price:ok=7 | X(5/7) | O(5/7) | O(7/7) | 5 | 0 |
| B | F | 2 | 4.02 | 2.79 | 0.98 | 1 | load-price-daily:ok=5 normalize-price:fail=2 normalize-price:ok=5 | load-price-daily:ok=2 normalize-price:ok=2 | 0 | X(5/7) | O(7/7) | O(7/7) | 7 | 1628 |

| 컨테이너 | 구간 | 표본 | CPU% 평균 | CPU% 최대 | 메모리 MiB 평균 | 메모리 MiB 최대 |
|---|---|---|---|---|---|---|
| airflow | A-recover | 6 | 2.7 | 3.8 | 1197 | 1210 |
| airflow | A-request | 10 | 3.2 | 8.2 | 1190 | 1210 |
| airflow | A-rerequest | 11 | 2.6 | 3.6 | 1197 | 1210 |
| airflow | B-recover | 3 | 2.3 | 3.2 | 1197 | 1210 |
| airflow | B-request | 12 | 2.6 | 4.7 | 1186 | 1210 |
| airflow | B-rerequest | 3 | 2.8 | 3.3 | 1198 | 1211 |
| airflow | B-reset | 1 | 2.1 | 2.1 | 1210 | 1210 |
| airflow | C-recover | 18 | 82.7 | 222.4 | 1242 | 1319 |
| airflow | C-request | 101 | 58.1 | 198.9 | 1207 | 1323 |
| airflow | C-rerequest | 28 | 15.3 | 103.3 | 1210 | 1328 |
| airflow | C-reset | 8 | 93.1 | 96.7 | 1245 | 1294 |
| airflow | idle | 31 | 5.1 | 12.1 | 1170 | 1212 |
| airflow-db | A-recover | 6 | 2.5 | 2.9 | 58 | 58 |
| airflow-db | A-request | 10 | 2.9 | 3.3 | 58 | 58 |
| airflow-db | A-rerequest | 11 | 2.9 | 3.2 | 58 | 58 |
| airflow-db | B-recover | 3 | 2.8 | 2.8 | 58 | 58 |
| airflow-db | B-request | 12 | 2.9 | 3.3 | 58 | 58 |
| airflow-db | B-rerequest | 3 | 2.8 | 2.9 | 58 | 58 |
| airflow-db | B-reset | 1 | 2.9 | 2.9 | 58 | 58 |
| airflow-db | C-recover | 18 | 3.9 | 4.8 | 58 | 60 |
| airflow-db | C-request | 101 | 4.1 | 6.2 | 58 | 60 |
| airflow-db | C-rerequest | 28 | 3.7 | 5.1 | 58 | 60 |
| airflow-db | C-reset | 8 | 3.7 | 4.8 | 58 | 58 |
| airflow-db | idle | 31 | 3.4 | 4.5 | 58 | 58 |
| postgres | A-recover | 6 | 9.2 | 21.9 | 39 | 40 |
| postgres | A-request | 10 | 25.4 | 39.1 | 39 | 41 |
| postgres | A-rerequest | 11 | 23.9 | 38.2 | 39 | 40 |
| postgres | B-recover | 3 | 24.7 | 37.3 | 40 | 40 |
| postgres | B-request | 12 | 24.0 | 39.2 | 38 | 40 |
| postgres | B-rerequest | 3 | 6.9 | 8.6 | 38 | 38 |
| postgres | B-reset | 1 | 6.6 | 6.6 | 38 | 38 |
| postgres | C-recover | 18 | 5.5 | 19.3 | 38 | 40 |
| postgres | C-request | 101 | 5.5 | 30.4 | 38 | 42 |
| postgres | C-rerequest | 28 | 2.5 | 4.8 | 38 | 39 |
| postgres | C-reset | 8 | 1.9 | 2.3 | 37 | 38 |
| postgres | idle | 31 | 2.3 | 3.1 | 37 | 38 |
| runner | A-recover | 6 | 224.0 | 285.6 | 91 | 102 |
| runner | A-request | 10 | 102.1 | 205.1 | 76 | 113 |
| runner | A-rerequest | 11 | 110.6 | 207.2 | 92 | 115 |
| runner | B-recover | 3 | 109.6 | 207.8 | 109 | 112 |
| runner | B-request | 12 | 115.0 | 205.2 | 84 | 114 |
| runner | B-rerequest | 3 | 192.3 | 196.4 | 33 | 87 |
| runner | B-reset | 1 | 192.3 | 192.3 | 21 | 21 |
| runner | C-recover | 18 | 19.0 | 175.8 | 8 | 83 |
| runner | C-request | 101 | 2.2 | 150.1 | 4 | 85 |
| runner | C-rerequest | 28 | 6.9 | 173.6 | 4 | 4 |
| runner | C-reset | 8 | 0.0 | 0.0 | 4 | 4 |
| runner | idle | 31 | 0.5 | 15.0 | 3 | 4 |
