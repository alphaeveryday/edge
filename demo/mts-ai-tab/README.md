# 가상 MTS — AI 분석 탭 (데모)

증권사 MTS의 AI 분석 탭 데모. 빌드 도구 없이 정적 html+js로 동작하며,
정적 서빙과 증권사 API는 [demo/mock-broker](../mock-broker/server.js)가 담당한다.
화면은 수령 디자인(claude.ai Design "KODEX 반도체 AI 분석", ALPHA-485)을 번역한
홈 → 검색 → 종목상세(호가·차트·뉴스·공시·AI 분석·종목정보·커뮤니티·재무 7탭) 3화면 구성이다.

## 실행

```bash
# 데이터는 동기화 경로가 실적재하므로 워커까지 함께 띄운다.
# 구(로컬 시드) 볼륨을 쓰던 경우 최초 1회 `docker compose down -v` 로 리셋할 것.
docker compose up --build mock-broker screening-worker intake
# http://localhost:18090 (첫 관통까지 폴링 주기상 ~10초)
```

compose 없이 로컬 프로세스로 띄우려면 (publication-api 는 18084 에 떠 있어야 한다):

```bash
PORT=18090 node demo/mock-broker/server.js
```

## 구조 — 계약의 신뢰경계 재현

[docs/contracts/publication-api.md](../../docs/contracts/publication-api.md)의 원칙(MTS는 Publication API를 직접 호출하지 않는다)을 실 HTTP 홉으로 재현한다 (ADR-0035 런타임 경로).

```
app.js (MTS 3화면 — 홈·검색·종목상세)
  → broker-api.js   /api/broker/* fetch — 얇은 래퍼(브라우저 mock 없음)
    → mock-broker (demo/mock-broker/server.js)   증권사 자체 제작 API — 고객 해시·채널 부착, 상태 매핑, 폴백 처리
      → publication-api (src/apps/onprem/publication-api)   On-Premise Publication API      (/api/broker/ai-analysis)
      → 토스증권 공식 Open API (openapi.tossinvest.com)   실시간 시세·일봉 소스 — 시세 7초 캐시   (/api/broker/quotes · /api/broker/chart)
```

- **실데이터는 세 경로다.** AI 분석 탭(publication-api 프록시), 시세(지수·관심종목·상세 헤더 가격 — 토스증권 Open API 프록시, 장중 실시간가·마감 후 당일 종가), 차트 탭(같은 소스 — 기본 '1일'은 최근 거래일 분봉(최신 봉 날짜로 필터해 장전·휴장엔 자동으로 직전 거래일, TTL 60초 캐시), 1주~1년은 일봉 약 250봉(티커당 KST 날짜 1회 캐시). count 상한 200이라 nextBefore 로 한 페이지 더 잇고, 기간 슬라이스·통계 카드는 화면이 파생한다). 호가·뉴스·커뮤니티 등 나머지는 증권사 자체 데이터라는 전제의 화면 고정값(목업)이다.
- 종목 유니버스(검색 노출 36종·이름·ETF 여부 — 홈 관심종목은 앞 4종)는 [demo/mock-broker/quotes-fallback.json](../mock-broker/quotes-fallback.json)이 SSOT다. 분석 유니버스 ETF 32종(게임산업 300950 은 ALPHA-624 확장분 — 분석 도착 전 NO_DATA)이 검색으로 이어진다. 키 미설정·외부 API 실패 시 이 스냅샷으로 폴백해 화면이 깨지지 않는다. 화살표·색·등락률 표기는 숫자에서 화면이 파생한다.
- 현재가 API에는 전일대비가 없어 mock-broker 가 일봉(count=2)으로 전일종가를 KST 날짜당 1회 캐시해 등락을 계산한다.
- 고객 해시는 mock-broker 서버가 부착한다 — 실제 생성 규칙·salt는 증권사 서버 관리 영역(ADR-0013), 브라우저에 두지 않는다.
- 실제 연동 시 mock-broker 레이어는 증권사 백엔드 구현으로 통째로 대체된다(화면 코드는 그대로). 브라우저에서 Publication API를 직접 fetch하는 경로는 계약 위반이라 데모에도 두지 않는다.

## 실시간 시세 키 (선택)

시세는 토스증권 공식 Open API 를 쓴다 — 키가 없으면 폴백 스냅샷으로 동작하므로 필수는 아니다.

1. 토스증권 WTS `설정 > Open API` 에서 `client_id`/`client_secret` 발급.
2. 같은 메뉴의 **허용 IP 관리**에 호출 IP 등록 — 미등록 IP 는 403 (로컬 개발이면 로컬 공인 IP, 데모 박스면 박스 EIP).
3. 키 주입 (커밋 금지 — `.env` 는 gitignore 되어 있다):
   - 로컬: 리포 루트 `.env` 에 `TOSS_CLIENT_ID=…`/`TOSS_CLIENT_SECRET=…` → `docker compose up`.
   - 데모 박스: `/opt/edge-onprem/.env` 에 동일 내용 1회 수동 배치. 배포 번들 tar 는 덮어쓰기만 하므로 재배포에도 유지되지만, 박스 재프로비저닝 시엔 재배치해야 한다.

## 실기기·시뮬레이터 시연 (홈 화면 추가)

화면은 PWA standalone 을 지원한다 — iOS 홈 화면에 추가해 실행하면 CSS 아이폰 베젤·가짜 상태바(15:30 고정) 연출이
걷히고 전체 화면 + 실제 iOS 상태바(실시계)로 뜬다. 데스크톱 브라우저는 기존 베젤 프리뷰 그대로다.

1. **홈 화면 추가**: 아이폰 Safari 로 데모 URL(CloudFront 또는 로컬 서버)을 열고 공유 → **홈 화면에 추가** → 생성된
   KB 아이콘으로 실행. 무대에선 아이콘 탭으로 앱을 켜는 장면부터 시작할 수 있다.
2. **무대 미러링**: 아이폰을 맥에 케이블로 연결 → QuickTime Player → 새 동영상 녹화 → 카메라 소스를 iPhone 으로.
   무선이 필요하면 AirPlay(발표장 네트워크 품질에 좌우되므로 유선 우선).
3. **시뮬레이터 대안**(실폰이 없거나 영상 촬영용): Xcode Simulator 의 Safari 로 열어 동일하게 홈 화면 추가 후 실행.
4. **발표장 네트워크 체크리스트**: 토스 Open API 는 허용 IP 등록제다 — 발표장 회선(또는 백업 핫스팟)의 공인 IP 를
   사전 등록하지 않으면 시세·차트가 폴백 스냅샷으로 동작한다(화면은 깨지지 않지만 "실시간" 멘트를 쓸 수 없다).
   데모 박스 경유(CloudFront URL)라면 상류 호출 IP 는 박스 EIP 이므로 발표장 회선과 무관하다 — 이 경우 확인할 것은
   발표장에서 데모 URL 접속 가능 여부뿐이다.

## 데모 조작

응답 데이터는 cloud 시드(`src/libs/schema/seed-local-cloud`)의 전달 레코드가 동기화 경로
(sync-agent→intake→screening-worker)를 관통해 만든 온프렘 게시분이다
(200 게시분: 091160 = 2026-07-16 수령 디자인 리포트, 069500 = 2026-07-15. compose 가
`PUBLICATION_KNOWN_TICKERS` 로 091160 을 지원 종목에 추가한다).
게시 상태를 바꾸면 화면에 즉시 반영된다: `docker exec edge-postgres-onprem psql -U edge -d edge_onprem -c "UPDATE publication SET status='UNPUBLISHED' WHERE status='PUBLISHED';"` → 조회가 204(NO_DATA)로 바뀐다.

`/`는 홈 화면에서 시작한다 — 검색·관심종목에서 종목을 탭해 상세로 들어가고, AI 분석 탭을 누르면
실호출이 나간다. 관심종목의 삼성전자 등 비 ETF 종목은 AI 분석 탭에서 404(미지원 종목) 상태가
자연 재현된다. 쿼리 파라미터를 주면 종목 상세의 AI 분석 탭으로 직행한다:

| URL | 재현 상태 |
|---|---|
| `/?ticker=091160` | 200 — KODEX 반도체 리포트(수령 디자인 본문) 노출 |
| `/?ticker=069500` | 200 — KODEX 200 설명 노출 |
| `/?ticker=305720` | 204 — 상장 종목이나 설명 없음(정상) 안내 |
| `/?ticker=000001` | 404 — 미지원 종목 안내 |
| `/?trade_date=2026-07-01` | 204 — 해당 기준일 게시분 없음 (기본 종목 069500) |
| `/?trade_date=2026-7-1` | 400 — 형식 오류(폴백 문구 + mock-broker 로그 경고) |
| publication-api 중지 후 조회 | 5xx/통신 실패 — 폴백 문구 |
