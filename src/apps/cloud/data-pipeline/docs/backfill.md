# data-pipeline — 백필

> 모듈 [README](../README.md) 의 상세 문서다. 레인·단계별 동작과 도입 경위가 함께 있다.

## 백필 — 포워드와 격리된 재구축 경로

포워드(`steps/ingest_*`)는 매일 도는 프로덕션이고, 백필은 과거를 다시 쌓는 일이다. 둘을 섞으면
**롤백이 불가능해진다** — 어느 파티션이 어느 경로에서 왔는지 사후에 가릴 수 없기 때문이다.
그래서 `backfill/` 패키지는 진입점부터 갈라져 있고(`data_pipeline.backfill.run`), 쓰기 좌표
셋으로 격리한다.

| 좌표 | 백필 | 포워드 |
|---|---|---|
| `source=` | `dartlab` | `dart` |
| `run_id=` | `backfill-dartlab-financial-<YYYYMMDD>` | `<job>-<stamp>` |
| 접두사 | `draft/`(`--draft`) — 승격 전 기본 | 없음 |

**롤백은 `run_id` 파티션 삭제**이고, 승격은 접두사 이동이다. 셋 중 하나만으로도 파티션이
겹치지 않지만 셋을 다 쓴다 — 격리 실패의 대가가 크고, 좌표 하나는 설정 실수로 뚫린다.

```bash
py -m data_pipeline.backfill.run financial --bucket edge-dev-pipeline-lake --draft --limit 20
py -m data_pipeline.backfill.run financial --bucket edge-dev-pipeline-lake --draft   # 전 종목
py -m data_pipeline.backfill.run verify   --bucket edge-dev-pipeline-lake --draft
```

**데이터가 전소해도 다시 쌓을 수 있어야 한다.** 그 조건은 외부 입력이 전부 재접근 가능하고
로컬 상태에 의존하지 않는 것이다. 이 백필의 외부 입력은 하나(HuggingFace 공개 데이터셋)이며,
종목 유니버스조차 그 데이터셋의 파일 목록에서 얻는다(로컬 종목 마스터를 읽지 않는다).
매니페스트도 레이크에 쓴다 — 로컬 디스크에 두면 그것이 전소했을 때 재개가 불가능하다.

- **raw(재무제표 백필)** — `raw/source=dartlab/dataset=financial_statements/market=KR/ingest_date=…/run_id=…/part-<ticker>.ndjson`.
  포워드(`source=dart`)가 쓰는 `fnlttSinglAcnt`(**주요계정만**)와 달리 전체 재무제표 27열을
  무변형으로 낸다 — 주요계정에는 매출액·매출원가·판관비가 없어 원가구조·영업레버리지를
  계산할 수 없다. provenance 5열(`our_ticker`·`market`·`fetched_at`·`backfill_source`·
  `backfill_oid`)만 부착한다.
- **매니페스트** — `operations_archive/backfill_manifests/source=…/dataset=…/run_id=…/manifest.json`
  에 항목별 `{key, rows, sha256, bytes}`. `verify` 가 이것으로 레이크를 재대조하므로
  **적재 후 조작·유실이 드러난다**. 재개는 이 매니페스트를 읽어 이미 받은 항목을 건너뛴다.

**이 입력은 PIT 가 아니다.** dartlab 데이터셋은 `(bsns_year, reprt_code)` 조합마다 `rcept_no`
가 하나뿐이다 — 정정공시 이력이 없고 **최종 확정치만** 있다(2016년 이후). OpenDART 재무 API
자체가 정정 전 수치를 지목할 파라미터를 주지 않으므로 벤더 문제가 아니다. 접수번호 앞 8자리로
"언제 처음 공개됐나"는 근사할 수 있지만, 사후 정정된 값을 그 시점 값으로 쓰면 조용히 미래를
본다. 진짜 PIT 는 `list.json`(정정 열거) + `document.xml`(rcept_no 원본) 파싱이 필요하고
별 `source` 로 추가할 자리다(후속).

### DataGuide 일봉 → `price_daily` 일회성 적재 (ALPHA-1148)

DB `price_daily` 의 과거 이력을 레이크 임시 존의 DataGuide 일봉 스냅샷
(`draft/curated/source=dataguide/dataset=price_daily/market=KR/as_of_date=…/item=…/*.csv.gz`)에서
한 번 싣는 스텝이다. 스냅샷은 갱신 담당이 없어 canonical 로 올리지 않고(ADR-0057 §5) DB 로 바로
싣는다. 종목 마스터(`instrument`)에 있는 종목의 열만 읽는다.

```bash
# 분류만 센다(쓰기 없음) — 실제 적재 전에 먼저 돌려 created·replaced_5min·kept_existing 을 본다.
python -m data_pipeline.run backfill-price-daily-dataguide --run-id dataguide-price-dry \
  --as-of-date 2026-08-02 --from 2006-10-01 --to 2026-07-31 --dry-run
# 실제 적재. 50거래일 묶음마다 커밋하고, 같은 인자로 다시 돌리면 같은 결과다(멱등).
python -m data_pipeline.run backfill-price-daily-dataguide --run-id dataguide-price-20261002 \
  --as-of-date 2026-08-02 --from 2006-10-01 --to 2026-07-31
```

`--as-of-date`·`--from`·`--to` 는 기본값이 없다. DB 접속은 다른 적재 스텝과 같은
`DATA_PIPELINE_DB__*` 를 쓴다(배포 환경에서는 `rds` task-def).

| 기존 행 | 처리 |
|---|---|
| 없음 | 삽입 |
| `data_version` 이 `fmp_5min` 으로 시작(5분봉 집산) | 교체하고 `simple_return`·`log_return` 을 비운다. 교체 전 행은 `operations_archive/replaced_rows/dataset=price_daily_dataguide_backfill/run_id=…/` 에 먼저 남긴다 |
| 이 스텝이 넣은 행 | 같은 값으로 다시 쓴다 |
| 그 밖(KIS 일일 적재분) | 건드리지 않는다 |

- 넣는 값: 시가·고가·저가·종가(원주가), 수정주가, 거래량. `available_at` 은 거래일 15:30 KST 다.
  `--as-of-date 2026-08-02` 면 `data_version=dataguide-20260802`(하이픈 없는 YYYYMMDD),
  `price_basis=raw_close;adj_asof=2026-08-02` 다.
- **수정주가는 스냅샷 기준이다.** 스냅샷 뒤에 분할·권리락이 생기면 낡는다. KIS 일일 적재 행에는
  수정주가가 없다.
- **`available_at` 은 실제 입수 시각이 아니다.** 과거 시점 재현에 이 행을 쓰면 그 시점에 이미
  알던 값으로 보인다.
- 여섯 항목 파일의 열 구성·날짜 행·행별 열 수가 하나라도 다르거나 머리행에 같은 열이 두 번
  있으면 한 행도 싣지 않는다. 실을 행이 하나도 없어도 실패로 끝난다(기간 밖·마스터와 맞는 열 0).
  값이 컬럼 형(NUMERIC(24,8)·BIGINT)에 그대로 들어가지 않는 칸은 격리하고 exit 2 로 끝난다.
- 결과는 `operations_archive/data_quality_logs/dataset=price_daily_dataguide_backfill/` 에 남는다.
- 되돌리기: 삽입한 행은 `DELETE FROM price_daily WHERE data_version = 'dataguide-20260802'`
  (위 `data_version` 그대로)로 지우고, 교체된 5분봉 집산 행은 위 보존본에서 복원한다(복원
  스크립트는 없다).
- dev 실행(2026-10-02 18:07~18:20 KST, `run_id=dataguide-price-20261002`): 마스터 2,804종목 중
  2,688종목·4,888거래일·8,690,491행을 읽어 8,467,064행 삽입, 5분봉 집산 123,230행 교체, KIS
  100,197행 유지, 격리 0. 12분 45초 걸렸고 적재 뒤 `price_daily` 는 8,707,934행·약 2.4GB 다.
  적재 전 로컬 리허설에서 겹치는 100,197행의 시가·고가·저가·종가가 KIS canonical 과 전부
  같음을 확인했다. 교체 전 행 123,230건은 보존본 14개 파일에 남아 있다.
