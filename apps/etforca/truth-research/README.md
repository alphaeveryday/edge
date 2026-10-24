# truth-research — 오늘 오른 종목 조사 에이전트

etforca.com/truth/today 의 데이터를 만든다. 오늘 크게 오른 종목마다 급등 이유를 공시(국내 한국거래소 KIND · 미국 SEC EDGAR)·보도·웹 원문과 대조(경쟁가설 분석, ACH)해 유망주·허위매물·판단 보류로 판정하고 랜딩 버킷 `truth/data/movers/`(미국 `truth/data/movers/us/`)에 게시한다. 흐름·판정 규칙·데이터 형식은 [설계](../../../docs/design/truth-movers.md), 미국은 설계 §6.

구조는 `truth-agent` 와 같다(pi 하네스 + DeepSeek, 종목당 세션 하나, ECS Fargate 단발 태스크). `truth-agent` 패키지의 저장소·뉴스 검색 색인·웹 도구·작업 폴더를 그대로 쓰고(국내 보도 자체는 운영 DB 에서 읽는다 — `dbnews.py`), 여기에는 시장별 설정(`markets.py`)·시세(`quotes.py`)·KIND(`kind.py`)·SEC(`sec.py`)·ACH 채점(`ach.py`)·조사 도구와 실행 흐름(`research.py`)만 둔다.

## 실행

```bash
uv run --package edge-truth-research python -m edge_truth_research run                 # 한 번 실행(운영은 장중 매시 :35)
uv run --package edge-truth-research python -m edge_truth_research run --new           # 아직 판정하지 않은 새 종목만(매분 감시가 띄운다)
uv run --package edge-truth-research python -m edge_truth_research prices              # 가격 갱신 + 새 급등주 감시(운영은 장중 매분). 모델 키를 쓰지 않는다
uv run --package edge-truth-research python -m edge_truth_research run --market us     # 미국(run·prices 공통). 운영은 뉴욕 시간대 스케줄
uv run --package edge-truth-research python -m edge_truth_research tool-specs --web 1   # pi 확장이 등록하는 도구 목록
uv run --package edge-truth-research pytest apps/etforca/truth-research/tests -q
```

`run` 은 pi 하네스(`pi/` 에서 `npm ci`)와 아래 환경변수가 필요하다.

| 환경변수 | 필수 | 설명 |
|---|---|---|
| `TRUTH_STORE` | ✓ | 비공개 저장소(truth-agent 와 같은 버킷 `s3://…` 또는 로컬 폴더). 급등주 상태·실행 보고를 쓴다 |
| `TRUTH_PUBLISH` | ✓ | 공개 데이터 위치(`s3://<랜딩>/truth/data` 또는 로컬 폴더) |
| `DEEPSEEK_RESIST_NEWS_API_KEY` | ✓ | 모델 키(truth-agent 와 같은 시크릿) |
| `TRUTH_DB_HOST` · `TRUTH_DB_PASSWORD` | ✓ | 국내 보도(빅카인즈)를 읽는 운영 PostgreSQL. 계정은 분석 엔진의 읽기 전용 `edge_analysis_v2_reader`(`TRUTH_DB_USER` 로 바꿀 수 있다), 비밀번호는 시크릿 `edge/analysis-v2/readonly`. 붙지 못하면 실행이 실패한다 |
| `GRAPH_URI` · `GRAPH_PASSWORD` | | 국내 종목 사건 그래프(PuppyGraph Bolt, `bolt://puppygraph.<name>.internal:7687`)와 그 로그인. 있으면 국내 세션에 `company_events`·`find_events` 와 사건 메뉴를 연다(`graph.py`). 그래프는 평일 05:30~16:00 KST 에만 떠 있다 |
| `RDS_CA_PATH` | | 서버 인증서 검증용 RDS CA 묶음(이미지 기본 `/app/rds-ca.pem`) |
| `EXA_API_KEY` | | 웹 검색·원문. 없으면 웹 도구를 끈다 |
| `TRUTH_SEC_CONTACT` | 미국 | SEC 요청 머리글의 연락처 이메일(없으면 SEC 가 403). 없으면 미국 공시 조회가 실패해 보류로 싣는다 |
| `TRUTH_MAX_TOOL_CALLS` · `TRUTH_WEB_CALL_LIMIT` · `TRUTH_SESSION_TIMEOUT` | | 세션당 도구 40회 · 웹 10회 · 420초 |
| `TRUTH_PREMISE_TOOL_CALLS` · `TRUTH_PREMISE_WEB_CALLS` · `TRUTH_PREMISE_TIMEOUT` | | 전제 검증 세션(ACH 동점일 때 한 번 더) 도구 24회 · 웹 6회 · 300초 |

시세(Naver 순위·Yahoo 5분봉·현재가)와 공시(KIND·SEC)는 키가 없다. 이미지는 `deploy-truth-research.yml` 이 `edge/pipeline:truth-research-latest` 로 올리고, 인프라는 `infra/terraform/modules/truth-research`.
