# data-pipeline

> 역할/아키텍처는 [docs/repo-structure.md](../../../../docs/repo-structure.md)·[docs/context.md](../../../../docs/context.md)가 SSOT.
> 이 문서는 로컬 실행·설정 계약·범위 경계만 둔다.

## 상세 문서

| 문서 | 내용 |
|---|---|
| [docs/overview.md](docs/overview.md) | 배치 단계(원본저장·정제·feature·적재)와 1분 장중 파이프라인의 구성 요소·도입 경위 |
| [docs/run-steps.md](docs/run-steps.md) | `run` 서브커맨드별 실행 예시와 주의 |
| [docs/deploy-schedule.md](docs/deploy-schedule.md) | 배포 이미지·SFN·Airflow·EventBridge 스케줄 |
| [docs/lake-contract.md](docs/lake-contract.md) | 레이크 경로 규약과 Minute 내용 주소 계약 |
| [docs/backfill.md](docs/backfill.md) | 포워드와 격리된 재구축(백필) 경로 |
| [docs/ops-ledger.md](docs/ops-ledger.md) | 운영 원장 — Planner·Reconciler 와 복구 절차 |

## 실행

Python 도구는 **uv**다(ADR-0001). Python 워크스페이스 루트는 `src/pyproject.toml`.

```bash
uv sync --package data-pipeline --group dev                         # src/에서 의존성 설치
uv run --package data-pipeline --group dev pytest apps/cloud/data-pipeline/tests
```

서브커맨드별 실행 예시(`run ingest-raw`·`normalize-*`·`load-*`·1분 세션 등)는 [docs/run-steps.md](docs/run-steps.md) 에 있다.

> uv가 없는 환경이면 표준 venv로 같은 일을 한다(`src/apps/cloud/data-pipeline`에서, pip ≥ 25.1):
> ```bash
> python3 -m venv .venv
> .venv/bin/pip install -e . --group dev   # dev 그룹(pytest)은 PEP 735 [dependency-groups]
> .venv/bin/pytest
> ```

## 설정 계약

수집 설정은 **TOML 베이스 파일 + 환경변수 오버라이드**로 로드한다. 진입점은 하나다:

```python
from data_pipeline import load_settings

settings = load_settings()           # 패키지 동봉 기본 설정 + env
settings.news.sources                # {이름: NewsSource}
settings.bigkinds_news               # BigKindsNewsSource (국내 뉴스 — 키 없음·카테고리 주도 전체 수집, category_codes 필수); 미설정이면 None
settings.price.source                # PriceSource (FMP EOD — 가격 전용 심볼맵, 현재 US)
settings.kis_price.source            # KisPriceSource (KIS 국내 일봉 — 앱키/시크릿 env·env=prod|vps); 미설정이면 settings.kis_price 은 None
settings.financial.source            # FinancialSource (FMP 재무 — 재무 전용 심볼맵, 현재 US); 미설정이면 settings.financial 은 None
settings.dart_financial.source       # DartFinancialSource (OpenDART 국내 재무 — 인증키 env·KR 6자리 맵); 미설정이면 settings.dart_financial 은 None
settings.dart_disclosure.source      # DartDisclosureSource (OpenDART 국내 공시 — 인증키 env·KR 맵·report_nm 유형필터); 재무와 다른 API. 미설정이면 settings.dart_disclosure 은 None
settings.etf.source                  # EtfSource (FMP 미국 ETF holdings — 인증키 env·ETF 전용 맵 etf_map, 현재 US); 미설정이면 settings.etf 은 None
settings.targets.symbols             # ["005930", ...]
settings.targets.keywords            # ["금리", ...]
```

- **구조/공개값** → [`src/data_pipeline/config/sources.toml`](src/data_pipeline/config/sources.toml).
  패키지에 **동봉돼 배포되는 기본 설정**이라 wheel 설치에서도 `load_settings()`가 그대로 동작한다.
  수집 대상은 `[targets]`만 바꾸면 fetcher 대상이 바뀐다 — 코드 수정 불필요.
- **비밀값(api_key 등)** → 커밋하지 말고 **환경변수**로 주입한다. 같은 경로의 env가 파일을 덮어쓴다(`env > file`):
  ```bash
  # news.sources.naver.api_key 를 주입
  export DATA_PIPELINE_NEWS__SOURCES__NAVER__API_KEY=...
  ```
  접두어 `DATA_PIPELINE_`, 중첩 구분자 `__`.
- **파일 경로**: `load_settings(path)` 인자 > `DATA_PIPELINE_CONFIG_FILE` env > 동봉 기본 설정.
  배포 환경(dev/prod)은 보통 env로 외부 설정 파일을 가리켜 동봉 기본값을 대체한다.
- **명시적 실패**: 필수값 누락·알 수 없는 키·대상 0개·공백 값·파일 없음은 조용한 기본값 대신
  `ConfigError`로 드러난다(AGENTS Rule 12). 단, 최상위 섹션이 없는 `DATA_PIPELINE_*` env 키(오타 포함)는
  pydantic-settings 표준 동작상 조용히 무시된다. **있는 섹션 아래의 모르는 키**(예: 이전 이미지에
  `DATA_PIPELINE_MINUTE_PRICE_WORKER__FETCH_CONCURRENCY`)는 `extra="forbid"`로 기동을 거부한다(ALPHA-1087 실측).

### KIS 공유 호출 예산 (ALPHA-1087, 기본 비활성)

KIS 호출자(분봉 워커·업종지수·iNAV·EOD 배치 등)는 기본적으로 **프로세스마다 따로** 간격을 둔다
(`KIS_MIN_INTERVAL_SEC`). 그래서 합계가 앱키 한도를 넘을 수 있다. 공유 예산을 켜면 모든 호출이 PostgreSQL
`call_budget`·`call_budget_class` 표에서 발신 슬롯을 받는다. 클래스 우선순위도 적용된다:
0 분 가격 워커 > 1 발화 보충(아직 호출자 없음) > 2 장중 레인(iNAV·업종지수·장중 수급) > 3 EOD 배치·과거일 백필. 허용 저장소에 닿지 못하거나 대기 상한을 넘기면 호출하지 않는다.
이때 `CallBudgetError`(`StopFetch` 계열 — 소스 수집 중단)를 낸다. 오류 문자열은 `CALL_BUDGET_UNAVAILABLE`·
`CALL_BUDGET_DEADLINE`·`CALL_BUDGET_MISCONFIGURED` 로 시작한다. `failures.http_failure()` 로 분류하는 경로에서는 실패 코드가
`CALL_BUDGET_BLOCKED`(TRANSIENT)이고, `StopFetch` 를 직접 기록하는 경로(일봉 가격·수급 등)는 `status='stopped'` 와 그 오류 문자열만 남긴다.
저장소 호출 1회(잠금 대기·이름 해석·연결·TLS·질의)는 `statement_timeout_ms` + 0.5s 안에 끝난다. DNS 도 이 기한 안에서
풀어 libpq 에 주소로 넘긴다(libpq 는 이름을 동기로 푼다 — 무응답 DNS 에서 20초 실측). 응답을 못 받으면 서버가 예약을
확정했어도 발신하지 않고, 그 커넥션은 버린다. `pace()` 전체는 `max_wait_sec` 안에 끝나고, `pace()` 가 돌아온 뒤 HTTP
연결까지의 지연은 이 기한에 들지 않는다. 결정과 검증 범위는 [ADR-0055](../../../../docs/adr/0055-kis-shared-call-budget-on-postgres.md)에 있다.

- 켜기: `DATA_PIPELINE_CALL_BUDGET__ENABLED=true`(terraform `call_budget_enabled`, 기본 `false`).
  그 밖의 키(`BUDGET_ID`·`RTT_MAX_SEC`·`SEND_WINDOW_SEC`·`MAX_WAIT_SEC` 등)는 `CallBudgetConfig` 가
  정본이다. 기본값 25ms·50ms 는 **로컬 실험 설정**이고, 운영 측정으로 확정한 값이 아니다.
- 분봉 워커 동시 요청 `DATA_PIPELINE_MINUTE_PRICE_WORKER__FETCH_CONCURRENCY`(기본 1, 최대 4)는
  공유 예산이 켜졌거나 전용 키 선언(아래)이 있을 때만 적용된다. 둘 다 아니면 경고를 남기고 1로 돈다. terraform 은
  `call_budget_enabled` 가 `true`면 2를, 전용 키 배선(아래)이 켜져 있으면 4를 싣는다. 둘 다 아니면 변수를 싣지 않는다(코드 기본 1). 이 필드를 모르는 이전 이미지가
  기동을 거부하지 않게 하기 위해서다 — 머지 배포에서 terraform-apply 가 이미지 배포보다 먼저 끝날 수 있다.
- 전용 키 선언 `DATA_PIPELINE_MINUTE_PRICE_WORKER__DEDICATED_APP_KEY`(기본 `false`, ALPHA-1247)는 분봉 워커의 앱키를
  다른 KIS 호출자가 쓰지 않을 때만 켠다. 켜면 공유 예산 없이도 위 동시 요청 수로 수집하고, 발신 속도는 워커의 호출 간격
  (`DATA_PIPELINE_MINUTE_PRICE_WORKER__MIN_INTERVAL_SEC`)이 정한다. 같은 키를 쓰는 호출자가 있는데 켜면 합산 발신이
  한도를 넘는다. terraform 은 `minute_price_dedicated_kis_enabled` 가 `true`일 때만 이 값을 싣고, 함께 동시 요청 4·호출 간격
  0.0625초를 싣고 워커의 KIS 키와 토큰 캐시를 2번으로 바꾼다(ALPHA-1248). dev 는 켜져 있다(ALPHA-1252).
- 운영 CLI(DB env 필요):
  ```bash
  python -m data_pipeline.sources.call_budget init kis 15     # 표·클래스 시드(이미 있으면 그대로)
  python -m data_pipeline.sources.call_budget pause kis       # 신규 허용 중단 + 예약 소진까지 대기
  python -m data_pipeline.sources.call_budget resume kis
  python -m data_pipeline.sources.call_budget status kis      # (id, rate, paused, 다음 슬롯까지 초; 음수=예약 없음)
  ```
  전환·롤백은 **세션 밖(야간)** 에 하고, 켠 상태에서 문제가 나면 `pause` 를 쓴다.
  `false` 로 되돌리면 호출자가 다시 제각각 간격을 두므로, 앱키 한도 준수는 보장되지 않는다(전환 전 상태와 같다).
- 수동 5분 백필(`scripts/backfill_intraday_5m.py`)은 KIS 태스크 정의에 공유 예산이 켜져 있으면
  시작하지 않는다. 이 스크립트는 예산 밖에서 호출하기 때문이다. 다만 이 가드가 완전한 차단은 아니며,
  1차 통제는 운영 절차다.
- 현재 상태(2026-09-29): dev에 **비활성으로 배포**됐다(스키마 적용, 예산 행 미초기화). 켜기 전 계측·실환경 측정·절차는
  ALPHA-1124·ALPHA-1125, 이미지 의존성 고정은 ALPHA-1126이 맡는다. 결정·검증 범위는 ADR-0055에 있다.

### KIS 호출 계측 — 분 가격 창 요약 로그 (ALPHA-1124)

분 가격 워커는 창 하나를 수집할 때마다 요약 한 줄을 남긴다(`data_pipeline.minute.kis_collector`, INFO).
수집이 예외로 끝난 창도 `status=RAISED` 로 남긴다.

```
kis.http.window caller=minute-price window=2026-10-02T05:32:00+00:00 status=VALID units=451 elapsed_ms=71200 attempts=463 rtt_ms=58300 rtt_max_ms=1900 pace_wait_ms=4100 transport_retry=1 transport_backoff_ms=1000 kis_EGW00201=12 rate_sleep_ms=8400 rate_exhausted=0 err_http_502=1
```
(값은 형식 예시다 — 실측이 아니다.)

| 항목 | 뜻 |
|---|---|
| `attempts` | 실제 HTTP 발신 횟수. 토큰 발급과 재시도를 포함한다(성공 건수와 다르다) |
| `rtt_ms` / `rtt_max_ms` | 발신부터 응답 본문 수신까지의 합계·최대. **KIS 응답 지연**은 여기에 쌓인다. 연결을 새로 맺는 호출은 그 수립 시간(TCP·TLS)도 여기에 든다 |
| `pace_wait_ms` | 발신 간격(또는 공유 호출 예산)을 기다린 시간 |
| `kis_<코드>` | 거절 응답(`rt_cd≠0`)의 `msg_cd` 별 건수. 재시도로 끝내 성공해도 센다. 코드 형상이 아니면 `kis_OTHER` |
| `rate_sleep_ms` | `EGW00201` 뒤 물러난 시간의 합. **유량 제한**은 `kis_EGW00201` 과 여기에 쌓인다. ⚠️ 2026-10-06 실측에서 KIS 는 `EGW00201` 을 전부 **HTTP 500** 으로 줬다 — 그 경우 운반 계층이 5xx 로 재시도하므로 이 두 항목은 0 으로 남고 `err_http_500`·`transport_retry` 에 쌓인다 |
| `rate_exhausted` | `EGW00201` 재시도 예산(5회)을 다 쓴 종목 수 |
| `transport_retry` / `transport_backoff_ms` | 5xx·네트워크 실패 재시도 횟수와 그 대기 |
| `err_<종류>` | 발신 실패 종류별 건수 — `err_http_503`, `err_TimeoutError` 등(상태코드·예외 클래스명) |
| `connects` | 연결 재사용 경로에서 연결을 맺으려 한 횟수(실패한 시도 포함). 실시간 수집은 KIS 연결 8개를 돌아가며 다시 쓴다(ALPHA-1153) — 동시 요청 1 이면 창당 0~8건 남짓이고(앞 창의 연결이 살아 있으면 0 이라 항목이 붙지 않는다), 동시 요청 N 이면 스레드마다 8개라 그 N 배까지다. `attempts` 에 가까우면 재사용이 안 되고 있는 것이다 |
| `keep_alive_off` | 재사용 경로의 5xx 가 60초에 20건 쌓여 워커가 재사용을 스스로 끈 횟수. 그 뒤로는 호출마다 새 연결로 보낸다(WARNING `연결 재사용을 끈다` 도 함께 남는다) |

- 읽는 법: 동시 요청 1(기본)에서 `elapsed_ms ≈ rtt_ms + pace_wait_ms + rate_sleep_ms + transport_backoff_ms` 다.
  어느 항이 늘었는지가 원인을 가른다. 동시 요청이 켜지면 합계가 겹쳐 `elapsed_ms` 보다 커진다.
- `attempts` 부터 `rate_exhausted` 까지 아홉 항목은 0 이어도 **이 순서로** 싣는다. 그 밖의 `kis_`·`err_`·`connects`·`keep_alive_off`
  항목은 발생했을 때만 뒤에 붙는다. 이 로그가 배포되기 전 기간은 "발생 0회"가 아니라 "미관측"으로 읽는다.
- 앱키·시크릿·토큰·URL·응답 본문·종목 코드는 싣지 않는다. 종목별 상세는 원장 `missing_units` 를 쓴다.
- 조회(CloudWatch Logs Insights, 분 가격 워커 로그 그룹):
  ```
  filter @message like "kis.http.window caller=minute-price"
  | parse @message /elapsed_ms=(?<elapsed>\d+) attempts=(?<attempts>\d+) rtt_ms=(?<rtt>\d+) rtt_max_ms=(?<rtt_max>\d+) pace_wait_ms=(?<pace>\d+) transport_retry=(?<t_retry>\d+) transport_backoff_ms=(?<t_backoff>\d+) kis_EGW00201=(?<egw>\d+) rate_sleep_ms=(?<rate_sleep>\d+) rate_exhausted=(?<exhausted>\d+)/
  | stats count() as windows, pct(elapsed, 50) as elapsed_p50, sum(attempts) as sends, sum(egw) as egw00201,
          sum(rate_sleep) as rate_sleep_ms, sum(pace) as pace_wait_ms, sum(t_retry) as transport_retry,
          sum(rtt) / sum(attempts) as rtt_avg_ms, max(rtt_max) as rtt_max_ms by bin(30m)
  ```
- 범위: 운반 계층(`PoliteClient.stats`) 계측은 모든 호출자에 들어 있지만, **요약 로그를 내는 것은 분 가격
  워커뿐**이다. 나머지 KIS 어댑터(iNAV·업종지수·일봉·수급·ETF 프로파일)의 거절 코드 집계와 토큰 발급
  카운터는 아직 없다(ALPHA-1124 남은 범위).

## 범위에서 의도적으로 제외한 것 (후속)

- 뉴스 근접중복 클러스터링(fuzzy)·교차벤더 dedup — canonical 은 exact article_id 병합 + 제목/URL
  충돌 로깅까지다. dedup_cluster·엔티티/컨셉 링크는 후속. **이벤트 태깅은 이 모듈 소관으로
  들어왔다**(ALPHA-138, `tagging/` 참조) — 피처 추출까지가 data-pipeline 경계이고, 그 피처를
  소비하는 분석(event 조립·스레드·가격 설명)이 analysis-engine 소관이다.
- 가격 factor·지표 계산 — canonical price_daily 위의 수정주가 파생·거래일 캘린더 정합(휴장일)·
  섹터 태깅·수익률/지표는 후속(S006·S007 이후 Curation). 정제(정규화·정합성·멱등 적재)까지는 완료.
- 재무제표 canonical 적재·지표(Factor) 계산 — raw financial_statements → 후속 Structuring/Curation
- 공시(disclosure) graph·eventization — 공급계약 fact(ALPHA-345)·사업부문 fact(ALPHA-346, pandas
  4-전략 파싱 → `canonical/disclosures/business_segment_fact`) 정제는 완료. graph 투영·theme 링킹·
  event 는 다운스트림(analysis-engine) 소관.
- 공시 **정정 supersession(point-in-time)** — 공급계약 canonical 은 파일링당 fact 를 rcept_no 로
  투영한다. 원본과 정정본([기재정정]…체결)은 서로 다른 rcept_no 라 각각 남고, 어느 정정본이 어느
  원본을 대체하는지의 링크는 list.json 행에 없다(정정 관련 필드·문서 파싱 필요; 원본이 정정 이전에
  수집되면 rm 마커조차 없음). 정정↔원본 collapse·이중계산 해소는 정체성 해소/SCD 문제라 후속
  트랙 소관이다(뉴스가 near-dup 를 news_dedup_cluster 로 미루는 것과 동형).
