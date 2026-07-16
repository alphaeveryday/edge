# analysis-engine

KODEX 반도체 ETF의 **당일 뉴스 정규화 + 설명 생성** 파이프라인 (Python, edge-cloud).
Step Functions state machine가 ECS Fargate 태스크 하나로 호출하며, 트리거가 없으면 오늘(Asia/Seoul) 기준으로 실행된다.

> 이전 리비전(FF5 → 뉴스 NN → 회귀 3-stage 수익률 모델)을 대체한다. 이 앱은 S3 canonical lake의 뉴스 **제목만** 읽어 Cloud Event Store에 정규화 이벤트를 적재하고, KODEX 구성종목 이벤트를 스레드화한 뒤 분석 에이전트로 설명을 생성한다.

## 흐름

```
S3 canonical/news (제목만)
  → 시드된 엔터티를 언급한 미정규화 뉴스 선별 (idempotent)
  → DeepSeek 제목 게이트/타입 판정 (ontology 검증)
  → document → document_assertion → source_event (canonical event)
  → KODEX 구성종목 이벤트 스레드화 (event_thread*)
  → 분석 에이전트(DeepSeek) → explanation_result (DRAFT)
```

- 뉴스 **본문·리드·URL·S3 원문은 모델 입력에 넣지 않는다** (제목 전용).
- **정규화 안 된 뉴스만** canonical event가 된다(문서 source id + 결정적 ID로 idempotent).
- `explanation_result` FK 전제(etf_profile·explanation_route·release_bundle)가 없으면 임의 값을 만들지 않고 결과를 S3에 쓰고 로그로 알린다.

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

컨테이너 이미지는 `src/` 컨텍스트에서 `-f apps/analysis-engine/Dockerfile`로 빌드하고, `infra/terraform/modules/analysis-engine`가 ECS 태스크와 Step Functions state machine를 정의한다. CI는 `.github/workflows/deploy-analysis-engine.yml`.

## 스키마 계약

Cloud Event Store(`libs/schema` SSOT, `public` 스키마)의 `document`·`news_document`·`document_entity`·`document_assertion`·`assertion_argument`·`source_event`·`event_argument`·`event_evidence`·`event_thread`·`event_thread_link`·`thread_discovery_snapshot`·`explanation_run`·`explanation_result`에 적재한다.
