# truth-research — 오늘 오른 종목 조사 에이전트

etforca.com/truth/today 의 데이터를 만든다. 오늘 크게 오른 종목마다 급등 이유를 한국거래소 KIND 공시·보도·웹 원문과 대조(경쟁가설 분석, ACH)해 유망주·허위매물·판단 보류로 판정하고 랜딩 버킷 `truth/data/movers/` 에 게시한다. 흐름·판정 규칙·데이터 형식은 [설계](../../../docs/design/truth-movers.md).

구조는 `truth-agent` 와 같다(pi 하네스 + DeepSeek, 종목당 세션 하나, ECS Fargate 단발 태스크). `truth-agent` 패키지의 저장소·뉴스 말뭉치·웹 도구·작업 폴더를 그대로 쓰고, 여기에는 시세(`quotes.py`)·KIND(`kind.py`)·ACH 채점(`ach.py`)·조사 도구와 실행 흐름(`research.py`)만 둔다.

## 실행

```bash
uv run --package edge-truth-research python -m edge_truth_research run                 # 한 번 실행(운영은 장중 매시 :35)
uv run --package edge-truth-research python -m edge_truth_research run --new           # 아직 판정하지 않은 새 종목만(매분 감시가 띄운다)
uv run --package edge-truth-research python -m edge_truth_research prices              # 가격 갱신 + 새 급등주 감시(운영은 장중 매분). 모델 키를 쓰지 않는다
uv run --package edge-truth-research python -m edge_truth_research tool-specs --web 1   # pi 확장이 등록하는 도구 목록
uv run --package edge-truth-research pytest apps/etforca/truth-research/tests -q
```

`run` 은 pi 하네스(`pi/` 에서 `npm ci`)와 아래 환경변수가 필요하다.

| 환경변수 | 필수 | 설명 |
|---|---|---|
| `TRUTH_STORE` | ✓ | 비공개 저장소(truth-agent 와 같은 버킷 `s3://…` 또는 로컬 폴더). 뉴스 말뭉치를 읽고 급등주 상태·실행 보고를 쓴다 |
| `TRUTH_PUBLISH` | ✓ | 공개 데이터 위치(`s3://<랜딩>/truth/data` 또는 로컬 폴더) |
| `DEEPSEEK_RESIST_NEWS_API_KEY` | ✓ | 모델 키(truth-agent 와 같은 시크릿) |
| `EXA_API_KEY` | | 웹 검색·원문. 없으면 웹 도구를 끈다 |
| `TRUTH_MAX_TOOL_CALLS` · `TRUTH_WEB_CALL_LIMIT` · `TRUTH_SESSION_TIMEOUT` | | 세션당 도구 40회 · 웹 10회 · 420초 |

시세(Naver 순위·Yahoo 5분봉·현재가)와 공시(KIND)는 키가 없다. 이미지는 `deploy-truth-research.yml` 이 `edge/pipeline:truth-research-latest` 로 올리고, 인프라는 `infra/terraform/modules/truth-research`.
