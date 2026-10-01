# 분석엔진 v2 로컬 대시보드

분석 결과, DB 저장 행, 출력 계약 검사, 에이전트 관측, 시스템 프롬프트 관리를 한 화면에서 제공한다. 패키지별 역할은 [저장소 구조](../../../../docs/repo-structure.md#분석엔진-v2-패키지)에 있다.

## 클라우드 실행 자동 관측

AWS에서 실행하고 로컬에서는 S3에 저장된 입력·툴 호출·최종 화면·DB 근거를 자동으로 내려받는다. 로컬에 DeepSeek 키나 DB 터널은 필요 없다. 예약은 없다.

```sh
python -m edge_analysis_v2.cloud.dashboard --profile edge-v2-observer --runs-dir /path/to/cloud-runs --port 8765
```

`http://127.0.0.1:8765/`의 에이전트 관측 탭에서 ETF 코드와 자료 기준시각으로 실행한다. 실행 중에는 약 5초, 대기 중에는 약 30초 간격으로 동기화한다. 대시보드가 꺼져 있어도 클라우드 분석은 계속되며 다시 열면 기록을 이어받는다. 인증 만료·네트워크 오류는 동기화 상태에 표시한다. S3 캐시는 다시 다운로드할 수 있고 기존 로컬 실행 폴더와 분리한다.

`edge-v2-observer`는 `work` 프로필에서 `edge-dev-analysis-v2-observer` 역할을 수임한다. 권한은 v2 실행 요청·상태 조회와 관측 S3 경로 읽기뿐이다. 프롬프트 편집은 기존 로컬 개발 모드에서 수행하며 클라우드는 배포된 프롬프트를 사용한다. [요청·저장 계약과 배포 순서](docs/cloud-observation.md)

## 실행

패키지를 설치한 Python 환경에서 실행한다. DB 터널과 인증서 경로는 실행 환경에 맞게 지정한다.

```sh
python -m edge_analysis_v2.dashboard.server --rds-ca /path/to/rds-ca.pem --port 8765 --env-file /path/to/.env --runs-dir /path/to/runs --contract-vault /path/to/ETF-ORCA
```

`http://127.0.0.1:8765/`에 접속한다. `--env-file`과 `--runs-dir`를 생략하면 실행 기능 없이 DB를 조회한다. `.env`에서 읽는 값은 `DEEPSEEK_API_KEY`와 선택적 `DEEPSEEK_MODEL`이다. 실행 기록·키·프롬프트 버전 이력은 Git에 추가하지 않는다.

프롬프트 관리는 실제 `prompts/*.yaml`을 수정한다. 저장은 이후 대시보드 실행부터 적용되며 실행 시작 시 YAML과 버전이 고정된다. 과거 버전 비교는 읽기 전용이다. 버전 이력은 실행 디렉터리의 `.prompt_versions/`에 저장한다.

계약 검사는 실제 DB 조립 응답을 읽기 전용으로 검증한다. 옵시디언 원문을 복사하지 않고 경로와 해시만 보관한다. 자세한 범위는 [계약 안내](src/edge_analysis_v2/contracts/README.md)를 참고한다.

## 검증

문서 스킬과 실행 권한, 컨테이너 검증 방법은 [분석 스킬 실행 계약](docs/agent-skills.md)을 참고한다.

```sh
python -m pytest tests -q
node --test integration_tests/test_review_refresh.cjs integration_tests/test_prompt_drafts.cjs
```

DB 통합 테스트는 Flyway 마이그레이션이 적용된 로컬 테스트 DB가 필요하다. `V2_TEST_DSN`은 `127.0.0.1:55439/analysis_v2`, `V2_FACTOR_TEST_DSN`은 `127.0.0.1:55440/analysis_v2`, `V2_SOURCE_TEST_DSN`은 `127.0.0.1:55445/edge`(cloud 스키마)를 가리켜야 한다. 실제 분석 DB를 테스트 대상으로 허용하지 않는다.

```sh
python -m pytest tests integration_tests -q
```
