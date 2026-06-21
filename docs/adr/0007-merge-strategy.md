# ADR-0007: 머지 전략 — 경계별 (feature→dev Squash, dev→main Merge commit)

- 상태: 승인됨
- 날짜: 2026-06-21
- 대체: [ADR-0004](0004-squash-only-merge.md)

## 맥락
[ADR-0004](0004-squash-only-merge.md)는 모든 머지를 Squash로 통일했다. 그러나 `dev`는
계속 재사용하는 **장수 통합 브랜치**라, 이를 Squash로 `main`에 머지하면 Squash가 **새 SHA의 커밋**을
만들어 `dev`와 `main`이 같은 내용·다른 커밋으로 **발산**한다. 그 결과 릴리스마다 `dev`를
`reset --hard origin/main` + force-push로 재정렬해야 하고, `main`에 브랜치 보호(force-push 금지,
"Require linear history")를 걸면 이마저 막힌다.

머지 방식은 **경계의 성격**에 맞춰야 한다 — 일회용 브랜치(feature/fix)와 장수 브랜치(dev)는 다르다.

## 결정
경계마다 머지 방식을 다르게 둔다.

**feature/\* · fix/\* → dev: Squash 머지**
- PR 하나 = 커밋 하나 = 되돌릴 수 있는 단위. PR 내부 중간 커밋은 `dev`에 남기지 않는다.
- Squash 시 **PR 제목이 최종 커밋 메시지**가 되므로 Conventional Commits 형식([[0003-branch-strategy]], [README.md](../../README.md))을 정확히 따른다.
- 머지 후 feature/fix 브랜치는 **삭제**한다. 브랜치가 일회용이라 발산이 생기지 않는다.

**dev → main: Merge commit (`--no-ff`), PR 필수**
- 릴리스 단위 PR. 리뷰와 CI 게이트(`pull_request` 이벤트 + required status checks)를 이 PR에서 통과시킨다.
- Merge commit은 기존 커밋의 SHA를 **공유**하므로 `dev`와 `main`이 **발산하지 않는다** → 동기화용 reset/force-push가 필요 없다.
- 머지 커밋이 "이번 릴리스에 무엇이 들어갔는지"를 기록한다. 머지 후 `main`에 태그한다([[0003-branch-strategy]]).

## 대안
- **모든 머지 Squash([ADR-0004](0004-squash-only-merge.md))** — 장수 `dev`→`main`에서 발산을 일으켜 매 릴리스 reset+force-push가 필요하고, `main` 브랜치 보호와 충돌한다. 그래서 대체했다.
- **dev → main을 ff-only** — 선형 히스토리에 발산도 없지만, GitHub UI에는 fast-forward 머지 버튼이 없어 CLI로만 가능하다. 그러면 `dev → main`의 PR 리뷰·CI 게이트를 포기하게 된다. 우리는 릴리스에 PR 게이트를 원하므로 채택하지 않았다.
- **Rebase 머지** — PR이 여러 커밋으로 흩어져 "PR 단위 롤백"이 어렵고, 재부모화로 SHA가 바뀌어 발산을 일으킨다.

## 결과
- `dev`는 PR당 커밋 하나로 깔끔하게 쌓이고, `main`은 릴리스 머지 커밋으로 릴리스 경계가 드러난다.
- `dev`/`main`이 발산하지 않아 릴리스 후 동기화 작업(reset/force-push)이 사라진다.
- CI는 `pull_request`(머지 전 게이트), CD는 `push: main` 또는 릴리스 태그로 트리거한다 — 머지 방식과 무관하게 동작한다.
- 한계: GitHub 저장소의 머지 버튼 설정은 **저장소 전역**이라 "dev는 Squash·main은 Merge commit"을 버튼만으로 분리 강제할 수 없다. 양쪽 버튼을 모두 켜두고 규약·리뷰로 운영하며, 더 강한 강제가 필요하면 ruleset/머지 큐로 보완한다(규칙 강제 단계).
