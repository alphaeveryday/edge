# app-landing — ETF Orca 랜딩페이지

`https://etforca.com` 에 서빙되는 정적 페이지다. 빌드 도구가 없다. HTML 한 장과 `assets/` 가 전부다.

| 파일 | 역할 |
|---|---|
| `landing.html` | 랜딩 본문. **Claude Artifact용 조각 문서**다(doctype·`<html>`·viewport 메타가 없다) |
| `assets/` | 로고와 글꼴(Pretendard 서브셋, SIL OFL 1.1 — `assets/fonts/LICENSE`) |
| `build.sh` | 조각 문서에 문서 머리를 붙여 `dist/index.html` 과 `dist/assets/` 를 만든다 |

## 왜 조각 문서인가

시안을 Claude Artifact 로 미리 보면서 고친다. Artifact 는 게시할 때 doctype·head·body 를 감싸 주므로 원본에는 그 부분이 없다. 그대로 서빙하면 문서 선언과 모바일 화면 폭 설정이 빠진다. 그래서 `build.sh` 가 머리 4줄을 붙인다.

원본을 완성된 문서로 바꾸려면 `build.sh` 의 머리 붙이기도 함께 없앤다. `build.sh` 는 `landing.html` 에 doctype 이 있으면 실패한다.

## 고치고 배포하기

1. `landing.html`(필요하면 `assets/`)을 고친다.
2. 로컬에서 확인한다.
   ```bash
   ./build.sh && python3 -m http.server -d dist 8000   # http://127.0.0.1:8000
   ```
3. PR 을 dev 에 머지하면 [`deploy-app-landing.yml`](../../../../.github/workflows/deploy-app-landing.yml) 이 S3 에 올리고 CloudFront 캐시를 비운다.

배포는 버킷의 `index.html` 과 `assets/` 만 다룬다. `assets/` 에서 지운 파일은 버킷에서도 지워진다.

버킷·CloudFront·도메인 레코드는 `infra/terraform/envs/dev` 의 `landing_site` 가, 영역과 인증서는 `infra/terraform/foundation` 이 소유한다.

## 방문 분석 (PostHog)

`landing.html` 머리에 PostHog 웹 스니펫이 있다. 로더는 PostHog 저장소의 스니펫 생성 코드(`docs/onboarding/product-analytics/_snippets/js-snippet-builder.ts`)로 만든 것 그대로다.

- 프로젝트는 랜딩 전용이다(US 리전 `us.i.posthog.com`, 프로젝트 ID 658097). 앱(`app-ui`)이 쓰는 프로젝트와 다르다.
- 스니펫의 `phc_` 키는 공개 수집용 프로젝트 키다. 비밀이 아니다. 개인 API 키(`phx_`)는 저장소에 넣지 않는다.
- **쿠키 없는 모드**(`cookieless_mode: 'always'`)로 돈다. 브라우저에 쿠키나 local/session storage 를 남기지 않는다. 랜딩에 개인정보 안내·동의가 없어서 지속 식별자를 두지 않기로 했다. 이 옵션을 빼려면 안내·동의부터 갖춘다.
  - 방문자는 PostHog 서버가 만든 해시로 센다(프로젝트·그날의 salt·IP·User-Agent·호스트). salt 가 매일 바뀌어 같은 사람이 다음 날 오면 새 방문자로 잡힌다.
  - IP 를 버리므로 국가·지역 정보와 봇 판별이 붙지 않는다. 세션 리플레이와 설문은 동작하지 않는다.
  - PostHog 프로젝트 설정(Project Settings > Web analytics > Cookieless server hash mode)에서 켜져 있어야 한다.
- 자동 수집(클릭 등)·히트맵·웹 바이탈은 켜져 있고 세션 리플레이는 꺼져 있다. 이 설정은 코드가 아니라 PostHog 프로젝트 설정에 있다.
- 로컬 미리보기(`http://127.0.0.1:8000`)에서 열어도 같은 프로젝트로 이벤트가 전송된다. 호스트로 막지 않았다. PostHog 에서 볼 때 호스트로 거른다.

## 알려진 한계

- `www.etforca.com` 은 서빙하지 않는다.
- 전망 조회는 예시 데이터다(`getOutlook` 이 이름으로 값을 만든다).
- "추가 신청하기" 는 전송 주소(`REQ_URL`)가 비어 있어 아무 데도 보내지 않는다.
- 없는 경로는 403 을 돌려준다.
