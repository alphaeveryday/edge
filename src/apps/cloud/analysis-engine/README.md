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
  → DB 의 대상 ETF 구성종목 source_event/thread 조회 (assemble-events 산출)
  → 분석 에이전트(DeepSeek) → explanation_result (DRAFT)
```

- `explanation_result` FK 전제(etf_profile·explanation_route·release_bundle)가 없으면 임의 값을 만들지 않고 결과를 S3에 쓰고 로그로 알린다.
- **매 런(평온 종료 포함) 런 아카이브 1건을 S3에 남긴다**(ALPHA-415) — `{result prefix}/runs/etf=…/trade_date=…/{request_id}.json`. 분해 요약·소비 트리거·route·이벤트·LLM 원문(verdict/key_evidence/unexplained — explanation_result 매핑에서 손실되는 필드)·영속 결과가 담긴다. 기록 실패는 런을 죽이지 않는다(관측은 본업이 아니다).

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
| `DEEPSEEK_MODEL` | 모델명 | `deepseek-chat` |
| `ALPHAMALE_RELEASE_BUNDLE_VERSION` | explanation_run 번들 고정 | (없으면 S3 fallback) |
| `ALPHAMALE_RESULT_S3_PREFIX` | FK 전제 없을 때 설명 결과 저장 위치 | `s3://<bucket>/operations_archive/etf_explanations/` |
| `ALPHAMALE_ETF_TICKER` | 대상 ETF | `091160` |

## 배포

컨테이너 이미지는 `src/` 컨텍스트에서 `-f apps/cloud/analysis-engine/Dockerfile`로 빌드한다. 실행 인프라는 `infra/terraform/modules/data-pipeline`이 정의한다(ALPHA-408에서 전용 모듈·SFN을 흡수) — 통합 파이프라인 SFN(raw→normalize→feature→**analyze**)의 마지막 페이즈로 돌며, task definition 은 `edge-dev-data-pipeline-analysis`. 특정일 수동 재실행은 이 task-def 를 `aws ecs run-task`로 직접 띄워 `--trade-date`/`--request-id`를 넘긴다. CI는 `.github/workflows/deploy-analysis-engine.yml`.

## 스키마 계약

Cloud Event Store(`libs/schema` SSOT, `public` 스키마)에서 **쓰는** 테이블은 분석 산출물뿐이다: `etf_contribution_observation`·`etf_contribution_member`·`explanation_route`·`explanation_run`·`explanation_result`. `price_movement_trigger`·`document`/`assertion`·`source_event`/`event_thread` 계열은 **읽기만** 한다(writer 는 data-pipeline — ALPHA-411·412).
