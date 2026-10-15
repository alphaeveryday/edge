로컬 v2 실행기가 관리자 자격 없이 개발 RDS에 결과를 저장하도록 전용 접속을 추가한다. #965의 결과 저장 권한은 유지하고, 새 AWS 역할은 고정 터널과 전용 시크릿만 사용할 수 있다.

| 항목 | 값 |
|---|---|
| AWS 프로필 / 역할 | edge-v2-writer / edge-analysis-v2-local-writer |
| DB 사용자 | edge_analysis_v2_writer |
| 비밀번호 위치 | Secrets Manager: edge/analysis-v2/writer |
| 터널 | EdgeV2-RdsWriterTunnel: 로컬 15433 → 개발 RDS 5432 |
| TLS | RDS CA로 서버 이름 검증, verify-full |

- 초기 설정만 기존 work 관리 계정 사용. 실행기는 writer 프로필만 사용.
- 원천 조회 계정·PUBLIC 권한·기존 서비스 설정 변경 없음.
- 최초 생성 시 임의 비밀번호를 시크릿에 저장. 재실행은 동일 자격을 사용하며 임의 회전하지 않음.
- 역할·시크릿·터널에 다른 설정이 이미 있으면 중단. 비밀값·원본 접속 예외는 로그에 출력하지 않음.
- 신규 상시 서버 없음. Secrets Manager 시크릿 1개 추가.
- 성공 조건: 전용 계정 로그인 → 결과 저장 → 다른 연결에서 재조회, 원천 접근·근거 변경 거부 확인.

## 실행 순서

초기 설정용 Python에는 boto3·psycopg가 필요하다. 운영 실행기는 `uv sync --package analysis-engine-v2 --extra cloud`로 설치한다.

1. `python tasks/v2-cloud-access/bootstrap.py aws --report <로컬 보고서.json>`: 전용 역할·터널·시크릿 생성.
2. `python tasks/v2-cloud-access/bootstrap.py profile --report <로컬 보고서.json>`: AWS writer 프로필 추가.
3. `aws ssm start-session --profile edge-v2-writer --region ap-northeast-2 --target i-0ba627536f36993d5 --document-name EdgeV2-RdsWriterTunnel`.
4. AWS 공식 RDS CA를 `tasks/v2-cloud-access/ap-northeast-2-bundle.pem`에 준비한 뒤 `bootstrap.py database --report <로컬 보고서.json>`: Flyway 역할에 전용 비밀번호와 LOGIN 설정.
5. `connect_results(ca_path)`로 접속하고 `ToolStore(connection)`에 전달. 사용 후 연결을 닫는다.

- 원본 프로필은 `work`, 설정 대상은 개발 계정 하나로 고정. 비밀값은 보고서에 포함하지 않음.
- 설정 스크립트는 기존 reader 초기 설정 패턴을 따름. 기존 IAM·DB 권한을 넓히지 않음.
- 비밀번호 자동 회전은 포함하지 않음. 회전 시 시크릿과 PostgreSQL 비밀번호를 함께 갱신해야 함.
- 앱의 기본 에이전트 진입점·대시보드 연결은 후속 작업.

## 확인 결과

- 단위 검사 27개 통과. 기존 의존성 버전 변경 없이 cloud 선택 의존성만 등록.
- 실제 writer 로그인 후 ToolStore로 18원 테스트 결과 커밋 → 별도 연결 재조회 일치.
- 원천 조회, 감사 수정·삭제, reader 시크릿 읽기 거부 확인.
- 초기 설정 재실행으로 기존 자격 유지 확인. 보고서·인증서·시크릿은 커밋하지 않음.
- DB 검증 기록: `access-check-d2cbf10d5ec04b94ac133b3ee3b558cb`, 대상 `__V2_ACCESS_TEST__`. 실제 ETF 분석·발행 결과가 아님.
- 에이전트 기본 실행 경로와 대시보드는 아직 이 연결을 사용하지 않음.

Refs: ALPHA-1089
