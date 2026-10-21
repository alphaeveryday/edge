# truth-agent

투자정보 기만 검증 사건의 계약 검사, 공개 데이터 생성, 매시 사건 갱신 배치다. 설계는 [서비스 설계](../../../docs/design/truth-service.md), 실행 틀을 고른 이유는 [ADR-0062](../../../docs/adr/0062-truth-agent-pi-harness-direct-publish.md)에 있다.

## 명령

```bash
uv run --package edge-truth-agent python -m edge_truth_agent validate apps/etforca/truth-agent/seed
uv run --package edge-truth-agent python -m edge_truth_agent run hourly     # 매시: 새 기사 → 해당 사건만 갱신
uv run --package edge-truth-agent python -m edge_truth_agent run discover   # 하루 한 번: 새 사건 후보(최대 3건)
```

`tool <이름>`·`tool-specs` 는 pi 확장(`pi/extension.ts`)이 부르는 내부 명령이다.

## 설정 (환경변수)

| 이름 | 필수 | 뜻 |
|---|---|---|
| `TRUTH_STORE` | ✓ | 비공개 저장소. `s3://버킷[/접두어]` 또는 로컬 폴더 |
| `TRUTH_PUBLISH` | ✓ | 공개 데이터 게시 위치. `s3://랜딩버킷/truth/data` 또는 로컬 폴더 |
| `DEEPSEEK_RESIST_NEWS_API_KEY` | ✓ | 이 서비스 전용 DeepSeek 키. pi 자식 프로세스에만 넘긴다 |
| `EXA_API_KEY` | | 없으면 웹 검색 도구를 끈다 |
| `TRUTH_LAKE_BUCKET` | | 뉴스 레이크 버킷. 없으면 뉴스 수집을 건너뛴다 |
| `TRUTH_MODEL` · `TRUTH_THINKING` | | 기본 `deepseek/deepseek-flash` · `medium` |
| `TRUTH_MAX_SESSIONS` · `TRUTH_PARALLEL` | | 한 실행의 세션 수 상한(8)·동시 실행 수(3) |
| `TRUTH_SESSION_TIMEOUT` · `TRUTH_MAX_TOOL_CALLS` | | 세션 하나의 시간(600초)·도구 호출(70회) 상한 |

컨테이너는 `TRUTH_PI_DIR`·`TRUTH_WEB_DIR`·`TRUTH_SEED_DIR` 를 이미지 안 경로로 덮는다. 저장소에서 돌릴 때는 기본값(저장소 배치)을 쓴다.

## 로컬 실행 준비

- Node 22.19 이상과 pi: `cd apps/etforca/truth-agent/pi && npm ci`. pi 형제 패키지는 `overrides` 로 1.0.4에 고정돼 있다. 범위 지정으로 1.3.x가 섞이면 기동하지 않는다.
- 렌더 검사용 Chromium: `uv run --package edge-truth-agent playwright install chromium`. 없으면 렌더 시험은 건너뛴다(사유를 표시한다).
- 저장·게시를 로컬 폴더로 주면 AWS 없이 돈다. 이때 `TRUTH_LAKE_BUCKET` 이 없으면 새 기사가 없어 세션도 열리지 않는다.

## 실행 결과

실행마다 저장소의 `runs/<날짜>/<실행 id>.json` 에 보고서를 남긴다. 보고서에는 바뀐 사건, 되돌린 사건, 충돌, 세션별 요약·비용이 들어간다. 로그 마지막 줄(`run_end`)에도 같은 요약이 찍힌다. 앞 실행이 아직 돌면(잠금 55분) 이번 실행은 `run_skipped` 를 남기고 끝난다.

세션이 실패하거나(모델 오류·시간 초과) 결과가 검사·렌더에서 되돌려진 사건은 `failed` 에 남고, 그 사건에 배정된 기사는 다음 매시 실행이 다시 시도한다. 3번 실행해도 반영되지 않으면 버리고 `dropped` 에 남긴다. 둘 중 하나라도 있으면 종료 코드 1이라 운영 알림이 울린다. 저장·게시된 다른 사건은 그대로 반영된다.

## 이미지

```bash
docker build -f apps/etforca/truth-agent/Dockerfile -t truth-agent .   # 레포 루트에서
```

dev 머지 시 `deploy-truth-agent.yml` 이 이미지 안에서 pi 기동·seed 검사·렌더를 확인한 뒤 `edge/pipeline:truth-agent-latest` 로 올린다.
