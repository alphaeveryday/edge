---
name: pr-cycle
description: Jira 이슈 확인부터 브랜치 생성·커밋·PR 생성·Codex 리뷰 왕복(풀링·수용 판단·재리뷰)·Squash 머지·이슈 전환까지 edge 저장소의 Git 작업 사이클 전체를 거버넌스(issue-first, feature/<KEY>-슬러그, Refs 푸터, dev 타겟, 경계별 머지)에 맞게 수행. 기능·버그·문서 작업을 시작할 때, "브랜치 파줘", "커밋해줘", "PR 올려줘", "머지해줘", "티켓 처리해줘", "릴리스하자"(dev→main) 등 git/Jira가 얽힌 모든 요청에 반드시 이 스킬을 사용할 것. 이미 진행 중인 브랜치/PR의 수정·보완·리뷰 반영·재실행 요청에도 사용. 단순 git 상태 조회(로그·diff 보기)에는 불필요.
---

# pr-cycle — 티켓 한 장의 Git 작업 사이클

티켓 하나를 이슈 확인 → 브랜치 → 커밋 → PR → Squash 머지 → 이슈 전환까지 거버넌스 위반 없이 가져간다.
규칙의 SSOT는 루트 [README.md](../../../README.md)의 "Git 컨벤션"이다 — 이 스킬과 README가 충돌하면 README를 따르고, 이 스킬의 갱신을 제안하라.

## Phase 0 — 컨텍스트 확인

`git branch --show-current`와 `git status`, 필요 시 `gh pr list --head <브랜치>`로 현재 위치를 파악하고 모드를 정한다:

| 상태 | 모드 |
|---|---|
| `dev` 위, 깨끗한 작업트리 | 새 사이클 — 1단계부터 |
| `dev` 위, 미커밋 변경 있음 | 변경이 이번 티켓의 작업물이면 브랜치를 만들어 가져간다(2단계로). 무관한 변경이면 처리 방법(stash·별도 커밋)을 사용자에게 확인한 뒤 새 사이클 시작 |
| `feature/*`·`fix/*` 위, PR 없음 | 진행 중 사이클 — 개발/커밋 단계부터 이어감 |
| `feature/*`·`fix/*` 위, 열린 PR 있음 | PR 보완 모드 — 리뷰 반영 커밋 후 push만 |
| `main` 위 | 작업 금지 — `dev`로 이동 후 시작 |

작업트리에 이번 티켓과 무관한 변경이 섞여 있으면 커밋에 쓸어담지 말고 사용자에게 처리 방법을 확인한다. 하나의 PR은 하나의 되돌릴 수 있는 단위여야 하기 때문이다.

## 1. 티켓 확인 (issue-first)

먼저 **Jira 대상 여부를 판별한다**: 기능(`feat`)·버그(`fix`) 작업은 Jira 이슈가 필수다. 키 없는 예외는 추적할 이슈가 없는 자명한 `docs`·`chore`뿐이며 (예: `fix/readme-typo`), 이 경우 `Refs:` 푸터도 생략하고 이 단계를 건너뛴다.

Jira 대상이면 아래 순서로 확인해 **활성 스프린트에 있는 키**를 확보한다. 브랜치를 만들기 전에 끝내야 한다 — 키가 브랜치 이름에 있어야 Jira가 PR을 이슈에 자동 연결하고, 스프린트에 있어야 보드에서 진행 상황이 보인다.

1. **스프린트 확인**: `searchJiraIssuesUsingJql`로 `project = ALPHA AND sprint in openSprints()` + 이슈 키 또는 작업 내용 검색. 있으면 그 키로 진행.
2. **백로그 확인**: 스프린트에 없으면 `project = ALPHA AND (sprint is EMPTY OR sprint not in openSprints()) AND statusCategory != Done` + 검색어로 백로그를 조회한다. 같은 작업의 이슈가 이미 있으면 **새로 만들지 말고** 그 이슈를 활성 스프린트로 올린다. 백로그 확인 없이 바로 생성하면 중복 이슈가 쌓인다.
3. **생성 + 스프린트 배치**: 백로그에도 없으면 `createJiraIssue`로 생성하고 활성 스프린트에 배치한다. 생성하는 이슈는 아래 두 필드를 **반드시 채운다** — 담당자 없는 이슈는 보드에서 주인 없이 떠돌고, story point 없는 이슈는 스프린트 용량 계산을 깨뜨린다.
   - **담당자**: 맥락상 누가 맡을지 명확하면(예: 요청자가 곧 작업자) `lookupJiraAccountId`로 계정을 찾아 설정한다. 불확실하면 **추측으로 배정하지 말고** 사용자에게 누구를 담당자로 할지 물어 확정한 뒤 설정한다.
   - **Story Point**: 감으로 찍지 않는다. 백로그·최근 완료 이슈 몇 건(2~3건 이상)의 story point를 조회해 상대 크기의 기준선으로 삼고, 이번 작업을 그 기준 이슈들과 견주어 산정한다. 산정값과 비교 근거(어떤 이슈 대비 크다/작다)를 함께 보고한다. 필드 ID(예: `customfield_*`)는 `getJiraIssueTypeMetaWithFields`로 확인한다.

스프린트 이동/배치는 `editJiraIssue`의 Sprint 필드로 시도한다. MCP 도구로 불가능하면(필드 권한·보드 API 제약) 사용자에게 보드에서 올려 달라고 요청하고, 배치가 확인된 뒤 다음 단계로 진행한다.

## 2. 브랜치 생성 + 즉시 push

```bash
git switch dev && git pull
git switch -c feature/<이슈키>-<슬러그>   # 버그면 fix/
git push -u origin <브랜치>
```

- 슬러그는 영문 소문자-하이픈으로 작업 요지를 담는다 (예: `feature/ALPHA-121-login-oauth`).
- **분기 직후 바로 push한다.** Jira의 "진행 중" 자동 전환은 원격 이벤트로만 동작하므로, 로컬 전용 브랜치는 보드를 움직이지 못한다.
- 브랜치 접두어(`feature/`·`fix/`)는 작업 성격이고, 커밋 `type`은 각 변경의 성격이다 — `feature/*` 브랜치에 `docs:` 커밋이 있어도 정상이다.

## 3. 개발 + 커밋

- 제목: `type(scope): 제목` — 한국어, 50자 이내, 마침표 없음. scope는 변경된 모듈명(전역이면 `repo`·`config` 또는 생략).
- 하나의 커밋 = 하나의 논리적 변경. 스키마 변경은 `generated` 모델 갱신과 함께 커밋한다.
- 이슈 키는 본문 마지막 줄 `Refs: <이슈키>` 푸터에만 둔다. 제목에 키를 넣지 않는다.
- co-author 트레일러는 넣지 않는다 (프로젝트 설정 `includeCoAuthoredBy: false`).

## 4. PR 전 게이트

PR을 올리기 전에 두 가지를 순서대로 통과시킨다:

1. **edge-review 스킬 실행** — 변경(diff)을 edge 규칙·계약(AGENTS 12룰·schema SSOT·신뢰경계·레이크)과 정통 버그로 리뷰하고, 검증된 finding 은 이 PR 안에서 고친다. edge-review 는 변경 모듈의 **빌드/테스트를 함께 확인**해 실패를 최우선 finding 으로 올리므로, 빌드·테스트가 깨진 채로 PR 이 올라가지 않는다(별도 빌드/테스트 단계를 두지 않는 이유 — 중복). 기본 effort는 `medium`(작은 PR), 넓거나 위험한 변경은 `high`. PR을 올린 뒤 리뷰 봇/사람이 잡을 걸 미리 잡아 왕복을 줄인다.
2. **docs-sync 스킬 실행** — 코드가 바꾼 사실이 문서에 반영됐는지 점검하고 드리프트를 이 PR 안에서 함께 해소한다. 사후 "문서 정합성 정정" 커밋이 반복돼 온 이력이 있다. (edge-review 가 코드를 고쳤을 수 있으니 문서 점검은 그 뒤에 둔다.)

## 5. dev 대상 PR

- base는 반드시 `dev`다. `feature/*`·`fix/*`에서 `main`으로 가는 경로는 없다 (핫픽스도 `fix/* → dev → main`).
- **PR 제목이 squash 후 최종 커밋 메시지가 된다** — 커밋 제목과 같은 Conventional Commits 형식으로 정확하게 쓴다.
- 본문은 PR 템플릿(요약/변경 사항/산출물/체크리스트)을 따르고, 맨 아래 `Refs: <이슈키>`를 채운다. squash 커밋에 키가 남아 `main`까지 따라가게 하는 장치다.
- `gh pr create`가 반환한 PR 번호 `<N>`을 기억해 둔다 — 이후 리뷰 풀링과 머지 subject에 필요하다.

## 6. Codex 리뷰 왕복

PR을 올리면 Codex 리뷰어가 자동 리뷰한다. **`codex-review-loop` 스킬을 실행**해 이 PR `<N>` 의 리뷰 왕복(베이스라인·풀링·수용 판단·수정→`@codex review` 재요청 반복)을 수행한다. 그 스킬이 `+1`(통과) 또는 남은 finding 전건 비수용에서 종료하고 **통과 여부 + 비수용 목록**을 반환한다.

- 통과(또는 전건 비수용)로 종료 → 7단계(머지)로 진행한다.
- 반환된 **비수용 finding과 그 이유는 머지 보고에 반드시 포함**한다 — 판단을 숨기지 않는다.

## 7. Squash 머지 + 브랜치 삭제

- `feature/*`·`fix/*` → `dev` 머지는 Codex 왕복(6단계)을 통과했으면 **확인 없이 실행한다** — 루프 종료 조건(`+1` 또는 전건 비수용)이 곧 머지 게이트라 별도 확인은 중복이다. 단 `dev → main` 릴리스 머지는 되돌리기 훨씬 번거로운 경계이므로 사용자 확인 후 실행한다.
- `gh pr merge <N> --squash --delete-branch --subject 'type(scope): 제목 (#<N>)'` — subject 끝의 `(#<N>)`을 유지해 dev 히스토리에서 PR을 추적할 수 있게 한다.
- `dev → main` 릴리스 PR은 이 사이클 밖의 별도 경계다: Squash가 아니라 **Merge commit** 을 쓴다 — `gh pr merge --merge`(README의 `--no-ff`와 같은 결과: gh의 merge는 항상 머지 커밋을 만든다). Squash는 장수 브랜치 `dev`를 `main`과 발산시킨다 (ADR-0007).

## 8. 사이클 마감

- `git switch dev && git pull`로 복귀하고 로컬 브랜치를 정리한다.
- Jira 이슈가 자동 전환되지 않았으면 `transitionJiraIssue`로 완료 상태로 옮긴다.

## 에러 처리

- push/PR 생성 실패: 1회 재시도 후에도 실패하면 현재 상태(브랜치, 커밋 여부)를 보고하고 중단한다 — 어중간한 상태를 사용자가 모르게 두지 않는다.
- `dev`·`main`에 직접 커밋하려는 상황이 감지되면 즉시 멈추고 브랜치를 만든다.
- 테스트 실패: 실패 출력과 함께 보고한다. 실패를 숨기고 "완료"라 하지 않는다.

## 테스트 시나리오

- **정상 흐름**: "검수 승인 API 구현해줘" → 티켓 확인(예: ALPHA-401) → `feature/ALPHA-401-review-approve` 분기+push → 구현·커밋 → edge-review+docs-sync 게이트 → dev 대상 PR(Refs 푸터) → Codex 리뷰 풀링, finding 1건 수용·수정·`@codex review` 재요청, `+1` 확인 → 사용자 확인 후 Squash 머지·브랜치 삭제 → 이슈 완료 전환.
- **에러 흐름**: edge-review 게이트가 `./gradlew :apps:onprem:tenant-console-api:build` 실패를 최우선 finding 으로 보고 → PR을 올리지 않고 수정 → 게이트 재실행.
