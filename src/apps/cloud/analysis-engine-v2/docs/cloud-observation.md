# 클라우드 실행과 로컬 관측

분석은 AWS에서 실행하고 결과와 툴 근거는 기존 PostgreSQL에 저장한다. 실행 입력·모델 이벤트·화면 응답·DB 검수 기록은 S3에 복사한다. 로컬 대시보드는 읽기 권한으로 새 기록을 자동 다운로드한다. 예약 실행은 전망 배치뿐이며 같은 단건 워크플로를 호출한다(`infra/terraform/modules/analysis-v2/outlook_batch.tf`).

## 요청 계약

`cloud/request.schema.json`이 외부 실행 요청의 정본이다. 내부 가격 사건만 `cloud/trigger-source.schema.json`에 맞는 `source`를 추가한다([접수 계약](../../../../../docs/contracts/analysis-v2-admission.md)). 에이전트 입력·툴 응답·화면 계약은 변경하지 않는다.

```json
{
  "analysis_id": "0123456789abcdef0123456789abcdef",
  "kind": "outlook",
  "etf_code": "091160",
  "analysis_at": "2026-10-01T08:30:00+09:00"
}
```

- `analysis_id`: 호출자가 생성한 32자리 소문자 UUID hex. 같은 요청의 재전송은 같은 ID 사용.
- `kind`: `outlook` 또는 `movement`.
- `etf_code`: 국내 ETF 6자리 코드.
- `analysis_at`: 자료 접근 상한. 시간대 필수. 재시도 때 현재 시각으로 바꾸지 않음.
- 알 수 없는 필드·중복 JSON 키·NaN·Infinity 거부. 호출자가 이미지·명령·S3 경로·DB 접속 정보를 지정할 수 없음.
- 초기 데이터는 기존 `columns + rows` 또는 객체 본문. 툴 응답은 기존 `{tool_run_id, result}`. 화면은 기존 `contracts/screen-output.schema.json`으로 검사.

## 관측 저장

- S3 경로: `analysis-v2/runs/{analysis_id}/`.
- 내용 해시로 고정한 파일과 순서가 있는 이벤트 조각을 먼저 업로드하고 `manifest.json`을 마지막에 갱신.
- manifest는 실행 상태·파일 목록·이벤트 목록만 포함. 에이전트가 읽는 데이터 아님.
- 실행 상태의 `db_connections`는 결과 DB 연결 통계: 연결 수와 연결 시간(p50·p95·최대·합), 비밀값 조회 수와 시간, 단계 수와 단계 시간 합(SQL 시간 ≈ 단계 시간 − 연결 시간), 재시도 수와 대기 시간. 비밀값과 SQL 문은 담지 않는다.
- 이벤트는 완성된 JSONL 행만 업로드. 다운로드 시 해시와 허용 파일명을 확인.
- 최종 화면·감사 자료는 DB에서 다시 읽어 복사. 원천 raw 데이터의 별도 감사 저장은 추가하지 않음. 에이전트 초기 입력은 실행 관측용으로 보존.
- 실행 중 약 5초 간격으로 전송. 인증키·AWS 자격증명은 저장하지 않음.
- 분석 성공과 관측 전송 성공을 구분. 작업 시작 실패·강제 종료는 Step Functions 상태도 조회해 표시.
- 클라우드 실행은 로컬 대시보드 재시작으로 중단 처리하지 않음. 로컬 연결 실패는 분석 실패로 바꾸지 않음.

## 배포 순서

1. 요청·전송 계약, 서버 DB 연결, 컨테이너와 단위 테스트.
2. 전용 IAM·작업 정의·Step Functions·이미지 배포. 기존 VPC/RDS/S3 재사용.
3. 로컬 자동 동기화·요청·관측 화면 연결.
4. 클라우드 실제 분석과 로컬 재시작·재다운로드 검증. 실패도 기록.

서버 코드와 인프라만 배포하며 매크로·재무 등의 부족한 원천은 가상값으로 대체하지 않는다. 예약과 기존 v1 소비자 전환은 이 배포에서 수행하지 않는다.

## 로컬 API

- `POST /api/jobs`: 위 실행 요청 JSON을 그대로 받음. 로컬 Origin·CSRF 검사 후 AWS IAM으로 실행. `202` 응답은 실행 상태 객체이며 최종 화면이 아님.
- `GET /api/jobs`: 수신한 실행 목록.
- `GET /api/cloud-sync`: 동기화 상태·마지막 수신 시각. 연결 실패는 분석 실패와 별개.
- `GET /api/screens/{kind}/{analysis_id}/{feature}`: DB에서 조립한 화면 파일을 그대로 반환. 기존 화면 스키마 유지.
- 관리자 근거·저장 행·계약 검사 화면도 DB에서 내보낸 파일 사용. 캐시에 없으면 미수신으로 표시하며 가상값을 넣지 않음.
- 동일 요청 ID 재전송은 기존 상태를 반환. 실패한 분석을 새로 실행하려면 새 ID를 사용.
- Step Functions는 실행 시작 실패·강제 종료도 기록. 종료 관측이 없으면 `interrupted`로 표시하고 DB 발행 여부는 미확인으로 남김.

## 운영

- 이미지: `edge/pipeline:analysis-v2-{commit SHA}`. 전용 작업 family의 최신 등록 리비전으로 새 실행 시작. 이미 시작된 작업은 기존 리비전을 유지.
- 롤백: 이전 이미지가 지정된 작업 정의를 새 리비전으로 등록. 기존 DB/S3 기록 삭제 없음.
- DeepSeek 키는 `edge/analysis-v2/deepseek`의 `DEEPSEEK_API_KEY`, 모델은 `DEEPSEEK_MODEL`. 키 값은 Terraform state·Git에 넣지 않음.
- 배포는 기존 `feature/* → dev` PR 흐름. 인프라 변경은 Terraform plan에서 기존 자원 변경·삭제를 확인한 후 적용.

## 알려진 한계

- 클라우드 모드 대시보드는 프롬프트 관리 화면을 제공하지 않음. 서버가 프롬프트 판본을 넘기지 않아 `/api/execution`의 `prompts_enabled`가 false.
- 화면 계약 감사 상태: 옵시디언 문서 경로가 설정되지 않으면 `not_configured`, 미검증 항목이 있으면 `partial`, 모두 통과하면 `passed`.
