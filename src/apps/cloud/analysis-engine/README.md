# analysis-engine

ETF **장중 설명 생성** 파이프라인 (Python, edge-cloud). **분봉 트리거 큐를 소비하는 상주
서비스**로 돌며, 트리거 한 건이 곧 한 번의 실행이다 — 대상 ETF·거래일은 그 트리거 행이
정본이다. 트리거 없이 시작할 수는 없다(ALPHA-806: 일 단위 실행은 확정 일봉을 기다려야 해서
장중에 층을 못 세웠고 같은 대상에 다른 답을 냈다). 표시명·구성종목명은 전부 그 ETF 의
canonical holdings·마스터에서 파생한다 — KODEX 반도체 하드코딩은 없다(ALPHA-467).
run() 은 한 번에 ETF 한 종을 돈다.

> ALPHA-411·412(완전 분리, ADR-0028) 이후 이 앱은 **feature 산출물의 소비자**다: L0 게이트는
> `load-price-triggers`가 쓴 `price_movement_trigger`를 소비하고, 이벤트는 `assemble-events`가
> 조립한 `source_event` 계보를 읽는다. 뉴스 읽기·제목 분류·계보 조립·threading 은 feature
> 페이즈(data-pipeline)로 이관됐다.

## 흐름 ([설계도](../../../../docs/analysis-engine/architecture/analysis-engine-logic.drawio) · [PNG](../../../../docs/analysis-engine/architecture/analysis-engine-logic.png))

```
price_movement_trigger 소비 (행 없음 = 평온 → 종료)
  → 구성종목 분해(가격 S3 읽기 — observation·packet 입력)
  → observation/route 적재 (소비한 trigger_id 에서 파생)
  → DB 의 대상 ETF 구성종목 source_event/thread 조회 — 참여자(event_argument)·측정값
    (event_measure)을 사건 단위 EventContext 로 집계 (assemble-events 산출)
  → 분석 에이전트(DeepSeek) → explanation_result (DRAFT)
```

- `explanation_result` FK 전제(etf_profile·explanation_route·release_bundle)가 없으면 **LLM 앞에서 런을 세운다**(`PipelineError`, ALPHA-797). 임의 FK 값도, S3 폴백도 만들지 않는다 — 전제 결손은 설정 누락이라 조용히 우회하면 그 대상의 설명이 영영 없다.
- **매 런 커버리지 1줄을 로그에 남긴다**(ALPHA-792) — `statics.coverage` 에 `exists`·`unbound` 가 실린다: 5분봉을 Glue Iceberg 로 읽었는지 canonical 합집합으로 폴백했는지(정본은 **그 거래일을 쓸 만큼 담을 때만** 정본이다 — 상류 적재가 멈추면 조용히 과거를 보게 되므로, ALPHA-793. '쓸 만큼' 은 착지 종목 수 ≥ 100 **그리고** 시장 프록시(069500)가 있음이다 — 행 하나만 보던 판정은 13종만 착지한 날을 통과시켰고, 그 13종에도 시장 지수는 있었으므로 둘 중 하나로는 못 거른다, ALPHA-833. 폭만 재고 **세션 깊이는 안 잰다** — 장중에 끊긴 날은 이 판정 밖이다), 층 분해가 왜 `None` 인지, 분해는 됐는데 **시장 층이 왜 빠졌는지**(`exists["market_layer"]` — 대상이 시장 프록시 자신이면 부재가 아니라 정상이라 안 적는다, ALPHA-833). 층은 **β=1 회계**다(ALPHA-862) — 대상·시장·구성종목의 구간수익은 **커밋된 상태축 1분봉**에서 직접 계산해 주입하고(ALPHA-866 — 이 세 축엔 레이크 폴백이 없다: 결손이면 부재 사유로 남는다), 섹터 후보만 요청일 하루를 DuckDB 로 직독한다(Athena 오프로드·60일 β 표본 폐지). 시변 β·ρ 판정은 칼만(ALPHA-803)이 가져온다. 이 값들은 원래 `CausalLake` 안에만 있어 운영에서 볼 수 없었다. 자격증명은 가린다(DSN 은 `_rdb` 가 원천에서 지우고 로그 쪽이 한 겹 더 받친다).
  - ⚠️ **커버리지가 설명하지 못하는 `None` 이 하나 있다**(ALPHA-785) — 분해는 됐는데 라우팅(`route_etf`)이 터져 그 분해를 **폐기**한 경우다. 재료 부재와 구분하려면 `statics.layers.failed` 의 `had_rollup`·`discarded_layers` 를 보고, 로그 보존 기간이 지난 뒤에는 원장의 `stage_results.routing_failed` 를 본다(그 런은 확신도도 `LOW` 로 내려간다).
- **완주한 런마다 런 아카이브 1건을 S3에 남긴다**(평온 종료 포함, ALPHA-415) — `{result prefix}/runs/etf=…/trade_date=…/{request_id}.json`. 분해 요약·소비 트리거·route·이벤트·LLM 원문(verdict/key_evidence/unexplained — explanation_result 매핑에서 손실되는 필드)·영속 결과가 담긴다. 기록 실패는 런을 죽이지 않는다(관측은 본업이 아니다). 반대로 **런이 raise 로 끊기면 아카이브도 없다** — 전제 결손·holdings 빈·수익률 미착지가 그 경우다.

## 구조

레이어드 패키지(ports & adapters). `domain/`은 순수 로직·모델(I/O 없음), `adapters/`는 I/O
경계, `pipeline`은 의존성 주입 오케스트레이션, `cli`는 composition root다.

```
src/edge_analysis/
  __main__.py · cli.py · config.py · observability.py · pipeline.py · window_batch.py
  domain/     models.py · decomposition.py · packet.py       # 순수, stdlib top-level import
  adapters/   lake · eventstore · llm · archive · readonly · trace · universe · price_daily
              causal_data   # 인과 조회 표면(코호트·정렬열·비중). PIT 를 코드가 바인딩한다
              sql_surface   # P2·P3·P5 의 자유 SELECT 표면. 시점 클램프가 뷰 안에 있다
              canonical_surface   # canonical(S3) PIT 자유 SELECT · 온톨로지 21표. 클램프가 뷰 안에 있다
              classification · segment_tables   # 산업분류 원장 적재 · 부문 매출 표 파서
              domain_docs   # 「사업의 내용」 RAG 조회 (버킷 없으면 미부착)
  causal/     contracts · p0_question … p9_registry · run    # P0–P9 귀속 파이프라인
              graph · verify · sandbox · chain · engine · stats · fit   # 검정 실행 기계
  statics/    frame · windows · tree · gates · render · vocab   # 정적 층 — LLM 이전에 코드가 고정하는 전부
              duck · interval · layers · route · etfcell   # 5분봉·시각창 집계 표면(DuckDB)·층 분해·라우팅
              (그 외 tool_* · paneltest · premium … — 어휘·설계 SSOT 는 statics/__init__ 과
               docs/analysis-engine/causal-attribution-design.md)
```

### 인과귀속 P0–P9 (`causal/`)

설명은 LLM 한 번 호출이 아니라 **단계별 폐쇄를 강제하는 파이프라인**으로 만든다
([설계도](../../../../docs/analysis-engine/architecture/causal-attribution-p0p9.drawio)).

```
P0 질문 고정 → 산술 게이트 → P1 지문 → P2 다중가설 → P3 그래프+공통원인 완비
   → P4 식별 3값 → P5 판별 검정 → 검정 실행 → 예산 → P6 민감도
   → P7 음성대조·혼재 스크린 → P8 처분 원장 → P9 누적
```

**닫는 것은 어휘가 아니라 다섯이다.** 가설 생성(P2)에는 골격도 후보 목록도 주지 않는다 —
어휘를 닫으면 새 메커니즘을 영영 못 본다. 대신:

- **회계 폐쇄** — 귀속의 합이 잔차를 넘으면 그래프가 틀렸다. 카이제곱보다 싸고 날카롭다
- **교란 폐쇄** — 그린 변수의 공통원인을 전수 선언한다(Hernán 조건). `assignment=chosen`
  (기업이 고른 사건: 배당·자사주·가이던스)이면 **컴파일러가 `U ↔ (T,Y)` 를 심고 모델이
  지울 수 없다**. 선언된 U 는 P5 에서 소거 검정을 받거나 P8 에 미소거로 남는다
- **처분 폐쇄** — 검토한 후보에 침묵이 없다. 기여 / 비기여 / 미결 중 하나로 반드시 판정된다
- **커버리지 폐쇄** — 메커니즘 영역 8종(정보·공통충격·수급·미시구조·피드백·제도·측정·무사건)
  마다 열었는지 적는다. 후보는 공시·뉴스에서 오므로 적지 않으면 정보 영역으로 쏠리고,
  **안 봤다와 보고 좁혔다가 산출물에서 같은 모양(부재)이 된다**
- **관계 폐쇄** — 가설 쌍마다 관계를 판정한다(Zaks 2017). `share` 는 `coincident` 에서만
  정의되므로 배타·포섭·직렬 쌍을 평탄하게 더하면 **정상 그래프를 산술이 죽인다**

따라서 **미소거 U 가 하나라도 있으면 "원인으로 확인됐습니다" 가 구조적으로 나갈 수 없다** —
`p8_findings.narrate` 가 주장 상한을 어긴 문장을 `PipelineError` 로 막는다.

**원인 하나를 고르는 일이 아니다.** 가설마다 `role` 을 붙여 인과 패키지를 배경조건·촉발원·
전달경로·증폭·종료로 갈라 보고한다 — Flash Crash 를 "대규모 매도가 원인"으로 끝내면
유동성 고갈(증폭)과 거래정지(종료)가 같은 칸에 들어가 개입 설계가 달라진다는 사실을 잃는다.
역할 신고는 지문의 default/deviant 가 감사한다(Halpern-Hitchcock: 배경조건 ≡ 그 참조류에서
전형적 · 촉발원 ≡ 이례적). `probable_cause` 는 **복수**다 — NTSB 규약이 병렬 나열을 명시
허용하고 실제 Asiana 214 의 PC 는 4개다.

- **식별은 3값**이다: `identified` / `identified_under(가정)` / `not_identified`.
  빈 조정집합은 "뒷문이 없다"가 아니라 "조정으로 막을 것이 없다"이고, 그 둘은 U 가
  걸려 있을 때 정반대다. `not_identified` 는 실패가 아니라 정상 종료다
- **식별이 안 되면 민감도**(P6 E-value)가 주장의 강도를 수치로 낸다 — 뒤집으려면 미관측
  교란이 처치·결과 양쪽과 얼마나 강하게 연관돼야 하는가
- **검정**은 간선 하나마다 파이썬을 써서 표본을 만들고 `placebo` 로 귀무분포를 붙인다.
  값은 원장(`placebo` 호출 기록)에서만 읽는다 — 모델이 타이핑한 p 는 게이트 G4 가 거부한다
- **못 잰 것은 침묵이 아니라 요청**이다. `impossible`·실행 불가 판별 검정·측정 불가 지문 축이
  전부 처분 원장에 `undetermined` 로 남아 다음 수집 의제가 된다
- 감사 흔적은 `explanation.raw.causal` 에 남는다 — 처분 전건·미소거 U·식별 상태와 가정·
  판별 검정·민감도·음성대조·원장 전량·에이전트가 쓴 코드

**표면은 둘인데 에이전트에게는 하나로 보인다.** P2·P3·P5 가 받는 것은 `Surfaces` 파사드
하나고, 질의에 뜬 뷰 이름으로 Postgres(`sql_surface`)와 canonical(`canonical_surface`)이
갈린다 — 어느 저장소에 사는지를 모델이 알아야 하면 그걸 맞추느라 질의가 틀린다.
**Cube 는 안 쓴다**: `*_latest` 에는 시점 창이 없어서 과거를 설명하면서 그 뒤에 정정된
재무·수정된 컨센서스를 보게 되고, **에러 없이 조용히 미래를 본다** — 인과 귀속에서 이보다
나쁜 실패는 없다. 그래서 `canonical.tables.as_of_sql` 쪽을 쓰되, 그 함수가 data-pipeline 에
있으므로 **생성 매니페스트**(`infra/canonical/pit-manifest.yml`)로 잇는다 — import 하면
psycopg3·lxml 이 딸려와 드라이버가 이중이 되고, 재구현하면 정정 처리 로직이 두 벌이 된다.
`CANONICAL_*` 셋이 다 있어야 붙고, 안 붙으면 그 어휘는 실리지 않은 채 P8 커버리지 원장에
**미개봉**으로 남는다(`Surfaces.missing`).

> 샌드박스는 **LLM 이 쓴 코드를 실행한다**(입력에 외부 사건 제목이 섞이므로 프롬프트 주입
> 표면이다). `as_of` 바인딩·창 절단·`__` 금지·import 허용목록·타임아웃으로 좁혀 두었지만
> 제한 exec 는 완전한 격리가 아니다. 태스크는 최소권한 역할·읽기 전용 DB 사용자로 돌리고,
> 필요하면 `CAUSAL_SANDBOX_ENABLED=false` 로 축약 경로(고정 추정량)로 내린다.

## 실행

```
python -m edge_analysis                       # 오늘(Asia/Seoul)
python -m edge_analysis --trade-date 2026-07-14 --request-id manual-1
# 분봉 트리거 단건(ALPHA-709) — 대상 ETF·trade_date 를 minute_price_trigger 행에서
# 유도한다(--trade-date 무시). 계보는 minute_price_trigger_id 축에 영속된다.
# 게시 게이트는 발화(route) 축(ALPHA-710) — 하루 다건 발화는 발화마다 게시되고,
# 같은 route 재실행만 DRAFT 보존이다. 분해 입력은 **두 시간축**이다(ALPHA-854):
#   상태 계산축 09:00~구간끝 · 설명축 [구간시작, 구간끝) — 둘 다 같은 확정 1분봉
#   (minute_ingestion_window 좌표로 strict 하게 읽는다)에서 한 번에 만든다.
# 설명 구간은 **호출자가 정한다**(`run(..., explain_window=...)`). 값이 없는 트리거
# 경로만 [직전 발화(없으면 09:00), 발화 분 끝) 으로 유도한다 — 발화는 "지난 설명 이후
# 무슨 일이 있었나"에 답하므로 창이 전부 과거다. 끝을 발화 분 뒤로 두면 아직 수집 전인
# 분을 요구해 매 발화가 지연 재시도로 접히고, 마감 직전 발화는 15:30 을 넘겨 DLQ 로 간다.
# 요청 시작은 5분 격자로 내리지 않는다(10:12 요청이면 첫 봉이 10:12~10:15).
# 분해가 답하는 것은 설명축이다: 그 구간 마지막 봉의 close 를 canonical price_daily
# **직전 거래일** 종가로 나눠 구성종목 수익률을 파생한다 — 판정(intraday-anchor-v2)과
# 같은 축(전일 종가 대비, ALPHA-747). 갭이 기여에 포함된다.
# 입력 unit 은 **대상 ETF + 구성종목**으로 좁힌다. 그 중 구간을 다 못 채운 종목은
# 떨어뜨리고 진행하며(벤더 미제공은 window 가 INCOMPLETE 로 terminal 커밋돼 재시도가
# 낫게 하지 않는다), 무엇을 얼마나 잃었는지 `stage_results.window` 에 남긴다
# (`dropped_units`·`price_weight_coverage`). 대상 ETF 자신의 결손만 fail-loud 다.
# 원장 불변식 위반(session 불일치·중복)은 DLQ, 빈 입력은 재시도 축이다.
# 시가 원장(minute_session_open)은 분해가 쓰지 않는다.
# ⚠️ 소비자가 같은 트리거를 처리 중이면 이 수동 실행은 **exit 1 로 양보한다**(route
# advisory lock, ALPHA-779) — 겹쳐 돌면 같은 트리거에 LLM 이 이중 과금된다. 재실행
# 자체를 막지는 않는다: 경합이 없으면 그대로 돈다.
python -m edge_analysis --trigger-id <trigger_id> --request-id manual-2

# 분봉 트리거 큐 상주 소비(ALPHA-719) — price-explanation-realtime 을 폴링해 위
# --trigger-id 경로를 태운다(ECS Service, 세션 결속 07:45~게이트 종료). 멱등은
# explanation_run 존재(route id 프리플라이트)로, 재시도는 SQS(visibility·DLQ)로 판정.
# 프리플라이트는 **처리가 끝나야** 참이라 처리 중 재배달은 못 거른다 — 그 창은 route
# advisory lock 이 닫는다(ALPHA-779, 락이 프리플라이트보다 먼저). 진 쪽은 메시지를
# 지우지 않고 처리 예산(900초)만큼 되돌린다: 지우면 이긴 쪽의 재배달 안전망이 사라지고,
# 짧게 되돌리면 재배달이 receive 예산(16)을 태운다.
# 분봉 window 원장·직전 거래일 파티션 미준비는 ReturnsNotReady 로 120초 지연 재시도.
# 그 사유는 `logger.info` 라 CLI 가 basicConfig(INFO) 를 건다 — 없으면 루트 기본
# WARNING 에 삼켜져 실패가 로그에 `start` 만 남긴다(08-05 실측 709건).
# 같은 큐의 ExposureReverted(가격이 전일 종가 1% 이내로 복귀, ALPHA-746)는 그 종목·세션의
# 분봉 기원 PUBLISHED 설명을 super-admin 무효화 API(로그인 세션 → /analyses/{run}/invalidate)
# 로 회수한다 — 엔진이 DB 를 직접 쓰지 않는다(INVALIDATION 발화자 단일화, ALPHA-440).
# 메시지마다 `consumer.message`(outcome·event_type·elapsed_s) 한 줄을 남긴다(ALPHA-908) —
# 수신부터 처분까지의 **점유 시간**이고 성공 갈래만이 아니다(deferred·locked·failed·
# malformed 도 큐를 점유한다). 이 레인의 대수를 정하는 입력이라 outcome·event_type 으로
# 갈라 백분위를 낸다. 다만 SIGTERM 뒤 stopTimeout 안에 안 끝난 처리는 이 줄이 없다.
# --max-polls 는 검증용: 계약 위반·처리 실패가 있으면 exit 1.
EDGE_EXPLANATION_QUEUE_URL=https://sqs.../price-explanation-realtime \
  python -m edge_analysis consume-triggers --max-polls 3
```

## 환경 변수

| 변수 | 용도 | 기본값 |
|---|---|---|
| `AWS_REGION` | S3/Secrets/DuckDB 리전. 하드코딩이 아니라 env 다 — 비면 다른 리전 배포에서 S3 뷰가 전량 NoSuchBucket 으로 빠진다 | `ap-northeast-2` |
| `ALPHAMALE_LAKE_BUCKET` | canonical 뉴스 S3 버킷 | `edge-dev-pipeline-lake` |
| `PGHOST`·`PGPORT`·`PGDATABASE`·`PGUSER`·`PGPASSWORD` | edge Postgres(Cloud Event Store) | — |
| `PGSCHEMA` | 스키마 | `public` |
| `DEEPSEEK_API_KEY` | 분류·설명 LLM | — (Secrets Manager 주입) |
| `DEEPSEEK_MODEL` | 모델명 | `deepseek-v4-flash` |
| `ALPHAMALE_RELEASE_BUNDLE_VERSION` | explanation_run 번들 고정 | — (필수, 없으면 런 실패) |
| `ALPHAMALE_RESULT_S3_PREFIX` | 런 아카이브 저장 위치 | `s3://<bucket>/operations_archive/etf_explanations/` |
| `ALPHAMALE_ETF_TICKER` | 대상 ETF | `091160` |
| `CAUSAL_ENABLED` | P0–P9 인과귀속 사용(끄면 단일 프롬프트 경로) | `true` |
| `CAUSAL_SANDBOX_ENABLED` | 검정 에이전트의 코드 실행. 끄면 축약 경로(고정 추정량) | `true` |
| `CAUSAL_REGISTRY_ROOT` | P9 메커니즘 레지스트리 경로. 비면 소환 기록을 남기지 않는다 — 단일 사례 귀속은 반복으로만 검정력을 얻으므로 이 경로가 비면 그 축적이 통째로 없다 | (없음) |
| `EDGE_RDB_DSN` | Postgres DSN(`host=… port=… dbname=…`). **비면 위 `PGHOST`/`PGPORT`/`PGDATABASE`/`PGUSER`/`PGPASSWORD` 로 조립한다** — 조립도 불가면 `coverage()["rdb"]=False` + 사유. 그 상태에선 사건 축 없이 갭+잔여만 남아 전 셀이 `PRICE_ONLY` 로 기운다 | (PG* 에서 조립) |
| `CAUSAL_BACKFILL_S3` | 백필 parquet(`pit_daily`·`fin_annual`·`flow_daily`·`sector_*`)의 S3 1순위 경로(후행 슬래시 없음). **비면 `v_pit`/`v_fin` 이 빈 스키마로 등록되고 판정이 사유 없이 0행** — "데이터가 없다"와 "통계가 안 섰다"가 같은 문장으로 나온다 | `s3://<bucket>/analysis/backfill` |
| `CAUSAL_BACKFILL_DIR` | 위 백필의 로컬 경로 겸 trace·ledger 쓰기 위치. 컨테이너에서 기본값(CWD 상대 `.tmp/causal-backfill`)은 쓰기 불가라 프롬프트 원문·원장이 조용히 유실된다 | `/tmp/causal-backfill` (로컬 기본 `.tmp/causal-backfill`) |
| `DUCKDB_S3_CHAIN` | DuckDB `CREDENTIAL_CHAIN` 순서. Fargate 는 컨테이너 자격증명 엔드포인트(`instance`)로만 붙는다 — `sso;config;env` 만 두면 `~/.aws` 가 없는 컨테이너에서 **S3 뷰 전량이 조용히 미등록**된다 | `env;instance;config;sso` |
| `DUCKDB_MEMORY_LIMIT` | DuckDB 메모리 상한. task 메모리보다 낮아야 `DUCKDB_TEMP_DIR` 로 spill 하며 버틴다 — 비면 컨테이너 한도를 먼저 쳐서 **OOMKilled(exit 137, 어느 질의였는지 로그에 안 남는다)** | `1.5GB` |
| `DUCKDB_TEMP_DIR` | spill 위치. 컨테이너에서 쓰기 가능한 곳은 `/tmp` 뿐 — 비면 spill 이 실패해 위 OOM 과 같은 결말 | `/tmp` |
| `AWS_PROFILE` | ⚠️ **컨테이너에는 넘기지 않는다.** 있으면 코드가 `PROFILE '…'` 절을 붙이고 프로필 파일이 없는 컨테이너에서 S3 접속이 죽는다. 로컬 개발에서만 쓴다 | (컨테이너 미주입) |
| `EDGE_DOMAIN_BUCKET` | 도메인 문서(「사업의 내용」) RAG 저장소. 비면 조회 도구 미부착 | (없음) |
| `EDGE_AWS_PROFILE` | 도메인 문서 버킷 접근 프로파일 (교차 계정일 때) | (기본 자격증명) |
| `CANONICAL_MANIFEST` | 생성 매니페스트(`infra/canonical/pit-manifest.yml`) 경로. 비면 재무·컨센서스·지배구조 어휘가 표면에 안 실린다 | (없음) |
| `CANONICAL_DATABASE` | canonical PIT 질의가 도는 Glue 데이터베이스 | (없음) — 예: `edge_lake_draft` |
| `CANONICAL_ATHENA_OUTPUT` | Athena 결과 저장 `s3://` 경로 | (없음) |
| `EDGE_ATHENA_WORKGROUP` | 시각창 집계 Athena 오프로드 워크그룹(ALPHA-780). **IAM 정책이 스코프로 쓰는 유일한 이름**이라 terraform 이 이 값만 주입한다 — 정책과 갈리면 질의 제출이 AccessDenied 다 | `market_data` |
| `EDGE_ATHENA_DB` · `EDGE_ATHENA_BARS_TABLE` | 시각창 패널 질의가 읽는 Glue 데이터베이스·5분봉 표. IAM 은 `glue:Get*` 를 `"*"` 로 주므로 정책과 안 엮여 컨테이너에 주입하지 않는다(코드 기본값) | `market_data_kr` · `edge_intraday_5m` |
| `EDGE_ATHENA_OUTPUT` | ⚠️ **컨테이너에는 넘기지 않는다.** 워크그룹이 결과 위치를 강제(`EnforceWorkGroupConfiguration=True`)하므로 같이 보내면 질의가 시작조차 못 한다. 코드는 이 값이 **설정됐을 때만** `ResultConfiguration` 을 붙인다 — 평소엔 워크그룹에 맡기고 실제 위치는 응답에서 확인한다 | (컨테이너 미주입) |
| `SUPER_ADMIN_API_URL` | ExposureReverted 회수 집행 대상(ALPHA-746) — super-admin-api base URL. 소비자 전용 | (없음 — 비면 회수 경로 fail-loud) |
| `SUPER_ADMIN_EMAIL`·`SUPER_ADMIN_PASSWORD` | 회수용 운영자 자격 (SSM SecureString 주입 — `/edge-dev-data-pipeline/super-admin/operator-email`·`operator-password`) | — |

## 배포

컨테이너 이미지는 `src/` 컨텍스트에서 `-f apps/cloud/analysis-engine/Dockerfile`로 빌드한다. 실행 인프라는 `infra/terraform/modules/data-pipeline`이 정의한다 — **분봉 트리거 큐를 소비하는 상주 ECS 서비스**(`minute_services.tf`의 `analysis-consumer`, task definition `edge-dev-data-pipeline-analysis-consumer`)로 돈다. 통합 파이프라인 SFN의 책임은 feature 까지고 analyze 페이즈는 없다(ALPHA-806 — 트리거 없이 도는 일 단위 분석은 장중에 층을 못 세워 분봉 경로와 다른 답을 냈다). 수동 재실행은 트리거 단건 재처리다: 같은 task-def 를 `aws ecs run-task`로 띄워 `--trigger-id`를 넘긴다. CI는 `.github/workflows/deploy-analysis-engine.yml`.

## 스키마 계약

Cloud Event Store(`libs/schema` SSOT, `public` 스키마)에서 **쓰는** 테이블은 분석 산출물뿐이다: `etf_contribution_observation`·`etf_contribution_member`·`explanation_route`·`explanation_run`·`explanation_result`. `price_movement_trigger`·`document`/`assertion`·`source_event`/`event_thread` 계열(`event_argument`·`event_measure` 포함)은 **읽기만** 한다(writer 는 data-pipeline — ALPHA-411·412).

## 주석 컨벤션

프로덕션 코드는 **WHAT은 코드가, WHY는 주석이** 원칙을 따른다(Google Python Style Guide).

- **docstring = 계약 (Google §3.8.1)**: 모듈·클래스·공개 함수/메서드는 **자명해도** docstring 을
  단다(1줄 요약). **생략은 사적(`_` 접두)이면서 짧고 자명한 것에 한한다.** 이름·타입으로
  드러나지 않는 것만 `Returns:`/`Raises:` 를 덧붙인다.
- **인라인 = WHY만**: 불변식·함정·비자명한 선택(지연 import 근거, 결정적 ID 교차 계약 등)과
  티켓 참조(ALPHA-###). WHAT(코드가 이미 말하는 것) 재진술은 금지.
- **금지**: 코드 재진술, 주석 처리된 죽은 코드, 변경 이력 주석(git 소관), 실제와 어긋나는 주석.
- **언어**: 한국어.

## 테스트 컨벤션

이 앱의 테스트는 레이어드 구조를 반영해 **Google Python Style Guide 관행**을 따른다
(레포 기본 관례와 다른 이 앱 한정 로컬 규약).

- **레이아웃**: 소스 레이어를 미러링한다 — `tests/domain/`·`tests/adapters/`, 앱-레벨은
  루트(`tests/test_config.py`·`test_observability.py`·`test_pipeline.py`). 파일명은 `test_<모듈>.py`.
- **구조**: 테스트당 하나의 동작, 서술적 이름(`test_...`), AAA(Arrange-Act-Assert)를 빈 줄로 구분.
- **주석**: 한국어. 모듈 도크스트링은 1줄 요약(+ 필요 시 검증 의도), 인라인 주석은 이름으로
  드러나지 않는 WHY만 짧게(AGENTS Rule 9). 장황한 설명은 지양한다.
- **격리**: `domain/`은 순수라 무거운 의존 없이 단위 검증, `adapters/`는 fake(가짜 conn·S3·
  client), `pipeline`은 의존성 주입으로 검증한다(monkeypatch·실 DB·네트워크 없음).
- **실행**: `uv run pytest`.
