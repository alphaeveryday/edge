# Widget Data Attributes — 입력 계약

> 이 문서는 위젯 로더의 **입력 계약(현 합의)** 이다. 고객사 페이지가 박는 `<script>`의
> `data-*` 속성과 그 처리/매핑 규칙을 정의한다.
>
> 단, **키 모델 자체**(위젯당 1키 등 조직/발급 구조)는 아직 변경 여지가 일부 남아 있다.
> 반면 **attribute 표면(`key + symbol`)** 은 그 내부 구조가 바뀌어도 가장 안 흔들리는 부분이라
> 본 계약으로 확정한다. Gateway endpoint·Public Embed Key 검증은 범위 밖이다(§9).
> attribute → iframe URL(`#key=&symbol=`) 전달은 본 마이그레이션에서 구현됐다(§2).

## 1. 결정 요약

attribute를 **`key + symbol` 두 개로 최소화**한다.

- 키(`data-embed-key`)가 **위젯 1개를 가리키는 포인터**다 — 디자인/노출모듈/정책/테마 등 위젯 설정은
  전부 서버(DB)에 있고, 키로 조회한다. 그래서 `data-widget-id`가 필요 없다(키 1개 = 위젯 1개).
- `data-theme`도 위젯 설정이므로 DB(`widget_config`)에서 내려준다 — attribute에서 제거.
- `data-client-id`는 신뢰 기준이 아니라 제거한다(§5).
- 고객사가 박는 임베드 코드가 **거의 안 바뀐다**는 게 핵심 가치다(설정 변경은 전부 DB에서 처리).

## 2. Script 삽입 예시

```html
<!-- ① 기본 설치 -->
<script
  src="./widget-loader.js"
  data-embed-key="pub_demo_1234"
  data-symbol="005930">
</script>
```

```html
<!-- ② 다른 종목 -->
<script
  src="./widget-loader.js"
  data-embed-key="pub_live_acme_001"
  data-symbol="000660">
</script>
```

위 단일 `<script>`(`widget-loader.js`)가 고객사 페이지의 진입점이다. 로더는 `data-*`를 읽어
현재 script 바로 뒤에 위젯 본체 iframe(`widget-frame.html#key=&symbol=`)을 생성한다. 실제 위젯
(`widget.js`)은 그 iframe 안에서 URL 파라미터(`#key=&symbol=`)를 읽어 렌더하며, CSS는 iframe으로
고객사 페이지와 격리된다. (본체 높이는 `postMessage`로 로더에 전달돼 iframe 높이가 동기화된다.)

> key·symbol을 쿼리스트링이 아니라 **fragment(`#…`)** 로 싣는 이유: fragment는 HTTP 요청 라인과
> Referer 헤더에 포함되지 않아, 종목·public key가 정적 서버·CDN·프록시 접근 로그에 남지 않는다.
> (`key`는 설계상 public 키지만(§5), `symbol`+key+IP+timestamp가 로그에 축적되는 것을 피한다.)

## 3. data attribute 표

| 속성명 | 필수 여부 | 예시 | 설명 | request 포함 여부 | 비고 |
| --- | --- | --- | --- | --- | --- |
| `data-embed-key` | 필수 | `pub_demo_1234` | Public Embed Key (위젯 1개를 가리킴) | iframe URL `key` → request `embedKey` | tenant·위젯 식별의 신뢰 기준 |
| `data-symbol` | 필수 | `005930` | 고객사 페이지가 전달하는 종목 식별자 | iframe URL `symbol` → request `symbol` | trim 후 non-empty만 검증 |

> 이전 초안의 `data-widget-id`·`data-theme`·`data-client-id`는 본 계약에서 제거됐다.
> widget·theme 등 위젯 설정은 키로 조회하는 서버(DB) 책임이다.

## 4. 필수 속성

- `data-embed-key`
- `data-symbol`

선택 속성은 없다 — 계약은 필수 2개가 전부다.

### 누락/잘못된 값 처리

- 필수 누락(`data-embed-key` 또는 `data-symbol`) → 로더가 빈 값으로 iframe URL을 만들고,
  본체(`widget.js`)의 `validateConfig`가 검증 실패로 에러 카드 렌더 + `console.error`.

## 5. 멀티테넌시 결정

실제 멀티테넌시의 신뢰 기준은 `data-embed-key`다.

- `data-embed-key`는 HTML에 노출되는 **public 키**라 비밀이 아니다.
- 키 자체가 아니라 서버의 **`allowed_origins`(Referer/Origin 검증)** 가 실질 방어선이다.
- Gateway는 Public Embed Key를 검증한 뒤 Organization/Application/위젯 설정을 조회해야 한다.
- 키 1개가 위젯 1개를 가리키므로 `widget-id` 같은 보조 식별자를 클라이언트가 줄 필요가 없다.
- 실제 권한/정책/데이터 접근 제어는 반드시 embedKey 검증 결과에 기반해야 한다.

## 6. symbol 처리 정책

고객사마다 종목 코드 형식이 다를 수 있으므로 위젯은 symbol format을 엄격하게 검증하지 않는다.

- 현재는 trim 후 non-empty만 검증한다.
- `005930`, `005930.KS`, `KRX:005930` 같은 형식을 모두 허용 가능하다.
- `widget.js`는 고객사가 준 `symbol`을 request에 그대로 전달한다.
- 실제 canonical symbol 변환은 Gateway adapter 또는 분석 API adapter에서 처리한다.

## 7. PoC mock 상태 (제거됨)

이전 PoC의 `data-mock-status`(success/empty/error/fallback 강제값)는 **계약과 구현에서 모두 제거**됐다.

- 라이브 경로(loader → iframe 부팅)는 항상 `success`만 렌더한다.
- `empty`/`error`/`fallback` 4상태 렌더 로직은 유지되며, 렌더 단위 테스트(`tests/widget.test.js`)로 검증한다.
- `test-client.html` 데모는 실제 고객사 MTS 삽입(AI 분석 탭의 단일 `<script>` 한 줄)만 보여주며, 별도 상태 확인 패널은 없다.

## 8. Widget → Request Draft

현 계약 기준 request 예시:

```json
{
  "embedKey": "pub_demo_1234",
  "symbol": "005930"
}
```

구현 결정:

- `embedKey`, `symbol`만 포함한다(`createGatewayRequest`).
- `widgetId`/`theme`/`clientId`는 보내지 않는다 — 위젯 설정은 키로 서버에서 조회한다.

## 9. 범위 밖 / 다음 작업 연결

- (완료) attribute → **iframe URL(`#key=&symbol=`) 전달**, widget-loader 엔트리, postMessage 높이 동기화 → 본 마이그레이션(ALPHA-268).
- 서버측 `allowed_origins`/`frame-ancestors`, `public_embed_keys`·`widget_config` 스키마 → 조영서 트랙. 현재 로더는 postMessage `event.source` 동일성만 확인한다.
- 실제 Gateway API 호출 연결 / 분석 서버 응답 → 위젯 응답 변환 adapter.
