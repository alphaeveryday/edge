# edge — 프로젝트 지침

이 프로젝트의 에이전트 지침은 AGENTS.md를 단일 출처(SSOT)로 둡니다.
Codex 등 다른 도구와 공유하기 위함입니다.

@AGENTS.md

## 하네스: Git 작업 사이클 · 문서 정합성

**목표:** 티켓→브랜치→PR→머지 사이클의 거버넌스 자동 준수, 코드-문서 드리프트 제거.

**트리거:** 기능·버그·문서 작업의 시작/커밋/PR/머지/Jira 티켓 처리 요청 시 `pr-cycle` 스킬을 사용하라. 변경 리뷰(diff·PR 점검, "코드리뷰 해줘") 요청 시 `edge-review` 스킬을, 코드 변경 후 문서 정합성 점검·갱신 요청 시 `docs-sync` 스킬을 사용하라 (둘 다 pr-cycle이 PR 전 게이트에서 자동 호출: 빌드/테스트 → edge-review → docs-sync). 단순 조회성 질문은 직접 응답 가능.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-07-02 | 초기 구성 — pr-cycle·docs-sync 스킬 (경량 시작, 에이전트 팀 없음) | skills/pr-cycle, skills/docs-sync | git 히스토리 반복 작업 1·2순위 스킬화 |
| 2026-07-02 | 티켓 확인을 3단계 에스컬레이션으로 확장 (스프린트→백로그→생성, 스프린트 배치 보장) | skills/pr-cycle | 백로그 미확인 시 중복 이슈 생성 우려 피드백 |
| 2026-07-04 | edge 특화 코드리뷰 스킬 신설 + PR 전 게이트에 편입 (빌드/테스트 → edge-review → docs-sync 3단계) | skills/edge-review, skills/pr-cycle | 내장 /code-review 대신 AGENTS 규칙·계약(schema SSOT·신뢰경계·레이크) 특화 리뷰를 게이트에 상시화 |
