# 뉴스 수집 (S002/S003) — 작업 문서 (에이전트 스크래치, git 미추적)

> 목적: 이 작업이 커져서 컨텍스트가 흩어짐. 다시 열었을 때 빠르게 복원하기 위한 개인 참조 문서.
> 상태: **구현 완료 — PR 3개 오픈, 리뷰/머지 대기** (2026-07-02 구현).
> 최종 갱신 맥락: §6 의 PR 3개를 모두 구현·푸시·오픈함.

## 0-1. 구현 결과 (2026-07-02)
- **PR 순차 진행**(사용자 피드백: 한 번에 여러 PR 금지) + **인프라는 인프라 담당자 소관**(사용자가 못 함): 현재 **#41 만 리뷰 가능**, #40(Terraform)은 draft 로 인프라 담당자 대기, #42 는 #41 머지 후 리베이스→ready. 코드 PR(#41/#42)은 로컬 스토리지 스텁이라 인프라 없이 머지 가능 — 실배포(스케줄 실행)만 #40 apply 필요.
- PR #40 `feature/ALPHA-129-s3-lake-terraform` — s3-lake·ecs-scheduled-task 모듈 + envs/dev 배선. terraform fmt/validate 통과(docker). **plan/apply 미실행**. 스케줄 enabled=false(placeholder cron 매시).
- PR #41(draft) `feature/ALPHA-103-fmp-raw-ingest` — Step1. lake/storage(local|s3)·sources/fmp(SYMBOL_MAP 코드 상수)·parse·dedup·steps/ingest_raw·run.py + `[storage]` 설정 + targets US9+KR9. 테스트 37개.
- PR #42(draft) `feature/ALPHA-104-news-normalize-canonical` — Step2 (**#41 위 스택 — #41 먼저 머지 후 리베이스**). quality 게이트(4필드)·steps/normalize(canonical parquet 멱등 병합)·bodies 분리 데이터셋. 테스트 45개.
- 머지 후 남은 일: (1) terraform apply + FMP 시크릿 값 주입(`edge-dev/data-pipeline/fmp`, JSON key `api_key`), (2) data-pipeline 이미지 빌드/push 배포 파이프라인(스케줄 활성화 전제), (3) 스케줄 cron 확정, (4) Jira ALPHA-103/104/129 상태 전환.

---

## 0. 한 줄 요약
FMP 단일 소스 뉴스를, **Step1 원본저장(raw, S3)** + **Step2 정규화·품질검증(canonical, S3)** 2스텝으로 `s3://stock-ai-lake/` 레이어드 레이크에 적재. Postgres/Flyway 미사용. IaC는 기존 Terraform 위에 s3-lake·scheduled-task·worker-cluster 모듈 추가.

## 1. 스토리 매핑 (Jira ALPHA / 에픽 ALPHA-90 "기초 데이터 파이프라인", 프로젝트 alphaeveryday, cloudId ef3aff91-ed27-4a0e-b3b9-424067f4d5f0)
- **S002 = ALPHA-103** (label s002, TODO): 등록 소스에서 신규 목록 수집·**중복없이 저장** → **Step1 원본저장**.
- **S003 = ALPHA-104** (label s003, TODO, 서브태스크 ALPHA-253 "제목·발행시각·언론사·URL 1차 저장 로직 7h"): URL에서 제목·발행시각·언론사 추출 → 1차 저장 → **Step2 정규화·품질**.
- **S028 = ALPHA-129** (Raw 저장): raw zone 저장 → **Step1(S002)에 흡수 확정**(S002와 중복).
- S001=ALPHA-102(완료, config), S004=ALPHA-105(가격), S005=ALPHA-106(공통스키마=Step2 일반화, 후속), S006/S007=마트적재(Postgres, 후속), S010=ticker 매칭(후속).
- 선행 완료: ALPHA-275(Flyway 환경), ALPHA-276(ERD). ALPHA-279(ECS Terraform)=origin/dev #22 머지됨.

## 2. 확정 결정 (대화 누적)
1. 소스 = **FMP만** (`/stable/news/stock`, US 9 + 심볼맵 KR ADR). BIGKinds(규모 큼)·Google RSS = 후속.
2. 저장 = **단일 `s3://stock-ai-lake/` 레이어드 레이크** (사용자 제공 계층구조가 SSOT). **Postgres/Flyway 테이블 없음** (마트 적재는 S006/S007).
3. **canonical은 run_id 없음** → article_id 키 **파티션 병합/덮어쓰기(멱등)**. **raw는 run_id별 append(재현성)**.
4. **본문 = `canonical/news/news_article_bodies/` 신설** (article_id+body parquet). news_articles(메타)와 분리. → 계층구조에 데이터셋 1개 추가 필요.
5. **IaC = Terraform** (CDK 아님, 프로토타입 CDK는 구버전 raw/curated 2버킷). 리전 ap-northeast-2, prefix edge-dev, 상태 로컬(S3 backend 주석).
6. `article_id = sha256(normalize_url(url))`.
7. MVP 개발은 **로컬 스토리지 스텁**(백엔드 추상화)으로, 실 S3는 모듈 apply 후 전환.

## 3. S3 경로 (news 관련만, 사용자 계층구조 대조 완료)
- raw:    `raw/source=fmp/dataset=stock_news/market=US/published_date=YYYY-MM-DD/run_id=…/`  (파티션 published_date, ingest_date 아님)
- canonical 메타: `canonical/news/news_articles/published_date=YYYY-MM-DD/source_vendor=fmp/`  (market 없음, run_id 없음, source_vendor= 키)
- canonical 본문: `canonical/news/news_article_bodies/published_date=YYYY-MM-DD/source_vendor=fmp/`  (신설)
- 로그: `operations_archive/collection_logs/source=fmp/started_date=…/run_id=…/`
- 품질(AC2): `operations_archive/data_quality_logs/dataset=news_articles/checked_date=…/run_id=…/`
- 범위 밖: canonical/news/news_article_mentions*(S010), derived/aggregates/ticker_day_news*(후속).

## 4. 코드 배치 — `src/apps/data-pipeline/src/data_pipeline/` (현재 config-only, src-layout, uv, py>=3.12)
```
lake/storage.py     # 백엔드 추상화 local|s3 + 레이크 경로 빌더(파티션 규약 SSOT)
sources/fmp.py      # FMP 뉴스 어댑터
parse.py            # normalize_url, url_hash, make_article_id, 날짜 파서
dedup.py            # is_new, dedup_keys  (삭제된 test_dedup.py 복원과 짝 — pytest 캐시에 흔적)
quality.py          # 품질 게이트 + 실패사유 로깅(AC2)
steps/ingest_raw.py # S002/S028: FMP → raw ndjson + collection_log
steps/normalize.py  # S003: raw → canonical news_articles + bodies + quality_log
run.py              # run_id/incremental 진입점 (uv 실행; 후속 ECS task command)
```
- 기존 `load_settings()`(config/) 재사용. env override 접두 `DATA_PIPELINE_`.
- 이식원(프로토타입 /Users/jingi723/Desktop/Development/new-data-pipeline): `app/common/{parse,http(PoliteClient),interfaces(NormalizedArticle),us_news,persist}` , `app/steps/us_news_ingest.py`. edge 컨벤션(uuid·public·timestamptz)으로 변환.
- NormalizedArticle 필드: source, article_id, title, url, published_at(ISO UTC), summary, normalized_url_hash, rss_category, publisher, source_mentions[{market,ticker}], raw.

## 5. IaC 델타 — `infra/terraform/` (origin/dev에 존재: modules network·ecs-cluster·ecs-service(상시)·alb)
- main.tf 주석: "워커(data-pipeline·analysis-engine) 클러스터는 별도(edge-dev-worker)로 분리 예정" ← 내 계획과 일치.
- ecs-service = 상시 서비스(ALB·ingress) → 배치성 data-pipeline엔 부적합.
추가할 것:
1. `modules/s3-lake` — 단일 stock-ai-lake 버킷(KMS·block-public·versioning), 라이프사이클 raw→Glacier90d/만료, canonical 보존.
2. `modules/ecs-scheduled-task`(신규) — EventBridge Scheduler → Fargate RunTask(command=ingest_raw|normalize).
3. worker 클러스터 = ecs-cluster 모듈 재사용해 edge-dev-worker.
4. Task IAM Role — 레이크 프리픽스 R/W + Secrets Manager(FMP 키). 하드코딩 금지.
5. envs/dev/main.tf 배선: network(재사용)→worker cluster→s3-lake→scheduled-task.

## 6. PR 계획 (브랜치 사다리 feature/<key>-slug → dev, squash, Refs: 푸터. main 직접 금지)
1. `feature/ALPHA-129-s3-lake-terraform` — TF s3-lake + IAM + worker cluster + scheduled-task 골격.
2. `feature/ALPHA-103-fmp-raw-ingest` — S002/S028: FMP 어댑터·dedup·raw 저장·collection_log + 단위테스트.
3. `feature/ALPHA-104-news-normalize-canonical` — S003: normalize·품질게이트·news_articles+bodies·quality_log + 단위테스트.
- 저장 백엔드 local 스텁으로 CI, 실 S3는 1번 apply 후.

## 7. 남은 확인/조율 (블로킹 아님)
- [확정] S028 = S002에 흡수(별도 스토리 진행 안 함).
- [TODO 미반영] 계층구조 문서/다이어그램에 `news_article_bodies` 데이터셋 추가 반영 필요.
- [TODO] 로컬 dev가 origin/dev보다 뒤 → `git pull` 필요.

## 8. 참고 소스 refs
- edge: `src/libs/schema/migrations/V202606300001__…sql`(news=인용용 메타, analysis-engine 소유, S003용 아님), `docs/adr/0005-db-as-contract.md`(단일 writer), `docs/schema.md`, README §Git컨벤션.
- 다이어그램: /Users/jingi723/Desktop/Development/diagram/{ETL_데이터파이프라인_아키텍처_v1, 데이터파이프라인_아키텍처_v1(인프라), stock-ai-lake-s3-architecture}.drawio.
- 프로토타입 설계: new-data-pipeline/{pipeline-design-draft.md, requirements.md(REQ-010/012/013/014/053/054), data-architecture.md}.
