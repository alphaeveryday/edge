# data-pipeline — 레이크 저장 계약

> 모듈 [README](../README.md) 의 상세 문서다. 레인·단계별 동작과 도입 경위가 함께 있다.

## 레이크 저장 계약

### Minute 내용 주소 후보·확정·소비 계약 (ALPHA-1060)

가격·iNAV·업종지수 writer의 `minute_artifact_format` 기본값은 `legacy`, dev 명시 설정은 `content_v2`다.
`DATA_PIPELINE_MINUTE_ARTIFACT_FORMAT=content_v2`를 명시하면 내용 주소 후보와
`schema_version=2` manifest를 쓰며, 수집 전에 확정 이력 스키마 존재를 검사한다.
가격 소비자(현재 창·시가), 가격/업종 롤업과 분석 엔진은 DB의 manifest URI·checksum을
함께 읽어 구/신형을 지원한다. dev 설정은 2026-09-08(#910)에 `content_v2` 로 바뀌었다.
후보가 저장됐다는 사실만으로 DB에 확정된 결과로 간주하지 않는다.

- artifact: `canonical/market_data/{dataset}/market=KR/session_date=D/session_id=S/window=HHMM/content=SHA/{bars|inav}.ndjson`.
  `dataset`은 `price_minute`, `etf_inav_minute`, `sector_index_minute`이며 iNAV만 `inav.ndjson`을 쓴다.
  SHA는 **저장 바이트**의 lowercase SHA-256이다. 같은 세대의 변경된 재수집은 다른 후보에
  저장하고, 같은 바이트를 다시 확정할 때는 artifact를 재사용할 수 있다.
- manifest: `operations_archive/minute_manifests/dataset={dataset}/market=KR/session_date=D/session_id=S/window=HHMM/generation=G/content=SHA/manifest.json`.
  SHA는 canonical JSON manifest 바이트의 해시다. 30일 만료 `canonical_run_artifacts`에 두지 않는다.
- v2 필드는 `schema_version`, `dataset`, `session_id`, `window_start`, `window_end`,
  `generation`, `units`, `artifact_key`, `artifact_checksum`으로 고정한다. 시간은 UTC `Z`로
  정규화한 1분 경계이며 경로 날짜·HHMM은 KST 기준이다. 네 unit 분류는 서로 배타적인 정렬
  목록이다. attempt·worker·실행시각·자기 URI를 넣지 않는다. 분류만 바뀌어도 manifest는 달라진다.
  validator는 형상과 artifact 주소 일치를 검사한다. reader는 DB의 dataset/session/window/generation/artifact checksum과 manifest를 대조하고
  manifest·artifact의 저장 바이트 해시를 각각 검증한다. 버전 필드 없는 기존 manifest도 읽는다. DB의 manifest URI와 checksum이 **둘 다 없는**
  legacy row에만 generation 경로 fallback을 허용한다. URI가 있는 manifest의 404·손상은
  실패하며 구형 파일로 우회하지 않는다. 롤업은 입력 검증이 실패하면 기존 5분 파일을 보존한다.
- 신형 writer는 기존 승자의 manifest와 artifact를 검증한 뒤 바이트·정규화된 unit 분류를
  비교한다. 동일하면 generation과 기존 URI/checksum을 유지한다(legacy 승자도 그대로 유지).
  실제 바이트/분류 변경만 +1이며 A→B→A는 1→2→3이다. 기존 승자 읽기 실패를 새 데이터로
  간주하지 않는다. 수집 attempt/실행시각 변화만으로 가격 job/outbox가 늘지 않는다.
- window·`minute_window_artifact_commit`·가격 job/outbox는 같은 PostgreSQL transaction이다.
  검증된 이전 승자와 새 승자의 이력을 함께 남기며, 동일 이력 재기록은 no-op이고 다른 좌표는
  전체 rollback이다. 최초 확정시각을 덮지 않는다. iNAV·업종은 가격 job/outbox를 만들지 않는다.
  PUT 후 DB 실패는 실제 lease 재claim으로 복구한다. 확정 창의 정정 테스트는 별도 reopen
  fixture를 쓰며, 자동 정정 스케줄을 추가한 것은 아니다.
- 신형 승자가 있는 세션에 legacy writer의 기동·쓰기는 거부된다. 활성화 전에 두 호환 reader
  배포·이력 migration·사전검사·구 writer drain을 확인해야 한다. 신형 세션을 legacy로
  되돌리는 대신 쓰기를 멈추고 content_v2 지원 이미지로 복귀한다. 호환 reader/스키마는 유지한다.
- `reconcile-minute-artifacts`는 가격·iNAV·업종 모두 DB 현재 승자와 확정 이력을 같은
  snapshot에서 읽고 manifest/artifact identity·checksum을 검증한다. 보고서는
  `committed_current`, `committed_history`, `uncommitted_candidate`, `legacy_unverified`,
  `integrity_error`, `scan_complete`를 구분한다. 과거 generation 숫자만으로 확정을 추측하지
  않는다. artifact만 저장된 후보도 발견하며, DB 참조 없는 legacy 객체는 보존한다.
- 기본 대사는 읽기 전용이다. ACTIVE 등 열린 세션 결과는 `provisional=true`이고 격리를
  거부한다. DRAINED/QC_RUNNING/FINALIZED/FAILED에서는 writer DB 확정이 닫혀 있으므로
  전체 검사 성공 후 미확정 내용 주소 후보만 논리 격리할 수 있다. 원본은 삭제/이동/덮어쓰지
  않는다. URI/checksum·사유·실행자를 담은 기록은
  `operations_archive/minute_artifact_quarantine/session_id=S/content=SHA/record.json`에
  불변 저장한다. 같은 인자 재실행은 같은 기록이며, 중간 기록 실패 후 재시도도 수렴한다.
  늦은 옛 writer PUT은 다음 스캔에서 발견될 수 있으므로 목록은 실행 시점의 snapshot이다.
- EOD는 세 레인의 대사를 결과에 포함한다. 유효한 미확정 후보 자체는 세션 확정을 막지 않지만,
  DB/LIST/객체 조회 실패 또는 무결성 오류는 성공으로 봉인하지 않는다. 뉴스·공시는 이 스캔의
  대상이 아니며 `supported=false`, `scan_complete=false`로 표시한다.
- `put_immutable`은 모든 기존 호출자에도 조건부 생성(`If-None-Match: *`)을 적용한다.
  충돌 시 GET 해시가 같은 객체만 재사용하며 다른 바이트는 오류다. 충돌 후 객체가 없으면
  최대 3회의 조건부 PUT/GET 뒤 일시 실패로 올린다. 권한·네트워크 오류는 그대로 전파한다.
  [S3 조건부 쓰기 계약](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)을 따른다.
- LocalStorage의 신규 생성은 같은 파일시스템의 완성된 임시 파일을 hard link로 원자적으로
  게시한다. 인스턴스·프로세스가 달라도 기존 객체를 덮지 않는다. `.storage-put-`로 시작하는
  파일명은 내부 임시 파일용으로 예약해 LIST에서 제외하며 정상·예외 종료 시 정리한다.
  기존 version을 교체하는 로컬 CAS의 보장 범위는 같은 인스턴스 내부로 유지된다.

대사 실행 예시(`src/`에서 실행, 운영 DB 접속용 `DATA_PIPELINE_DB__*`와 S3 storage 설정을
이미 주입한 환경). 대상 `SESSION_ID`는 원장에서 조회한 세션 ID다. 자격·설정·조회/격리 기록
실패는 exit 2, 무결성 위반(참조 객체 404 포함)은 exit 1, 완전한 대사/요청 격리만 exit 0이다.
S3 접근 거부·timeout은 무결성 판정과 구분하며 EOD도 QC_RUNNING에서 재시도할 수 있게 남긴다.

```bash
uv run python -m data_pipeline.run reconcile-minute-artifacts --session-id "$SESSION_ID"
uv run python -m data_pipeline.run reconcile-minute-artifacts --session-id "$SESSION_ID" \
  --quarantine --actor operator --reason "closed session reconciliation"
```

전환 사전검사는 **운영자 AWS 조회 자격**으로 실행한다. ECS/ECR·Scheduler GetSchedule 조회, IAM
`SimulatePrincipalPolicy`, S3 lifecycle/policy/encryption 조회와 DB 읽기 권한이 필요하며,
이 API 권한을 상주 writer 역할에 추가하지 않는다. `PIPELINE_DIGEST`와 `ANALYSIS_DIGEST`는
각각 CI/CD가 검증한 이미지의 `sha256:...` 값이다. 현재 실행 task의 digest뿐 아니라
minute-session(EOD reader)과 desired 0 서비스의 다음 기동 digest를 대조한다. 분석 서비스의
desired 0 배포는 이미지 push 이후의 새 deployment여야 한다. start·stop·업종 롤업 Scheduler의
실제 target revision/cluster/전체 command도 검증한 minute-session 정의와 일치해야 한다.
worker·consumer·planner의 필수 universe URI, dataset/source 인자를 서로 대조하며
universe 객체도 실제로 읽어 검증한다. 미검증 environment/role override는 전환을 막는다.
별도 family로 실행한 reader도 실제 명령으로 식별하며, 승인된 task revision·image·명령과
다른 reader는 전환을 막는다.

```bash
AWS_PROFILE=edge uv run python -m data_pipeline.minute.artifact_preflight \
  --cluster edge-dev-worker --service-prefix edge-dev-data-pipeline \
  --bucket edge-dev-pipeline-lake --pipeline-digest "$PIPELINE_DIGEST" \
  --analysis-digest "$ANALYSIS_DIGEST"
```

migration `202609071200`, 세 레인의 닫힌 최신 세션·미종료 과거 세션/claim 0,
기존 writer service desired/running 0 및 STOPPING 포함 writer·세션 시작 태스크 0, 호환 reader·실제/다음
기동 이미지, 필수 S3 권한과 보존 경로의 lifecycle 비충돌을 모두 확인해야
`activation_allowed=true`/exit 0이다. 미확인·진행 중·실패는 exit 2다. IAM 허용을
bucket policy/KMS의 추가 제약으로 오독하지 않도록 현재 dev의 bucket policy 없음/SSE-S3
계약도 대조한다. 다른 policy·암호화 구성은 별도 검증 전까지 통과시키지 않는다.
사전검사 PASS는 조회 시점 증거이며 CI/CD 성공을 대신하지 않는다. 활성화 직전에 재실행하고,
자동 Terraform apply가 세션을 가로지르지 않도록 다음 세션 시작 전 여유를 확보한다.


수집물은 단일 lake 버킷(예: dev `s3://edge-dev-pipeline-lake/`, 또는 local 스텁)에 쓴다.
경로 규약의 SSOT 는 [`lake/storage.py`](../src/data_pipeline/lake/storage.py)의 빌더다.

- **raw(뉴스)** — `raw/source=fmp/dataset=stock_news/market=…/published_date=…/run_id=…/` 에
  run_id 별 append(재현성). FMP 뉴스는 기존 계약대로 런 내 중복을 article_id 로 제거하고
  mentions 를 병합한다. 국내 BigKinds 뉴스는 같은 dataset·규약으로 `source=bigkinds`
  (`--source bigkinds`) 아래 쌓이며, BigKinds `resultList[]` row 를 전량 보존한다(런 내
  dedup 없음). `CONTENT` 도 BigKinds 응답 원본 필드 그대로 저장한다. **전량 보존은 받아온
  것을 안 버린다는 뜻이지 전부 받는다는 뜻이 아니다** — 무엇을 받을지는 카테고리 필터가
  정하고(경제 대분류 전체·검색어 없음, ALPHA-417 — 종목 매핑은 정규화 탐지 소관), 받은
  뒤로는 무변형 보존이다.
- **raw(가격)** — `raw/source=fmp/dataset=price_daily/market=…/ingest_date=…/run_id=…/` 에
  run_id 별 append. 파티션 키는 뉴스(published_date)와 달리 **ingest_date(수집일)** 다 —
  EOD 응답은 한 심볼이 여러 거래일을 한 번에 주므로 원본을 수집일 기준으로 보존한다.
  raw 는 받은 행을 **전부 보존**한다(중복 판정 안 함) — (market, ticker, trade_date)
  정체성 upsert·거래일별 분해는 후속 canonical/market_data(S006/S007) 소관.
  국내 KIS 일봉은 같은 dataset·규약으로 `source=kis`(`--source kis`) 아래 쌓인다.
- **raw(재무제표)** — `raw/source=fmp/dataset=financial_statements/market=…/ingest_date=…/run_id=…/` 에
  run_id 별 append. **가격과 동형(bronze 통일)** — 받은 행을 수집일 기준으로 **전부 보존**한다
  (중복 판정 안 함). 재무는 드물게·비동기로 공시돼 매일 재폴링하면 같은 스냅샷이 날마다 쌓이지만,
  중복 제거·정정(SCD)·point-in-time 판정은 후속 canonical(silver) MERGE 소관이다. 각 행에
  statement_type·period_type·filing_date 등이 그대로 보존돼 canonical 이 정체성 추출에 쓴다.
  국내 OpenDART 재무는 같은 dataset·규약으로 `source=dart`(`--source dart`) 아래 쌓이며,
  DART `list[]` 원본 행에 `our_ticker`·`stock_code`·`corp_code`·`bsns_year`·`reprt_code` 등
  수집 provenance 만 부착한다.
- **raw(공시)** — `raw/source=dart/dataset=disclosures/market=KR/ingest_date=…/run_id=…/` 에
  run_id 별 append. **가격·재무와 동형(bronze 통일)** — 공시목록(list.json) 행을 수집일 기준으로
  **전부 보존**한다(정정·정체성 판정 안 함). 단 한 순회 안의 **완전히 같은 행**은 소스가 접는다
  (페이지 이동 중복) — `list_rows_seen` 과 raw 행 수가 다를 수 있고 그 차이는 유실이 아니다. 재무제표(`fnlttSinglAcnt`, `dataset=financial_statements`)와
  **다른 API**다 — 공시는 개별 공시서류(공급계약·사업부문 등)를 다룬다. 메타 행은 `part-*.ndjson`
  에, 공시서류 원본 본문(document.xml)은 ndjson 에 못 섞는 바이너리(euc-kr HTML ZIP)라 같은 파티션
  아래 **`documents/{rcept_no}.zip` 로 받은 ZIP 을 무변형 저장**하고, 메타 행의 `document_raw_path`
  가 그 객체를 가리킨다(메타↔본문 링크). ⚠️ **메타는 유니버스 행 전량이지만 본문은 대상 유형만**
  이다(ALPHA-865) — 유형은 행마다 실리는 `is_target` 플래그이고, 비대상 행은 `document_raw_path`·
  `body_format` 이 명시적 None 이다(키 부재가 아니다). 예외 하나: `rcept_no` 가 결측·비문자열인
  행은 유형과 무관하게 뺀다 — 본문 객체 키도 canonical 병합 정체성도 rcept_no 라 **보존해도
  영영 못 쓰는 행**이고, 조용히 버리지 않고 `rows_dropped_malformed` 로 센다. 그래서 이 데이터셋은
  `records_saved`(보존 전량)와 `ops.records_out`(대상 건수 = `records_saved_target`)이 **의도적으로
  다른 첫 로그**다 — 유실(`failed_records`)이 대상 스코프라 산출도 같은 스코프여야 한다([ops-ledger.md](ops-ledger.md) 의 ops
  봉투 스코프 규칙). ⚠️ **그 키는 자기 run_id 파티션이 아닐 수 있다**
  (ALPHA-720): 같은 수집일(UTC 기준 ±1일)에 이미 받아 둔 본문은 다시 내려받지 않고 **기존 키를
  가리킨다**. 전량 대사와 증분 경계 페이지가 기존 행을 다시 내므로 이 장치가 같은 ZIP의 반복
  다운로드를 막는다. minute worker는 첫 조회의 본문 색인을 프로세스 생명 동안 되먹여 이후
  poll의 같은 S3 prefix LIST도 없앤다. 메타는 각 run이 실제 관측한 범위를 그대로 저장한다.
  ⚠️ 1분 레인(ALPHA-875)이 붙었던 동안은 그 "슬롯 수"가 하루 10 → **720 window** 라 이 존의
  하루 메타량이 ~70배였다(본문은 seen-map 이 막았다). 987 이 잠시 저녁 배치로 되돌렸고,
  **ALPHA-1068이 접수 원장 증분 poll과 주기 전량 대사를 넣어 1분 레인을 복원했다**.
  대가를 알고 택했던 형상이고(완전성 근거를 스스로
  없앨 수 없다) `input_run_id` 없는 `normalize-disclosure` 전체 백필만 그 커진 존을 훑는다.
  배치 수집은 exact 메타 key를
  `operations_archive/raw_run_manifests/dataset=disclosures/run_id=…/manifest.json`에 확정하고,
  두 공시 정제의 `input_run_id` 경로는 그 객체와 명시된 raw만 GET한다. 완료 bytes 손상·manifest
  결손·불완전·계보 불일치는 과거 raw 스캔으로 폴백하지 않고 실패한다. 재사용 건수는
  collection_log 의 `documents_reused` 로 드러나고, 본문 fetch 가 실패한 건은 객체가 없어
  다음 실행이 자동 재시도한다. list.json 이 안 주는 `source_url` 은 rcept_no 로 구성해
  붙인다. 정체성 병합·정정 판정·corp_code↔ticker bridge 는 후속 canonical 소관.
- **raw(ETF 구성종목)** — `raw/source={fmp|krx}/dataset=etf_holdings/market={US|KR}/ingest_date=…/run_id=…/`
  에 run_id 별 append. **가격·재무와 동형(bronze 통일)** — ETF holdings 는 스냅샷이라 매 실행이 현재
  구성종목 전량을 주고, 받은 행을 수집일 기준으로 **전부 보존**한다(정정·정체성 판정 안 함). 단 한 순회 안의 **완전히 같은 행**은 소스가 접는다
  (페이지 이동 중복) — `list_rows_seen` 과 raw 행 수가 다를 수 있고 그 차이는 유실이 아니다. 수집 대상은
  종목 유니버스가 아니라 ETF 목록(`etf.source.etf_map`·`krx_etf.source.etf_map`)이라 **1 ETF → N
  구성종목**으로 펼쳐지고, 각 행에 벤더 기준일(FMP `updatedAt`·KRX `trd_dd`)·`our_etf_id`·`market`·
  `fetched_at` 를 부착한다. 같은 스냅샷 중복 제거·기준일 SCD·point-in-time 판정은 후속 canonical(silver)
  소관. US=FMP(ALPHA-337)·KR=KRX 로그인 게이트 PDF(ALPHA-336) — 정규화는 `normalize-etf`(342·343).
- **raw(ETF iNAV)** — `raw/source=kis/dataset=etf_inav/market=KR/ingest_date=…/run_id=…/` 에
  run_id 별 append(ALPHA-555). 일별 NAV(`dataset=etf_nav`)와 **다른 축**이라 dataset 을 나눈다 —
  저건 거래일 grain 종가 확정 NAV, 이건 장중 시각 grain 추정 NAV 다. 응답이 **항상 30행 고정**이라
  조회 창 = `--interval-sec` × 30 이고, **소급 조회가 불가능**하다(`FID_INPUT_HOUR_1` 무시·`tr_cont`
  없음 — 실측). 그래서 폴링 창을 겹치게 잡아 같은 시각이 여러 run 에 중복 수집되는 것이 **정상**이며,
  겹침이 유일한 갭 방어 수단이라 raw 는 전부 보존하고 중복 제거는 canonical 소관이다.
  각 행에 `interval_sec`·`our_etf_id`·`market`·`kis_symbol`·`fetched_at` 를 부착한다.
  ⚠️ 괴리율 `dprt` 는 **퍼센트**다(실측 069500·2026-07-25: `stck_prpr/nav − 1` = **0.00114115**,
  ×100 = 0.11411 → 반올림 0.11 = `dprt`. 비율 가설이면 0.00 이라 안 맞는다. 교차 근거로
  `nav_vrss_prpr` 121.24 = `stck_prpr − nav`). 분석엔진 `sql_surface` 의 `v_nav.premium` 은
  **비율**이라 단위가 갈린다 — canonical 이 같은 이름을 쓰면 두 표면을 조인하는 쪽이 100배
  틀린 괴리를 본다. 그래서 canonical 레코드는 **`premium_pct`** 로 단위를 이름에 담는다
  (ALPHA-851 — `minute/inav_collect.record_of`: `unit_id`·`ts`·`nav`·`market_price`·
  `premium_pct`, 거기에 Worker 가 `source` 를 얹는다). `fetched_at` 는 **싣지 않는다** —
  canonical artifact 의 checksum 이 곧 세대 identity 라 실행 시각이 섞이면 값이 같은
  재실행마다 checksum 이 달라져 `ArtifactImmutabilityError` 가 난다(raw 는 반대로 붙인다).
  legacy 키는 `canonical/market_data/etf_inav_minute/market=KR/session_date=…/window=HHMM/
  generation=…/inav.ndjson` 이고, dev(content_v2)의 키는 위 "Minute 내용 주소 후보·확정·소비
  계약" 절을 따른다. 쓰는 주체는 상주 iNAV Worker 다(ALPHA-851·882 —
  `run inav-worker`, [ops-ledger.md](ops-ledger.md) 의 "상주 iNAV Worker" 항목).
  `ingest-raw-inav` 스텝은 **산출물이 로그**다(raw 는 무변형 보존이라 판단 재료가 로그뿐이다). 그래서
  로그 사전이 곧 계약이다 — ETF 마다 다음이 나온다:

  | 줄 | 레벨 | 뜻 |
  |---|---|---|
  | `벤더 지연` | INFO / **장중에 창 폭 초과면 WARN** | 수신시각 − 최신 `bsop_hour`. `구간=개장전\|장중\|마감후` 를 함께 본다 — 마감 후엔 창 폭의 십수 배가 정상이다 |
  | `개장 전 라벨` | INFO | 창이 개장 이전으로 뻗었다. **전일 값인지 미실측** — 오류가 아니라 관측이다 |
  | `시각 라벨 형식 이탈` | WARN | HHMMSS 6자리를 벗어난 행. 최신 판정에서 뺐다 |
  | `라벨 범위가 창 폭을 넘는다` | WARN | 한 창의 행이 아니다. ⚠️ **혼재만 잡는다** — 전일이 통째로 반복되면 범위가 정상이라 못 문다 |
  | `응답 행 수가 계약과 다르다` | WARN | 창 수치가 계약 행수(30) 기준이라 실제와 어긋난다 |
  | `표본 간격이 요청과 다르다` | WARN | 벤더가 `FID_HOUR_CLS_CODE` 를 무시했다. raw 의 `interval_sec` 이 거짓이 된다 |
  | `괴리 단위 드리프트 의심` | WARN | `dprt` 가 퍼센트 가설을 벗어났다. **그 종목 표본 한정** |
  | `괴리 단위 표본 부족` / `대조 불가` | WARN | 대조에 쓴 행이 모자라거나 0건. 빠진 필드명을 함께 남긴다 |

  지연이 창 폭에 가까우면 이 API 로 장중 실시간이 성립하지 않는다 — 1분 레인 편입 설계가
  그 수치에 걸려 있다. 단위 가드는 어긋날 때만 경고한다(정상은 조용 — 확정된 사실을 폴링마다
  되풀이하면 진짜 신호가 묻힌다). ⚠️ 허용 오차는 **못 조인다**: `dprt` 2자리 표기가 반올림인지
  절사인지 미실측이고, 절사면 조인 순간 정상 표본의 40%가 드리프트로 잡힌다.
  ⚠️ **지연의 부호로 전일 오염을 판정하지 않는다.** 응답에 날짜가 없어 이 콜만으로는 불가능
  하고 부호는 양방향으로 틀린다 — 라벨이 구간 끝이면 최신 행이 정상적으로 미래라 음수가
  오탐이고, 15:30 이후 실행에서는 전일 잔값이 **양수**로 위장한다(하필 창 폭과 비슷해 "실시간
  불가"의 강한 증거처럼 읽힌다). 로그는 관측한 사실만 남긴다.
- **raw(투자자 수급)** — 확정과 추정을 **다른 dataset 으로 가른다**(iNAV↔NAV 와 같은 이유):
  - EOD 확정 — `raw/source=kis/dataset=investor_flow_daily/market=KR/ingest_date=…/run_id=…/`
    (ALPHA-482). 거래일 grain 확정 순매수. 자연키 날짜는 행의 `stck_bsop_date` 다.
  - 장중 추정 — `raw/source=kis/dataset=investor_flow_intraday/market=KR/ingest_date=…/run_id=…/`
    (ALPHA-767). 그날 슬롯 grain 가집계(`*_fake_*`) 추정. **응답에 날짜 필드가 없어**
    `asof_date`(수집 시각의 KST 날짜)를 provenance 로 부착하는 것이 필수다 — 없으면 canonical
    이 어느 거래일 스냅샷인지 복원할 수 없다(KRX holdings 의 `trd_dd` 와 같은 형태).
    각 행에 `our_ticker`·`market`·`kis_symbol`·`asof_date`·`fetched_at` 를 붙인다.
    슬롯 응답이 그날 것을 누적해 오므로 슬롯 간 중복은 **정상**이고 정리는 canonical 소관이다.
- **canonical(EOD 투자자 수급, 정제 Step2)** — `normalize-investor`는 현재 input run에서 gate를
  통과한 모든 `(market,ticker,trade_date)` winner를 직접 parquet key·SHA-256과 함께
  `dataset=investor_flow_daily` canonical run manifest에 기록한다(ALPHA-1040). 같은 값 재확정도
  winner이고 같은 vendor 중복은 **그 거래일 정규장 마감(15:30 KST) 뒤 가장 이른 수집분**이 이기며
  (장중 수집분은 최후순위 — ALPHA-1107: 다음 날 재수집분은 시간외 체결이 더해진 값이라 최신 승이면
  D일 값이 매일 덮인다), 교차 vendor 충돌은 winner에서 제외해
  quality와 exit 2에 남긴다. canonical→quality→completed manifest 순으로 공개하고, 빈 입력도
  유효한 빈 completed manifest다. 저장·무결성 실패는 incomplete manifest와 exit 1로 fail-closed한다.
- **적재(EOD 투자자 수급)** — 정상 `load-etf-flow --input-run-id`는 같은 run의 completed
  manifest와 그 direct parquet만 GET하고 key·SHA·정렬/고유 winner·파티션 정체성을 검증한다
  (ALPHA-1041). 결손·손상 때 LIST/fullscan으로 넓히지 않으며 빈 manifest는 canonical LIST/GET 없이
  성공한다. 물리 parquet 행과 논리 winner 처리량을 분리하고, 개별 DB 행 실패는 savepoint로
  격리해 다른 winner를 commit하되 exit 2와 최종 SFN Failed를 보존한다. 날짜 창과 `--all`은
  명시 복구 전용이다. 마트 행은 순매수 값이 바뀌거나 **`available_at` 이 앞당겨질 때만** 갱신한다 —
  옛 raw 재정제로 canonical 승자가 D일 수집분으로 돌아오면 값이 같아도 시각이 복구된다(ALPHA-1107).
- **canonical(장중 투자자 추정, 정제 Step2)** — `canonical/market_data/investor_flow_intraday/
  market=…/trade_date=…/part-*.parquet` 에 게이트 통과 행을 **(market,ticker,trade_date,asof_slot)
  키로 멱등 병합**(ALPHA-768). EOD 확정(`investor_flow_daily`)과 파티션 축은 같지만 **행 키가
  한 축 많다** — 하루 4~5 슬롯이 한 종목·한 날짜에 공존하므로 ticker 단독으로 병합하면 마지막
  슬롯이 앞을 덮어 장중 추이가 사라진다. 거래일은 raw 의 `asof_date`(수집이 붙인 provenance)가
  주고, 같은 슬롯 재관측은 최신 fetched_at 이 이긴다.
  `normalize-investor-estimate`는 매 실행
  `operations_archive/canonical_run_manifests/dataset=investor_flow_intraday/run_id=…/manifest.json`
  에 직접 parquet 키·그 바이트의 SHA-256과 이번 실행에서 게이트를 통과한 모든
  `(ticker,asof_slot)` winner를 기록한다(ALPHA-1035). 값이 바뀐 행만이 아니라 같은 값
  재확정도 포함하고 논리 키는 중복 제거한다. SHA-256은 뒤 normalize가 같은 canonical 키를
  덮어써도 consumer가 앞 run_id에 뒤 run 값을 붙이지 않게 fail-closed 하는 근거다. 행
  실패는 다른 winner의 canonical·manifest 기록을 막지 않지만 quality log와 exit 2에 남으며,
  raw 목록/읽기 또는 canonical·quality·manifest 저장 실패는 exit 1과
  `canonical_written=false`로 fail-closed 한다. `load-investor-intraday` 정상 경로(ALPHA-1036)는
  같은 run의 manifest를 직접 GET해 명시된 parquet key와 winner만 적재하고 SHA-256이 producer가
  확정한 바이트와 같은지 검증한다. 누적 parquet의
  과거 행은 `physical_rows_read`, 현재 winner는 `logical_rows_read`로 분리한다. manifest가 없거나
  손상됐거나 hash가 달라졌거나 winner가 canonical에 없으면 LIST·전체 스캔으로 넓히지 않고
  exit 1로 실패한다.
  개별 DB 행 오류는 savepoint로 격리해 다른 winner를 commit하되 quality와 exit 2에 남긴다.
  정제 exit 2도 성공 winner 적재까지 진행하지만 장중 수급 SFN의 최종 상태는 Failed로 닫힌다.
- **수집 로그** — `operations_archive/collection_logs/source=…/dataset=…/started_date=…/run_id=…/log.json`
  (`dataset=`로 갈라 같은 벤더의 뉴스·가격·재무 로그가 같은 run_id 를 공유해도 안 덮어쓴다)
- **canonical(가격, 정제 Step2)** — `canonical/market_data/price_daily/market=…/trade_date=…/part-*.parquet`
  에 게이트 통과 행을 **(market,ticker,trade_date) 키로 멱등 병합**. raw 와 달리 run_id·source_vendor
  파티션이 없다(멱등 — 같은 raw 를 몇 번 정제해도 결과 동일). market·trade_date 가 파티션, ticker 는
  파티션 내 행 키다. 같은 벤더 재적재는 KR 이면 **최근 거래일 마감 후 수집분과 OHLC 가 같은 마감 후
  수집분 중 가장 이른 것**이 이긴다(ALPHA-1120: 매일 5일 창을 다시 받는데 영업일 재수집은 시간외
  체결로 거래량만 늘리고, 평일 휴장일 런은 직전 거래일 종가를 공식 종가가 아닌 값으로 돌려주며,
  벤더 정정은 OHLC 를 바꾼다 — 그래서 평상시는 D일 수집분, 휴장일 수집분은 제외, 정정은 정정분이
  이긴다. 휴장일 판정은 `OPS_KR_HOLIDAYS`, 런마다 quality_log `kr_holidays_loaded` 로 남는다).
  비KR 은 최신 fetched_at 이 이긴다. **벤더 교차 같은 키 충돌은
  fail-loud**(둘 다 제외 + quality_log·비0 종료 — USD 를 KRW 로 태깅하는 통화 오염 방지). 통화는
  market 별 태깅만 하고 FX 환산하지 않는다. `load-price-daily` 마트는 값이 바뀌거나 `available_at` 이
  앞당겨질 때만 갱신한다(수급 적재와 같은 규약). 종가·수정종가·거래량과 함께 시가·고가·저가도
  싣는다(ALPHA-1148) — 시·고·저가 비어 있던 행을 채우기만 하는 갱신은 `available_at` 을 뒤로
  밀지 않고, 채워진 뒤의 정정은 값과 시각을 함께 옮긴다. 다른 적재기가 `price_basis` 를 채운
  행을 덮을 때는 그 값도 비운다.
- **canonical(뉴스, 정제 Step2)** — `canonical/news/news_articles/language={ko|en}/published_date=…/part-*.parquet`
  에 게이트 통과 행을 **article_id 키로 멱등 병합**. **정체성 `article_id = url_hash(원문 URL)`**
  (FMP `url`/BigKinds `PROVIDER_LINK_PAGE`)은 **소스 무관**이라 canonical 이 소스를 흡수한 **통합
  구조**가 된다 — `source_vendor` 는 파티션이 아니라 **컬럼**(provenance). 파티션은 **`language`
  (벤더 고정 파생: bigkinds=ko·fmp=en)→published_date 2단**(다운스트림 언어모델이 언어별로
  프루닝/분기하게 함, ALPHA-352). 같은 언어 안에선 같은 원문 URL 이면 벤더 불문 한 행으로 병합
  (통합 dedup)하되, **언어 파티션이 다르면 같은 URL 이라도 병합 안 함**(교차언어 dedup 은 다운스트림
  소관); URL 없으면 정체성은 BigKinds `NEWS_ID`→`title|date` 폴백. run_id 없음(멱등). 같은 article_id
  재적재는 최신 fetched_at 이 메타 대표를 이기되 **mentions 는 union**(종목↔기사 링크 보존). 다른
  article_id 가 같은 정규화 제목이면 **exact 병합 없이 duplicate_signal 로깅만**(URL 충돌은 곧 같은
  id 라 자동 병합). fuzzy 클러스터는 다운스트림 news_dedup_cluster 소관. mentions 는 JSON 문자열로 보존.
  **종목 매핑은 정규화의 일이다(ALPHA-416)**: BigKinds 행의 mentions 는 canonical ETF holdings
  최신 스냅샷(KR) **중 유니버스 뿌리(`krx_etf.source.etf_map`) 안 ETF 의 구성종목** 종목명
  인덱스로 제목+리드에서 substring 탐지해 합성한다(구 raw 의
  `our_ticker` provenance 와 union — 이행기 호환). 이름 비교는 **NFKC 정규화 후 substring**
  (인덱스·기사 텍스트 양쪽 — 저장소 관례). **동명이(같은 이름, 다른 ticker)는 어느 쪽도 고르지
  않고 인덱스에서 뺀다**(ALPHA-448) — 이름을 키로 덮어쓰면 parquet 나열 순서가 승자를 정해
  mention 이 비결정적으로 틀린다. 유니버스가 바뀌면 전체 백필 재정규화로 과거 기사에
  소급되고, 탐지 계측(`detected_name_counts`)·제외된 동명이(`mention_index_ambiguous_names`)·
  인덱스 상태는 quality_log 에 남는다.
  FMP 는 ingest 병합 mentions[] 그대로(영문 기사라 한글 이름 탐지 무의미).
  `lead_text` 는 벤더 리드(BigKinds `CONTENT` 200~256자 스니펫·FMP `text`)를 자르지 않고 통과시킨
  것으로, 태깅 입력이다(결측은 NULL — 게이트 대상 아님. **공백뿐이어도 NULL 로 접는다**,
  ALPHA-860 — 그래서 canonical `lead_text` 는 결코 빈 문자열이 아니다). 본문 전문 크롤은 범위 밖이다.
  `normalize-news` 는 현재 실행이 실제로 쓴 파티션의 `language`·`published_date`·직접 parquet 키와
  그 파티션에서 이번 입력이 통과시킨 `article_id`만
  `operations_archive/canonical_run_manifests/dataset=news_articles/run_id=…/manifest.json` 에
  남긴다(ALPHA-1030). 같은 run 재시도는 먼저 `canonical_written=false`로 이전 완료 표식을
  무효화하고, canonical과 quality log가 모두 성공한 뒤에만 `true`로 교체한다. 입력 0건은 빈
  `canonical_partitions`를 가진 유효 manifest다. `LoadDocuments`는 이 직접 키와 현재 논리 ID만
  소비하며(ALPHA-1031), `TagNews`도 같은 범위를 소비한다(ALPHA-1032).
- **feature(뉴스 assertion, 태깅 Step3)** — `feature/news/assertions/language=ko/published_date=…/part-*.parquet`
  에 태깅 결과를 **article_id 키로 멱등 병합**(입력 canonical 과 같은 파티션 축이라 한 canonical
  파티션이 한 feature 파티션에 대응 — 날짜창 프루닝이 곧 비용 통제).
  **이 파티션에는 writer 가 둘이다(ALPHA-900)** — 배치 `tag-news` 가 part 파일을 통째로 되쓰고,
  1분 뉴스 Consumer 가 같은 파티션 아래 `minute/{article_id}.{input_fingerprint}.parquet` 로
  기사당 미러를 남긴다. 그래야 두 레인의 LLM 장부가 서로를 본다 — 그 전에는 배치가 이 날짜축만,
  장중이 job 축(`feature/news_extraction/job=…`)만 보고 있어 **같은 기사를 반드시 두 번 태웠다**.
  미러 구역은 **다음 배치 런까지의 임시 자리**다: `tag-news` 가 읽어 part 파일에 병합한 뒤
  지우고(`minute_mirrors_absorbed` 로 계측), **소비자(`load-assertions`)는 흡수 전 미러를 아예
  안 읽는다**(`minute_mirrors_unabsorbed` — 유실이 아니라 대기다). 흡수 전 조각을 바로 읽으면
  `tag-news` 가 거는 mentions 게이트를 통째로 우회하기 때문이다: 배치는 유니버스 무관 기사를
  일부러 태깅에서 빼는데(ALPHA-416) 1분 레인에는 아직 그 게이트가 없어(ALPHA-690) 그런 기사의
  미러가 오고, `load-assertions` 는 mentions 를 안 본다. 그래서 `tag-news` 가 흡수 시점에
  거르고(`minute_mirrors_dropped_no_mention`), 소비는 흡수된 part 파일만 본다. 지연은 없다 —
  SFN 이 `TagNews` 뒤에 `LoadAssertions` 를 돌리므로 같은 런에서 흡수분이 실린다.
  ⚠️ 그래서 **흡수는 canonical 이 없는 날짜에도 닿아야 한다** — 기사 정본은 PG 이고 canonical
  은 다음 `normalize-news` 에 오므로 장중만 본 기사의 발행일이 아직 canonical 에 없을 수 있다.
  `tag-news`는 manifest 날짜와 KST 전일·당일의 정확한 미러 prefix를 함께 조회한다.
  ⚠️ canonical 에 **아예 없는** 기사(장중만 본 기사)의 미러는 거르지 않는다 — mentions 를 판정할
  근거가 없는 것이지 무관한 게 아니다. 미러 키에 입력 지문이 들어가는
  것은 정정 때문이다 — `article_id` 만 쓰면 배치가 읽고 지우는 사이의 정정 판정이 같은 키를
  덮은 뒤 곧바로 삭제된다. 오래된 backfill 미러는 명시적 날짜 또는 `--all` 복구가 정리한다.
  **canonical 이 아니라 feature
  인 이유**: 여기 값은 벤더 원본의 결정론적 정규화가 아니라 **LLM 추론 결과**라 재실행이 값을 바꿀
  수 있고 호출마다 돈이 든다 — raw 에서 언제든 무료로 재생성되는 canonical 과 라이프사이클이 다르다.
  그래서 **한 번 만든 건 다시 만들지 않는다**(`tagger_version`·`ontology_version` 이 바뀔 때만 재태깅;
  단 `llm_error` 는 '물어보지도 못했다'는 뜻이라 다음 런이 재시도한다 — 일시 장애가 기사를 영구히
  누락시키지 않게). **행은 기사 1건 = 1행**이다(assertion 1건=1행이 아니다) — 사건 0건인 기사(시황·
  논평 등 다수)가 행을 잃으면 '태깅했는데 사건이 없었다'와 '태깅한 적 없다'가 구분되지 않는다.
  `assertions`·`reasons` 는 JSON 문자열(canonical 뉴스 mentions 와 같은 관례), `status` 는 기사별로
  무슨 일이 있었는지(ok·no_title·llm_error·llm_unparseable·bad_doc_class). `entity_id` 는 NULL —
  엔티티 해소는 entity 마스터(RDB)를 읽어야 해 적재(ALPHA-190)와 같은 소관이고 `text` 가 그 입력이다.
- **canonical(공시 공급계약, 정제 Step2)** — `canonical/disclosures/supply_contract_fact/report_date=…/part-*.parquet`
  에 게이트 통과 fact 를 **rcept_no(14자리 접수번호=문서키) 키로 멱등 병합**. raw 와 달리 run_id·
  source_vendor 파티션이 없다(멱등). 파티션은 `report_date`(rcept_dt, 공시 접수일) 하나, rcept_no 는
  파티션 내 행 키다. 같은 rcept_no 재적재(정정본 재수집)는 최신 fetched_at 우선. `source_vendor`(dart)는
  현재 KR·DART 단독이라 컬럼(provenance)이지 파티션이 아니다. 파서 출력(계약상대방·금액·매출액대비·
  계약기간·confidence)에 메타 provenance(corp_code·ticker·corp_name·source_url)를 조인한다. graph
  투영·theme 링킹·event 는 범위 밖(analysis-engine 소관).
- **canonical(공시 사업부문, 정제 Step2)** — `canonical/disclosures/business_segment_fact/report_date=…/part-*.parquet`
  에 게이트 통과 fact 를 **(rcept_no, segment_ordinal) 키로 멱등 병합**. 공급계약과 동형(멱등·report_date
  파티션·source_vendor 컬럼)이나 **1 문서 → N 부문**(fan-out)이라 행키에 파스 순서 `segment_ordinal` 을
  둔다 — `segment_name` 은 한 문서에서 유일하지 않다(제품/용역 sub-row 로 같은 부문 반복). 파서(4-전략
  추출)가 뽑은 `revenue_krw·revenue_share_pct·share_basis·period` 에 메타 provenance 를 조인한다.
- **canonical(ETF 구성종목, 정제 Step2)** — `canonical/holdings/etf_holdings/market=…/as_of_date=…/part-*.parquet`
  에 검증된 전량 수집본을 **ETF 단위로 교체**한다(ALPHA-1059). raw의 source/run 경계를 유지하고
  수집 로그의 성공·정체성·수집/저장/raw 건수 일치와 필수 행 검증을 확인한다. 수집기는 holdings
  로그의 `raw_sha256`에 실제 저장한 객체별 SHA-256을 남기고 정규화는 읽은 바이트와 대조한다.
  같은 run 재시도의 새 raw가 이전 성공 로그와 결합되는 것을 막기 위한 필수 증거다.
  해시가 없는 과거 로그도 자동 전량 교체 근거로 사용하지 않으며, 재수집 또는 별도 운영 대조가
  필요하다. 부분 수집·필수 행
  탈락·혼합 기준일은 해당 수집본 전체 반영을 보류하고 기존 canonical과 정상본 포인터를 보존한다.
  raw와 달리 run_id·source_vendor 파티션은 없다. market·as_of_date가 파티션이며
  (etf_id, constituent_ticker)가 파티션 내 행 키다
  (1 ETF → N 구성종목 fan-out). 기준일 as_of_date 는 벤더가 준다 — FMP `updatedAt`(datetime→date)·
  KRX `trd_dd`(우리가 지정). **market-스코프 파티션이라 한 파티션엔 한 벤더만**(US=fmp·KR=krx disjoint)
  → 입력 raw의 source/market 조합을 검증한다. 같은 ETF·기준일의 최신 전량본을 fetched_at으로
  선택하며 축소 정정에서 빠진 구성종목은 남기지 않는다. 같은 시각의 서로 다른 raw 내용은 충돌이다.
  스코프 재실행은 최신본을 과거 수집본으로 덮지 않는다. `weight_pct·shares·market_value`는 참고 필드(KRX 해외기초는 대시(-)→null), `source_vendor`(fmp|krx)는 컬럼(provenance).
  KRX `SECUGRP_ID/MKT_ID`의 실측 조합은 `constituent_asset_type`(`EQUITY|CASH|OPTION|UNKNOWN`)
  으로 보존한다. holdings·instrument 적재기는 주식만 적재하고 현금·옵션은
  `skipped_unsupported_asset`과 유형별 수로
  계측하되 유실에는 넣지 않는다. 미지 유형은 `skipped_unknown_asset_type`으로 유실에 남긴다
  (ALPHA-1017). `load-etf-holdings`는 정상 제외 합계를 `ops.unsupported_records`에도 남겨 실행
  이력에서 적재·지원 제외·유실을 분리한다(ALPHA-1020). 현금 행은 적재하지 않지만 그 비중 합은
  `etf_holding_snapshot_status.cash_weight_ratio`에 남긴다. 현금 행이 없거나 비중을 모르는 현금 행이
  있으면 NULL이다(ALPHA-1244). 음수 현금이면 주식만의 합이 1을 넘는다.
  파티션은 `part-00000.parquet`로 조건부 교체한 뒤 나머지 직접 자식 part만 지운다.
  구형 part 삭제 실패 뒤 같은·과거 런 재시도와 다른 ETF 수집에서도 이미 교체한 최신
  target에 오래된 구성종목을 다시 합치지 않고 정리를 재시도한다.
  중첩 보관 객체와 raw 입력은 삭제하지 않는다. holdings 적재기와 canonical 기반 트리거는
  정규화와 같은 직접 자식 `part-*.parquet`만 읽어 보관본의 삭제 종목이 재유입되지 않게 한다.
  🔴 **이 파티션의 etf_id 집합은 분석 유니버스가 아니다** — 파티션은 지워지지 않아 config 에서 뺀
  ETF 의 옛 행이 남고, 참조 계열(명부만 필요한 ETF)도 섞여 들어온다. 읽는 쪽은 유니버스 뿌리
  (`krx_etf.source.etf_map` 키)로 한 번 거른다 — `ingest-price-raw`·`load-etf-holdings`·
  `load-price-triggers`·`normalize-news`·`load-instruments` 다섯이 같은 정본
  (`_krx_expected_etfs`)을 `expected_etfs` 인자로 받는다. 안 거르면 마스터에 없는 ETF 가 매 런
  `failed_records` 로 잡혀 원장이 **영구 INCOMPLETE** 가 된다.
  `normalize-etf` 는 실제로 갱신한 파티션을
  `operations_archive/canonical_run_manifests/dataset=etf_holdings/run_id=…/manifest.json` 에도
  남긴다. 부분 수집으로 반영할 대상이 없으면 빈 파티션 목록을 남겨, 하류가 계속 실행돼도
  기존 DB status/data_version을 재스탬프하지 않는다.
  정규 `load-etf-holdings --input-run-id <normalize-run>` 은 이 직접 키를 GET 해 그
  파티션만 읽는다 — 과거 전체 스캔은 `--all`, 과거 일부 복구는 `--input-run-id` 또는
  `--from/--to` 를 명시한 운영 경로뿐이다. 범위 없는 호출은 거부한다(ALPHA-1011).
  시장 SFN과 Ops 원장은 `normalize-etf`·`normalize-etf-profile` exit 2를 실패 attempt이자
  충족된 하류 의존으로 기록한다. 이 경우 보존된 last-good 입력으로 `LoadInstruments`까지
  진행하지만 마지막 strict gate는 전체 실행을 실패로 닫고, exit 1은 즉시 하류를 막는다
  (ALPHA-1047 compatibility phase).
  세 KR 마스터 생산자(`normalize-etf`·`normalize-etf-profile`·`normalize-instrument-profile`)는
  `--input-run-id`로 요청된 raw와 그 collection log가 전량 성공한 경우에만 데이터셋별
  `operations_archive/latest_good_partition_pointers/dataset=…/market=KR/pointer.json`을 CAS로
  전진시킨다. alias는 mutable canonical이 아니라 run-scoped 불변 Parquet artifact와 SHA-256을
  가리킨다. 행/수집 부분 실패(exit 2), 빈 런, 과거 런은 기존 pointer를 보존하며, 범위 없는 복구
  정제는 shared canonical만 수렴시키고 pointer를 전진시키지 않는다. ETF 전체 raw 복구는
  검증된 최신 전량본으로 기존 union/partial 오염도 교체한다. 방문한 파티션의 기존 ETF에 전량
  raw 근거가 없거나 raw 목록 조회 중 canonical이 바뀌면 실패한다. 완전본 후보가 전혀 없는
  과거 파티션은 보존하며 전량 검증된 결과로 취급하지 않는다. 복구 후 적재도 해당 normalize
  manifest의 `--input-run-id` 범위로 수행한다. 별도 전체 스캔인 로더 `--all`은 이 검증 범위를
  벗어난 과거 파티션도 읽으므로 검증된 복구 결과를 적재하는 대용으로 쓰지 않는다.
  instrument profile의 수집·정제는
  계속 수동 전용이다(ALPHA-1047 producer phase).
  `load-instruments --latest-good`은 서로 다른 source run을 가리키는 세 pointer를 함께 허용하고,
  pointer bytes/ETag·partition·artifact SHA/물리·논리 행 수를 품질 로그에 남긴다. pointer 결손·손상·
  dangling artifact·정체성 불일치는 shared canonical fallback 없이 exit 1이다(ALPHA-1048).
- **품질 로그(정제 Step2)** — `operations_archive/data_quality_logs/dataset=…/checked_date=…/run_id=…/log.json`
  에 검증 실행당 1건. 몇 건 읽고/통과/탈락·canonical 적재했는지와 **탈락 사유**(OHLCV 정합성 위반·결측·
  비수치 등)·벤더 교차 충돌을 남긴다 — 잘못된 가격을 조용히 버리지 않는다(Rule 12). 뉴스(`dataset=
  news_articles`)도 같은 규약으로 남기되 blocking 탈락 사유(제목 결측·발행시각 파싱 불가/범위 밖)와
  non-blocking 경고(url·publisher 결측)를 구분하고, canonical 적재 결과·근접중복 신호(duplicate_signals)를
  함께 기록한다. canonical 은 멱등이라 run_id 가 없지만,
  '이 검증 실행이 무엇을 걸렀나'는 실행 단위 감사라 run_id 로 가른다(수집 로그와 분리).
- 백엔드는 `[storage]` 설정으로 고른다. 기본 `local`(루트 `./.lake`), 배포는
  `DATA_PIPELINE_STORAGE__BACKEND=s3` + `DATA_PIPELINE_STORAGE__BUCKET=…` 로 전환.
