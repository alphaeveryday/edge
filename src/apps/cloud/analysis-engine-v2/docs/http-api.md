# 분석 API

백엔드는 분석을 요청하고, 상태가 `completed`가 되면 화면별 JSON을 조회한다. 이미 저장된 분석은 ETF 코드로 최신 완료본을 조회한다. 분석 실행은 기존 Fargate 작업이 담당하고, API는 PostgreSQL에서 결과를 읽어 기존 화면 계약으로 조립한다. 앱에서 직접 호출하는 API가 아니라 인증된 백엔드 간 API다.

## 구현 계약

| 메서드·경로 | 역할 |
|---|---|
| `POST /v2/analyses` | 분석 접수. 새 요청은 202, 동일 요청 재조회는 200 |
| `GET /v2/analyses/{kind}/{analysis_id}` | queued / running / completed / failed 상태 |
| `GET /v2/analyses/{kind}/{analysis_id}/screens/{feature}` | 지정 분석의 완성된 화면 JSON |
| `GET /v2/etfs/{etf_code}/analyses/{kind}/latest/screens/{feature}` | ETF의 최신 발행 완료본 |

- 인증: API Gateway `AWS_IAM`. 허용된 서버 역할의 SigV4 서명. 앱에 AWS 자격증명을 넣지 않는다.
- `kind`: `outlook` / `movement`.
- `analysis_id`: 호출자가 생성하는 소문자 UUID hex 32자리. 타임아웃 재요청은 같은 ID와 같은 본문. 다른 요청으로 재사용하면 409.
- `etf_code`: 영문 대문자·숫자 6자리. `0177X0` 포함.
- `analysis_at`: 시간대가 포함된 자료 접근 상한. 미래 시각은 거부한다.
- 기존 실행 ID 재사용은 새 실행을 만들지 않는다. 실패를 다시 실행하려면 새 ID가 필요하다. DB에 기록되지 않은 실행의 재전송 보장은 Step Functions 실행 이력 보존기간(90일) 이내다.
- 화면 JSON에 API용 래퍼를 추가하지 않는다. 응답 헤더 `X-Analysis-Id`로 선택된 분석을 확인한다.
- `feature`: 공통 `all`, `summary`, `detail`. 전망만 `factors`, `conclusion`, `factor_details` 추가. `factor_details`는 `all`과 별도 조회.
- 최신: `data_source=database`, `status=completed`, 발행시각이 있는 행 중 `analysis_at`, `published_at`, `analysis_id` 내림차순. 실행 중이거나 실패한 최신 요청이 기존 완료본을 가리지 않는다.
- 최신은 오늘을 뜻하지 않는다. 응답의 `publication.analysis_at`을 표시한다. 여러 화면을 같은 발행본으로 묶으려면 최신 조회의 `X-Analysis-Id`를 받아 이후 지정 ID로 조회한다.
- 오류: `{"error":{"code":"...","message":"..."},"request_id":"..."}`. 인증 실패·Gateway 제한은 AWS 기본 오류일 수 있다.
- 400 잘못된 요청, 403 인증·권한, 404 미존재, 409 ID 충돌·결과 미완료, 413 과대 요청, 503 의존 서비스 불가. 비밀값·SQL·스택은 반환하지 않는다.

## 요청 예시

```json
{
  "analysis_id": "0123456789abcdef0123456789abcdef",
  "kind": "outlook",
  "etf_code": "091160",
  "analysis_at": "2026-10-01T08:30:00+09:00"
}
```

```json
{
  "analysis_id": "0123456789abcdef0123456789abcdef",
  "kind": "outlook",
  "etf_code": "091160",
  "analysis_at": "2026-10-01T08:30:00+09:00",
  "status": "queued"
}
```

## 변경·검증 범위

1. API·공통 조립 함수·요청 계약·테스트. 기존 화면 스키마와 에이전트 프롬프트 유지.
2. API Gateway·Lambda·전용 결과 읽기 계정·배포. 기존 테이블 재사용. 예약과 v1 트리거 연결 없음.
3. 인증 거부, 실제 저장 결과 조회, 동일 ID 재요청, 실제 분석 접수→완료→조회 확인. 옵시디언 문서를 실제 주소·결과로 갱신.

최소 테스트: 영문 ETF 코드, 미래 시각·중복 키·과대 본문 거부, 동일 ID 중복 실행 방지, 다른 본문 충돌, DB 완료와 작업 실패 구별, 최신 완료본 선택, 합성자료 제외, 화면별 기존 JSON 보존, 비밀값 오류 노출 차단.

## 참고

- [Stripe API 문서](https://docs.stripe.com/api): 기능별 요청 인자·응답·오류·예시 구성 참고.
- [AWS HTTP API와 Lambda](https://docs.aws.amazon.com/lambda/latest/dg/services-apigateway.html): payload 2.0 연동.
- [Step Functions StartExecution](https://docs.aws.amazon.com/step-functions/latest/apireference/API_StartExecution.html): 실행 이름·입력의 중복 처리와 보존기간.
