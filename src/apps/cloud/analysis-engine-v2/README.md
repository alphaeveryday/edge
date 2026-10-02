# 분석엔진 v2 로컬 대시보드

분석 결과, DB 저장 행, 출력 계약 검사, 에이전트 관측, 시스템 프롬프트 관리를 한 화면에서 제공한다. 패키지별 역할은 [저장소 구조](../../../../docs/repo-structure.md#분석엔진-v2-패키지)에 있다.

## 실행

패키지를 설치한 Python 환경에서 실행한다. DB 터널과 인증서 경로는 실행 환경에 맞게 지정한다.

```sh
python -m edge_analysis_v2.dashboard.server --rds-ca /path/to/rds-ca.pem --port 8765 --env-file /path/to/.env --runs-dir /path/to/runs --contract-vault /path/to/ETF-ORCA
```

`http://127.0.0.1:8765/`에 접속한다. `--env-file`과 `--runs-dir`를 생략하면 실행 기능 없이 DB를 조회한다. `.env`에서 읽는 값은 `DEEPSEEK_API_KEY`와 선택적 `DEEPSEEK_MODEL`이다. 실행 기록·키·프롬프트 버전 이력은 Git에 추가하지 않는다.

모델 실행은 전망 600초, 오늘의 가격변동 설명 300초를 기본 한도로 사용한다. 별도 고정 턴 제한은 없으며, 시간 초과 시 불완전한 응답을 발행하지 않고 실패로 기록한다.

프롬프트 관리는 실제 `prompts/*.yaml`을 수정한다. 저장은 이후 대시보드 실행부터 적용되며 실행 시작 시 YAML과 버전이 고정된다. 과거 버전 비교는 읽기 전용이다. 버전 이력은 실행 디렉터리의 `.prompt_versions/`에 저장한다.

계약 검사는 실제 DB 조립 응답을 읽기 전용으로 검증한다. 옵시디언 원문을 복사하지 않고 경로와 해시만 보관한다. 자세한 범위는 [계약 안내](src/edge_analysis_v2/contracts/README.md)를 참고한다.

## 검증

문서 스킬과 실행 권한, 컨테이너 검증 방법은 [분석 스킬 실행 계약](docs/agent-skills.md)을 참고한다.
API·가격 사건의 실행 접수와 운영 연결 전 범위는 [v2 실행 접수 계약](../../../../docs/contracts/analysis-v2-admission.md)을 참고한다.

```sh
python -m pytest tests -q
node --test integration_tests/test_review_refresh.cjs integration_tests/test_prompt_drafts.cjs
```

DB 통합 테스트는 Flyway 마이그레이션이 적용된 로컬 테스트 DB가 필요하다. `V2_TEST_DSN`은 `127.0.0.1:55439/analysis_v2`, `V2_FACTOR_TEST_DSN`은 `127.0.0.1:55440/analysis_v2`, `V2_SOURCE_TEST_DSN`은 `127.0.0.1:55445/edge`(cloud 스키마)를 가리켜야 한다. 실제 분석 DB를 테스트 대상으로 허용하지 않는다.

접수 통합 테스트는 `V2_ADMISSION_TEST_DSN=postgresql://v2_local:local_only@127.0.0.1:55446/analysis_v2`인 별도 로컬 PostgreSQL을 사용한다. 테스트가 임시 스키마에 새 접수 테이블을 만들고 제거하므로 전체 원천 적재는 필요 없다.

```sh
python -m pytest integration_tests/test_admission.py -q
```

```sh
python -m pytest tests integration_tests -q
```

### 가격 설명 자동 전달

- 클라우드 워커는 분석 완료 후 기존 금융사 전달 원장에 등록한다. 관리자의 승인 단계는 없다.
- 실제 DB 기반 발행 결과만 대상이다. 목데이터·미발행 결과는 전달하지 않는다.
- 기존 전달 락과 커서를 재사용한다. 동일 분석 재시도, 이미 전달된 변경 없는 본문, 최신 발행보다 늦게 완료된 과거 분석은 다시 보내지 않는다.
- 전달 실패 시 분석 본문은 보존하고 실행을 실패로 표시한다. 동일 실행 재시도는 모델을 다시 호출하지 않고 완료된 결과의 전달을 재시도한다.
- 배포 순서: v2 전달 API·수신 서버 → 전달 원장 최소 권한 → 워커 활성화. 전망·적재는 변경하지 않는다.
