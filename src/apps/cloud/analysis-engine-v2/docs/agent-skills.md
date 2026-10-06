# 분석 스킬 실행 계약

분석 워커는 기존 프로세스 내부 MCP를 유지한다. 별도 MCP 서버나 포트를 만들지 않는다.
`agent/runner.py`는 실행마다 임시 작업 디렉터리와 SDK 로컬 플러그인을 만들고 다음 두 스킬을 제공한다.
에이전트는 필요할 때 스킬을 선택해서 읽으며, 스킬 호출 없이 분석하거나 결과를 제출할 수 있다.

- `skills/hypothesis-analysis-workflow/SKILL.md`
- `skills/etf-hypothesis-analysis/SKILL.md`

원본은 사용자가 지정한 `etf-research-agent/.claude/skills/`의 같은 이름 문서다.
패키지 사본은 직접 편집하지 않는다. 원본 수정 뒤 아래 개발용 명령으로 두 사본을 함께 갱신하고 리뷰·배포한다.
빌드와 실행은 커밋된 패키지 사본만 사용하므로 개발자 PC의 절대 경로에 의존하지 않는다.

```sh
python scripts/sync_skills.py --source-dir <etf-research-agent의-.claude/skills-경로>
```

이 명령은 원문 복사만 하며 모델 호출·해시 적합 검사·실행 시작 시 로딩 검사를 추가하지 않는다.
실행의 `skills.json`에는 제공한 스킬 이름과 문서 SHA-256이 저장되며 관측 업로드에도 포함된다.
해시는 문서 버전 기록일 뿐 실행 허용이나 스킬 로딩 검증에 사용하지 않는다.
작업 규칙은 `agent/workspace/AGENTS.md`에 두고 실행 폴더와 관측 기록에 복사한다.
현재 SDK가 AGENTS.md를 자동으로 읽는다고 가정하지 않고, runner가 해당 내용을 시스템 지침에 포함한다.

## 적용 우선순위

작업별 YAML은 목적·깊이·필수 범위·제출 절차, AGENTS.md는 공통 작성 원칙을 담당한다.
시스템 지침 맨 앞의 prompts/research.md가 종료 조건·재귀 분석 정의·실행 경계를, prompts/output-contract.md가 공통 근거·출력 계약을 담당한다.
조사 스킬은 ACH 기반 비교·질문 선택·하위 답 반영의 적용 방법을 보충하고, ETF 스킬은 사업·이익·기대·평가·ETF 가격 연결을 담당한다.
네 질문의 필수 충족 기준은 전망 YAML에 두고 스킬마다 반복하지 않는다. 두 스킬은 서로의 로딩을 요구하지 않는다. 전체 책임과 버전 확인 방법은 [지시문 구조](system-prompts.md)를 따른다.
연구 출처·사례·개발 검수는 [분석 방법 배경](analysis-methodology.md)에 보존하며 실행 의존성으로 삼지 않는다.
지원하지 않는 다른 스킬·검증 스크립트·추가 분류·`[INFERENCE]` 지시는 실행 스킬에서 제거했다.

| 작업 | 목적과 깊이 | 종료 기준 |
|---|---|---|
| 전망 | 사업·이익·시장 기대·현재 가격·향후 한 달 판단까지 전체 조사 루프 유지 | 중요한 연결 해결 또는 확인된 실제 조사 한계. 가능한 핵심 조사를 미래 과제로 넘기지 않음 |
| 오늘의 가격변동 설명 | 오늘 먼저 볼 배경·상태와 이전 설명의 변화. 의문→필요한 확인→설명·중요도 갱신의 가벼운 루프 | 선택할 항목의 사실·시점·ETF 관련성·사업 의미·중요한 반론을 확인하면 작성. 전망의 심층 범위로 확대하지 않음 |

가벼운 설명은 범위를 좁히는 것이며 사실·시점·수치·근거 기준을 낮추는 것이 아니다.
최대 5불릿 지시는 프롬프트 규칙이며 이 변경에서 새 정량 검사를 추가한 것은 아니다.

## 실행 경계

- SDK 0.2.160을 고정한다. 개인/프로젝트 설정을 로드하지 않고 설정 디렉터리도 실행마다 분리한다.
- 네이티브 도구는 `Skill`, `Read`만 노출한다. Read는 승인된 스킬 문서와 작업 폴더의 AGENTS.md만 허용한다.
- 스킬 호출 횟수·로딩 성공·문서 해시를 분석이나 최종 응답의 선행 조건으로 검사하지 않는다.
- 셸, 파일 쓰기, 직접 웹 접근, 추가 MCP와 하위 에이전트를 허용하지 않는다.
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
docker build -f apps/cloud/analysis-engine-v2/Dockerfile -t analysis-v2:skills . # src/에서 실행
docker run --rm --read-only --cap-drop ALL --entrypoint python \
  -e DEEPSEEK_API_KEY -e DEEPSEEK_MODEL analysis-v2:skills \
  -m edge_analysis_v2.agent.smoke --artifacts /tmp/probe
# 스킬을 호출하지 않아도 분석 도구와 최종 응답이 동작하는지 확인하려면 --without-skills 추가
```

모델 smoke 검사는 실제 모델 비용이 발생한다. 기본 모드는 두 스킬의 네이티브 로딩 후
내부 MCP 1회 호출과 구조화 응답 수락을 확인한다. `--without-skills` 모드는 스킬을
호출하지 않고 같은 경로를 검증한다. DB 접속이나 ETF 분석 품질 검증은 수행하지 않는다.
