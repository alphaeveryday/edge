# truth-web

`etforca.com/truth/` 정적 화면. 빌드 도구가 없고 워크스페이스에 등록하지 않는다. 설계는 [투자정보 기만 검증 서비스 설계](../../../docs/design/truth-service.md).

## 데이터

화면 코드에는 사건 내용이 없다. 같은 경로의 `data/index.json`(요약)과 `data/cases/<id>.json`(상세)을 읽는다. `today.html`(오늘의 급등주 해석)은 `data/movers/latest.json` 을 읽고 1분마다 다시 받는다(가격은 매분 갱신, '가격 HH:MM 기준'으로 표시). 지난번 본 목록과 비교해 새 급등주·분석 완료·판정 변경을 팝업으로 알린다. 지난 날짜는 `data/movers/dates.json` 목록에서 골라 `data/movers/<날짜>.json` 을 읽고, 날짜·판정 선택은 주소(`?date=&tag=`)에 남는다([설계](../../../docs/design/truth-movers.md)). 국내·미국 전환이 있고 미국은 같은 모양의 `data/movers/us/` 를 읽는다(`?market=us`, 날짜·시각은 뉴욕 기준, 설계 §6). 사건 파일은 `truth-agent` 가, `data/movers/`·`data/logos/` 는 조사 에이전트 `truth-research` 가 만든다. 배포 워크플로는 `truth/data/` 를 건드리지 않고, 그 자리가 비어 있을 때만 seed 사건으로 한 번 채운다. 예외는 수동 실행의 `reseed=true` 로, 정정·게시 중단이 급할 때 seed 로 `truth/data/` 를 다시 쓴다(seed 에 없는 사건 파일은 지운다).

## 로컬 미리보기

```bash
mkdir -p /tmp/truth-preview/truth && cp apps/etforca/truth-web/* /tmp/truth-preview/truth/
uv run --package edge-truth-agent python -m edge_truth_agent build-site \
  --cases apps/etforca/truth-agent/seed --out /tmp/truth-preview/truth/data
python -m http.server 8010 --directory /tmp/truth-preview   # http://127.0.0.1:8010/truth/
```

`file://` 로 열면 데이터 요청이 막힌다. 위처럼 HTTP 로 연다.

주소는 확장자 없는 형태다(`/truth/list`, `/truth/issue?id=…`, `/truth/privacy`). CloudFront 에 주소 변환이 없어 배포가 각 페이지를 `.html` 키와 확장자 없는 키에 함께 올린다(예전 `.html` 주소도 열린다). `python -m http.server` 는 확장자 없는 주소를 모르니 로컬에서는 `.html` 을 붙여 연다. 게시 전 렌더 검사의 로컬 서버는 배포본처럼 확장자 없는 주소를 `.html` 로 답한다.

## 방문 분석

`analytics.js` 가 PostHog 를 붙인다. 프로젝트·키는 랜딩과 같다([app-landing README](../app-landing/README.md#방문-분석-posthog)). 처음 방문하면 화면 아래 막대에서 쿠키 사용을 묻는다. '허용'하면 쿠키(1년 유지)로 재방문을 이어서 세고, 고르기 전이나 '거부'하면 랜딩처럼 쿠키 없이 센다(`cookieless_mode: 'always'`, 방문은 집계된다). 쿠키 동의는 2026-10-28 부터 묻는다(`analytics.js` 의 `ASK_FROM`). 각 페이지 끝 `.cookie-note` 가 수집 항목·전송처·보관 기간을 알리고 선택을 다시 고르게 한다. 수집 항목을 바꾸면 이 문구도 함께 고친다. 자동화 브라우저(`navigator.webdriver`, 게시 전 렌더 검사·CI)에서는 보내지 않는다.

## 개인정보 처리방침

`privacy.html` 이 이 사이트 전용 처리방침이다(etforca.com/truth/privacy.html, `deploy-truth-web.yml` 이 함께 올린다). ETF Orca 앱 처리방침과 별개다. 수집 항목을 바꾸면 `analytics.js`·각 페이지 `.cookie-note`·`privacy.html` 을 함께 고친다.
