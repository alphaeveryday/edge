# app-landing — ETF Orca 랜딩페이지

`https://etforca.com` 에 서빙되는 정적 페이지다. 빌드 도구가 없다. 구성은 HTML 한 장, `assets/`, 검색엔진용 파일 두 개다.

| 파일 | 역할 |
|---|---|
| `landing.html` | 랜딩 본문. **Claude Artifact용 조각 문서**다(doctype·`<html>`·`<head>`·`<body>`·viewport 메타가 없다) |
| `assets/` | 로고, 파비콘(`icon.png`·`apple-touch-icon.png`), 공유 미리보기 그림(`og.png`), 글꼴(Pretendard 서브셋, SIL OFL 1.1 — `assets/fonts/LICENSE`) |
| `robots.txt`·`sitemap.xml` | 검색엔진이 사이트 최상위에서 읽는 파일 |
| `build.sh` | 조각 문서에 문서 뼈대(doctype·`<html>`·`<head>`·`<body>`)를 씌워 `dist/index.html` 을 만들고 `assets/`·`robots.txt`·`sitemap.xml` 을 `dist/` 에 복사한다 |

## 왜 조각 문서인가

시안을 Claude Artifact 로 미리 보면서 고친다. Artifact 는 게시할 때 doctype·head·body 를 감싸 주므로 원본에는 그 부분이 없다. 그대로 서빙하면 문서 선언과 모바일 화면 폭 설정이 빠진다. 그래서 `build.sh` 가 doctype·`<html>`·charset·viewport 를 붙이고 `<head>` 와 `<body>` 를 명시한다.

`<head>` 와 `<body>` 는 `landing.html` 안의 경계 줄 `<!-- build:body -->` 를 기준으로 나눈다. 이 줄 위는 머리, 아래는 본문이 된다. 경계 줄 위에는 머리에 들어갈 요소(`<title>`·`<meta>`·`<link>`·`<script>`·`<style>`)만 둔다. 경계 줄이 없거나 둘 이상이면 `build.sh` 가 실패한다. 브라우저는 `<head>` 가 없어도 머리를 추론하지만, `<head>` 없이 서빙했을 때 구글 서치 콘솔 소유 확인이 실패했다(ALPHA-1264).

원본을 완성된 문서로 바꾸려면 `build.sh` 의 뼈대 씌우기도 함께 없앤다. `build.sh` 는 `landing.html` 에 doctype 이 있으면 실패한다.

## 고치고 배포하기

1. `landing.html`(필요하면 `assets/`)을 고친다.
2. 로컬에서 확인한다.
   ```bash
   ./build.sh && python3 -m http.server -d dist 8000   # http://127.0.0.1:8000
   ```
3. PR 을 dev 에 머지하면 [`deploy-app-landing.yml`](../../../.github/workflows/deploy-app-landing.yml) 이 S3 에 올리고 CloudFront 캐시를 비운다.

배포는 버킷의 `index.html`·`robots.txt`·`sitemap.xml` 과 `assets/` 만 다룬다. `assets/` 에서 지운 파일은 버킷에서도 지워진다.

버킷·CloudFront·도메인 레코드는 `infra/terraform/envs/dev` 의 `landing_site` 가, 영역과 인증서는 `infra/terraform/foundation` 이 소유한다.

## 종목 검색과 전망

"다루는 ETF" 의 검색 칸은 `landing.html` 에 적어 둔 대상 ETF 148종(`코드:이름`)에서 이름으로 찾는다. 목록은 `data-pipeline` 의 `sources.toml` `[krx_etf.source.etf_map]` 을 옮긴 것이라, 대상이 바뀌면 함께 고친다.

- 종목을 누르면 같은 도메인의 `outlook/<코드>.json` 을 읽어 리포트를 그린다. 파일은 앱의 전망 화면 응답과 같은 모양이다.
- 이 파일은 랜딩 배포가 아니라 `infra/terraform/modules/landing-export` 의 람다가 매일 08:00 KST 에 쓴다. 파일이 없으면 "준비 중" 안내를 보여 준다.
- 검색 칸 아래의 종목 버튼 네 개(`data-pick`)는 이름을 치지 않고 전망을 연다. 2026-10-14 장중 거래대금 상위 4종을 수동으로 지정한 값이라 순위가 바뀌어도 따라가지 않는다. 모바일에서 두 줄에 들어가는 개수다.
- "미리 보기" 버튼(`data-preview`)은 검색 칸으로 내려가 첫 번째 종목 버튼의 전망을 연다.
- 목록에 없는 이름에서 "추가 신청하기" 를 누르면 PostHog 이벤트 `etf_request`(속성 `etf` = 입력한 이름)를 보낸다.

## 검색과 공유 미리보기

`landing.html` 머리에 설명(`description`), 대표 주소(`canonical`), 공유 미리보기(Open Graph), 아이콘, 구조화 데이터(JSON-LD)가 있다.

- 구조화 데이터는 사이트(`WebSite`), 운영 주체(`Organization`), 앱(`MobileApplication`) 세 개체다. 페이지와 App Store 목록에 실제로 있는 값만 넣는다. 평점은 넣지 않았다(HTML 에 고정하면 낡는다). App Store 주소나 인스타그램 계정이 바뀌면 함께 고친다.
- 주소는 `https://etforca.com` 을 그대로 적었다. 도메인을 바꾸면 `landing.html` 머리와 `robots.txt`, `sitemap.xml` 을 함께 고친다.
- `assets/og.png`(1200x630)에는 페이지 제목 문구가 그림으로 들어 있다. 제목을 바꾸면 그림도 다시 만든다.
- 구글 서치 콘솔에는 URL 접두어 속성 `https://etforca.com` 으로 등록했다(팀 구글 계정). 소유 확인은 머리의 `google-site-verification` 메타 태그로 한다. 이 태그를 지우면 소유 확인이 풀린다.
- 네이버 서치어드바이저에는 등록하지 않았다. 등록할 때 `https://etforca.com/sitemap.xml` 을 제출한다.

## 푸터 링크

푸터의 이용약관·개인정보 처리방침·고객 지원은 앱이 App Store 에 등록한 페이지(`https://etforca.edgesignal.dev/` 의 `terms.html`·`privacy.html`·`support.html`)를 가리킨다. 이 페이지들은 랜딩 배포 대상이 아니다. 주소가 바뀌면 `landing.html` 푸터를 함께 고친다.

## 방문 분석 (PostHog)

`landing.html` 머리에 PostHog 웹 스니펫이 있다. 로더는 PostHog 저장소의 스니펫 생성 코드(`docs/onboarding/product-analytics/_snippets/js-snippet-builder.ts`)의 출력과 같다.

- 프로젝트는 랜딩 전용이다(US 리전 `us.i.posthog.com`, 프로젝트 ID 658097). 앱(`app-ui`)이 쓰는 프로젝트와 다르다.
- 스니펫의 `phc_` 키는 공개 수집용 프로젝트 키다. 비밀이 아니다. 개인 API 키(`phx_`)는 저장소에 넣지 않는다.
- **쿠키 없는 모드**(`cookieless_mode: 'always'`)로 동작한다. 브라우저에 쿠키나 local/session storage 를 남기지 않는다. 랜딩에 개인정보 안내·동의가 없어서 지속 식별자를 두지 않기로 했다. 이 옵션을 빼려면 안내·동의부터 갖춘다.
  - 방문자는 PostHog 서버가 만든 해시로 센다(프로젝트·그날의 salt·IP·User-Agent·호스트). salt 가 매일 바뀌어 같은 사람이 다음 날 오면 새 방문자로 잡힌다.
  - IP 를 버리므로 국가·지역 정보와 봇 판별이 붙지 않는다. 세션 리플레이와 설문은 동작하지 않는다.
  - PostHog 프로젝트 설정(Project Settings > Web analytics > Cookieless server hash mode)에서 켜져 있어야 한다.
- 자동 수집(클릭 등)·히트맵·웹 바이탈은 켜져 있고 세션 리플레이는 꺼져 있다. 이 설정은 코드가 아니라 PostHog 프로젝트 설정에 있다.
- 로컬 미리보기(`http://127.0.0.1:8000`)에서 열어도 같은 프로젝트로 이벤트가 전송된다. 호스트로 막지 않았다. PostHog 에서 볼 때 호스트로 거른다.

## 알려진 한계

- `www.etforca.com` 은 같은 경로의 `etforca.com` 으로 영구 이동한다(GET·HEAD 301, 그 밖 308). 내용을 따로 서빙하지 않는다.
- 종목 전망은 하루 한 번(08:00 KST) 갱신된다. 그 뒤에 새로 발행된 전망은 다음 날 반영된다.
- 로컬 미리보기에는 `outlook/` 파일이 없어 종목을 눌러도 "준비 중" 안내가 뜬다. 실제 리포트를 보려면 `dist/outlook/` 에 파일을 받아 둔다(예: `curl --create-dirs -o dist/outlook/091160.json https://etforca.com/outlook/091160.json`).
- 없는 경로는 404 를 돌려준다. 안내 페이지는 없고 본문은 S3 의 XML 이다.
