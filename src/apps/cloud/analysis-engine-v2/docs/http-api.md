# 분석 API

백엔드는 분석을 요청하고 완료되면 앱 화면용 JSON을 조회합니다. 이미 만든 분석은 재실행 없이 조회합니다. 전망·오늘 움직임·5요인 상세 JSON은 기존 화면 계약 그대로이며, API는 DB에 저장된 결과를 조립합니다.

> 아래 예시는 호출 형식 설명용입니다. 실제 주소와 배포 검증 결과는 옵시디언 `ETF ORCA/분석 API.md`에 기록합니다.

## 목차
1. 시작하기·인증
2. 분석 요청
3. 상태 조회
4. 지정 분석의 화면 조회
5. ETF 최신 화면 조회
6. 화면 종류·오류
7. 운영·검증 기록

## 1. 시작하기·인증

**Base URL:** 개발 환경의 Terraform 출력 `analysis_v2_api_url`. 호출 서버의 `ANALYSIS_API_URL`에 설정합니다.

```text
분석 요청(POST) → 상태 조회(GET) → completed 확인 → 화면 조회(GET)
```

- 새 글이 필요 없으면 5번의 최신 조회만 호출합니다.
- 인증: AWS IAM SigV4. 허용된 앱 백엔드 역할 또는 검수 역할을 사용합니다.
- 공개 API 키는 없습니다. 앱·브라우저에 AWS 자격증명을 넣지 않습니다.
- 요청: `Content-Type: application/json`. 시각: 시간대를 포함한 RFC3339.
- 예약 실행 없음. 기존 v1 트리거의 자동 호출 전환은 별도 작업입니다.

서버에서는 아래 예시처럼 실행 역할을 사용합니다. 로컬 검수만 `boto3.Session(profile_name="edge-v2-observer")`로 바꿉니다.

```python
import json
import os
from urllib.request import Request, urlopen
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

BASE_URL = os.environ["ANALYSIS_API_URL"]
session = boto3.Session(region_name="ap-northeast-2")

def call(method, path, body=None):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    signed = AWSRequest(method=method, url=BASE_URL + path, data=payload,
                        headers={"Content-Type": "application/json"})
    SigV4Auth(session.get_credentials().get_frozen_credentials(),
              "execute-api", "ap-northeast-2").add_auth(signed)
    request = Request(signed.url, data=payload, method=method,
                      headers=dict(signed.headers))
    with urlopen(request, timeout=30) as response:
        return response.status, dict(response.headers), json.load(response)
```

---

## 2. 분석 요청

`POST /v2/analyses`

| 인자 | 형식 | 필수 | 설명 |
|---|---|:---:|---|
| `analysis_id` | string | 예 | 새 UUID의 소문자 hex 32자리. 동일 요청 재전송 시 유지 |
| `kind` | string | 예 | `outlook` 전망 / `movement` 가격변동 설명 |
| `etf_code` | string | 예 | 대문자·숫자 6자리. `091160`, `0177X0` |
| `analysis_at` | string | 예 | 자료 접근 상한. 미래 시각 불가 |

```json
{
  "analysis_id": "0123456789abcdef0123456789abcdef",
  "kind": "outlook",
  "etf_code": "091160",
  "analysis_at": "2026-10-01T08:30:00+09:00"
}
```

**새 접수: `202 Accepted`**

```json
{
  "analysis_id": "0123456789abcdef0123456789abcdef",
  "kind": "outlook",
  "etf_code": "091160",
  "analysis_at": "2026-10-01T08:30:00+09:00",
  "status": "queued"
}
```

- `Location` 헤더에 상태 조회 경로를 반환합니다.
- 202는 접수 성공입니다. 실제 종목·자료 확인과 모델 실행은 워커가 수행하며 이후 실패할 수 있습니다.
- 동일 ID·동일 요청: 기존 상태를 200으로 반환. 다른 ETF·종류·시각에 같은 ID를 쓰면 409.
- 통신 오류·503 후에는 같은 ID·본문으로 재시도합니다. 실패한 분석을 새로 실행할 때만 새 ID를 만듭니다.
- DB 기록이 남은 ID는 재사용하지 않습니다. DB 기록 전 실패한 실행의 중복 방지는 Step Functions 이력이 남는 90일 이내입니다.
- 현 개발 환경에서는 분석을 **최대 2개씩 실행하고 완료 후 다음 요청**을 보냅니다. HTTP 처리량과 모델·DB 실행 용량은 다릅니다.

---

## 3. 상태 조회

`GET /v2/analyses/{kind}/{analysis_id}`

| 경로 인자 | 설명 |
|---|---|
| `kind` | 요청한 분석 종류 |
| `analysis_id` | 접수한 분석 ID |

**200 응답:** 요청 응답과 같은 다섯 필드이며 `status`가 바뀝니다.

| 상태 | 뜻 | 다음 행동 |
|---|---|---|
| `queued` | 워크플로 접수, DB 분석 행 생성 전 | 약 5초 후 재조회 |
| `running` | 분석 실행 중 | 재조회 |
| `completed` | DB 저장 완료 | 화면 조회 |
| `failed` | 실행 실패·시간 초과·중단 | 원인 확인 후 새 ID로 실행 |

- DB 저장 후 관측 파일 전송만 실패하면 분석 상태는 `completed`입니다.
- 완료는 문장 품질 합격을 의미하지 않습니다.
- 없는 ID·다른 종류의 ID는 404. 과거 로컬 실행으로 DB에 저장한 실제 분석도 조회됩니다.

---

## 4. 지정 분석의 화면 조회

`GET /v2/analyses/{kind}/{analysis_id}/screens/{feature}`

| 경로 인자 | 설명 |
|---|---|
| `kind` | `outlook` / `movement` |
| `analysis_id` | 완료된 분석 ID |
| `feature` | 6번 표의 화면 종류 |

**200 응답:** `data` 같은 포장 객체 없이 기존 화면 JSON을 반환합니다. 아래는 `movement/summary` 예시입니다.

```json
{
  "summary": "A사의 공급 계약으로 장비 납품 물량이 늘어날 예정이에요.",
  "publication": {
    "etf_code": "091160",
    "analysis_at": "2026-10-01T04:13:08+00:00",
    "published_at": "2026-10-01T04:15:00+00:00"
  }
}
```

- `X-Analysis-Id`: 반환된 분석 ID.
- 미완료·실패한 분석은 409 `RESULT_NOT_READY`. 상태 API로 구분합니다.
- 분석은 완료됐지만 발행할 항목이 없으면 404 `NOT_PUBLISHED`. 재조회로 화면이 생기지는 않습니다.
- 없는 분석은 404, 조회 장애는 503입니다.

---

## 5. ETF 최신 화면 조회

`GET /v2/etfs/{etf_code}/analyses/{kind}/latest/screens/{feature}`

예: `GET /v2/etfs/091160/analyses/outlook/latest/screens/summary`

| 경로 인자 | 설명 |
|---|---|
| `etf_code` | 대문자·숫자 6자리 |
| `kind` | 분석 종류 |
| `feature` | 원하는 화면 |

**200 응답:** 4번과 같은 화면 JSON입니다.

- 실제 데이터 분석의 발행 완료본만 선택합니다. 목데이터·실행 중·실패는 제외합니다.
- `analysis_at` 최신순 → `published_at` 최신순 → `analysis_id` 내림차순. 늦게 끝난 과거 분석이 새 분석을 밀어내지 않습니다.
- 최신은 오늘을 뜻하지 않습니다. `publication.analysis_at`을 표시합니다.
- `X-Analysis-Id`, `Content-Location` 헤더로 선택된 발행본을 확인합니다.
- 여러 화면을 조립할 때 최신 조회는 한 번만 합니다. 이후 같은 ID로 요약·본문·5요인·결론을 조회해야 발행본이 섞이지 않습니다.
- 완료된 발행본이 없으면 404입니다.

---

## 6. 화면 종류·오류

| feature | 가격변동 설명 | 전망 |
|---|---|---|
| `all` | 요약·선정 항목·발행 정보 | 스티커·요약·본문·5요인 요약·결론·발행 정보 |
| `summary` | 요약 카드 | 종합 스티커·요약 카드 |
| `detail` | 중요도순 상세 항목 | 본문과 오늘의 업데이트 |
| `factors` | 미지원 | 5요인 요약 |
| `conclusion` | 미지원 | 최종 결론·도움/부담 키워드 |
| `factor_details` | 미지원 | 5요인 상세 지표·이슈. `all`과 별도 조회 |

정확한 필드는 기존 화면 설계 문서와 `contracts/screen-output.schema.json`을 따릅니다. API는 문장을 새로 생성하지 않습니다. ISO 응답 시각은 UTC일 수 있으므로 표시할 때 KST로 변환합니다.

```json
{
  "error": {
    "code": "RESULT_NOT_READY",
    "message": "Analysis has not completed; read its status."
  },
  "request_id": "요청 추적 ID"
}
```

| HTTP | 코드 | 처리 |
|---|---|---|
| 400 | `INVALID_REQUEST` | 인자·시각·JSON 확인 |
| 403 | `FORBIDDEN` 또는 AWS 기본 오류 | 역할·서명 확인 |
| 404 | `NOT_FOUND` | ID·종목·발행본 확인 |
| 404 | `NOT_PUBLISHED` | 분석 완료, 발행 화면 없음. 기존 화면 유지 |
| 409 | `ID_CONFLICT` | 다른 요청에 ID를 재사용했는지 확인 |
| 409 | `RESULT_NOT_READY` | 상태 조회 |
| 413 | `REQUEST_TOO_LARGE` | 본문 4096바이트 이하 |
| 429 | AWS 기본 오류 | 호출 간격을 늘려 재시도 |
| 503 | `SERVICE_UNAVAILABLE` | 잠시 후 동일 요청 재시도 |

Gateway 인증·제한·연동 오류는 AWS 기본 형식 또는 5xx일 수 있습니다. HTTP 상태를 먼저 확인하며, JSON 파싱 실패를 분석 실패로 해석하지 않습니다. 비밀값·SQL·내부 스택은 반환하지 않습니다.

## 7. 운영·검증 기록

- API Gateway → Lambda → 기존 Step Functions/Fargate 또는 PostgreSQL 조회.
- DB 테이블 추가 없음. API 전용 계정은 결과 8개 테이블 SELECT만 허용합니다.
- 기본 조회 5요청/초, 분석 요청 0.2요청/초·순간 2요청 제한. 분석 동시 실행 용량은 별도입니다.
- 단위 테스트 316개, 격리 PostgreSQL 1개, Lambda Linux 이미지 빌드 통과.
- 구현 PR #1040. 배포 확인은 미인증 요청 거부 → 기존 화면 조회 → 새 분석 접수·완료·화면 조회 순서입니다.

문서 구성은 [Stripe API 문서](https://docs.stripe.com/api)를 참고했습니다. 인증·실행 중복 처리는 [AWS HTTP API](https://docs.aws.amazon.com/lambda/latest/dg/services-apigateway.html), [StartExecution](https://docs.aws.amazon.com/step-functions/latest/apireference/API_StartExecution.html)의 계약을 따릅니다.
