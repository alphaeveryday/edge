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

모델 실행은 전망 600초, 오늘의 가격변동 설명 300초를 기본 한도로 사용한다. 별도 고정 턴 제한은 없으며, 시간 초과 시 불완전한 응답을 발행하지 않고 실패로 기록한다.

### 공개 웹 조사

실제 DB 분석에 `TINYFISH_API_KEY`를 설정하면 기존 분석 MCP에 `search_web(query, page)`와
`read_web_document(url, offset)`가 추가된다. 로컬 `analysis.database_run`은 `--env-file` 또는
환경변수에서, 클라우드 워커는 기존 `DEEPSEEK_SECRET_ARN`이 가리키는 Secret JSON에서 읽는다.
키가 없으면 웹 도구를 등록하지 않고 초기 입력의 `web_research.enabled=false`로 표시한다.
합성 시나리오는 외부 웹을 사용하지 않는다. 별도 MCP 서비스·DB 마이그레이션·의존성 추가는 없다.

- 검색은 편입종목에 제한되지 않으며 탐색 전용이다. 본문 호출의 성공한 `tool_run_id`를 근거로 쓴다.
- TinyFish Search/Fetch만 호출한다. Agent·Browser·로그인·쿠키·업로드·임의 헤더는 제공하지 않는다.
  고정 HTTPS API 주소만 서버가 호출하고 API 리다이렉트는 따르지 않는다. 키는 모델 프로세스에 전달하지 않는다.
- 입력·결과 URL의 프로토콜·포트·인증정보·DNS를 검사해 비공개 주소, loopback, Tailscale,
  메타데이터 주소를 거부한다. **TinyFish 내부 브라우저의 DNS·리다이렉트·하위 요청은 공급자 통제 영역**이다.
  결과 URL 검사는 사후 방어이며 공급자 내부 접속을 사전에 통제했다는 뜻이 아니다. 우리 VPC·로그인 상태를 연결하지 않는다.
- 실행당 외부 호출 최대 30회(실패 포함), 동시 호출 1개, API 소켓 제한 45초, 응답 2MB,
  문서 50만 자로 제한한다. 실패는 기록하고 자동 재시도하지 않는다. 빈 검색 결과와 공급자 실패를 구분한다.
- 본문은 16,000자 단위로 반환한다. `next_offset`으로 같은 실행의 동일 수집본을 이어 읽는다.
  읽은 각 구간·출처·수집시각은 기존 `tool_runs`에 저장되고 대시보드 도구 호출 상세에서 확인한다.
  아직 읽지 않은 구간은 실행 메모리에만 있으며, 실행 간 캐시나 원문 전체 영구 보관을 보장하지 않는다.
- 검색 날짜 필터는 기준일 검증이 아니다. 본문은 발행일이 없거나 기준일 이후이면 최종 근거로 거부한다.
  날짜만 있는 기준일 당일 문서도 시각 미확인으로 거부한다. `historical_revision_verified=false`이므로
  오래된 발행일이 현재 본문의 과거 존재를 입증하지 않는다. 추출 본문은 완전한 원문·표의 재현을 보장하지 않는다.
- 웹 내용은 명령이 아닌 외부 자료다. 시스템 프롬프트·비밀정보·사용자 정보를 검색어에 넣지 않는다.
  본문의 지시로 스킬·파일·도구 접근 범위를 넓힐 수 없으며, 기존 문서 읽기 경계는 유지한다.

API 계약: [Search](https://docs.tinyfish.ai/api-reference/search-the-web),
[Fetch](https://docs.tinyfish.ai/api-reference/fetch-and-extract-content-from-urls).

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
