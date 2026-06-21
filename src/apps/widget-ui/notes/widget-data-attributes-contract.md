# Widget Data Attributes Contract Draft (S016)

## 1. 문서 목적

본 문서는 S016에서 script `data-*` 속성 계약 초안을 정리하는 문서다.

- 확정 운영 스펙이 아니다.
- Gateway/Widget 연동 전 프론트엔드 삽입 계약 초안이다.
- 실제 Gateway endpoint, Public Embed Key 검증, ML API 호출, 분석 DB 조회는 구현하지 않는다.

## 2. Script 삽입 예시

```html
<script
  src="./widget.js"
  data-embed-key="pub_demo_1234"
  data-client-id="demo-sec"
  data-widget-id="asset-event-impact"
  data-symbol="005930"
  data-theme="default">
</script>
```

`data-mock-status`는 PoC 테스트 전용이므로 실제 고객사 설치 예시에는 포함하지 않는다.

위 단일 `<script>` 태그가 바로 S015 loader의 진입점이다. S015 loader는 S104/S016 구현 과정에서 함께 완성되었으며, 단일 script 태그로 `widget.js`가 로드되면 loader가 현재 script 위치를 찾아 그 바로 뒤에 위젯 container를 생성한다. 역할은 S104(상태별 렌더링 검증), S016(data attribute 계약 정리), S015(loader 부팅과 렌더링 진입점 검증)로 분리한다. S015 상세 완료 기록은 `Jira ALPHA-116`를 참고한다.

## 3. data attribute 표

| 속성명 | 필수 여부 | 예시 | 설명 | Gateway request 포함 여부 | 비고 |
| --- | --- | --- | --- | --- | --- |
| `data-embed-key` | 필수 | `pub_demo_1234` | Public Embed Key | 포함: `embedKey` | 실제 tenant 식별의 신뢰 기준 |
| `data-client-id` | 선택 | `demo-sec` | 고객사 식별 보조값 | 값이 있으면 포함: `clientId` | 보안/권한 판단 기준으로 사용 금지 |
| `data-widget-id` | 필수 | `asset-event-impact` | 렌더링할 위젯 종류 | 포함: `widgetId` | 예: 자산 이벤트 영향 위젯 |
| `data-symbol` | 필수 | `005930` | 고객사 페이지가 전달하는 종목 식별자 | 포함: `symbol` | trim 후 non-empty만 검증 |
| `data-theme` | 선택 | `default` | 위젯 테마 | 포함: `theme` | 현재 `default`만 공식 지원 |
| `data-mock-status` | 테스트 전용 | `success` | PoC 상태 강제값 | 포함하지 않음 | 실제 Gateway 계약 아님 |

## 4. 필수/선택/테스트 전용 구분

### 필수

- `data-embed-key`
- `data-widget-id`
- `data-symbol`

### 선택

- `data-client-id`
- `data-theme`

### 테스트 전용

- `data-mock-status`

> 실제 Gateway 호출 모드(local-api)와 endpoint 연동 속성은 이 스파이크(ALPHA-263) 범위가 아니다. Gateway 내부 흐름(mock tenantContext → 분석 API → adapter)과 함께 후속 티켓 S046·S049에서 정의한다.

## 5. 멀티테넌시 결정

실제 멀티테넌시의 신뢰 기준은 `data-client-id`가 아니라 `data-embed-key`다.

- `data-embed-key`는 Public Embed Key다.
- Gateway는 Public Embed Key를 검증한 뒤 Organization/Application/Tenant Context를 생성해야 한다.
- `data-client-id`는 클라이언트가 임의로 넣을 수 있으므로 보안/권한 판단에 사용하면 안 된다.
- `clientId`는 logging, debug, consistency check 정도로만 활용한다.
- 향후 Gateway는 `clientId`가 제공되면 embedKey에서 식별된 organization과 일치하는지 확인할 수 있다.
- 실제 권한/정책/데이터 접근 제어는 반드시 embedKey 검증 결과에 기반해야 한다.

## 6. symbol 처리 정책

고객사마다 종목 코드 형식이 다를 수 있으므로 위젯은 symbol format을 엄격하게 검증하지 않는다.

- S016에서는 trim 후 non-empty만 검증한다.
- `005930`, `005930.KS`, `KRX:005930` 같은 형식을 모두 허용 가능하다.
- `widget.js`는 고객사가 준 `symbol`을 Gateway request에 그대로 전달한다.
- 실제 canonical symbol 변환은 Gateway adapter 또는 분석 API adapter에서 처리한다.
- S104/S016 mock 내부에서만 `005930.KS`와 `005930` 매칭을 위한 mock 전용 normalize 로직을 둘 수 있다.

## 7. theme 처리 정책

- 현재 공식 지원 theme은 `default`만 둔다.
- 값이 없으면 `default`로 처리한다.
- 알 수 없는 값이면 현재는 `default`로 fallback한다.
- `light`, `dark`, 고객사 커스텀 테마는 추후 확장 가능하다.

## 8. mockStatus 처리 정책

- `data-mock-status`는 S104/S016 PoC 테스트 전용이다.
- 지원값은 `success`, `empty`, `error`, `fallback`이다.
- 잘못된 값이 들어오면 `success`로 fallback한다.
- 실제 Gateway request에 포함하지 않는다.
- 실제 고객사 설치 코드에는 포함하지 않는다.
- `test-client.html`의 기본 화면은 실제 고객사 MTS 삽입 데모 중심으로 정리했고, `data-mock-status` 기반 상태 확인 위젯(success/empty/error/fallback)은 페이지 하단의 접힌 "개발자 검증 정보 보기" 패널에서만 노출한다. 계약 상세는 본 문서와 `Jira ALPHA-116`, `Jira ALPHA-205`에서 확인한다.

## 9. Widget → Gateway Request Draft

S016 기준 request 예시:

```json
{
  "embedKey": "pub_demo_1234",
  "clientId": "demo-sec",
  "widgetId": "asset-event-impact",
  "symbol": "005930",
  "theme": "default"
}
```

구현 결정:

- `embedKey`, `widgetId`, `symbol`, `theme`은 항상 포함한다.
- `clientId`는 optional이며 값이 있을 때만 request에 포함한다.
- `mockStatus`는 request에 절대 포함하지 않는다.

## 10. 조영서 확인 필요 사항

- Gateway가 실제로 받을 endpoint
- GET/POST 여부
- embedKey 전달 위치: body/header/query 중 어디인지
- clientId를 request에 유지할지 여부
- embedKey와 clientId가 불일치할 때 처리 방식
- Organization/Application 식별 결과를 downstream에 어떻게 전달할지

## 11. 다음 작업 연결

- S046: 실제 Gateway API 호출 연결
- S049: 분석 서버 응답을 위젯 응답으로 변환하는 Gateway adapter 구현
