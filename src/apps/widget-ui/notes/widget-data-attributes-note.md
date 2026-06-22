# Widget Data Attributes — 입력 계약 (S016)

> 이 문서는 위젯 로더의 **입력 계약(현 합의)** 이다. 고객사 페이지가 박는 `<script>`의
> `data-*` 속성과 그 처리/매핑 규칙을 정의한다.
>
> 단, **키 모델 자체**(위젯당 1키 등 조직/발급 구조)는 아직 변경 여지가 일부 남아 있다.
> 반면 **attribute 표면(`key + symbol`)** 은 그 내부 구조가 바뀌어도 가장 안 흔들리는 부분이라
> 본 계약으로 확정한다. Gateway endpoint·Public Embed Key 검증·iframe URL 전달은 범위 밖이다(§9).

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
  src="./widget.js"
  data-embed-key="pub_demo_1234"
  data-symbol="005930">
</script>
```

```html
<!-- ② 다른 종목 -->
<script
  src="./widget.js"
  data-embed-key="pub_live_acme_001"
  data-symbol="000660">
</script>
```

`data-mock-status`는 PoC 테스트 전용이므로 실제 고객사 설치 예시에는 포함하지 않는다(§7).

위 단일 `<script>` 태그가 S015 loader의 진입점이다. 단일 script로 `widget.js`가 로드되면 loader가
현재 script 위치를 찾아 그 바로 뒤에 위젯 container를 생성한다. S015 상세 완료 기록은 `Jira ALPHA-116`를 참고한다.

## 3. data attribute 표

| 속성명 | 필수 여부 | 예시 | 설명 | request 포함 여부 | 비고 |
| --- | --- | --- | --- | --- | --- |
| `data-embed-key` | 필수 | `pub_demo_1234` | Public Embed Key (위젯 1개를 가리킴) | 포함: `embedKey` | tenant·위젯 식별의 신뢰 기준 |
| `data-symbol` | 필수 | `005930` | 고객사 페이지가 전달하는 종목 식별자 | 포함: `symbol` | trim 후 non-empty만 검증 |
| `data-mock-status` | 테스트 전용 | `success` | PoC 상태 강제값 | 포함하지 않음 | 실제 계약 아님 |

> 이전 초안(ALPHA-263)의 `data-widget-id`·`data-theme`·`data-client-id`는 본 계약에서 제거됐다.
> widget·theme 등 위젯 설정은 키로 조회하는 서버(DB) 책임이다.

## 4. 필수/테스트 전용 구분

### 필수

- `data-embed-key`
- `data-symbol`

### 테스트 전용

- `data-mock-status`

선택 속성은 없다 — 계약은 필수 2개가 전부다.

### 누락/잘못된 값 처리

- 필수 누락(`data-embed-key` 또는 `data-symbol`) → 검증 실패, 에러 카드 렌더 + `console.error`.
- `data-mock-status`에 잘못된 값 → `success`로 fallback(§7).

## 5. 멀티테넌시 결정

실제 멀티테넌시의 신뢰 기준은 `data-embed-key`다.

- `data-embed-key`는 HTML에 노출되는 **public 키**라 비밀이 아니다.
- 키 자체가 아니라 서버의 **`allowed_origins`(Referer/Origin 검증)** 가 실질 방어선이다.
- Gateway는 Public Embed Key를 검증한 뒤 Organization/Application/위젯 설정을 조회해야 한다.
- 키 1개가 위젯 1개를 가리키므로 `widget-id` 같은 보조 식별자를 클라이언트가 줄 필요가 없다.
- 실제 권한/정책/데이터 접근 제어는 반드시 embedKey 검증 결과에 기반해야 한다.

## 6. symbol 처리 정책

고객사마다 종목 코드 형식이 다를 수 있으므로 위젯은 symbol format을 엄격하게 검증하지 않는다.

- S016에서는 trim 후 non-empty만 검증한다.
- `005930`, `005930.KS`, `KRX:005930` 같은 형식을 모두 허용 가능하다.
- `widget.js`는 고객사가 준 `symbol`을 request에 그대로 전달한다.
- 실제 canonical symbol 변환은 Gateway adapter 또는 분석 API adapter에서 처리한다.

## 7. mockStatus 처리 정책

- `data-mock-status`는 PoC 테스트 전용이다.
- 지원값은 `success`, `empty`, `error`, `fallback`이다.
- 잘못된 값이 들어오면 `success`로 fallback한다.
- 실제 request에 포함하지 않으며, 실제 고객사 설치 코드에도 포함하지 않는다.
- `test-client.html`의 기본 화면은 실제 고객사 MTS 삽입 데모 중심이고, `data-mock-status` 기반 상태 확인
  위젯(success/empty/error/fallback)은 페이지 하단의 접힌 "개발자 검증 정보 보기" 패널에서만 노출한다.

## 8. Widget → Request Draft

S016 기준 request 예시:

```json
{
  "embedKey": "pub_demo_1234",
  "symbol": "005930"
}
```

구현 결정:

- `embedKey`, `symbol`만 포함한다(`createGatewayRequest`).
- `widgetId`/`theme`/`clientId`는 보내지 않는다 — 위젯 설정은 키로 서버에서 조회한다.
- `mockStatus`는 request에 절대 포함하지 않는다.

## 9. 범위 밖 / 다음 작업 연결

- attribute → **iframe URL(`?key=&symbol=`) 전달**, widget-loader 엔트리, postMessage → iframe 전달 마이그레이션.
- 서버측 `allowed_origins`/`frame-ancestors`, `public_embed_keys`·`widget_config` 스키마 → 조영서 트랙.
- S046: 실제 Gateway API 호출 연결 / S049: 분석 서버 응답 → 위젯 응답 변환 adapter.
