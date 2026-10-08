# data-pipeline — 운영 원장

> 모듈 [README](../README.md) 의 상세 문서다. 레인·단계별 동작과 도입 경위가 함께 있다.

## 운영 원장 — expected_task·Planner·Reconciler (ALPHA-530)

SFN/ECS 실행을 **사후 복구 가능하게 관측**하는 Postgres projection(`ops_*` 5테이블,
`migrations-cloud`). 실행을 **제어하지 않는다**(관측만 — ADR-0030). 답하는 질문: *원래 실행돼야
했지만 아예 시작되지 않은 작업은 무엇인가.* 코드: `src/data_pipeline/ops/`.

- **상태 4축을 섞지 않는다**: plan_status(DUE·SKIPPED) / task_outcome(PENDING·FULFILLED·FAILED·
  BLOCKED·MISSED) / attempt.execution_status(RUNNING·SUCCEEDED·FAILED·TIMED_OUT) /
  data_status(UNKNOWN·VALID·VALID_EMPTY·INCOMPLETE·INVALID). STALLED 는 저장 상태가 아니라
  RUNNING+시간초과로 파생하는 health(이슈로만 남김).
- **Task Catalog**(`ops/catalog.py`) — 논리 작업의 안정적 ID·정적 의존 SSOT. **등록 30작업 =
  시장 레인(`etf-daily`) 17 + 뉴스 레인(`news`) 6 + 공시 보충 배치(`disclosure`) 4
  + 장중 수급 레인(`investor-intraday`) 3**, 여기에 **SFN 없는 Airflow 전용 원천 관측 레인(`source-daily`) 9**
  (ALPHA-1130 — `sfn_state_name` 이 비어 SFN 셈 밖이고, Reconciler 는 작업별 `evidence_key` 로 증거를 모은다)
  (ALPHA-724 가 공시 4작업의 소유 레인을 옮겼고 — 총계 불변 —
  ALPHA-769 가 장중 수급 3작업을 **신설**했다: 시장 SFN 이 돈 적 없는 스텝이라 이쪽은 총계가
  늘어난다. 30 → 26 은 ALPHA-875 가 그 공시 4작업을 SFN 원장 밖 1분 세션으로 보낸 몫이었고
  **26 → 30 은 ALPHA-987 이 저녁 배치로 되돌린 복원**, **30 → 26 은 ALPHA-1068이 실제 dev
  E2E 뒤 증분 1분 원장으로 다시 옮긴 결과**이며 ALPHA-1073이 보충 배치 4작업을 복원했다)(ECS Task state 35개 중 — **정의 파일**
  기준으로 `statemachine.tf` 33 + `news_pipeline.tf` 2 다. 공시·장중 수급 .tf 는 state 를
  새로 정의하지 않고 부분집합 필터로 재사용하므로 저 33 안에 있다 — 레인별 계수는
  `pipeline_type` 축을 써라. 36→35 는 ALPHA-806 이 AnalyzeOne 을 걷어낸 몫이다.
  ALPHA-181 → 578 → 553 PR2 → 591 → 769 → 806 → 875 → 987 → 1068).
  레인은 `CatalogEntry.pipeline_type` 축이고
  Planner 가 `entries(pipeline_type)` 로 자기 레인만 계획한다 — 섞으면 상대 레인 작업이 매 런
  MISSED 다. 뉴스 6작업의 직렬 2개는 state 이름이 뉴스 SFN 의 것(`NewsLoadAssertions`·
  `NewsAssembleEvents`)이고 depends_on 도 뉴스 SFN 게이트 축으로 그렸다. 제외 5개는 ① `fmp` 수집
  4개(**FMP 공용키 bandwidth 한도 소진**으로 SFN 토글 `us_fmp_enabled` 를 껐다 — 안 도는 스텝을
  등록하면 매 런 MISSED, 한도 회복·토글 on 과 함께 등록, ALPHA-558) ② `CollectDartFinancial`
  (**하류 소비자 0** — `financial_statements` 를 읽는 정제·적재·분석이 없어, 등록하면 대응할
  이유 없는 실패 경보가 된다). `AnalyzeOne` 은 제외가 아니라 **state 자체가 없다**(ALPHA-806 이
  analyze 페이즈를 걷었다 — 36→35). **KRX ETF와 DART 공시 배치는 ALPHA-596 이 직접 계측으로 올렸다** — `tasks.tf` 가 두
  task-def 에 DB env 를 주면서, 컨테이너 종료 즉시 판정되고 그전엔 못 얻던 `records_out`·
  `failed_records`·`data_status` 가 함께 올라온다("벤더 컨테이너에 RDS 접속을 주는 신뢰경계
  변경"이라는 전제는 실측 결과 이미 무너져 있었다: 실행 역할·보안그룹이 task-def 전체 공유라
  IAM·네트워크는 그전에도 열려 있었고, `kis` 가 벤더 컨테이너면서 DB password 를 받는 반례).
  ⚠️ **배선이 먼저, 플래그 해제가 나중** — 이미지 CD 와 terraform apply 가 독립 워크플로라
  플래그가 먼저 뜨면 Reconciler 가 영구 거짓 LEDGER_GAP 을 연다(ALPHA-596 은 PR 을 둘로 쪼갰고,
  ALPHA-610 도 #379→후속으로 같은 순서를 밟았다 — 중간 상태는 `_WIRING_AHEAD_OF_FLAG` 유예가
  덮고, 그 유예는 플래그가 올라가는 순간 스스로 실패해 제거를 강제한다).
  **TagNews 도 ALPHA-610 이 올려 SFN 작업의 `instrumented=False` 는 0개다**(Airflow 전용 원천 관측
  `MACRO_COLLECTION` 도 `macro` 배선 #1036 뒤 ALPHA-1140 이 올려 0개) — SFN 등록 30작업이 전부 자기
  원장을 직접 쓴다(장중 수급 3작업도 `kis`·`bigkinds`·`rds` task-def 를 재사용해 DB env 를 그대로 받는다). 그래서 attempt 결측은 더는 정상이 아니라 `LEDGER_GAP` 이고, 그 스텝이
  기사별 LLM 실패를 격리해 exit 0 으로 끝나도 `failed_records` 가 `data_status=INCOMPLETE` 로
  올라온다(07-27 940/940 전건 실패가 초록으로 보였던 그 경로 — ALPHA-589 는 스텝이 스스로 exit 1
  을 내는 별건이다). 수집 커버리지는 `Collect*` 13개 중 8개 — 시장 9개 중 5개 + 뉴스 2개 중
  1개(BigKinds) + 공시 1개(DART) + 장중 수급 1개 중 1개다. 공시 장중 수집은 minute 원장,
  보충 배치는 ops 원장이 관측한다.
  근거 표는 `ops/catalog.py` docstring, CI 는 `test_ops_catalog` 가 양방향으로 잠근다 —
  `instrumented=True`↔`tasks.tf` DB env 배선 대조 포함(어긋나면 그 작업이 조용히 계측 없이 돈다).
  MVP 3작업(ALPHA-530)이었던 것:
  `PRICE_COLLECTION_KIS`·`NORMALIZE_PRICE`·`LOAD_PRICE_DAILY`(정제→feature 게이트 직후 첫 price
  canonical consumer). 종목 반복은 작업이 아니라 completeness/manifest, 개별 규칙은 quality_check.
- **`ops` 로그 봉투**(ALPHA-181) — 모든 스텝이 자기 로그(collection_log·quality_log)에
  `"ops": {"records_out": N, "failed_records": M}` 를 남긴다. ETF holdings 적재는 선택적 저장
  신호 `unsupported_records`도 낸다. ETF holdings 수집 실패는 선택 필드 `failure`에
  `{category, code, summary}`를 남긴다. 세 값은 `failures.py`의 고정 어휘만 허용하며 공급자
  응답 전문·토큰·계정 식별자는 복사하지 않는다. observer도 등록 어휘와 정확히 일치하는 구조만
  기존 `ops_task_attempt.failure_reason`으로 렌더링한다. 그래서 인증 실패·일시 장애·부분 실패를
  상세 화면에서 구분하되 변조되거나 미등록된 값은 원문 없이 `step_nonzero_exit`로 강등된다.
  로그의 `ops_attempt_id`가 현재 원장 시도와 일치할 때만
  저장해 같은 `run_id` 재시도의 옛 로그를 최신 건수로 오인하지 않는다. 관측(`ops/entry.py:_observe_from_log`)은
  **이 봉투만** 읽으므로 task_key 별 분기가 없다 — 새 작업을 카탈로그에 등록해도 리더를 안 고친다.
  봉투가 스텝 안에 사는 이유: 어느 카운터가 유실인지는 스텝만 안다(적재의
  `skipped_unknown_etf`·`skipped_unknown_instrument` 는 유실, `skipped_self`·
  `skipped_foreign_etf`(유니버스 뿌리 밖 ETF 의 행)·`gated_out`·`already_tagged` 는 정상 동작).
  ⚠️ **스코프 규칙** — 산출과 유실은 *이 런이 재판정한 범위*에서 함께 온다. 재판정 없이 건너뛴
  항목은 산출로도 유실로도 세지 않는다(세면 옛 실패가 산출로 뒤집힌다). 그래서 매 런 입력을 다시
  읽고 다시 거르는 스텝(수집·정제·적재)은 기존 행도 산출로 세지만, 처리분을 건너뛰는 스텝
  (`tag-news`·`assemble-events`·`enrich-corp-code`·`load-price-triggers`)은 no-op 재실행이 0건 →
  `UNKNOWN` 이다. 상태 기반 완전성("지금 이 데이터셋이 온전한가")은 completeness 축 소관(ALPHA-490).
  봉투가 없거나 두 키 중 하나라도 결측이면 리더는 낙관값으로 메우지 않고 warning + `UNKNOWN`(Rule 12).
  `LOAD_ASSERTIONS`만 저장 전용 선택 pair
  `entity_resolution_arguments_total`·`entity_resolution_arguments_resolved`를 함께 낸다. 분모는
  실체 역할 argument, 분자는 ticker·명부·채번으로 실제 접지된 `resolved_any`다. observer는
  비율을 재계산하지 않고 두 원시 카운터만 그대로 전달한다. wrapper가 실행 중에 주입한
  `ops_attempt_id`가 현재 attempt와 일치할 때만 pair를 승인하므로, 같은 run의 겹친 재시도가 공유
  로그를 덮어써도 다른 시도의 값으로 오인하지 않는다.
  `LOAD_ASSERTIONS`는 전량 미해소 집계에서 정책 제외(`policyExcluded`)와
  판정 대상 미해소(`actionableUnresolved`)를 구분한다. 인물·업종·명시된 모호어만
  정책 제외하며 기존 total/resolved/unresolved와 미해소 표본은 보존한다.
  두 새 지표는 함께 제공하는 선택 필드이므로 과거 진단도 유효하다.
  `LOAD_ASSERTIONS`와 `ASSEMBLE_EVENTS`는 선택 필드 `quality_diagnostics`도 낸다(ALPHA-1067).
  이 값은 `news_resolution_v1` 계약의 원인(`instrument_not_found`·`instrument_ambiguous`·
  `registry_miss`·`concept_rejected`·`arguments_missing`)·역할·표현·건수와 첫 기사 표본이며, 상위 10건·
  8KiB로 제한되고 기사 본문은 담지 않는다. assertion 진단은 모든 실체 argument 미해소를,
  event 진단은 접지 참여자가 하나도 없는 event만 센다.
  assertion metrics의 선택 쌍 `excludedAssertions`(부분 추출 + 해소 인자 없는 주장)와
  `technicalFailures`(실패 목록 + malformed + 원문 없음 + incomplete)는 함께 계측한다
  (ALPHA-1076). 두 값의 합은 기존 `failed_records`와 같고 원장의 1건 이상 `INCOMPLETE`
  계약은 유지한다. 구 진단(쌍 없음)도 계속 읽으며 운영 주의 판정은 API가 별도로 제공한다.
  observer와 wrapper가 구조·닫힌 사유
  어휘·크기·현재 `ops_attempt_id`·성공 exit를 다시 검증한 뒤 `ops_task_attempt`에 저장하므로,
  같은 run의 재시도가 바뀐 로그를 내면 각 attempt에는 자기 진단만 남는다. 정상 런에서 확인한
  정식명 변형 별칭은 canonical master가 실제로 존재하고 단일 종목일 때만 assertion 해소에
  사용한다. event 조립에서는 그 canonical ticker가 기사 mentions 허용집합에도 있어야 한다.
  그룹명·브랜드처럼 상장사 귀속이 해석인 표현은 별칭에 포함하지 않는다.
  2026-09-19 미해소 재현으로 검증한 정식명·약칭 28개 표기를 추가했다(ALPHA-1080).
  공백·괄호·법인표기를 일반적으로 제거하는 규칙이 아니라 명시적 별칭이며, 다른 종목의
  실제 이름과 충돌하면 `ambiguous`로 남긴다.
- **ETF 수집 완전성**(ALPHA-611) — `NAV_COLLECTION_KIS`·`ETF_PROFILE_COLLECTION_KIS`·
  `ETF_HOLDINGS_COLLECTION_KRX` 세 작업은 Planner가 실행 전에
  `krx_etf.source.etf_map`의 key(our_etf_id)를 기대 snapshot으로 고정하고, 공통 수집 스텝이
  `ops.received_count`로 실제 unique ETF 수를 낸다. Wrapper는 원장의 기대값만 분모로 사용해
  `{expected, received, missing}`을 `expected_task.completeness`에 저장한다.
  따라서 현재 종목 수를 코드에 하드코딩하지 않으며, 수집기가 기대값까지 줄여 신고해 스스로
  만점 처리할 수 없다.
  이 선택 필드가 없는 나머지 작업은 기존처럼 완전성 미확인 `UNKNOWN`이다.
- **Dataset Contract / freshness 첫 슬라이스**(ADR-0043, ALPHA-654) —
  `ETF_HOLDINGS_COLLECTION_KRX`는 Catalog가 별도 typed registry의
  `ETF_HOLDINGS_KRX_EOD` 계약 key만 참조한다. Planner는 계약 version·정책·해석한
  `LATEST_KR_TRADING_DAY`를 snapshot하고, 기존 `expected_as_of_date`에 그 거래일을 저장한다.
  KRX 응답에는 요청한 `trdDd`와 독립적인 actual-as-of evidence가 없으므로 wrapper는 현재 시도의
  raw 산출물과 수집 로그가 실제로 관측됐을 때도 `actual_as_of_date=NULL`,
  `freshness_status=UNKNOWN`, reason=`ACTUAL_AS_OF_UNVERIFIED`를 기록한다. 이때
  `collected_at`만 채우고 Monitor 평가 시각인 `observed_at`은 NULL로 남긴다. 계약 연결 작업은
  **매 시도**(예외 종료 포함) freshness를 덮는다 — 산출물을 관측하지 못한 재시도는
  `collected_at=NULL`·reason=`EVIDENCE_MISSING`으로 리셋해, 같은 raw 키를 덮어쓴 재시도에 앞
  시도의 수집 증거가 남지 않게 한다(카운터와 같은 규칙). 계약 미연결 작업의
  freshness NULL은 `UNKNOWN`이 아니라 `NOT_APPLICABLE`이다.
  `NAV_COLLECTION_KIS`는 `ETF_NAV_KIS_DAILY` 계약을 참조한다. KIS 응답 원본의
  `stck_bsop_date` 집합 중 최댓값을 `actual_as_of_date`로 쓰며, 질의 종료일·실행일·
  `fetched_at`으로 대체하지 않는다. 유효 날짜가 없으면 `UNKNOWN`을 보존한다.
- **카운터 저장**(ALPHA-182·1020) — 봉투 카운터는 판정 뒤 버리지 않고 `expected_task`의
  `records_out`·`unsupported_records`·`failed_records` 컬럼에 남긴다(운영 대시보드의 건수 열,
  ALPHA-514 — 없으면
  런×작업마다 S3 로그를 뒤져야 한다). **판정 규칙은 그대로다** — 저장 전용이다. 결측·malformed
  (음수·NaN·소수·BIGINT 초과)는 0 이 아니라 **NULL** 이고, 값이 있는데 못 쓰면 경고를 남긴다
  ("신호 없음"이 "0건 처리"로 위장되지 않게, Rule 12). 스코프는 **그 작업의 마지막 시도**다 —
  매 시도가 세 컬럼을 함께 덮고, Reconciler 는 판정을 뒤집어도 건수를 몰라 다시 쓰지 않는다.
  `unsupported_records`는 정상 지원 제외라 `INCOMPLETE`나 유실 합계에 관여하지 않는다.
  그래서 `FAILED` 옆의 건수는 앞 시도의 것일 수 있다.
  `LOAD_ASSERTIONS`의 엔티티 해소 pair는 호환용 task 행과 함께 그 값을 만든 정확한 attempt 행에도
  저장한다(ALPHA-1000·ALPHA-1002). 둘은 성공 exit의 같은 시도에서만 함께 기록되고, 한쪽
  결측·malformed·`resolved > total`·비정상 exit이면 모두 **NULL**로 덮는다. 기존 행은 백필하지
  않았으므로 NULL은 0건이 아니라 계측 전/없음이다.
- **재시도 결과 보존**(ALPHA-1063) — Reconciler는 원래 SFN history와 같은
  `expected_task`에 기록된 수동·one-off attempt를 실제 `started_at` 순으로 합쳐, 가장 나중에
  시작한 물리 시도의 exit code로 outcome과 dependency를 판정한다. 그래서 원래 SFN 실패 뒤
  성공한 수동 복구를 다음 주기 대조가 다시 FAILED로 덮지 않는다. 과거 Reconciler가 대조
  시각으로 만든 backfill 행은 SFN `TaskStateEntered.timestamp`로 보정하며, wrapper와 동시에
  결과를 쓸 때는 `expected_task.updated_at` CAS가 대조 전의 낡은 판정을 막는다. 성공 시도는
  이전 실패의 `outcome_reason`도 지운다.

### 실행 흐름 (스펙 §5)

```
EventBridge(daily·news×2(00:10·08:10)·disclosure(19:30)) → Planner(plan-run) : DB 트랜잭션(pipeline_run+expected_task+snapshot)
                                              → commit → 결정적 execution_name → SFN StartExecution
                                                (레인은 OPS_PIPELINE_TYPE — 자기 레인 카탈로그만 계획)
각 ECS 태스크(catalog SFN 30작업) → wrapper instrument : attempt 시작/종료·data_status 관측(원장 장애 시 통과)
EventBridge(reconcile) → Reconciler : SFN/ECS 증거로 예정↔실제 대조(MISSED/BLOCKED/STALLED/…)

(Airflow 주체 레인, ALPHA-1088 — 장중 수급(2026-10-03 상시 전환, ALPHA-1141)·원천 관측 source-daily(ALPHA-1130))
Airflow DAG → plan(OPS_ORCHESTRATOR=AIRFLOW) : 같은 원장 계획, SFN 미시작
            → 업무 ECS 태스크(OPS_EXCLUSIVE_STEP) → wrapper : 작업별 실행권 획득 후 실행, 원장 불명이면 미실행(75),
                                                         같은 작업의 미종료 시도·ECS 보류가 있으면 보류(76)
            → report(reconcile, OPS_RUN_KEY+OPS_ORCHESTRATION_STATUS+OPS_EXECUTION_HOLDS)
                                                       : DAG 판정을 orchestration_status 로, 보류를 EXECUTION_HOLD 로
Reconciler(Airflow 런) : SFN 대신 원장 attempt·ECS 로 대조, 보고가 없으면 원장에서 상태 투영
Reconciler(모든 레인)  : ECS STOPPED 확인 → RUNNING 시도를 닫는다(실행권 게이트 해제). exit 가 없거나 신호 종료
                         (≥128)·외부 종료 stopCode 의 비0 이면 FAILED + outcome_reason stopped_result_unknown
                         (업무 결과 아님 — 산출·선행 완료로 세지 않는다). 판정 규칙은 Airflow 와 공용 사례표로 대조
Reconciler(주기, Airflow): sweep_airflow_runs — 끝나지 않은 시도가 남은 Airflow 런을 대조하고, 원장에 없이 DAG
                         수명보다 오래 도는 ECS 태스크를 EXECUTION_HOLD 로 남긴다(report·callback 없이도)
```

**실행 주체(ALPHA-1088).** `ops_pipeline_run.orchestrator`(SFN|AIRFLOW)가 슬롯의 주체다. 다른 주체가
이미 계획한 run_key 를 다시 계획하면 Planner 가 실행하지 않고 LAUNCH_CONFLICT 로 드러낸다(두 주체의
같은 run_id 이중 실행 방지). Airflow 경로의 중복 실행 방지·exit 75·보류(exit 76, `EXECUTION_HOLD`)·재처리(`OPS_REPROCESS`)·
전환·롤백·보류 해제 절차는 [`src/apps/cloud/airflow/README.md`](../../airflow/README.md)가 정본이다. env 가 없는 SFN·수동
경로의 wrapper 동작은 종전 그대로다(원장 장애에도 작업 진행).

Planner 는 StartExecution **전에** 원장을 남긴다 — SFN 이 안 떠도 "실행 자체가 안 됐다"를 잡기
위함(ECS 안에서 자기 expected_task 를 만들면 불가능). `ExecutionAlreadyExists` 는 즉시 LAUNCHED
로 보지 않고 DescribeExecution 으로 입력을 비교한다(동일=LAUNCHED, 상이=LAUNCH_CONFLICT).

**슬롯 = 분(ALPHA-564).** 멱등키는 `run_key = <pipeline_type>:<YYYY-MM-DDTHH:MM>`(KST)이고
`pipeline_run_id`·`execution_name` 이 여기서 결정적으로 파생된다. 날짜가 아니라 **시각**인 이유는
`UNIQUE (run_key)` 가 곧 "한 슬롯 1회 계획"이라, 날짜로 두면 하루 여러 번 도는 레인(뉴스
00:10·08:10, iNAV 15분)의 2회차부터가 1회차에 흡수되고 **수동·백필 실행이 원장에 들어올
자리가 없기** 때문이다. 결과:

- **애드혹 실행도 `plan-run` 으로 돌리면 관측된다** — 실행 분이 그 실행의 슬롯이 된다.
  `start-execution` 을 직접 쓰면 원장에 안 남아 그 런은 대조 대상이 아니다.
- 같은 분 재호출은 여전히 run 1개(Planner 재기동 무해). 수동 실행이 스케줄 분에 정확히 걸리면
  그 슬롯으로 **흡수**되고 `created=False` 로 드러난다 — 새로 도는 게 없다는 뜻이니 로그를 보라.
- 키 형식의 출처는 `planner.slot_run_key` **하나**다. Reconciler 의 `_due_slots` 도 그 함수를 쓴다 —
  두 곳에서 조립하면 어긋나는 순간 없는 슬롯을 찾아 **실제 런이 영영 대조되지 않는다**. 같은
  이유로 `OPS_DAILY_SCHED_HHMM`·`OPS_NEWS_SCHED_HHMM`·`OPS_DISCLOSURE_SCHED_HHMM`·
  `OPS_INVESTOR_INTRADAY_SCHED_HHMM` 은 별도 변수가 아니라 terraform 이 각 스케줄 cron 에서
  뽑고, cron 을 KST 로 읽으므로 `schedule_timezone` 은 `Asia/Seoul` 로 강제된다. ⚠️ 공시와
  장중 수급 것만 **스케줄이 ENABLED 일 때만 주입한다**(ALPHA-722·769, 장중 수급은
  `investor_intraday_orchestrator = "AIRFLOW"` 일 때도 주입 — 스케줄은 꺼져도 Airflow 가 그 슬롯을
  돌리므로, ALPHA-1088) — 슬롯 기준은 Reconciler
  에게 "이 시각엔 런이 있어야 한다"는 주장이라, 꺼진 채 넣으면 뜰 리 없는 슬롯을 결측으로
  판정해 **참인** PLANNER_MISSING 을 그날 지난 슬롯마다 연다(현재 공시는 19:30·장중 수급 5개).
  빈 값 = 그 레인 결측 판정 없음이 안전 기본값이다(`entry._lane_sched_hhmms`).
- **주말은 레인마다 다르다**(ALPHA-874) — 뉴스 크론만 주 7일이고 시장·장중 수급은 MON-FRI 다.
  그래서 `OPS_DAILY_SCHED_WEEKEND`·`OPS_NEWS_SCHED_WEEKEND`·`OPS_DISCLOSURE_SCHED_WEEKEND`·
  `OPS_INVESTOR_INTRADAY_SCHED_WEEKEND` 가 HH:MM 과 **같은 cron 의 일·요일 필드**에서 파생돼 함께
  주입된다(`"true"`/`"false"`, `entry._lane_sched_weekend`). 이게 없으면 주말 건너뛰기가 레인 무관
  상수가 되어 어느 쪽이든 틀린다 — 상수 "건너뛴다"면 주 7일 레인의 결측 탐지가 **조용히 0** 이 되고,
  상수 "안 건너뛴다"면 MON-FRI 레인이 매 토·일 거짓 PLANNER_MISSING 을 연다(뜰 런이 없어 `run_present`
  로 영영 RESOLVE 되지 않는다). ⚠️ 이 형제에는 위의 ENABLED 조건이 **없다** — HH:MM 이 빈 값이면
  `_due_slots` 가 그 레인을 먼저 건너뛰므로 플래그만 남아도 무해하다. 미주입이면 `False`(=평일 전용,
  종전 동작)이고, `"true"`/`"false"` 외의 값은 fail-loud 다.
- 주기 Reconciler 는 레인별로 "가장 최근에 슬롯이 지난 **예정일**"의 **그날 지난 스케줄 슬롯 전부**를
  대조한다(ALPHA-591 — 뉴스의 앞 슬롯이 최신 하나에 밀려 영영 미대조되지 않게). 평일 전용 레인이면
  그 예정일이 주말을 건너뛴 직전 평일이다. ⚠️ 수동 슬롯은
  여전히 `OPS_RUN_KEY` 로 지정해야 대조된다 — 지정 없이 초기에 죽은 수동 런은 조용히
  남는다(ALPHA-565).

### 실행 (로컬/수동)

```bash
# Planner — 원장 기록 + SFN 시작. OPS_STATE_MACHINE_ARN·DATA_PIPELINE_DB__* 필수.
OPS_STATE_MACHINE_ARN=arn:aws:states:…:stateMachine:edge-dev-data-pipeline \
  python -m data_pipeline.run plan-run
# Reconciler — 예정↔실제 대조(advisory lock 으로 중복 실행 방지).
python -m data_pipeline.run reconcile
# Outbox Relay(1분 파이프라인, ALPHA-670) — outbox NEW → SQS 발행. 상주(ECS Service)가
# 기본이고 --max-ticks 는 로컬 확인·일회성 배출용이다(그 모드는 **미발행 0건을 확인**해야
# exit 0 — IDLE 은 "지금 집을 게 없다"일 뿐이라 완료 판정에 못 쓴다).
# 큐 매핑은 필수: 빠지면 그 큐의 event 가 전부 DEAD 가 되므로 기동을 거부한다.
# 큐 매핑은 **JSON 한 변수**로 준다 — destination 이름에 하이픈이 있어 nested 형태
# (…__QUEUE_URLS__price-analysis-realtime=)는 셸이 변수 할당으로 파싱하지 못한다.
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_MINUTE_RELAY__QUEUE_URLS='{"price-analysis-realtime":"https://sqs…/price","news-extraction-realtime":"https://sqs…/news","news-extraction-backfill":"https://sqs…/backfill","price-explanation-realtime":"https://sqs…/explain"}' \
  python -m data_pipeline.run relay --max-ticks 5
# DLQ 대사(1분 파이프라인, ALPHA-672) — DLQ 에 도착했는데 DB job 이 non-terminal 이면
# SQS_MAX_RECEIVE 사유로 DEAD 에 CAS 한다. **주기 실행**이고 메시지는 지우지 않는다
# (근거 보존). 원 큐 매핑도 함께 요구한다 — DLQ 자리에 원 큐가 들어가면 정상 배달
# 중인 job 이 전부 DEAD 가 되므로 겹치면 기동을 거부한다. 원 큐 매핑은 relay 어휘
# **4종**(트리거 설명 큐 포함), DLQ 매핑은 **job 큐 3종**을 다 채워야 한다(빠진
# 레인은 아무도 대사하지 않는다 — 트리거 DLQ 는 job 테이블이 없어 대사 대상이
# 아니다, ALPHA-709). 끊긴 대사는 exit 1 이다.
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_MINUTE_RELAY__QUEUE_URLS='{"price-analysis-realtime":"https://sqs…/price","news-extraction-realtime":"https://sqs…/news","news-extraction-backfill":"https://sqs…/backfill","price-explanation-realtime":"https://sqs…/explain"}' \
DATA_PIPELINE_MINUTE_CONSUMER__DLQ_URLS='{"price-analysis-realtime":"https://sqs…/price-dlq","news-extraction-realtime":"https://sqs…/news-dlq","news-extraction-backfill":"https://sqs…/backfill-dlq"}' \
  python -m data_pipeline.run dlq-reconcile --max-ticks 5
# redrive(1분 파이프라인, ALPHA-672) — **막힌 것**만 되살린다(DEAD job 또는 Relay 가
# 발행 불가로 격리한 DEAD delivery event). 정상 진행 중이거나 SUCCEEDED 는 거부한다.
# --reason 은 필수다: 실행자와 함께 대체되는 delivery event 행에 남는 유일한 감사 근거다.
# 배선이 어긋난 채 커밋된 행(Relay 가 destination↔event_type 불일치로 격리)은
# --destination 으로 올바른 큐를 지정해 바로잡는다 — event_id 가 결정적이라
# producer 를 고쳐 재실행해도 그 행은 안 바뀐다(미지정=직전 event 값 복사).
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run redrive --kind news --job-id <job_id> --reason "큐 URL 오타 수정 후 재시도"
# 세션 계획(1분 파이프라인, ALPHA-698) — 하루치 session + window 를 멱등 생성한다
# (스케줄 경로에서는 아래 `start-minute-session` 이 부른다). 재실행은 no-op 이고 exit 0 — 새로 생겼는지는 출력의
# `created` 가 말한다. ⚠️ 가격 세션은 `--universe` 가 **필수**다: 빠뜨리면 정규장 390 만
# 계획되고 시간외 구간이 아무 실패 신호 없이 누락된다. window 범위와 universe_hash 가
# 그 파일에서 나온다(무엇을 정본으로 볼지는 운영자가 정한다 — CLI 는 찾아 나서지 않는다).
# exit: 0=계획됨 / 1=계획하면 안 되는 상태(다른 universe 로 고정·이미 drain 이후) /
# 2=계획 자체를 못 함(설정·인자 결손·어휘 밖 dataset·source_group·DB 장애).
# ⚠️ iNAV 세션(`--dataset etf_inav_minute --source-group kis`)도 `--universe` 를 쓰지만
# 격자는 **항상 390**이다 — 어댑터 하한이 09:00 이라(`kis_inav.MARKET_OPEN`) 시간외를
# 계획하면 매 거래일 08:00~08:59 의 60 window 가 아무도 못 채운 채 DUE 로 남고, iNAV 는
# 소급이 불가라 영구 결손이다. 시간외 종목이 든 universe 를 줘도 안 넓힌다.
# 공시 세션(`--dataset disclosure_minute --source-group dart`)도 **390**이다(ALPHA-1072).
# `--universe`는 받지 않는다. 장중 관측은 09:00~15:30이고 장외 회수는 마감 배치 소관이다.
# 첫 poll·주기 대사는 날짜창 전체를 읽고 사이 poll은 접수 원장 증가분까지만 읽는다.
# 정제 두 스텝은 그 poll의 exact raw key만 소비하며 날짜창은 세션 날짜(KST)에서 유도한다.
# ⚠️ **업종지수 세션(`--dataset sector_index_minute --source-group kis`)은 세 번째
# 형상이다**(ALPHA-887): `--universe` 를 **안 받는데**(주면 거부) 격자는 **390**이다 —
# 위 둘의 조합이 아니라 각 축이 따로 정해진다는 뜻이다. universe 를 안 쓰는 이유는 소스
# 단위여서가 아니라 **기대 집합 45종이 universe.json 에 아예 없어서**다(지수는 ETF 명부에도
# 구성종목에도 없다) — 정본은 `[minute_sector_index.index_map]` 이고, planner 가 그 표의
# 해시를 세션에 고정한다. 그래야 장중 재배포로 표가 바뀔 때 Worker 가 거부한다(안 그러면
# 한 세션 안에서 기대 집합이 조용히 갈린다). 격자가 390 인 이유는 이 TR 이 정규장 지수만
# 주기 때문이고, 소급이 불가라 못 채운 window 는 영구 결손이다.
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run plan-minute-session --dataset disclosure_minute \
    --source-group dart --session-date 2026-08-10   # universe 없음 · 390 window
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run plan-minute-session --dataset sector_index_minute \
    --source-group kis --session-date 2026-08-10    # universe 없음 · 390 window
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run plan-minute-session --dataset price_minute \
    --source-group kis --session-date 2026-08-04 --universe /path/universe.json
# universe.json 생성(1분 파이프라인, ALPHA-735) — canonical KR holdings 의 **ETF 별 최신
# 스냅샷 합집합**(ALPHA-590 규칙) **∩ 유니버스 뿌리(`krx_etf.source.etf_map`)** 에서 만든다
# — 파티션에 남은 폐지분·참조 계열은 여기 안 든다. 손으로 유지하는 목록은 ETF 편입·제외 때마다
# 조용히 어긋난다. 여기에 config `[minute_universe].sector_etf_ids`(층 분해의 섹터 후보
# ETF)를 **참조 계열 축**(`Universe.sector_etf_ids`)으로 얹는다 — 봉만 받고 트리거 판정은
# 안 받는 계열이다(`etf_ids` 는 price-consumer 의 판정 집합이라 거기 얹으면 발화 대상이 된다).
# 반영까지 하는 것은 **스텝**이다(ALPHA-953). 쓸 자리는 소비자와 같은 `--universe` URI 를
# 인자로 받는다 — 상수로 박으면 var.minute_universe_uri 가 옮겨졌을 때 생산자와 소비자가
# 둘 다 exit 0 으로 갈린다. 무변경이면 no-op(PUT 자체를 안 한다), 교체하면 직전 객체를
# `<uri>.bak-<run_id>` 로 남긴다. 평일 07:00 KST 에 장전 레인(ALPHA-963)이 이 스텝을
# 부르므로 아래는 **수동 회수·확인용**이다(예: 그 런이 실패한 날).
AWS_PROFILE=edge DATA_PIPELINE_STORAGE__BACKEND=s3 \
DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
  uv run --package data-pipeline python -m data_pipeline.run build-minute-universe \
    --universe s3://edge-dev-pipeline-lake/config/minute/universe.json
# ⚠️ **거래일 07:30 KST(REBUILD_CUTOFF_KST) 이후엔 스텝이 스스로 거부한다** — 세션이 이미
# 계획된 뒤라면 원장의 (universe_version, universe_hash)는 옛 값에 고정된 채 객체만 바뀌어,
# 재기동된 worker 가 매 틱 blocked 로 돌면서도 안 죽는다. 장전 체인은 이 시각 전에 끝나야
# 한다(그게 계약이고 크론이 그것을 지킨다). 비거래일엔 흔들 계획이 없어 시각을 안 본다.
# ⚠️ **새 축을 담은 객체는 이미지 배포 뒤에 올린다.** Universe 는 extra="forbid" 라
# 옛 이미지가 읽으면 ValidationError 이고, planner 가 exit 2 면 스케일업을 안 해 그날
# 레인이 안 뜬다(그 실패는 minute-session non-zero exit 경보로 드러난다).
# 반대 순서는 안전하다(축 기본값이 ()이라 옛 객체는 그대로 읽힌다).
#
# 파일로만 뽑아 눈으로 대조할 땐 스크립트를 쓴다(업로드하지 않는다. `--out` 없으면 stdout).
# 마감 시각을 넘겨 오늘 안에 꼭 갈아야 할 때도 이 경로다 — 그때는 ①기존 객체를 지우지 말고
# `.bak-수동` 으로 옮기고 ②백업의 extended_hours_ids 를 새 객체에 손으로 옮겨 담고
# ③원장의 universe_version 과 universe_hash 를 **둘 다** 고치고 ④소비자를 재기동한다.
AWS_PROFILE=edge DATA_PIPELINE_STORAGE__BACKEND=s3 \
DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
  uv run python apps/cloud/data-pipeline/scripts/build_minute_universe.py --out /tmp/universe.json
# 세션 drain(1분 파이프라인, ALPHA-698) — phase 를 DRAINING 으로 옮긴다(스케줄
# 경로에서는 아래 `stop-minute-session` 이 건다). Worker 가 ack 하면 DRAINED 가 되고 그다음이 qc-minute-session 이다.
# ⚠️ **이미 drain 이후인 것도 exit 0** 이다 — DB 커밋 뒤 출력 전에 죽은 실행의 재시도가
# 정상 운영이라, 그걸 실패로 내면 정상 재시도가 EOD 흐름을 세운다. 방금 걸었는지는
# 출력의 `drain_requested` 가 말한다. 없는 세션은 exit 2 다(지목이 틀린 것이라 재시도로
# 낫지 않는다).
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run drain-minute-session --session-id <session_id>
# EOD 세션 QC(1분 파이프라인, ALPHA-693) — drain 이 끝난(DRAINED) 세션 하나를 판정해
# 닫는다. DUE 잔존을 MISSING 으로 확정하고 FINALIZED + final_checksum 을 기록한다.
# ⚠️ 확정 대상은 **이미 도래한** window 뿐이다(scheduled_at ≤ now) — 장중에 drain 이
# 잘못 걸린 세션을 QC 해도 아직 오지 않은 분을 봉인하지 않는다. 판정 결과는 stdout JSON.
# exit: 0=확정 / 1=원장이 스스로와 모순(사람이 봐야 한다) / 2=판정 자체를 못 함(재시도 가능).
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run qc-minute-session --session-id <session_id>
# EOD 5분봉 확정(1분 파이프라인, ALPHA-839) — 그날 5분 파생을 마감 후 한 번 확정한다.
# 커밋 후크(`minute/rollup.py:maybe_rollup`)는 "방금 커밋된 window 의 버킷이 닫혔을 때"만
# 발화해 **지나간 거래일을 영영 안 채운다** — 마지막 버킷 뒤에 온 정정이나 통째로 안 돈
# 날에는 다음 커밋이 없다. 집계는 후크와 **같은 함수**(`_rollup_day`)라 같은 커밋 세대
# 집합이면 산출이 바이트까지 같다(재실행도 멱등).
# ⚠️ 계획·커밋을 **원장에서 읽는다** — `--universe` 를 받지 않는다(거부한다). 마감 후의
# universe 파일은 수동 편집 대상이라 그날 계획과 갈릴 수 있고, 갈리면 없는 분을 결손으로
# 세거나 있는 분을 계획 밖으로 버린다.
# ⚠️ **백필이 소유하는 날**의 파티션은 거부한다(`rollup.writer_owns()` — 경계
#   `WRITER_SINCE` + 경계 앞 예외 집합) — 그 앞은 fmp·토스·KIS 백필의 정본이라 과거
# --session-date 재실행 하나가 벤더 원본을 파생본으로 갈아치운다. 날짜를 여기 적지
# 않는다: 경계는 옮겨진다(ALPHA-836 — 롤업이 온전한 계열을 갖는 날로 이동했다).
# ⚠️ **롤업 소유일 안에도 소유 축이 하나 더 있다**(`rollup.OWNER_SOURCE_GROUP`,
#   ALPHA-847): 산출 키에는 source_group 이 없는데 세션은 source_group 으로 갈리므로,
#   그날 세션이 둘이면(kis·toss) `--source-group` 만 바꾼 실행이 **같은 part-0 을 다툰다**.
#   세션이 둘 이상인 거래일은 소유 source_group 실행만 쓰고 나머지는 거부한다(exit 1,
#   로그에 소유자와 거부된 실행이 함께 남는다). 세션이 하나인 날은 안 본다 — 벤더는 설정
#   축이라(`WorkerConfig.source`) 무조건 걸면 벤더를 바꾼 날부터 파생이 통째로 멎는다.
#   🔴 대가: 소유 세션 행은 있는데 커밋이 0건이고 비소유 세션만 온전한 날은 그날 파생이
#   안 나온다(둘 다 거부된다). 아래 `unfilled_settled_days` 가 그날을 결손으로 잡는다.
# 출력에 결손 판정이 함께 실린다 — 5분 파생엔 원장이 없어서 배치가 조용히 안 돌면
# 물어볼 곳이 그것뿐이다. 목록이 **둘**인 이유는 처방이 다르기 때문이다:
#   · `unfilled_settled_days` — 파티션이 비었다 → **1분 재수집**이 필요하다
#   · `contested_days` — 다른 writer 가 물고 있어 파생이 영구 정지했다 → **소유자 결정**
#     이 필요하다(우리 part-0 이 이미 있어도 잡는다: 후크가 먼저 쓴 뒤 백필이 끼어들면
#     그 시점의 **부분본**이 완성본처럼 남는데, 그게 운영에서 더 흔한 순서다)
#   · `settled_day_count` — 그 **분모**(후보 일수). 빈 목록은 "구멍 없음"과 "본 게 없음"
#     둘 다라 분모 없이는 못 가른다
# ⚠️ 판정 축 셋:
#   · 세션 phase = `FINALIZED`이고 `final_checksum`이 64자리 소문자 sha256인 값. EOD QC가
#     원장과 artifact를 대조해 checksum으로 봉인한 날만 settled다. DRAINED·QC_RUNNING·
#     FAILED와 checksum이 없거나 잘못된 FINALIZED는 후속 rollup 결손 판정의 후보가 아니다.
#   · 날짜 창은 `[rollup.scan_lower(), 오늘)` — **`--session-date` 와 무관하게 오늘**이
#     상한이고, 하한은 롤업이 소유하는 가장 이른 날이다(경계 앞 예외를 포함한다 —
#     소유하는 날은 감시해야 구멍이 조용히 남지 않는다).
#     대상 날짜로 묶으면 과거 하루를 되돌리는 실행에서 감시 창이 가장 좁아진다.
#   · 파티션은 **타 writer 파일 유무를 먼저** 보고, 없을 때만 우리 산출의 부재를 본다.
#     한 축으로만 물으면 한쪽이 샌다 — "비었나"만 보면 거부된 날이 영원히 "채워짐"이고,
#     "우리 part-0 있나"만 보면 후크가 먼저 쓴 뒤 끼어든 날의 부분본을 못 본다.
# 실측(2026-08-07 dev): `unfilled=['2026-08-04']` · `contested=[]` · 분모 3.
# ⚠️ 스캔은 rollup **뒤**에 돈다 — 창이 `[rollup.scan_lower(), 오늘)` 이라 대상 날짜가 그 안에
# 있어서, 앞서 돌면 방금 그날을 채운 실행이 같은 출력에서 그날을 결손이라 보고한다.
# exit: 0=확정(또는 비거래일 no-op) / 1=확정 안 함(세션 없음·커밋 0건·닫힌 버킷 0·다른
# writer 파일 존재·백필 소유일·**비소유 source_group** — 전부 재시도로 안 낫는다)
# / 2=판정 자체를 못 함
# (설정·인자 결손·DB/S3 장애 — 재시도하면 될 수 있다). 구멍 판정 스캔만 실패하면 rollup
# 이 성공했을 때만 2 이고, rollup 이 거부했으면 **1 이 이긴다**(두 사실은 독립이다).
# 우선순위: rollup 예외 → 2 · 정당한 거부(key 없음) → 1 · 스캔 실패만 → 2 · 그 외 0.
# `--session-date` 미지정=오늘(KST). `--dataset` 은 **봉 dataset 만** 받는다
# (price_minute·sector_index_minute — `rollup.ROLLUP_DATASETS` 가 정본). 어휘 전체로
# 열지 않는 이유: 뉴스 세션도 390 window 를 계획해서, 열어 두면 뉴스 커밋 지평으로 잘린
# 5분봉이 가격 파일을 덮는다.
# 산출은 dataset 마다 **같은 파티션의 다른 파일**이라 둘을 따로 돌려야 한다
# (행은 서로소 — 업종코드 vs 종목코드).
# ⚠️ **업종지수는 장중 후크가 없다**(`SectorIndexWorker._after_commit` 은 비어 있다 —
# 후크는 ALPHA-839 가 지울 경로다). 배치가 유일 writer 이고, 평일 16:00 KST 스케줄이
# 그것을 부른다(ALPHA-955 — `aws_scheduler_schedule.minute_session["rollup-sector"]`).
# 아래 명령은 **그 스케줄이 못 채운 날을 손으로 되돌릴 때** 쓴다.
# 🔴 **가격은 아직 스케줄이 없다** — 장중 후크가 매일 만들고 있어 당장 공백은 없지만,
# 지나간 날은 이 명령으로만 채워진다(EOD 확정 스케줄은 ALPHA-839 PR2).
#   시각이 업종지수와 다를 수밖에 없다: stop cron 16:10(ALPHA-1074) + 상한 1800초 + 확인분
#   60초 = 최악 16:41 이고 stop 태스크의 Fargate 기동 시간도 더 붙는다.
#   그 전에 뜨면 늦은 recovery 커밋이 5분 파생에 영영 안 들어간다(후크와의 배타성은
#   코드가 아니라 스케줄 시각이 진다 — 이 스텝은 phase 게이트를 의도적으로 안 건다).
#   업종지수는 09:00~15:30 격자라 그 하한이 훨씬 이르다(16:00 근거는 terraform 주석).
#   `OPS_KR_HOLIDAYS` 는 `aws_ecs_task_definition.minute_session` 에 **이미 주입돼 있다**
#   — 그 task-def 를 재사용하면 자동 충족이고, 새 task-def 를 파면 필수다.
AWS_PROFILE=edge \
DATA_PIPELINE_STORAGE__BACKEND=s3 \
DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run rollup-minute-session --dataset price_minute \
    --source-group kis --session-date 2026-08-04
# 업종지수(45종 → part-sector-index.parquet). source-group 은 kis 뿐이다.
AWS_PROFILE=edge \
DATA_PIPELINE_STORAGE__BACKEND=s3 \
DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run rollup-minute-session --dataset sector_index_minute \
    --source-group kis --session-date 2026-08-10
# 상주 iNAV Worker(1분 파이프라인, ALPHA-851) — 장중 추정 NAV 를 window 단위 canonical
# artifact 로 확정한다. 세션이 먼저 계획돼 있어야 한다
# (plan-minute-session --dataset etf_inav_minute --source-group kis — `--universe` 는
# price 와 **같은 파일**을 쓴다. 세션 identity·기대 집합이 거기서 나온다).
#
# 가격 Worker 와 갈리는 곳 넷:
#  · **기대 집합은 판정 축 ETF 뿐이다**(`etf_ids`) — 기준은 NAV 가 있는가가 아니라
#    **질의 심볼 맵(`etf_map`)에 있는가**다. 맵 밖 unit 은 `_rows_for` 가 INVALID 로
#    내고 invalid 하나면 window 전체가 INVALID 다. 구성종목은 NAV 도 맵도 없고, 참조
#    계열(`sector_etf_ids`)은 NAV 는 있어도 맵 밖이다(ALPHA-903 — 빌더가 두 축의 겹침을
#    거부해 그 상태를 지키지만 영구 보장은 아니다. `_expected_units` 도크스트링에 이유가
#    있다). ⚠️ NAV 축만의 판단이다 — 참조 계열의 **봉**은 가격 레인이 그대로 수집한다.
#  · **job·outbox 를 만들지 않는다** — 하위 소비자가 없어 window 확정에서 멈춘다.
#    (가격 것을 빌려 쓰면 NAV 가 price-analysis-realtime 으로 나가 설명이 발화된다.)
#  · **복구를 하지 않는다**(`recovery_budget_per_tick = 0`, 2026-08-08 결정) — iNAV 는
#    추정값이라 분 단위 완전성 요구가 낮다. 놓친 분은 놓친 채로 두고 결손은 원장이
#    드러낸다(`/api/v1/sources/minute` 의 overdue_no_evidence).
#  · 격자는 **390**(09:00–15:30) — 어댑터 하한이 09:00 이라 시간외를 계획하지 않는다.
#
# 질의 심볼은 `[krx_etf.source.etf_map]` 에서 온다(세션 universe 와 **다른 출처**다) —
# 갈리면 그 unit 이 매 window invalid 로 드러난다(조용히 missing 으로 접지 않는다).
# 자격증명은 일별 NAV 와 같은 쌍이다(같은 벤더·같은 계정).
#
# 🔴 **`--session-date` 는 오늘만 받는다**(다른 워커와 다르다 — 그쪽은 지난 거래일을
# 받는다). 이 벤더에는 소급 질의 경로가 아예 없고 응답 행에 날짜가 없어(`bsop_hour` =
# HHMMSS), 과거 날짜로 돌리면 **지금 값이 그 날짜의 불변 artifact 로 굳는다**. 되돌릴
# 방법이 없어(재수집 불가) 기동에서 거부한다. 그래서 아래 예시는 날짜를 안 준다(=오늘).
# 🔴 **휴장일·개장 전은 수집 전에 멈춘다**(KIS 가 빈 응답이 아니라 직전 거래일 행을
# 그대로 주기 때문 — 2026-07-25 실측). **멈추는 방식이 셋으로 갈린다**(ALPHA-882):
#   휴장일 · 상주(`--max-ticks` 없음) → exit **0**  (스케줄러가 정상 통과)
#   휴장일 · bounded                  → exit **1**  (확인 게이트라 "한 window 도 못
#                                                    봤다"를 성공으로 보고하지 않는다)
#   개장 전 · 상주                    → **종료하지 않고 09:00 까지 기다린다**
#   개장 전 · bounded                 → exit **1**  (확인은 즉답이어야 한다)
# ⚠️ 개장 전 상주 실행은 **블록된다** — 07:45 에 손으로 돌리면 09:00 까지 75분을 기다린다.
# 확인용으로 돌릴 거면 `--max-ticks` 를 준다(즉답). 상주가 기다리는 이유는 그게 ECS
# 서비스의 모습이기 때문이다: 종료하면 desired 1 을 유지하는 ECS 가 재기동 루프를 돌고,
# 백오프가 첫 정상 기동을 09:00 뒤로 밀어 소급 불가한 window 를 잃는다.
# ⚠️ 위 휴장일 분기는 `OPS_KR_HOLIDAYS` 를 받아야 성립한다 — 안 주면 `is_trading_day` 가
# 주말만 아는 상태로 **조용히 퇴화**해 평일 공휴일에 가드가 안 걸린다(terraform 은
# inav-worker 서비스에 심는다. 그 배선은 test_session_ops 의 계약 검사가 지킨다).
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY=... \
DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET=... \
KIS_TOKEN_CACHE_PARAM=/edge-dev-data-pipeline/kis/access-token \
OPS_KR_HOLIDAYS=2026-08-15,2026-10-03 \
  python -m data_pipeline.run inav-worker --universe /path/universe.json --max-ticks 3
# 토큰 만료(24h) 재발급은 **붙어 있다**(ALPHA-889). 상주 전환(ALPHA-882)이 만료를 반드시
# 만나는 것으로 바꿨기 때문이다 — 만료 신호(rt_cd `EGW00121/123` 또는 4xx)를 보면 공유
# 캐시와 컬렉터의 토큰 사본을 **둘 다** 버리고 1회 재발급해 그 window 를 살린다.
# ⚠️ 여기서 안 잡으면 자가치유가 아니다: `StopFetch` 는 tick 의 `except Exception` 에
# 삼켜져 WINDOW_FAILED 가 되고 루프는 계속 돈다 — 컨테이너가 죽고 재기동하는 게 아니라
# 그날 남은 window 가 조용히 전부 실패하고, iNAV 는 소급이 불가라 영구 결손이다.
# 🔴 **재발급 뒤에도 만료면 전역 실패로 전파한다**(MISSING 으로 안 접는다) — 그건 종목
# 축이 아니라 자격증명·시계 문제라, 접으면 "벤더가 안 준다"로 읽혀 원인을 가린다.
# 로그 신호: `토큰 만료 — 캐시 폐기 후 1회 재발급`(정상 회복) vs `재발급 뒤에도 만료`(사람 확인).

# 상주 Price Worker(1분 파이프라인, ALPHA-706) — ECS Service 명령. 세션이 먼저 계획돼
# 있어야 하고(위 plan-minute-session — `--session-date`·`--universe` 를 **같은 값**으로),
# 갈리면 다른 session_id 가 유도되거나 Worker 가 처리를 거부한다. SIGTERM 은 tick
# 경계에서 멈추고 fence lease 를 즉시 반납한다(교체 무대기 인계). `--session-date`
# 미지정=오늘(KST). `--max-ticks` 는 로컬 확인용 — WINDOW_FAILED 가 있거나 한 window
# 도 못 본 채 차단만 됐으면(경쟁 fence·universe 불일치) exit 1.
# 자격증명은 **source 마다 다른 쌍**이다(ALPHA-735) — 기본 source=kis 는 APP_KEY/APP_SECRET,
# source=toss 로 되돌릴 때만 CLIENT_ID/CLIENT_SECRET. 결손은 기동에서 죽는다.
# 상주 워커는 토큰(24h)보다 오래 사므로 KIS_TOKEN_CACHE_PARAM 을 함께 준다(발급 분당 1회).
# ⚠️ **키와 토큰 캐시는 짝이다**(ALPHA-1252). dev 의 price-worker 는 2번 KIS 키(`kis/oauth-2`)와 2번 캐시
# (`…/kis/access-token-2`)를 쓴다. 1번 키로 돌릴 때만 `…/kis/access-token` 을 준다. 어긋나게 주면 캐시에 든
# 다른 키의 토큰을 미스로 보고 새로 발급해 덮어써서, 그 캐시를 쓰는 운영 태스크가 발급을 반복한다.
# ⚠️ price-worker 태스크 정의로 돌리면 **지난 날짜 수집도 2번 키로 나간다.** 장중에 운영 워커와 같이 돌리면 두
# 프로세스가 각자 간격만 지켜 합산이 2번 키의 한도를 넘는다 — 분 세션이 없는 시간에만 돌린다.
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_MINUTE_PRICE_WORKER__APP_KEY=... \
DATA_PIPELINE_MINUTE_PRICE_WORKER__APP_SECRET=... \
DATA_PIPELINE_MINUTE_PRICE_WORKER__TRIGGER_SCHEMA_VERSION=intraday-anchor-v2.1 \
KIS_TOKEN_CACHE_PARAM=/edge-dev-data-pipeline/kis/access-token-2 \
  python -m data_pipeline.run price-worker --session-date 2026-08-04 \
    --universe /path/universe.json
# ⚠️ `--session-date` 가 **지난 거래일이면 벤더 TR 과 콜 형상이 바뀐다**(ALPHA-846) —
# 소급 TR 로 하루를 한 번에 받아 캐시하므로 첫 window 에 362종 × 4페이지 ≈ 1,450콜이
# 몰리고(그 뒤 window 는 벤더 호출 0), 시간외 universe 는 기동에서 거부된다. 그 거부는
# **KIS 한정**이다 — 소급 TR 이 정규장만 주기 때문이라, 임의 과거 구간을 받는 토스는 안 막는다.
#
# 🔴 **과거일 백필 선행조건 하나 + 알아둘 것 둘** — 1) 을 안 지키면 태스크가 크게 터진다:
#  1) 그 날짜의 1분 canonical prefix 가 **비어 있어야 한다**. artifact 키에는 벤더·세션
#     축이 없어(ALPHA-705) 다른 벤더 세션이 같은 generation 을 이미 썼으면
#     `ArtifactImmutabilityError` 로 태스크가 죽고 ECS 가 같은 window 에서 재기동한다:
#       aws s3 ls --recursive \
#         s3://<lake>/canonical/market_data/price_minute/market=KR/session_date=<날짜>/
#  2) 과거일 세션은 **outbox 이벤트를 안 낸다**(ALPHA-863) — 커밋은 window·job 만 쓴다.
#     판정은 `--session-date` 하나이고(`make_price_collector` 가 `is_backfill` 로 돌려준다)
#     벤더와 무관하다. 그러니 백필 뒤 `dataset_commit_outbox` 에 행이 없는 것이 정상이고,
#     수동 DEAD 격리도 필요 없다. 그 발행 의도는 job의 `delivery_expected=false`에 같이 남아
#     조회 시각이나 생성 시각으로 다시 추정하지 않는다(ALPHA-1066). 실시간 job은 true라 필수
#     event 부재가 자정을 지나도 전달 실패로 보인다. 무엇을 수집했는지는 window·job 원장에 남는다.
#  3) 마감 창(15:29)의 값이 **당일 레인과 다르다**. 소급 경로는 종가 단일가 봉(KIS 라벨
#     15:30)을 그 창에 접어(`kis_minute.fold_closing_auction`) 종가 = 공식 종가지만, 당일
#     레인은 그 봉을 세션 안에서 못 받아 접수 구간 봉(vol 0·단일가 전 가격) 그대로다
#     (ALPHA-1127·1128). 재수집한 하루가 정본이다. 소급 TR 은 접수 구간(15:20~15:29) 행을
#     주지 않으므로(10-02 원문 3종목 — 거래소 규칙상 그 구간은 체결이 없다) 소급 15:29 창은 단일가 봉만(시가=고가=저가=종가=단일가)이고 15:20~15:28
#     창은 전 종목 결손이다(ALPHA-1153 — 채우지 않는다).
#
# 확정된 세션 재수집(ALPHA-1135) — 이미 FINALIZED 인 가격 세션이 틀린 봉으로 봉인됐을 때
# (ALPHA-1127: 08-04~09-29). 위 1) 의 "prefix 가 비어 있어야" 는 **새 세션** 얘기다 — 같은
# 세션을 다시 여는 이 경로는 재커밋이 generation+1 로 새 키에 쓰고 옛 객체는 이력으로 남는다.
# 순서(하루 단위, 장 마감 뒤·주말 — 소급 TR 이 앱키 예산을 하루 ~1,800콜 쓴다):
#   ① 그 세션의 universe 를 찾는다 — Worker 는 원장의 `universe_version`·`universe_hash` 와
#      다른 파일이면 처리를 거부한다. 원장 값은 `minute_ingestion_session` 에서, 후보 파일은
#      `config/minute/universe.json.bak-*`(변경일 백업만 있다 — 해시로 대조).
#   ② reopen — **지난 날짜의** FINALIZED·FAILED 가격 세션만 연다(그 밖은 exit 1 — 오늘 세션을
#      열면 Worker 가 당일 TR 을 타 종가 단일가 없는 값을 재봉인한다. 다음 날 돌린다). `--reason` 필수(옛
#      final_checksum 과 함께 출력에 남는 것이 감사 근거). `--windows 1529` 처럼 창 시작 KST
#      HHMM 으로 일부만 열 수 있다(없으면 전부, 빈 값은 거부). 지목한 창 하나라도 없으면
#      아무것도 안 바뀐다. session_id 는 원장 `minute_ingestion_session`(dataset·source_group·
#      session_date)에서 읽는다.
#   ③ price-worker 를 그 날짜로 **`--max-ticks` 없이** 띄운다 — DRAINED 를 볼 때까지 산다.
#      `DATA_PIPELINE_MINUTE_ARTIFACT_FORMAT=content_v2`(legacy 세션에도 쓴다 — 옛 승자는 이력에
#      기록된다). 지난 날짜라 소급 TR·outbox 미발행. checksum 이 바뀐 창만 새 세대가 된다.
#   ④ 원장에서 그 세션의 `data_status IN ('DUE','CLAIMED')` 가 0 이 된 것을 확인하고
#   ⑤ drain — **③의 Worker 가 살아 있을 때** 건다. drain 은 phase 만 바꾸고 DRAINED 로 넘기는
#      ack 는 Worker 만 한다(Worker 를 먼저 끝내면 세션이 DRAINING 에 머물고 QC 가 거부한다).
#      Worker 는 ack 뒤 스스로 끝난다(exit 0).
#   ⑥ qc(다시 FINALIZED + 새 final_checksum) → ⑦ rollup(롤업 소유일만 — `WRITER_SINCE` 앞은
#      거부되고 그게 맞다).
#   ⚠️ ④ 전에 drain 이 걸려도 옛 확정분은 안 잃는다 — QC 는 재오픈 뒤 못 받은 창(generation
#      ≥ 1 인 DUE)을 MISSING 으로 접지 않고 세션을 FAILED 로 세운다. 그때는 ②부터 다시.
#   ⚠️ 소급 재수집은 저유동 종목의 무거래 분을 **결손**으로 남긴다(ALPHA-1153 — 소급 TR 이 그 분의 행을
#      주지 않고 어댑터가 채우지 않는다). 당일 수집분 기준 정규장 창당 무거래 종목은 10~71개(10-02, 451종)라
#      재수집한 창은 대부분 INCOMPLETE 로 커밋된다 — 당일 레인의 VALID(무거래 종목은 벤더 flat 행)와 다르다.
#      접수 구간 15:20~15:28 창은 **전 종목 결손**(INCOMPLETE)이고 15:29 창은 단일가 봉만이다 — 당일 레인의
#      VALID_EMPTY(벤더 flat 행)와 다르다.
#
# 미수집(MISSING) 창 회수(ALPHA-1153) — 한 번도 커밋 안 된 창(generation 0)을 `--windows` 로 지목해
# 같은 순서로 받는다. 이 순서 전체가 `tests/e2e/test_minute_missing_window_recovery.py` 에 있다.
# 위와 다른 점:
#   - **벤더가 준 행만 싣는다.** 소급 TR 은 무거래 분의 행을 주지 않고, 어댑터는 그 분을 채우지 않는다
#     (종가 단일가 접수 구간 15:20~15:29 도 예외 없다 — 그 구간을 열면 15:20~15:28 은 전 종목 결손,
#     15:29 는 단일가 봉만이다). 그래서 대상 분에 행이 없는 종목은
#     그 창에서 **결손**(manifest `missing`, 원장 `missing_units`)이 되고, 결손이 기대 종목의 1%·3종을
#     넘으면 창은 INCOMPLETE 로 커밋된다 — 저유동 종목이 많으면 회수 뒤에도 INCOMPLETE 가 남는 것이
#     정상이다. 그 창은 나중에 근거가 생기면 같은 `--windows` 로 다시 열어 다음 generation 으로 덮는다.
#     사후 판정은 "MISSING 0" 이 아니라 창마다 성공·결손 종목 수로 한다.
#   - ② 전에 `scripts/probe_historical_minute.py` 로 대형주 몇 종의 대상 분에 체결 행이 있는지 본다
#     (exit 1 이면 열지 않는다) — 벤더가 그 분을 통째로 안 주는 날인지 미리 안다. 15:29 는 회수처럼
#     15:30 단일가를 접어 본다. 15:20~15:28 은 소급 TR 이 행을 주지 않아(10-02 원문 3종목) exit 1 이 된다
#     — 그 창을 열면 전 종목 결손으로 커밋된다.
#   - ⚠️ 5분 파생은 종목 결손을 표시하지 않는다 — 롤업은 커밋된 창의 있는 봉만 모으고 출력(ticker·ts·
#     OHLCV)에 분 수가 없어, 결손 분이 있는 종목의 5분봉이 온전한 봉과 구분되지 않는다. 접수 구간
#     창(15:20~15:29)까지 다시 받으면(확정 세션 재수집처럼 전부 열 때) 15:20 버킷은 행이 없고 15:25
#     버킷은 15:29 창의 단일가 봉 하나로 만들어진다(시가=종가=단일가). 결손 정보의 정본은 1분 창의
#     manifest·원장이다.
#   - 멈추는 길: 연 뒤 못 받았으면 ⑤(Worker 가 ack) → ⑥. generation 0 인 창은 다시 MISSING 이 되고
#     나머지 창과 `final_checksum` 은 열기 전과 같다. Worker 가 창을 집었다 실패했으면 그 claim 의
#     lease(300초)가 끝난 뒤에야 ack 된다.
#   - 발행 event 가 없으므로 가격 판정 기록(`minute_price_judgment`)의 그 창들은 빈 채로 남는다 —
#     봉 회수와 판정 공백은 별개다.
#   - KIS 콜은 창 수와 무관하게 종목 수 × 페이지(종목당 1~4, 상한 8)다. 하루치를 받은 뒤에도
#     창당 약 25초가 든다(09-29 시험 실측, 451종 — 수집은 0.2초였다. 커밋 뒤 그날 5분 파생을
#     다시 쓰는 시간으로 추정).
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run reopen-minute-session --session-id <session_id> \
    --reason "ALPHA-1135 라벨 오독 재수집"
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_MINUTE_PRICE_WORKER__APP_KEY=... \
DATA_PIPELINE_MINUTE_PRICE_WORKER__APP_SECRET=... \
DATA_PIPELINE_MINUTE_PRICE_WORKER__TRIGGER_SCHEMA_VERSION=intraday-anchor-v2.1 \
DATA_PIPELINE_MINUTE_ARTIFACT_FORMAT=content_v2 \
KIS_TOKEN_CACHE_PARAM=/edge-dev-data-pipeline/kis/access-token \
  python -m data_pipeline.run price-worker --session-date 2026-09-29 \
    --universe s3://edge-dev-pipeline-lake/config/minute/universe.json.bak-<그 세션의 파일> &
# (④ 확인 뒤) 같은 Worker 가 살아 있는 동안
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run drain-minute-session --session-id <session_id>
# 상주 가격 판정 Consumer(1분 파이프라인, ALPHA-711) — Price Job SQS 를 소비해 분봉
# canonical 로 판정한다(LLM 0). 임계는 price_triggers 의 abs_threshold(발화)·
# revert_threshold(회수) 재사용(섹션 필수), --universe 는 planner·worker 와 같은
# 파일/객체(s3://… 지원). --max-ticks 는 로컬 확인용 — 배선 오류 신호
# (poison·misrouted·orphan·ahead)가 있으면 exit 1.
# 판정마다 minute_price_judgment 에 실제 쓴 입력·결과를 같은 트랜잭션으로 남긴다(무발화 포함).
# 무발화도 발화와 같은 claim·window 세대 fence 를 탄다 — 기록 실패·세대 정정·소유권 상실·같은
# 시도 키의 다른 판정(JUDGMENT_RECORD_CONFLICT)은 성공하지 않고 커널의 재시도 절차로 간다.
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_MINUTE_PRICE_CONSUMER__QUEUE_URL=https://sqs.../price \
DATA_PIPELINE_MINUTE_PRICE_CONSUMER__DETECTION_POLICY_VERSION=intraday-anchor-v2.1 \
  python -m data_pipeline.run price-consumer --universe /path/universe.json --max-ticks 5
# 상주 뉴스 추출 Consumer(1분 파이프라인, ALPHA-713) — News Job SQS 를 소비해 기사
# 정본(PG document)을 읽고 tagging/extract 로 추출, feature 존에 결과를 불변 PUT 한다.
# 그 결과를 배치가 읽는 날짜축 feature 파티션에도 미러한다(ALPHA-900) — 없으면 배치
# tag-news 가 같은 기사를 다시 유료로 태운다.
# 추출 성공은 event 계보(source_event 7종 + threading)로 **즉시 단건 조립**된다
# (ALPHA-727, minute/event_assembly.py — assemble-events 와 같은 결정적 ID·스레드).
# LLM 설정은 tag-news 와 같은 LLM_* env 관례(기본 base_url·model=DeepSeek).
# realtime·backfill 은 같은 스텝을 큐 URL 만 바꿔 서비스 2개로 띄운다.
# --max-ticks 는 로컬 확인용 — 배선 오류 신호(poison·misrouted·orphan·ahead)면 exit 1.
DATA_PIPELINE_DB__PASSWORD=... \
LLM_API_KEY=... \
DATA_PIPELINE_MINUTE_NEWS_CONSUMER__QUEUE_URL=https://sqs.../news-extraction-realtime \
  python -m data_pipeline.run news-consumer --max-ticks 5
# 상주 News Worker(1분 파이프라인, ALPHA-707) — BigKinds 를 매분 폴링해 관측 전량을
# 원장 판정, 신규/정정만 job+outbox 로. 세션이 먼저 계획돼 있어야 한다
# (plan-minute-session --dataset news_minute --source-group bigkinds — universe 없음).
# 엔드포인트·카테고리 정본은 [bigkinds_news](배치와 공유), pacing 은 [minute_news_worker]
# (기본: interval 1s·timeout 45s·max_pages 4 — ALPHA-645 실측 근거). --max-ticks 는
# 로컬 확인용 — WINDOW_FAILED 가 있거나 한 window 도 못 본 채 차단만 됐으면 exit 1.
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run news-worker --session-date 2026-08-04 --max-ticks 3
# 상주 Disclosure Worker(1분 파이프라인, ALPHA-875·1068) —
# 공시를 매분 폴링한다. **수집만이 아니라
# 체인 전체**를 한 window 에서 돈다: collect → normalize(공급계약) → normalize(사업부문)
# → load → assemble. CLI 가 아니라 스텝 함수를 부르므로 `catalog.by_cli` 동시 소유 충돌이 없다.
# 세션이 먼저 계획돼 있어야 한다(plan-minute-session --dataset disclosure_minute
# --source-group dart — universe 없음. 격자는 09:00~15:30 390개 — 장외 회수는 마감 배치 소관).
# 엔드포인트·유형 필터 정본은 [dart_disclosure.source](배치와 공유), pacing·예산은
# [minute_disclosure_worker](기본: interval 1s·timeout 10s·페이지 예산 60·본문 예산 5·
# 전량 대사 60 poll). 첫 poll과 전량 대사는 날짜창 끝까지 읽고, 사이 poll은 직전 전량 관측의
# rcept_no 집합과 total_count 증가분을 확인할 때까지만 읽는다. 같은 건수의 교체·정정은 전량
# 대사가 최대 60 poll 안에 회수한다. collection log와 window manifest의 observation_scope가
# full/incremental/state-changed fallback을 구분한다. 대상 본문·두 canonical
# manifest·load pending 내구화 전 실패는 커서를 전진시키지 않고 다음 window가 재시도한다.
# 다시 읽어도 결과가 같은 문서 단위 거부(정제의 본문 내용 판정·조립의 계약 대상 결손 —
# `_CONFIRMED_REJECT_REASONS`)는 커서를 막지 않는다(ALPHA-1154). 그 window 는 INCOMPLETE 로
# 남고 manifest 의 rejected_documents 에 접수번호·단계·사유·원문 위치가 남는다 — 커서 전진은
# 전건 처리 완료가 아니다. 재처리는 backfill-normalize-disclosure --from/--to(정제→적재→조립).
# ⚠️ 페이지 예산은 이 워커의 소스 `max_pages` 로 **주입**된다 — 벤더 섹션의 500(백필용)이
# 그대로면 lease 검증이 실제보다 짧은 tick 을 통과시킨다.
# 질의 날짜창은 **세션 날짜(KST)** 에서 나온다: 매 tick 당일, 세션 첫 tick 만 D-1 포함
# (중단 캐치업 — 일 콜을 절반으로 줄인다). --max-ticks 는 로컬 확인용 — WINDOW_FAILED 가
# 있거나 한 window 도 못 본 채 차단만 됐으면 exit 1.
# ⚠️ 한 tick 이 1분을 넘는 것은 이 레인의 정상이다(dev 실측 window 당 ~14초·tick 당 ~27초).
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY=... \
  python -m data_pipeline.run disclosure-worker --session-date 2026-08-07 --max-ticks 3
# 보유한 DART raw 전체를 재처리한다. --from/--to 를 주면 접수일 기준 포괄 범위만 재처리한다.
DATA_PIPELINE_DB__PASSWORD=... \
  python -m data_pipeline.run backfill-normalize-disclosure --run-id dart-backfill-20260809
# 업종지수 Worker(1분 파이프라인, ALPHA-887) — KRX 업종지수 45종 분봉을 window 단위
# canonical artifact 로 확정한다. 세션이 먼저 계획돼 있어야 한다
# (plan-minute-session --dataset sector_index_minute --source-group kis — universe 없음).
#
# iNAV Worker 와 갈리는 곳 셋:
#  · **기대 집합이 universe 가 아니라 config** 다(`[minute_sector_index.index_map]` 45줄).
#    지수는 ETF 명부에도 구성종목에도 없어 universe.json 이 이 45종을 모른다. planner 가
#    그 표의 해시를 세션에 고정하고 Worker 가 대조한다 — **장중에 이미지가 바뀌어 표가
#    달라지면 기동에서 거부한다**(안 그러면 한 세션 안에서 기대 집합이 조용히 갈린다).
#  · **`no_trade` 축이 없다** — 지수는 자기가 체결되지 않아 `cntg_vol == 0` 인데 OHLC 가
#    움직이는 봉이 정상이다(실측 3.9%). 가격의 4분류를 그대로 물리면 매 window 가 INVALID 다.
#  · **오늘이 아닌 `--session-date` 를 거부한다** — 이 TR 에 날짜 파라미터가 없어 과거일로
#    돌리면 45종 전건 missing 이 그 날짜 원장에 굳는데, 소급이 불가라 채울 방법이 없다.
#
# 자격증명은 `[kis_nav.source]` 를 그대로 쓴다(iNAV 와 같은 쌍 — 같은 KIS 계정이고 쿼터가
# 앱키 전역이라 어차피 하나다). ⚠️ `[minute_price_worker]` 가 아닌 이유는 그 섹션이
# `sources.toml` 에 없어서다 — 전부 env 라, 그걸 쓰면 업종지수와 무관한 필수 필드
# (`trigger_schema_version`·`destination`)까지 주입해야 설정이 로드된다.
# ⚠️ 상주 배선(ECS 서비스)이 **있다**(ALPHA-887 — `sector-index-worker`. iNAV 와 같이
# 세션 오케스트레이션이 desired_count 를 올리고 내린다). 아래 명령은 그 배선과 별개인
# **수동 확인 게이트**다 — 상주 태스크가 이미 fence 를 쥐고 있으면 window 를 못 잡는다.
# --max-ticks 는 확인 게이트다: WINDOW_FAILED 가 있거나 **한 window 도 못 봤으면 exit 1**.
DATA_PIPELINE_DB__PASSWORD=... \
DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY=... \
DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET=... \
  python -m data_pipeline.run sector-index-worker --session-date 2026-08-10 --max-ticks 3
# 세션 스케일 오케스트레이션(1분 파이프라인, ALPHA-712·717·719·875·882·887) — 상주 서비스의 desired_count
# 를 세션 수명에 맞춰 바꾸는 **유일한 주체**다(terraform 은 그 값을 ignore_changes 로 뒀다).
# 실제 스케일 대상은 9종이다: 정의 10종 중 analysis-consumer 는 오토스케일링 소유(코드가
# 공용 목록에서 뺀다, ALPHA-912).
# EventBridge Scheduler 가 부르지만 손으로도 같은 명령을 친다.
#
# ⚠️ **`--dataset` 은 구동 레인(price_minute)만 받는다.** 선택 레인 넷(news_minute·
# disclosure_minute·etf_inav_minute·sector_index_minute)은 어휘엔 있어도 인자로는
# 거부된다(**exit 1** — 실측). 여기서 올리고 내리는
# 서비스 목록은 dataset 별이 아니라 **공용**이고 `_scale` 은 dataset 을 아예 안 봐서,
# 승객 dataset 으로 stop 을 부르면 phase 게이트는 그 세션만 보고(claim 0 → 즉시 통과)
# 큐·outbox 게이트는 전역이라 **살아 있는 price-worker 가 내려간다**. terraform 의
# `minute_session_dataset` 기본값도 price_minute 라 실제 경로는 없지만, 손으로 치던
# 사람은 `--dataset` 에서 막힌다.
#   ⚠️ 같은 부류의 오류에 `plan-minute-session` 은 2 를 낸다(어휘 밖 dataset). 이쪽은
#   `SystemExit(문자열)` 이라 1 로 떨어지는 것이고 **의도된 구분이 아니다** — 정리 대상.
#   ⚠️ **자기 워커를 소유해도 이 조건은 안 풀린다**(ALPHA-882) — 소유와 구동 레인은
#   다른 축이다(`states.SCALED_DATASETS`). news_minute 이 news-worker 를, etf_inav_minute
#   이 inav-worker 를 소유하는 지금도 둘 다 인자로는 못 온다.
# **선택 레인은 이 명령에 얹혀 계획·드레인된다 — 단 토글 env 가 켜진 레인만이다**
# (`MINUTE_SESSION_{NEWS,DISCLOSURE,INAV,SECTOR_INDEX}_SOURCE_GROUP`). 현재 dev 는 넷
# (news·disclosure·inav·sector)이 모두 켜져 있다.
# ⚠️ **토글 env 가 없는(빈) 레인은 계획도 스케일도 안 된다** — 그 레인만 조용히 빠진 채
# 세션이 선다(`session_ops._OPTIONAL_LANES`). 손으로 칠 때 아래 예시에서 한 쌍을 빼면 그 결과다.
#
# start: 거래일 판정(OPS_KR_HOLIDAYS) → plan-minute-session(오늘 KST 고정) → desired 0→1.
# ⚠️ 비거래일이면 아무것도 하지 않고 exit 0. 계획이 실패하면 **올리지 않고** 그 exit 를
# 그대로 낸다 — 세션 없이 뜬 Worker 는 기동을 거부해 하루 종일 재기동 루프를 돈다.
# ⚠️ 스케일업은 항상 force-new-deployment 다(desired 0 동안 CD 재배포가 no-op 라, 빼면
# 직전 세션의 낡은 다이제스트로 뜬다).
# 공시 source_group은 `dart`, 마감 batch는 19:30 ENABLED다. 장중 390창·16:10 종료와
# 함께 전환하고, 두 배포가 완료된 비거래 경계 다음의 정상 start를 사용한다.
DATA_PIPELINE_DB__PASSWORD=... \
OPS_KR_HOLIDAYS=2026-01-01,2026-03-02 \
MINUTE_SESSION_CLUSTER=arn:aws:ecs:ap-northeast-2:...:cluster/edge-dev-worker \
MINUTE_SESSION_SERVICES=edge-dev-data-pipeline-price-worker,edge-dev-data-pipeline-relay,edge-dev-data-pipeline-price-consumer,edge-dev-data-pipeline-news-consumer-realtime,edge-dev-data-pipeline-news-consumer-backfill,edge-dev-data-pipeline-analysis-consumer \
MINUTE_SESSION_ANALYSIS_SERVICES=edge-dev-data-pipeline-analysis-consumer \
MINUTE_SESSION_NEWS_SOURCE_GROUP=bigkinds \
MINUTE_SESSION_NEWS_WORKER_SERVICES=edge-dev-data-pipeline-news-worker \
MINUTE_SESSION_DISCLOSURE_SOURCE_GROUP=dart \
MINUTE_SESSION_DISCLOSURE_WORKER_SERVICES=edge-dev-data-pipeline-disclosure-worker \
MINUTE_SESSION_INAV_SOURCE_GROUP=kis \
MINUTE_SESSION_INAV_WORKER_SERVICES=edge-dev-data-pipeline-inav-worker \
MINUTE_SESSION_SECTOR_INDEX_SOURCE_GROUP=kis \
MINUTE_SESSION_SECTOR_INDEX_WORKER_SERVICES=edge-dev-data-pipeline-sector-index-worker \
  python -m data_pipeline.run start-minute-session --dataset price_minute \
    --source-group kis --universe s3://edge-dev-pipeline-lake/config/minute/universe.json
# stop: drain 요청 → **원장 게이트**가 빌 때까지 폴링 → 활성 전 레인 QC → desired 1→0.
# 한 레인 QC 실패도 뒤 레인과 scale-down을 막지 않고, 최종 exit 은 2 > 1 > 0 으로 집계한다. 게이트는 셋이고
# 순서대로 비어야 한다 — session.phase 가 DRAINED 이후(= in-flight window 0) → 게이트 큐
# 깊이 0 → 미발행 outbox NEW 0. 큐 깊이는 approximate 라 **연속 5회(≈60초)** 확인한다.
# ⚠️ 시각으로 내리지 않는 이유가 이것이다 — 15:30 이 지났다고 내리면 recovery 레인이
# 집고 있던 window 가 조용히 결손된다.
# exit: 0=QC 성공/재사용 뒤 내렸음(또는 오늘 세션이 없어 미변경) / 1=상한까지 게이트가
# 안 비어 **내리지 않았거나** QC 불변식 위반 / 2=drain 또는 QC 실행 자체를 못 함.
AWS_PROFILE=edge \
DATA_PIPELINE_STORAGE__BACKEND=s3 \
DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
DATA_PIPELINE_DB__PASSWORD=... \
MINUTE_SESSION_CLUSTER=arn:aws:ecs:ap-northeast-2:...:cluster/edge-dev-worker \
MINUTE_SESSION_SERVICES=edge-dev-data-pipeline-price-worker,edge-dev-data-pipeline-relay,edge-dev-data-pipeline-price-consumer,edge-dev-data-pipeline-news-consumer-realtime,edge-dev-data-pipeline-news-consumer-backfill,edge-dev-data-pipeline-analysis-consumer \
MINUTE_SESSION_ANALYSIS_SERVICES=edge-dev-data-pipeline-analysis-consumer \
MINUTE_SESSION_NEWS_SOURCE_GROUP=bigkinds \
MINUTE_SESSION_NEWS_WORKER_SERVICES=edge-dev-data-pipeline-news-worker \
MINUTE_SESSION_DISCLOSURE_SOURCE_GROUP=dart \
MINUTE_SESSION_DISCLOSURE_WORKER_SERVICES=edge-dev-data-pipeline-disclosure-worker \
MINUTE_SESSION_INAV_SOURCE_GROUP=kis \
MINUTE_SESSION_INAV_WORKER_SERVICES=edge-dev-data-pipeline-inav-worker \
MINUTE_SESSION_SECTOR_INDEX_SOURCE_GROUP=kis \
MINUTE_SESSION_SECTOR_INDEX_WORKER_SERVICES=edge-dev-data-pipeline-sector-index-worker \
MINUTE_SESSION_GATE_QUEUES=https://sqs.../edge-dev-data-pipeline-price-analysis-realtime,https://sqs.../edge-dev-data-pipeline-news-extraction-realtime \
MINUTE_SESSION_DRAIN_TIMEOUT_SEC=1800 \
  python -m data_pipeline.run stop-minute-session --dataset price_minute --source-group kis
```

배포는 `aws_ecs_task_definition.ops`(data-pipeline 이미지 재사용) + 스케줄러 **9개 ENABLED**(daily 1·뉴스 2·
공시 1(19:30) =plan-run, reconcile 1,
장전 유니버스 1(SFN 직접), 1분 세션 start·stop·rollup-sector 3) + DLQ. 장중 수급 5개는 실행 주체가
Airflow 라 DISABLED 다(ALPHA-1141). 1분 세션 3개만 `aws_ecs_task_definition.minute_session`
(전용 IAM 역할 — 레이크 읽기 + 상주 서비스 10종 `ecs:UpdateService` + 게이트 큐(realtime 2종) 조회)을 띄운다. 설명 큐는 게이트에 없다 — 지연 재배달(장중 returns 대기) 비가시 메시지가 레인 전체를 밤새 붙잡는다(잔여는 다음 세션 소비).
ENABLED인 세 레인(daily·뉴스·공시) 스케줄은 SFN 직접 시작이 아니라 **Planner 경유**다
(뉴스는 ALPHA-591 에서 전환). 원장 DB 는 canonical 과 같은 Cloud Event Store(public 스키마,
`ops_` 접두사).

### 복구 절차

**증거의 출처 규칙(ALPHA-566).** occurrence 의 `ecs_task_arn`·`exit_code` 는 **그 태스크의 ECS
생애주기 이벤트**(`TaskSubmitted`·`TaskSucceeded`·`TaskFailed`·`TaskTimedOut`·`TaskStartFailed`)
에서만 읽는다. `TaskStateExited`·Choice·Pass·Parallel 의 details 는 실행 증거가 아니라 **상태
데이터 흐름**이라, 그 `output` 에 앞 페이즈의 누적 JSON(다른 스텝의 `TaskArn`·`ExitCode`)이 그대로
실려 온다. 이걸 안 가르면 남의 실행 결과를 주워 와 마지막 값으로 덮는다 — dev 실측에서 실패한
투자자 태스크 1개가 성공한 17개 작업을 전부 FAILED + `LEDGER_GAP` 으로 만들었다. **양방향**이라
순서가 반대면 성공 ARN 이 실패를 덮어 거짓 초록이 된다. 화이트리스트는 넓혀도(남의 ARN 유입)
좁혀도(ARN 결측 → 거짓 `LEDGER_GAP`) 틀리므로, 5종 전부가 테스트로 걸려 있다.

- **MISSED**(미실행): Reconciler 가 증거(SFN history·ECS)로 판정. "attempt 행 없음"만으로 단정하지
  않는다 — 원장 누락은 `LEDGER_GAP` 으로 backfill, ECS 생성 확인 불가는 `EVIDENCE_LOST`.
  **실행이 RUNNING 인 동안은 작업별 deadline 만으로 MISSED 를 찍지 않는다**(ALPHA-181) — deadline
  오프셋은 스테이지별 SLA 가 없어 잠정값이라 정상 실행 중에도 뒤 스테이지에서 자주 지난다.
  "아직 차례가 아니다"와 "아예 시작되지 않았다"는 다르고, `missed_at` 은 `COALESCE` 라 한 번
  찍히면 지워지지 않는다. 런 전체 hard deadline(6h)은 실행 중이어도 존중한다 — 그게 안전망이다.
- **미승격 raw 재처리**: 실패 런 raw 는 `normalize-<step> --input-run-id <실패 run_id>` 로 수동
  재처리(ADR-0030). 원장의 `ops_task_attempt`·`ops_reconciliation_issue` 가 어느 run 인지 알려준다.
- **비래치 MISSED**: 늦게 성공하면 `MISSED → FULFILLED`(missed_at 보존, MISSED 이슈 RESOLVED).

### 게이트 경계 — 이번 범위 밖 (ALPHA-452/453)

`data_status` 는 future gate 의 **정본이 아니다**(관측값). 완전성 결손은 `INCOMPLETE` 로 **기록만**
하고 downstream 을 차단하지 않는다(ADR-0030 — "관측만"). "데이터 없음 vs 움직임 없음"을 가르는
coverage 계측(**ALPHA-452**)·게이트 정책·UNEVALUABLE(**ALPHA-453·490**)이 gate 의 정본을 소유하며,
원장은 그 assessment 를 **참조/projection** 할 뿐이다. 이번 MVP 에 `gate_decision` 물리 컬럼을 두지
않은 이유다.

### 알려진 한계 (후속)

edge-review 4라운드로 실질 결함은 수렴했고, 아래는 **의도적으로 남긴** 경계다:

- **dep 완료 판정의 ECS fallback 미적용** — 선행 작업 완료를 SFN history 의 exit code 로만 본다.
  드물게 exit code 가 ECS 에만 있으면(SFN output 잘림) 선행을 미완으로 봐 downstream 을 MISSED
  대신 **BLOCKED** 로 마감한다 — 방향이 안전(BLOCKED 가 "선행 때문"을 더 정확히)하고, 매 dep 마다
  ECS 콜을 더하는 대가가 이 사소한 불일치보다 커서 두었다(Rule 2).
- **SFN 통합 실패(TaskFailed) 를 실패로 인정** — exit code 를 못 얻고 ECS 도 미확정일 때 SFN
  TaskFailed 를 FAILED 로 본다. ⚠️ runTask.sync 는 **컨테이너 exit≠0 도 TaskFailed 로 올린다**
  (cause JSON 의 `Containers[].ExitCode` 에 종료 코드가 실린다 — 2026-09-28 실측 exit 2). 그래서
  exit code 를 우선 조회하는 순서가 중요하고, SFN 브랜치 꼬리도 같은 cause 에서 exit code 를
  풀어 부분 성공(exit 2) 계속 조건에 쓴다(ALPHA-1113).
- **완전성(VALID)의 부분 배선** — ETF 3작업은 정적 `etf_map` snapshot과 `received_count`가
  연결됐다(ALPHA-611). 반면 가격·수급·공시처럼 런타임 holdings에서 종목 유니버스를 파생하는
  작업은 계획 시점의 독립 정본이 없어 여전히 `UNKNOWN`이다(false-VALID 를 내느니 UNKNOWN —
  스펙 §6). 그 작업들의 스냅샷 배선은 별도 범위다.
