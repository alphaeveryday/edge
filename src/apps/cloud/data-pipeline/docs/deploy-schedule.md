# data-pipeline — 배포·스케줄 실행

> 모듈 [README](../README.md) 의 상세 문서다. 레인·단계별 동작과 도입 경위가 함께 있다.

## 배포/스케줄 실행

dev 배포 이미지는 `src/apps/cloud/data-pipeline/Dockerfile` 로 빌드해 기존 `edge/pipeline`
ECR repository 에 `:${git_sha}` 와 `:data-pipeline-latest` 태그로 push 한다(`deploy-data-pipeline.yml`).

Terraform 의 `modules/data-pipeline` 은 ECS task definition 과 Step Functions state machine 을
만든다. 상태머신(`edge-dev-data-pipeline`)은 **raw → normalize → feature 3페이즈**를
한 실행에서 완주한다(ALPHA-355·386·408, [ADR-0028](../../../../../docs/adr/0028-unified-pipeline-sfn.md);
analyze 페이즈는 ALPHA-806 에서 상주 소비자로 옮겨 나갔다) —
각 페이즈는 잡을 병렬 ECS RunTask 로 돌리고, **앞 페이즈가 전량 성공해야** 다음으로 넘어간다 —
단 **raw 는 예외**다(ALPHA-460): 소스 하나가 실패해도 무관한 소스의 정제·분석은 계속 돈다.
정제가 빈 입력을 정상 성공으로 처리하므로 있는 만큼 처리하면 되기 때문이다. 대신 실패 직후
SNS 알림이 나가고, 그 런은 끝에서 FAILED 로 마감된다(막지 않되 조용하지도 않게).
모든 브랜치에 같은 `--run-id` 를 넘겨 raw partition·canonical·collection_log 를 같은 실행 단위로
묶는다. 3페이즈는 같은 브랜치 빌더가 잡 목록만 바꿔 찍어낸다(구조 동일).

뉴스(지식) 레인은 별도 상태머신 `edge-dev-data-pipeline-news`(ALPHA-553)로 **분리 완료**다 — 시장
레인과 자연 주기가 달라(시장=장마감 EOD, 뉴스=종일 유입) 자체 주기(**주 7일** 00:10·08:10
KST, dev ENABLED 컷오버 — 요일은 ALPHA-874 로 넓혔고 슬롯은 ALPHA-893 이 3개→2개로 줄였다)로 `news raw → NormalizeNews → [TagNews·LoadDocuments] → LoadAssertions →
AssembleEvents` 를 돌린다. 같은 브랜치 빌더를 재사용하고(news_* 페이즈), `instrument` 마스터는
시장 SFN 이 단일 writer 로 쓰고 뉴스 SFN 은 읽기 전용 공유한다. PR2(ALPHA-553)로 시장 SFN 에서
뉴스 스텝(수집·정제·태깅·문서 + 직렬 LoadAssertions·AssembleEvents)이 제거됐다. ⚠️ 여기 있던
"시장 analyze 가 뉴스 SFN 의 이전 런이 조립해 둔 event 를 소비한다"는 서술은 **ALPHA-806 부터
사실이 아니다** — 그 티켓이 시장 SFN 에서 analyze 페이즈를 걷어냈고 설명은 분봉 트리거 큐를
소비하는 상주 서비스만 만든다. 그래서 ALPHA-893 이 오후 슬롯을 내려도 잃는 소비자가 없다. 뉴스 레인은
운영 원장에 **자체 `pipeline_type`(`news`)·하루 2슬롯 기대로 편입돼 있다**(ALPHA-591) — 뉴스
스케줄도 daily 와 같이 Planner(plan-run, `OPS_PIPELINE_TYPE=news`) 경유로 SFN 을 시작한다
(카탈로그 절 참고).

공시 레인도 같은 형태로 분리됐다가 **한 번 더 옮겨 갔다** —
`edge-dev-data-pipeline-disclosure`(ALPHA-722)가 세워져
`CollectDartDisclosure → [NormalizeDisclosure·NormalizeDisclosureSegment] →
LoadDisclosure` 를 돌았고(부분집합 필터 재사용, 새 state 정의 0개 — 체인은 Feature 의
LoadDisclosure 에서 닫힌다. 별도 이벤트 조립 state 는 **없다**),
시장 SFN 에서 공시 체인이 빠졌다(15:40 런은 공시를 돌리지 않는다). 875 가 공시를 1분
세션으로 넘겼다가 987이 저녁 배치로 되돌렸고, ALPHA-1068이 증분 1분 수집을 복원했다.
**장중은 09:00~15:30 390창, 종료는 16:10, 마감 보충 배치는 평일 19:30이다**(ALPHA-1071).
ALPHA-1073의 배치 카탈로그 복원 앱을 선행 배포한 뒤 ALPHA-1072·1074에서 격자와 스케줄을
함께 전환한다. 기존 720창 세션·워커·배치가 종료된 비거래 경계에서 앱 이미지와 Terraform
적용을 모두 확인하고 다음 거래일 start를 받는다. stop 실패 시 서비스를 내리지 않으므로
기존 알림에 따라 잔류 워커를 정리한 뒤 배치를 실행한다. 복구는 정상 drain 뒤 minute
source group을 비우고 19:30 배치를 유지한다.

주말 활성화 직후에는 Reconciler가 활성화 전 금요일의 새 시각도 기대해 `PLANNER_MISSING`을
열 수 있다. 이번 전환의 대상은 `planner_missing:disclosure:2026-09-11T19:30` 하나다.
첫 활성 슬롯인 2026-09-14 19:30의 실제 run과 4개 expected task를 확인하고, 감시 대상이
그 월요일 슬롯으로 이동한 뒤에만 기존 `Ledger.resolve_issue`로 이 금요일 이슈를 닫는다
(`resolution_reason="schedule_not_active_at_slot"`, `resolution_source="manual:ALPHA-1074"`).
금요일 당시 스케줄이 DISABLED였다는 전환 전 조회와 실제 apply 시각을 이슈에 함께 기록한다.
실제 예정된 월요일 이후 누락은 이 정리 대상이 아니며, 금요일 run이나 성공 작업을 꾸며내지
않는다. 첫 슬롯 전에는 이슈를 닫아도 다음 reconcile이 다시 열므로 기다린다. 첫 거래일
390창·16:10 drain·19:30 배치 및 이 경보 정리 확인까지 ALPHA-1074를 완료 처리하지 않는다.
장중 워커는 직접 함수를 호출해 minute 원장에 기록하고 배치 CLI는 ops 원장에 기록한다.
`plan-run`의 빈 카탈로그 거부는 유지하며, 복원된 공시 배치는 자기 4작업을 계획한다.
배치의 완료 경계는 typed fact 적재다. 기존 장중 이벤트 조립은 유지하지만 장외 공시의
`source_event` 자동 조립은 이번 복원 범위에 포함하지 않는다. 분석엔진의 기존
`statics/tool_business.py`는 `s3_supply_fact`를 직접 조회한다.

정상 SFN의 `LoadDisclosure`는 `--input-run-id`로 completed dual manifest의 direct key winner를
pending에 commit한 뒤 pending만 typed 적재한다. shared canonical 상위 prefix LIST/fullscan은
하지 않으며 manifest 결손·손상을 fullscan으로 우회하지 않는다. issuer 지연과 일시 실패는
durable pending이 다음 정상 슬롯까지 보존한다. shared canonical은 명시 복구에서만 읽는다.

공시 복구는 기존 경로를 사용한다(ALPHA-1073). 목록·본문·정규화의 일시 실패는 다음 장중
재시도 또는 마감 배치의 날짜창 재조회로 회수한다. 본문이 이미 저장됐다면 기존 키를 재사용한다.
배치 watermark는 `ingest_lane=batch`의 목록 완주만 인정하고 직전 날짜를 포함해 다시 읽는다.
목록 완주는 본문·정규화·적재 완료와 다르므로 반복 실패는 원장에 남기고 대상 raw run으로
재처리한다. 신규 수집이 0건이어도 정상 배치 적재는 날짜 제한 없이 기존 pending을 회수한다.
수집 실패로 배치 적재까지 못 간 경우에는 기존 `load-disclosure --pending-only` 복구 경로를
사용한다(DB·storage 설정 필요). watermark 탐색 10일 한계를 넘는 공백은 자동 회수로 간주하지
않고 대상 기간을 확인해 명시 복구한다. 첫 배치도 `window_source`·`window_from/to`를 확인해
워터마크 부재/조회 실패의 기본창 밖 공백이 있으면 대상 기간을 명시 복구한다. 배치 성공은
실패했던 minute window를 수정하지 않는다.


**장중 수급 레인**(`edge-dev-data-pipeline-investor-intraday`, ALPHA-769)도 같은 형태다 —
`CollectKisInvestorEstimate → NormalizeInvestorEstimate → LoadInvestorIntraday` 를 평일 5슬롯
(09:35·10:05·11:25·13:25·14:35 KST)으로 돈다. **다만 컷오버가 아니라 신설이다**: 이 3스텝은
시장 SFN 이 한 번도 돈 적이 없어(ALPHA-767·768 이 층만 만들고 배선을 안 붙였다) 두 레인이 같은
스텝을 동시에 소유하는 겹침 창이 없고, 그래서 스케줄을 처음부터 ENABLED 로 세웠다. 슬롯 수는
우리가 고른 게 아니라 소스가 정한다 — 벤더 갱신이 하루 4~5회뿐이고 유형별로 시각이 갈려
합집합이 5개다(+5분은 정각 반영 지연이 미관측이라 둔 여유).
⚠️ 2026-10-03 부터 이 5슬롯의 실행 주체는 **Airflow** 다(ALPHA-1141, dev
`investor_intraday_orchestrator = "AIRFLOW"`). 이 레인의 EventBridge 스케줄 5개는 DISABLED 이고
SFN 정의·Reconciler 슬롯 대조는 남는다. 전환·롤백 절차는 [`src/apps/cloud/airflow/README.md`](../../airflow/README.md).

⚠️ 이 레인의 `LoadInvestorIntraday` 도 **창 없이 돈다** — 공시와 같은 이유(풀스캔이 백로그 회수
경로)이고, 그 때문에 **공휴일에도 실일을 한다**. 원장 카탈로그에서 이 작업만
`kr_trading_calendar=False` 인 근거가 그것이다(수집·정제는 비거래일에 대상 자체가 없어 True).

⚠️ **컷오버가 필요한 이유는 성능이 아니라 원장 정체성이다.** 작업 정체성의 정본은
`catalog.by_cli(step, source)` 인데 두 레인의 CLI 가 글자 그대로 같아(`ingest-raw-disclosure`
등), 같은 스텝을 두 레인이 동시에 소유하면 `by_cli` 가 먼저 온 쪽을 돌려줘 장중 런의 attempt
가 시장 레인 task_key 로 기록된다 — 장중 런은 영구 MISSED, 시장 런은 resolve 경로 없는
`LEDGER_GAP` 이다. 그래서 컷오버는 선택이 아니라 전제다.

`LoadDisclosure` 의 issuer 해소는 **레인 간 읽기 전용 공유**다 —
`company_profile.dart_corp_code` 를 채우는 `EnrichCorpCode` 는 시장 SFN 소관이라, 유니버스에
새로 들어온 회사는 그 슬롯에서 `skipped_unresolved_issuer` 로 계측된 뒤 다음 일일런 이후
슬롯이 줍는다(조용한 유실이 아니라 계측된 지연). 뉴스 SFN 이 `instrument` 마스터를 빌려 읽는
것과 같은 형태다.

**raw 수집(12잡)** — 벤더 API 키가 필요해 각자의 시크릿 세트를 쓴다. 시장 SFN 에 든 것은 이 중
9잡이다 — `ingest-raw --source fmp`·`ingest-raw --source bigkinds`(뉴스 SFN)와 `ingest-raw-disclosure`
(공시 레인)는 빠졌다(`statemachine.tf` 의 `market_excluded_states`). 그중 FMP 잡은
`us_fmp_enabled=false`(기본)라 꺼져 있다.

- `ingest-raw --source fmp`
- `ingest-price-raw --source fmp`
- `ingest-raw-financial --source fmp`
- `ingest-raw --source bigkinds`
- `ingest-price-raw --source kis`
- `ingest-raw-financial --source dart`
- `ingest-raw-disclosure`(공시, dart 세트) — 단일 벤더라 `--source` 없음
- `ingest-raw-etf`(미국 ETF 구성종목, fmp 세트)
- `ingest-raw-etf --source krx`(국내 ETF 구성종목, **krx 세트** — 로그인 게이트)
- `ingest-raw-nav`(국내 ETF NAV, **kis 세트** — 단일 벤더라 `--source` 없음)
  - ⚠️ KIS 토큰 발급은 앱키당 분당 1회라, 같은 앱키를 쓰는 `ingest-price-raw --source kis` 와
    **동시 실행하면 한쪽이 403**(EGW00133) 이다. SFN 에는 kis 브랜치가 4개 나란히 편입돼 있다
    (price·nav·investor·etf_profile). 흡수는 두 겹이다:
    - **공유 캐시(ALPHA-573)** — `KIS_TOKEN_CACHE_PARAM` env(터라폼이 kis task-def 에 주입,
      SSM SecureString)가 있으면 발급한 토큰을 컨테이너 사이로 공유해 발급이 하루 1회로
      수렴한다(토큰은 24h 유효). 403 을 맞으면 1분을 기다리기 전에 승자의 쓰기를 짧게
      폴링(2초×5)해 그 토큰을 가져간다. **캐시가 없거나 실패하면 아래 대기·재시도로 폴백**한다
      — 최악이 캐시 없던 시절의 동작이다. env 가 없는 로컬 실행은 항상 이 폴백 경로다.
    - **대기·재시도(ALPHA-458)** — `kis_auth` 가 403 EGW00133 을 만나면 61초 + 지터(0~20초)
      대기 후 재시도한다(예산 `TOKEN_RATE_LIMIT_MAX_RETRY = 4`, 총 5회 시도 — 동시 발급자
      수보다 커야 한다). 유량 제한이 아닌 4xx 는 기다려도 안 풀리므로 즉시 올린다.
  - **기준일(as-of) 규약**(ALPHA-387): 스케줄이 KST 15:40(장 마감 후, ALPHA-414)이라 거래일
    런은 그날 PDF 를 받는다(dev 실측: 07-22·23·24 스냅샷 내용 상이). 비거래일 런은 빈 응답이
    아니라 **직전 거래일 PDF** 가 온다(토 07-18 응답 = 금 07-17 바이트 동일) — 그래서 어댑터가
    `_as_of` 로 "거래일이면 오늘, 아니면 직전 거래일"을 라벨한다. 안 그러면 존재하지 않는
    거래일의 스냅샷이 canonical 에 as-of 로 남는다. 휴장일 집합은 Planner 와 같은
    `OPS_KR_HOLIDAYS`(terraform `kr_holidays`)를 krx task-def 에도 주입해 공유한다.
  - **trdDd 단일 거래일 백필**(ALPHA-1063): `ingest-raw-etf --source krx`에 같은 날짜의
    `--from/--to`를 주면 그 거래일 snapshot을 다시 받는다. 대상 연도를 포함한
    `OPS_KR_HOLIDAYS` 설정이 필수이고, 다일·반쪽 창과 휴장일·미래일은 fail-loud 한다.
    빈 응답도 계속 실패이며, ALPHA-460 이후 그 실패가 뒤 페이즈를 막지는 않는다
    (알림 + 런 FAILED 마감).
- `ingest-raw-etf-profile`(국내 ETF 프로필 = ETF 마스터 표시명 출처, **kis 세트**, ALPHA-462)
- `ingest-raw-investor`(종목별 투자자 수급, **kis 세트**, ALPHA-482) — 유니버스는 canonical KR
  holdings 파생(가격과 같은 축). `NormalizeInvestor → LoadEtfFlow` 체인의 raw 선행이다.
  - 시장 SFN은 `--max-failed-symbols 1`을 명시한다(ALPHA-798). 실패 심볼 1개까지 실행은 성공하되
    collection log의 partial·실패 상세·failed_records를 보존해 원장에는 INCOMPLETE로 남긴다.
    2개 이상 또는 저장 0건은 기존처럼 비영이다.
  - **EOD 서빙 블랙아웃 규약**(ALPHA-518·562): 확정 수급이 서빙되기 전에 질의하면 rt_cd=2
    `msg_cd=OPSQ2001 msg1="TIME LIMIT 00:00 ~ 15:40"` 이 온다. 이건 데이터 결손이 아니라
    **"지금이 서빙 개시 전"이라는 상시 조건**이라, 아무 때나 기다린다고 풀리지 않는다.
    그래서 **거래일이고 남은 재시도 예산(5×15초) 안에 15:41(KST)을 넘길 수 있을 때만**
    백오프로 대기하고, 아니면 대기 없이 그 심볼을 격리한다. 해소 시각이 msg1 의 상한 15:40 이
    아니라 **15:41** 인 것은 실측이다(15:40:53~59 실패, 15:41:00 이후 성공). 거래일 조건을
    빼면 비거래일 런이 심볼당 75초를 태워 유니버스 전체가 ~10시간이 된다(2026-07-26 실측:
    28분에 22종목). 휴장일 집합은 Planner·KRX·iNAV 와 같은 `OPS_KR_HOLIDAYS` 를 공유한다.

**수집 — 상태머신 밖(수동 전용)**

- `ingest-raw-instrument` / `normalize-instrument-profile`(KRX 상장 **전종목** 종목기본정보,
  ALPHA-829) — **SFN 에 편입돼 있지 않고 ops 카탈로그에도 없다.** 배선은 별도 티켓 소관이라
  그전까지는 **손으로 돌릴 때만** 수집된다. 카탈로그 등록을 함께 하지 않은 건 의도다 —
  `required=True` 로 넣으면 원장이 매 런 이 작업의 빈 칸을 만들어 놓고 미이행으로 센다.
  - 자격증명이 `krx_etf` 와 **다르다**: 저쪽은 계정 로그인(`mbr_id`/`pw` → JSESSIONID),
    이쪽은 무상태 `AUTH_KEY` 헤더다. 시크릿도 별개(`edge-dev-data-pipeline/krx/api-key`).
    ```bash
    DATA_PIPELINE_KRX_INSTRUMENT__SOURCE__AUTH_KEY=... \
      uv run --package data-pipeline python -m data_pipeline.run ingest-raw-instrument
    uv run --package data-pipeline python -m data_pipeline.run normalize-instrument-profile \
      --input-run-id <fresh-collection-run-id>
    ```
  - ⚠️ **당일 조회가 막혀 있다**(`basDd < 오늘`). 기준일은 달력이 직전 거래일로 정하므로
    `--from/--to` 는 **거부**한다 — 무시하고 돌면 소급한 줄 착각한다.
  - ⚠️ 달력을 쓰므로 `OPS_KR_HOLIDAYS` 주입이 필요하다(미주입이면 공휴일을 거래일로 보고
    0행을 받아 게이트에 걸린다). 위 krx 잡과 같은 요구사항이다.
- `ingest-raw-inav`(국내 ETF **장중** iNAV, **kis 세트** — 일별 NAV 와 같은 앱키·유니버스)
  — **SFN 에 편입돼 있지 않다.** 위 raw 페이즈 잡 목록에 없고 `statemachine.tf` 에도 없다.
  이 raw 스텝은 **손으로 돌릴 때만** 돈다. 장중 iNAV 자동 수집은 이 스텝이 아니라 상주
  `inav-worker`(ALPHA-882)가 canonical 로 직접 한다([ops-ledger.md](ops-ledger.md) 의 "상주 iNAV Worker" 항목). 잘못된 시각에 돌리는 것 자체는 아래 가드가 막는다.
  - 일별(`FHPST02440200`)과 **시장코드가 갈린다**: iNAV 는 `FID_COND_MRKT_DIV_CODE="E"`, 일별은 `"J"`.
    `"J"` 로 보내면 전건 `rt_cd=2` 로 튕긴다(실측).
  - ⚠️ **소급 백필이 없다.** 날짜·시각 지정이 무시돼 항상 "지금 기준 최근 30행"만 온다 —
    놓친 구간은 영구 유실이다. 일별 NAV 처럼 창을 주고 나중에 주워올 수 없다.
    그래서 `--from/--to` 를 주면 **실행을 거부**한다(무시하고 돌면 갭을 못 메운 채 exit 0 이 된다).
  - **기준일 가드**(ALPHA-557): 응답에 날짜 필드가 없어(`bsop_hour` 만 옴) 거래일을 수집 시각으로
    붙여야 하는데, KIS 는 오늘 데이터가 없어도 **직전 거래일 데이터를 반복**한다(위 ALPHA-387 과
    같은 함정). 그래서 **거래일이고 09:00(KST) 이후**일 때만 수집하고, 아니면 `status=skipped`
    + 사유로 남기고 raw 를 쓰지 않는다. 장 마감 후(15:30~)는 막지 않는다 — 그때 오는 건 오늘
    종가 구간이라 라벨이 맞다. 휴장일 집합은 Planner·KRX 와 같은 `OPS_KR_HOLIDAYS` 를 공유한다
    (`kis` task-def 에도 주입). 이 skip 은 **정상 상태**라 raw-ingest-skipped 알람 토큰을 쓰지
    않는다 — 드러남은 collection_log 가 맡는다.
- `ingest-raw-investor-estimate`(종목별 **장중** 투자자 추정, **kis 세트** — EOD 투자자 수급과
  같은 앱키·같은 유니버스, ALPHA-767) — **장중 수급 레인**(`edge-dev-data-pipeline-investor-intraday`,
  평일 5슬롯 09:35·10:05·11:25·13:25·14:35 KST)의 raw 스텝이다(ALPHA-769 — 이 절 제목과 달리 수동
  전용이 아니다. 2026-10-03 부터 Airflow 가 실행한다, ALPHA-1141). dataset 은 `investor_flow_intraday` 로
  EOD(`investor_flow_daily`)와 **갈라 둔다** — 값이 가집계 추정(`*_fake_*`)이고 시간축이
  거래일이 아니라 그날의 슬롯(`bsop_hour_gb`)이라, 한 데이터셋에 섞으면 소비자가 잠정과
  확정을 구분할 수 없다.
  - EOD(`FHPTJ04160001`)와 **tr_id·파라미터가 갈린다**: 장중은 `HHPTJ04160200` 이고 종목코드
    하나(`MKSC_SHRN_ISCD`)만 받는다 — 날짜 파라미터가 아예 없다.
  - **갱신은 하루 4회**(외국인 09:30·11:20·13:20·14:30 / 기관 10:00·11:20·13:20·14:30) —
    합집합 5슬롯이 레인 스케줄의 근거다.
  - ⭐ **응답이 누적이다**(2026-08-06 dev 실측): 한 콜이 그날 슬롯 **전부**를 준다(14:51 한 번에
    325종목 1,574행 = 종목당 최대 5행). 그래서 슬롯을 하나 놓쳐도 다음 슬롯이 회수하고, 레인이
    `retry 0` 을 쓰는 근거가 된다. 슬롯 필드 `bsop_hour_gb` 의 도메인도 같은 실측에서 **`"1"`~
    `"5"` 한 자리 코드**로 확인됐다(`"0930"` 같은 시각 문자열이 아니다 — 슬롯 1 은 기관값이
    284/284 전건 0이라 09:30 외국인 갱신에 대응한다). 값은 장 시작부터의 **누적 순매수**라
    슬롯 간 차분이 그 구간의 순매수이고, 거래가 없던 종목은 그 슬롯 행이 아예 없다.
  - 다섯 슬롯 모두 `--max-failed-symbols 1`을 쓴다(ALPHA-798). 고립 실패는 다음 슬롯의 누적 응답,
    마지막 14:35 슬롯은 당일 수동 plan-run으로 회수할 수 있다. 회수 전까지 원장 INCOMPLETE가
    남고, 2개 이상 또는 저장 0건은 실행도 실패한다.
  - ⚠️ **ETF 자체는 0행이다.** 거래소가 ETF 의 장중 투자자 귀속을 생산하지 않는다(KIS 장중
    투자자 4종 전수조사로 확정). 우리 유니버스는 ETF **구성종목**(개별주식)이라 적용되지만,
    holdings 유니버스에 섞여 오는 ETF 자신은 빈 응답이 정상이다.
  - ⚠️ **소급 백필이 없다**(iNAV 와 같다). 날짜 지정이 없어 오늘치만 오고, 놓친 슬롯은 그날
    안에서만 회복된다. 그래서 `--from/--to` 를 주면 **실행을 거부**한다.
  - **기준일 가드**: 응답에 날짜 필드가 없어 거래일을 수집 시각(KST)으로 붙이는데, 비거래일·
    개장 전에 KIS 가 직전 슬롯을 주면 어제 데이터가 오늘 거래일로 굳는다(위 iNAV·ALPHA-387 과
    같은 함정). 그래서 **거래일이고 첫 슬롯(09:30 KST) 이후**일 때만 수집한다.
  - **가드는 iNAV 와 같은 `skip_reason` 규약이다**(ALPHA-769 에서 통일). 못 돌 시각이면
    `status=skipped`·exit 0 으로 마감하고 사유를 collection_log 에 남긴다. 종전엔 예외를 올려
    `status=error`·exit 1 이었는데, 레인이 **평일 cron** 이라 그대로 두면 공휴일마다 런이
    FAILED 여서 예정된 무산출과 진짜 고장이 구분되지 않는다. 기제는 공유 스텝
    (`ingest_raw_investor`)에 심었고 EOD 어댑터엔 이 속성이 없어 동작이 불변이다. 어댑터의
    `fetch` 는 여전히 같은 사유로 raise 한다 — **직접 호출자**를 위한 것이고, 조건은
    `skip_reason` 하나가 정본이라 두 경로가 갈릴 수 없다.
  - **뒤 두 스텝도 같은 레인이다**(ALPHA-768·769): `normalize-investor-estimate`(raw → canonical
    `investor_flow_intraday`) → `load-investor-intraday`(→ 동명 테이블). EOD 체인
    (`normalize-investor` → `load-etf-flow`)과 스텝을 **복제하지 않고 갈랐다** — 정체성 키에
    `asof_slot` 이 붙어 병합 키·PK·창 프루닝이 전부 달라 인자로 갈아끼울 수 없다(수집 스텝은
    저장 위치만 달라 인자로 갈랐던 것과 대조). 레인이 자동으로 돌리지만 세 스텝을 손으로 이어
    돌려도 체인이 닫힌다 — 복구·검증용이다
    (`src/` 에서. 수집은 KIS 앱키, 적재는 DB 접속이 필요하다 — 위 각 절의 env 와 같다):
    ```bash
    RUN_ID=manual-investor-20260827-0935
    export RUN_ID
    # 수집 — 거래일이고 09:30(KST) 이후일 때만. 아니면 사유를 남기고 skip(exit 0)한다
    DATA_PIPELINE_KIS_INVESTOR__SOURCE__APP_KEY=... DATA_PIPELINE_KIS_INVESTOR__SOURCE__APP_SECRET=... \
      uv run --package data-pipeline python -m data_pipeline.run ingest-raw-investor-estimate \
      --run-id "$RUN_ID"
    # 정제 — 같은 수집 run만 읽고 canonical winner manifest를 확정한다
    uv run --package data-pipeline python -m data_pipeline.run normalize-investor-estimate \
      --run-id "$RUN_ID" --input-run-id "$RUN_ID"
    # 적재 — 같은 manifest의 직접 parquet와 winner만 읽는다. 선행: Flyway 적용 완료
    DATA_PIPELINE_DB__HOST=127.0.0.1 DATA_PIPELINE_DB__PASSWORD=... \
      uv run --package data-pipeline python -m data_pipeline.run load-investor-intraday \
      --run-id "$RUN_ID" --input-run-id "$RUN_ID"
    ```
    - **컬럼은 추정 수량 3개뿐**(`net_qty_foreign_est`·`net_qty_institution_est`·
      `net_qty_total_est`). 벤더가 `frgn`·`orgn`·`sum` 가집계 수량만 주고 개인·기관 세분·
      순매수 대금을 안 준다 — EOD 의 백만원→원 환산도 `currency` 태깅도 대상이 없다.
      `_est` 접미사는 표면에서 잠정임이 읽히게 하는 장치다.
    - ⚠️ `asof_slot` 은 **TEXT 로 원문 보존**한다. 도입 당시(마이그레이션 `V202608051740`)엔
      `bsop_hour_gb` 의 도메인이 미관측이었고, 이후 `"1"`~`"5"` 슬롯 코드로 실측 확인됐다(위 ⭐
      응답 누적 항목, 2026-08-06) — 시각이 아니라 코드라 시각으로 파싱하지 않는다.
    - 정정 정책은 **최신값 덮어쓰기**(형제 로더와 같은 모델) — 벤더가 가집계를 고치면
      canonical 이 최신 `fetched_at` 으로 수렴하고 마트는 `DO UPDATE` 로 따라간다.

**정제(normalize, 시장 SFN 5잡)** — 레이크만 읽고 canonical 을 쓰므로 벤더 키가 불요라, 시크릿 없는
bigkinds task-def 를 재사용한다(새 task-def·IAM 불요). **`--input-run-id $.run_id` 로 이 실행이
수집한 raw 만 정제한다**(ALPHA-389) — 정제 비용이 여태 쌓인 raw 전체가 아니라 이번 런에
비례한다. 적재는 여전히 멱등이다(병합이 기존 행을 읽어 합친다).

- `normalize-price` · `normalize-etf-profile` · `normalize-etf-nav` · `normalize-investor`
- `normalize-etf`(ETF 구성종목, ALPHA-342·343)
- (`normalize-news` 는 뉴스 SFN, `normalize-disclosure`·`normalize-disclosure-segment` 는 공시 레인,
  `normalize-investor-estimate` 는 장중 수급 레인 소관이다 — `market_excluded_states`)

**feature(구 derive, 병렬 잡 + 직렬 선행 2스텝: load-instruments → enrich-corp-code + 직렬 꼬리: load-price-triggers)** — canonical 을
소비해 분석이 읽을 feature/factor 산출물을 만든다. 정제 뒤라야 하고(전부 canonical 을 읽는다) 병렬 잡들은 서로 독립이다.
시장 SFN 의 병렬 잡은 `load-etf-nav`·`load-price-daily`·`load-etf-holdings`·`load-etf-flow` 다 — 아래 목록의
`tag-news`·`load-documents`·`load-assertions`·`assemble-events` 는 뉴스 SFN, `load-disclosure` 는 공시 레인 소관이다.
시크릿이 다른 잡은 task-def 도 따로다. 최종 범위는 뉴스/공시 assertion·event·event_thread
추출 + 가격이벤트 생성까지(ALPHA-408) — 추출 스텝들은 alphamale 로직 이관 합의 후 편입한다.

- `tag-news`(→ 레이크 feature 존, **deepseek 세트**) — SFN은 `--input-run-id`로 NormalizeNews
  manifest의 직접 parquet와 현재 논리 ID만 읽고 `--limit`(기본 10000)으로 LLM 호출 수를 묶는다.
  KST 전일·당일 장중 미러 prefix는 직접 조회해 canonical이 아직 없는 미러도 흡수한다.
  실제 변경한 파티션·`article_id`는
  `operations_archive/feature_run_manifests/dataset=news_assertions/run_id=…/manifest.json`에
  기록하며, 모든 파티션과 quality log가 성공한 뒤에만 `feature_written=true`가 된다.
  상한에 걸린 잔여가 있으면 manifest를 완료하지 않고 같은 run 재시도가 이어받는다(mentions 있는 미태깅
  기사만 고른다 — 유니버스 무관 기사는 `skipped_no_mention` 으로 계측하며 태깅하지 않는다).
  LLM 호출은 기사별로 병렬 실행한다(ALPHA-519, `LLM_CONCURRENCY` env·기본 32·상한 100) —
  카운터·격리·병합은 취합 후 메인스레드라 순차 실행과 결과가 같다
- `load-instruments`(→ Cloud Event Store RDB, **rds 세트**) — 정상 `--latest-good`은 세 pointer를
  먼저 직접 GET·검증하고 그 불변 artifact만 SHA 확인 뒤 읽는다. canonical parent LIST는 0이며,
  세 입력이 모두 검증되기 전에는 DB transaction을 열지 않는다. `--all`은 명시 복구 전용이다.
  DB 접속정보는 이 task-def 에만 주입한다.
  공용 env 에 두면 `DbConfig` 가 password 없이 구성돼 로드 시점에 죽어 **수집·정제 스텝까지 전멸**한다
- `enrich-corp-code`(**직렬**, load-instruments 뒤 → FeatureParallel 앞, ALPHA-491·532, **rds_dart 세트**
  =DB+DART) — company_profile 의 NULL dart_corp_code 를 corpCode.xml 매칭으로 채운다. LoadDisclosure 의
  issuer 해소(9→309)가 그 값에 의존하므로 병렬 앞 직렬이다. DB·DART 를 둘 다 부르므로 rds·dart 결합
  시크릿 task-def 를 쓴다(결합 없으면 rds 로 돌 때 source.enabled=false 로 skip). NULL 가드 멱등
- `load-price-triggers`(→ Cloud Event Store RDB, **rds 세트** 재사용, **직렬 꼬리** — FeatureParallel 뒤에 돈다.
  LoadPriceDaily·LoadEtfHoldings 의 DB commit 을 읽어야 해서다, ALPHA-1039) — 구성종목 가중 proxy
  3% 게이트(엔진 L0 정본, ALPHA-411). 정상 실행은 NormalizePrice manifest 범위만 읽고 최신 KR
  거래일 결손만 현재 운영 실패로 센다. 과거 결손은 reconciliation debt로 보존하며, canonical
  전체·기간 스캔은 명시 복구 경로다(ALPHA-1039·1062)
- `load-documents`(→ Cloud Event Store RDB, **rds 세트** 재사용, ALPHA-374·410·1031) —
  NormalizeNews manifest의 직접 parquet만 GET하고 현재 실행 `article_id`를 document로 적재한다.
  결손·손상 manifest는 전체로 넓히지 않고 실패한다. 자연키 멱등, LoadAssertions의 FK 선행.
  문마다 후보 전량을 `executemany` 로
  보낸다(ALPHA-906) — 예전엔 후보마다 최대 3왕복(document·lead·publisher)이라 31.8만 행이면
  왕복이 최대 95만 번이었고, 그것이 뉴스 SFN 이 상한에 물리던 원인이었다(TIMED_OUT 전건이 이 스텝
  미완). `created` 와 로그 표본은 `RETURNING` 이 돌려준 행에서만 뽑는다 — 배치의 `rowcount`
  로는 **어느 행**이 들어갔는지를 알 수 없다
- `load-disclosure`(→ Cloud Event Store RDB, **rds 세트** 재사용, ALPHA-476·532·1045) — canonical 공시 →
  document(DISCLOSURE)·disclosure_document·disclosure_fact. issuer 는 앞 직렬 enrich-corp-code 가 채운
  dart_corp_code 로 해소(DART API 불요라 rds 세트). 자연키 멱등·정정 DO UPDATE.
  정상 경로는 두 completed run manifest의 direct key·SHA·winner를 검증해 canonical 행을
  pending에 먼저 commit한 뒤 pending만 소비하므로 shared canonical LIST를 하지 않는다.
  명시 복구는 `--all` 또는 `--from/--to`로 canonical을 pending에 bootstrap하고,
  `--pending-only`는 canonical을 읽지 않고 잔여만 회수한다. **적재 로더 중 유일하게
  `--window-days`도 받는다**(ALPHA-721)
- `load-assertions`(**직렬**, 뉴스 SFN 의 feature 페이즈 뒤 — ALPHA-376·410·553·1033) — feature assertion →
  document_assertion·assertion_argument. document FK 의존이 병렬이면 레이스라 직렬로 둔다.
  정상 경로는 TagNews manifest의 직접 part만 GET하고 현재 `article_id`만 논리 처리한다.
  결손·손상 manifest는 풀스캔으로 넓히지 않고 실패하며, 누적 part의 물리 읽기 행과 manifest
  논리 행을 quality log에서 분리한다. 과거 일부/전체 복구는 `--from/--to`/명시 `--all`이다.
  **실패한 범위는 다음 런이 이어 싣는다**(ALPHA-1052, `manifest_carry_forward`) — 성공 시
  manifest 옆에 소비 마커를 쓰고, 시작할 때 "manifest 는 있는데 내 마커가 없는" run 을 함께
  싣는다. 이게 없던 동안은 이 스텝이 죽으면 그 범위가 **영구 유실**이었다: 다음 런 manifest
  에는 그 `article_id` 가 없다(이미 태깅돼 생산자의 `changed_ids` 밖). 마커는 소비자별이라
  한 manifest 를 둘이 읽는 계보(normalize_news → tag-news·load-documents)도 서로를 안 지운다.
  마커는 **범위가 온전히 착지했을 때만** 쓴다 — `missing_document` 가 있으면 그 범위는
  미소비로 남는다(그 결손의 회수 수단이 같은 manifest 재실행이다). 회수는 **보조 작업**이라
  manifest 마다 격리한다: 옛 manifest 하나가 깨져도 이번 런은 살고, 못 실은 사유는
  `unfinished`·`stale`·`failed`·`over_limit` 로 갈라 남는다. 창(7일) 밖으로 밀린 것은
  `skipped` 마커로 닫아 탐색이 수렴하게 한다(consumed 와 이름이 다르다 — "안 싣고 닫았다"가
  "실었다"로 둔갑하면 안 된다). **적재도 격리한다**(ALPHA-1053) —
  후보를 범위에 귀속해(겹치는 기사는 자기 범위 몫) 회수 run 마다 중첩 트랜잭션(savepoint)으로
  감싼다. 하나로 묶지 않는 것은 오염된 manifest 하나가 건강한 회수분까지 매 런 되돌려
  그쪽도 수렴 못 하기 때문이다. 롤백된 그룹은 **수치도 되돌린다**(savepoint 는 DB 쓰기만
  되돌리고 카운터는 파이썬 변수다) — 채번·`created`·해소율 분모까지. 실패한 그룹이 들고
  있던 기사를 주장하는 manifest 는 전부 미소비로 남는다(`blocked_by_rollback`).
  ⚠️ 남은 구멍: document 조회는 그룹 savepoint **밖**이라 그 실패는 여전히 런 전체를
  죽인다 — 읽기이고 자기 범위만으로도 같은 질의가 나가서 그대로 뒀다.
  한 파티션에 같은 기사의 판정이 둘 있으면 **`tagged_at` 최신만 싣는다**(ALPHA-900,
  `rows_superseded`) — `tag-news` 의 압축과 같은 규칙이다. 안 그러면 사건 자연키가 갈린 옛
  판정이 함께 INSERT 되고 `ON CONFLICT DO NOTHING` 이라 영영 안 덮인다.
  **파티션 사이도 같은 규칙이다**(ALPHA-1051, `rows_moved_partitions`) — `article_id` 는 원문
  URL 해시라 불변인데 `published_date` 는 벤더 재등록으로 **이동하고**(BigKinds 가 같은 URL 을
  다른 `DATE`·`NEWS_ID` 로 다시 준다) 옛 파티션 행은 아무도 지우지 않아 한 기사가 두 파티션에
  남는다. 이걸 manifest 손상으로 보고 실패하면 그 런의 범위가 통째로 유실된다(ALPHA-1052) —
  손상이 아니므로 최신 판정만 싣고 계속 간다. 소비 순서는 이 스텝이 날짜 오름차순으로
  세운다(동률이면 늦은 파티션 승 — 생산자 배열 순서에 안 걸리게). 보장 범위는 **이번 런이
  싣는 행**까지고, 앞선 런이 이미 실은 옛 판정은 자연키가 갈리면 DB 에 남는다(ALPHA-1052).
  **흡수 전 장중 미러는
  읽지 않는다**(`minute_mirrors_unabsorbed`) — 그 조각은 아직 `tag-news` 의 mentions 게이트를
  안 거쳤다.
  역할별 엔티티 해소(ALPHA-831 — 명부·채번 축 포함)와 해소율은 quality log 로 남고,
  채번 경로는 entity·concept 마스터 행도 만든다
- `assemble-events`(**직렬**, 뉴스 SFN 의 LoadAssertions 뒤 — ALPHA-412·553, **events 세트**=LLM+DB) —
  분석엔진 추출 체인의 이식: canonical 뉴스 제목 분류(LLM) → document/assertion/source_event
  계보 조립 → event_thread threading. **배치=catch-up 이다(ALPHA-730)** — event 의 실시간 정본은
  1분 단건 조립(ALPHA-727)이고, 배치는 미조립 잔여 소진 + UNKNOWN 재평가·미연결 회수만 맡는다.
  적재 직전 doc 단위 advisory lock 아래 자국을 재확인해 단건 경로가 분류 창에서 먼저 조립한
  기사를 skip 하고, 트랜잭션은 날짜별 커밋이라 threading 락 점유가 1분 소비자를 오래 막지
  않는다. 자체 분류기 폐기는 단건 경로 커버리지 실증 후 후속. 결정적 ID 산식·프롬프트는
  엔진과 동일(정본), 창 미지정 = 오늘(KST) 하루 — 뉴스 SFN 은 `--window-days 1` 로 [어제,오늘]
  겹침(ALPHA-592, 자정 crossing·overnight 갭 방지). event 의 소비자는 ADR-0028 기준 analyze 였으나
  **ALPHA-806 이 시장 SFN 에서 analyze 를 걷어낸 뒤로는 분봉 트리거 큐의 상주 소비자**다. 제목 분류 LLM 콜은 배치별 병렬 실행한다(ALPHA-520, tag-news 와 같은
  `LLM_CONCURRENCY` env) — 단 threading 은 novelty 가 available_at 순서·prior 카운트에 의존해
  **직렬** 유지다

재무(financial)는 canonical 스텝이 아직 없어 정제 페이즈에서 제외한다(raw-only). 정제가 실패(exit 1)하면
feature 로 넘어가지 않는다. 부분 성공(exit 2)은 알림 뒤 계속 가고 마지막 게이트에서 런을 FAILED 로 닫는다
(raw 예외는 위 ALPHA-460).

**analyze 페이즈는 없다(ALPHA-806).** 이 SFN 의 책임은 feature 까지다. 설명은 분봉 트리거
큐를 소비하는 **상주 서비스**(`minute_services.tf` 의 `analysis-consumer`)만 만든다 — 트리거
없이 도는 일 단위 팬아웃은 확정 일봉을 기다려야 해서 장중엔 층을 못 세웠고(`layer_route=미상`),
같은 대상에 분봉 경로와 다른 답을 냈다.

수동 재실행은 **트리거 단건 재처리**다: `analysis-consumer` task-def 를 `aws ecs run-task` 로
띄워 Command 를 `["--trigger-id","<분봉 트리거 id>"]` 로 덮는다. `--trade-date` 단독 실행 경로는
없다 — 트리거 행이 대상·거래일의 정본이다.

> ※ task-def 는 시크릿 세트 단위로 만든다(`tasks.tf` 의 `secret_sets` 맵에 키를 넣으면 자동 생성) —
> 현재 10개: `fmp`·`bigkinds`·`kis`·`dart`·`krx`·`deepseek`·`rds`·`macro`·`events`(LLM+DB)·`rds_dart`(DB+DART).
> 전부 같은 이미지를
> 쓰고 command override 로 스텝을 고른다. 스케줄러 현황(레인별 ENABLED 시각)은
> infra/terraform/README.md 가 정본이다 — 시장 15:40·뉴스 00:10/08:10·공시 마감 보충 배치(평일 19:30)는
> ENABLED이고, 장중 수급 5슬롯은 Airflow 가 실행해 EventBridge 스케줄이 DISABLED다(ALPHA-1141).

수동 실행·백필은 `plan-run`(Planner) 경유가 계약이다 — 그 실행이 자기 슬롯으로 원장에 남아
관측된다. `aws stepfunctions start-execution` 직접 시작은 pipeline_run/expected_task 가 없는
**무원장 실행**이라 Reconciler 대조 밖이다(신규 배선의 최초 검증처럼 원장이 아직 없는 경우가
아니면 쓰지 마라).
