# 분석 스킬 실행 계약

분석 워커는 기존 프로세스 내부 MCP를 유지한다. 별도 MCP 서버나 포트를 만들지 않는다.
`agent/runner.py`는 실행마다 임시 작업 디렉터리와 SDK 로컬 플러그인을 만들고 `skills/` 아래 네 스킬을 제공한다.
어떤 문서가 언제 전달되는지는 [지시문 구조](system-prompts.md)에 있다.

전망의 마지막 필드 작성은 예외다. `structured_only=True`로 완성된 조사·본문·요약·요인 값을 직접 받고,
파일·스킬·작업관리 도구와 MCP 없이 칸을 채운다. SDK의 `StructuredOutput`, 응답 스키마 검사,
근거 번호 수정 요청(최대 2회), 시간 제한과 실행 기록은 유지한다. 이 모드는 조사 도구·스킬·메모 복구와 함께 켤 수 없다.

- 스킬 문서의 원본은 이 패키지의 `skills/<이름>/SKILL.md`다. 직접 고치고 리뷰·배포한다.
- 작업이 선언한 스킬(`run_model(skills=...)`)은 전문이 작업 지시와 함께 전달되므로 `Skill`·`Read` 호출 없이 적용된다. 전망과 가격변동 설명은 `cause-investigation`과 `analysis-report-writing`을 선언한다.
- 선언하지 않은 스킬은 에이전트가 필요할 때 `Skill`로 연다. 스킬 호출 없이 분석하거나 결과를 제출할 수 있다.
- 실행의 `skills.json`에는 쓸 수 있던 스킬, 선언한 스킬, 실행 중 연 스킬, 문서 SHA-256이 저장되며 관측 업로드에도 포함된다. 해시는 문서 버전 기록일 뿐 실행 허용 검사에 쓰지 않는다.
- 연구 출처·사례·개발 검수는 [분석 방법 배경](analysis-methodology.md)에 보존하며 실행 의존성으로 삼지 않는다.

| 작업 | 목적과 깊이 | 종료 기준 |
|---|---|---|
| 전망 | 사업·이익·시장 기대·현재 가격·향후 한 달 판단까지 전체 조사 루프 유지 | 중요한 연결 해결 또는 확인된 실제 조사 한계. 가능한 핵심 조사를 미래 과제로 넘기지 않음 |
| 오늘의 가격변동 설명 | 오늘 먼저 볼 배경·상태와 이전 설명의 변화. 의문→필요한 확인→설명·중요도 갱신의 가벼운 루프 | 선택할 항목의 사실·시점·ETF 관련성·사업 의미·중요한 반론을 확인하면 작성. 전망의 심층 범위로 확대하지 않음 |
| 질문 하나에 답하기 | 물은 것을 도구로 확인해 질문 크기에 맞게 답함 | 답을 얻으면 끝. 스킬을 선언하지 않음 |

## 실행 경계

- SDK 0.2.160을 고정한다. 개인/프로젝트 설정을 로드하지 않고 설정 디렉터리도 실행마다 분리한다.
- 네이티브 도구는 `Skill`, `Read`, `Write`, `Edit`를 노출한다. 작업 상태는 내부 MCP의 `workspace.update_tasks`로 저장한다. 문서는 승인된 스킬·작업 지시(AGENTS.md)와 실행별 notes/의 Markdown만 읽는다. 쓰기·수정·폴더 생성은 notes/ 안에서만 허용한다.
- 스킬 호출 횟수·로딩 성공·문서 해시를 분석이나 최종 응답의 선행 조건으로 검사하지 않는다.
- 셸, notes/ 밖 쓰기, 직접 웹 접근, 외부 MCP와 하위 에이전트를 허용하지 않는다. 프로세스 내부 workspace MCP가 원자료 페이지 조회와 notes/ 폴더 생성을 제공한다.
- 미완료 TODO가 남은 정상 종료에는 최대 3회 후속 메시지를 보내며 계속 미완료면 발행을 실패시킨다. workspace.json에 상태·메모를 저장한다. runner의 명시적 resume은 같은 원자료에 한해 중단된 메모와 TODO를 복구하며, 클라우드 큐의 자동 재시도와는 별개다.
- 대화 종료나 예외 후 임시 작업 디렉터리를 정리한다. 관측 결과는 별도 실행 폴더에 남긴다.

SDK 훅은 도구 권한 검사다. OS 수준에서 프로세스의 모든 파일 읽기를 격리하는 장치는 아니다.
SDK 자식 프로세스는 부모 환경을 상속하며 내부 MCP와 같은 태스크 권한 범위에서 동작한다.
따라서 임의 코드 실행을 허용하는 샌드박스로 확대해서는 안 된다.

## 컨테이너와 Fargate

기존 클라우드 워커 진입점을 유지한다. 배포 워크플로는 새 태스크 리비전에 다음을 설정한다.

- UID/GID `1001:1001`, 읽기 전용 루트, Linux capability 전체 제거.
- `/tmp`만 태스크 전용 빈 볼륨으로 마운트. 이미지의 `VOLUME /tmp`와 경로를 맞춘다.
- 공유 볼륨이나 예상 밖 마운트가 있는 태스크는 자동 수정하지 않고 배포를 실패시킨다.

볼륨 구성은 [AWS bind mount 문서](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/bind-mounts.html)를 따른다.
실제 AWS 배포 전에는 태스크의 SG/IAM과 허용한 외부 API 경로를 확인해야 한다.
이 변경은 VPC egress 제어나 SDK와 DB 자격증명 간 OS 격리를 구현하지 않는다.
기존 태스크에서 이미지뿐 아니라 위 필드도 새 리비전으로 등록하므로 진행 중인 태스크는 바꾸지 않는다.

## 검증

```sh
python -m pytest tests -q
docker build -f apps/engine/analysis-engine-v2/Dockerfile -t analysis-v2:skills . # src/에서 실행
docker run --rm --read-only --cap-drop ALL --entrypoint python \
  -e DEEPSEEK_API_KEY -e DEEPSEEK_MODEL analysis-v2:skills \
  -m edge_analysis_v2.agent.smoke --artifacts /tmp/probe
# 스킬을 호출하지 않아도 분석 도구와 최종 응답이 동작하는지 확인하려면 --without-skills 추가
```

모델 smoke 검사는 실제 모델 비용이 발생한다. 기본 모드는 두 스킬의 네이티브 로딩 후
내부 MCP 1회 호출과 구조화 응답 수락을 확인한다. `--without-skills` 모드는 스킬을
호출하지 않고 같은 경로를 검증한다. DB 접속이나 ETF 분석 품질 검증은 수행하지 않는다.
