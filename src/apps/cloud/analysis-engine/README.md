# analysis-engine

ETF **당일 설명 생성** 파이프라인 (Python, edge-cloud). 대상 ETF 는 `ALPHAMALE_ETF_TICKER`
(기본 `091160` KODEX 반도체)로 받고, 표시명·구성종목명은 전부 그 ETF 의 canonical holdings·
마스터에서 파생한다 — KODEX 반도체 하드코딩은 없다(ALPHA-467). run() 은 한 번에 ETF 한 종을 돈다.
통합 파이프라인 SFN의 analyze 페이즈로 실행되며, 트리거가 없으면 오늘(Asia/Seoul) 기준으로 실행된다.

> ALPHA-411·412(완전 분리, ADR-0028) 이후 이 앱은 **feature 산출물의 소비자**다: L0 게이트는
> `load-price-triggers`가 쓴 `price_movement_trigger`를 소비하고, 이벤트는 `assemble-events`가
> 조립한 `source_event` 계보를 읽는다. 뉴스 읽기·제목 분류·계보 조립·threading 은 feature
> 페이즈(data-pipeline)로 이관됐다.

## 흐름

```
price_movement_trigger 소비 (행 없음 = 평온 → 종료)
  → 구성종목 분해(가격 S3 읽기 — observation·packet 입력)
  → observation/route 적재 (소비한 trigger_id 에서 파생)
  → DB 의 대상 ETF 구성종목 source_event/thread 조회 — 참여자(event_argument)·측정값
    (event_measure)을 사건 단위 EventContext 로 집계 (assemble-events 산출)
  → 분석 에이전트(DeepSeek) → explanation_result (PUBLISHED) + tenant_delivery NEW
    fan-out (전 테넌트, 게시와 같은 트랜잭션 — ALPHA-493)
```

- `explanation_result` FK 전제(etf_profile·explanation_route·release_bundle)가 없으면 임의 값을 만들지 않고 결과를 S3에 쓰고 로그로 알린다.
- **그날 첫 결과만 게시·발번한다** — 같은 (ETF, trade_date) 재실행은 DRAFT 보존 + 발번 생략(`publish_skipped` 로그). WITHDRAWN 후 재게시(CORRECTION 발번)는 후속 티켓 몫.
- **매 런(평온 종료 포함) 런 아카이브 1건을 S3에 남긴다**(ALPHA-415) — `{result prefix}/runs/etf=…/trade_date=…/{request_id}.json`. 분해 요약·소비 트리거·route·이벤트·LLM 원문(verdict/key_evidence/unexplained — explanation_result 매핑에서 손실되는 필드)·영속 결과가 담긴다. 기록 실패는 런을 죽이지 않는다(관측은 본업이 아니다).

## 구조

레이어드 패키지(ports & adapters). `domain/`은 순수 로직·모델(I/O 없음), `adapters/`는 I/O
경계, `pipeline`은 의존성 주입 오케스트레이션, `cli`는 composition root다.

```
src/edge_analysis/
  __main__.py · cli.py · config.py · observability.py · pipeline.py
  domain/     models.py · decomposition.py · packet.py       # 순수, stdlib top-level import
  adapters/   lake.py · eventstore.py · llm.py · archive.py  # I/O, 무거운 deps 지연 import
```

## 실행

```
python -m edge_analysis                       # 오늘(Asia/Seoul)
python -m edge_analysis --trade-date 2026-07-14 --request-id manual-1
```

## 환경 변수

| 변수 | 용도 | 기본값 |
|---|---|---|
| `AWS_REGION` | S3/Secrets 리전 | `ap-northeast-2` |
| `ALPHAMALE_LAKE_BUCKET` | canonical 뉴스 S3 버킷 | `edge-dev-pipeline-lake` |
| `PGHOST`·`PGPORT`·`PGDATABASE`·`PGUSER`·`PGPASSWORD` | edge Postgres(Cloud Event Store) | — |
| `PGSCHEMA` | 스키마 | `public` |
| `DEEPSEEK_API_KEY` | 분류·설명 LLM | — (Secrets Manager 주입) |
| `DEEPSEEK_MODEL` | 모델명 | `deepseek-v4-pro` |
| `ALPHAMALE_RELEASE_BUNDLE_VERSION` | explanation_run 번들 고정 | (없으면 S3 fallback) |
| `ALPHAMALE_RESULT_S3_PREFIX` | FK 전제 없을 때 설명 결과 저장 위치 | `s3://<bucket>/operations_archive/etf_explanations/` |
| `ALPHAMALE_ETF_TICKER` | 대상 ETF | `091160` |

## 배포

컨테이너 이미지는 `src/` 컨텍스트에서 `-f apps/cloud/analysis-engine/Dockerfile`로 빌드한다. 실행 인프라는 `infra/terraform/modules/data-pipeline`이 정의한다(ALPHA-408에서 전용 모듈·SFN을 흡수) — 통합 파이프라인 SFN(raw→normalize→feature→**analyze**)의 마지막 페이즈로 돌며, task definition 은 `edge-dev-data-pipeline-analysis`. 특정일 수동 재실행은 이 task-def 를 `aws ecs run-task`로 직접 띄워 `--trade-date`/`--request-id`를 넘긴다. CI는 `.github/workflows/deploy-analysis-engine.yml`.

## 스키마 계약

Cloud Event Store(`libs/schema` SSOT, `public` 스키마)에서 **쓰는** 테이블은 분석 산출물뿐이다: `etf_contribution_observation`·`etf_contribution_member`·`explanation_route`·`explanation_run`·`explanation_result`·`explanation_run_event_evidence`(설명 실행이 사용한 근거 lineage — ALPHA-603)·`tenant_delivery`(NEW write-time fan-out — ALPHA-493). `price_movement_trigger`·`document`/`assertion`·`source_event`/`event_thread` 계열(`event_argument`·`event_measure` 포함)과 `event_evidence`·`tenant`(fan-out 대상 목록, writer 는 super-admin-api) 는 **읽기만** 한다(트리거·이벤트 계열 writer 는 data-pipeline — ALPHA-411·412). lineage 는 `event_evidence` 를 **참조만** 하고 그 행을 만들지 않는다.

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
