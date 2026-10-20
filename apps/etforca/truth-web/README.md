# truth-web

`etforca.com/truth/` 정적 화면. 빌드 도구가 없고 워크스페이스에 등록하지 않는다. 설계는 [투자정보 기만 검증 서비스 설계](../../../docs/design/truth-service.md).

## 데이터

화면 코드에는 사건 내용이 없다. 같은 경로의 `data/index.json`(요약)과 `data/cases/<id>.json`(상세)을 읽는다. 이 파일은 `truth-agent` 가 만든다. 배포 워크플로는 `truth/data/` 를 건드리지 않고, 그 자리가 비어 있을 때만 seed 사건으로 한 번 채운다. 예외는 수동 실행의 `reseed=true` 로, 정정·게시 중단이 급할 때 seed 로 `truth/data/` 를 다시 쓴다(seed 에 없는 사건 파일은 지운다).

## 로컬 미리보기

```bash
mkdir -p /tmp/truth-preview/truth && cp apps/etforca/truth-web/* /tmp/truth-preview/truth/
uv run --package edge-truth-agent python -m edge_truth_agent build-site \
  --cases apps/etforca/truth-agent/seed --out /tmp/truth-preview/truth/data
python -m http.server 8010 --directory /tmp/truth-preview   # http://127.0.0.1:8010/truth/
```

`file://` 로 열면 데이터 요청이 막힌다. 위처럼 HTTP 로 연다.
