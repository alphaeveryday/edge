# edge — 프로젝트 지침

이 프로젝트의 에이전트 지침은 AGENTS.md를 단일 출처(SSOT)로 둡니다.
Codex 등 다른 도구와 공유하기 위함입니다.

@AGENTS.md

모든 산출물(문서·UI 문구·발표 자료) 작성 시 [docs/writing-rules.md](docs/writing-rules.md)의 톤 규칙을 따른다.

## 하네스: Git 작업 사이클 · 문서 정합성

**목표:** 티켓→브랜치→PR→머지 사이클의 거버넌스 자동 준수, 코드-문서 드리프트 제거.

**트리거:** 기능·버그·문서 작업의 시작/커밋/PR/머지/Jira 티켓 처리 요청 시 `pr-cycle` 스킬을 사용하라. 변경 리뷰(diff·PR 점검, "코드리뷰 해줘") 요청 시 `edge-review` 스킬을, 코드 변경 후 문서 정합성 점검·갱신 요청 시 `docs-sync` 스킬을 사용하라 (둘 다 pr-cycle이 PR 전 게이트에서 자동 호출: edge-review → docs-sync — 빌드/테스트 확인은 edge-review 안에 포함). 단순 조회성 질문은 직접 응답 가능.

**변경 이력:** [.claude/harness-changelog.md](.claude/harness-changelog.md)
