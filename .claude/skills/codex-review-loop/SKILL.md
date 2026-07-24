---
name: codex-review-loop
description: 열린 PR 에 붙는 Codex 자동 리뷰어(chatgpt-codex-connector) 대응에 사용 — "Codex 리뷰 돌려줘", "리뷰 왕복", "PR 리뷰 반영해줘", 이미 올라간 PR 의 Codex 리뷰 반영·재요청·재실행 요청 시. pr-cycle 이 Phase 6 에서 자동 호출한다. 대상은 이미 존재하는 PR 번호다 — 브랜치·커밋·머지는 pr-cycle 소관.
---

# codex-review-loop — Codex 리뷰 왕복

열린 PR `<N>` 에 Codex 리뷰어(`chatgpt-codex-connector`)가 붙인 신호를 풀링하고, finding 이 있으면 수용 여부를 판단해 반영·재리뷰를 반복한 뒤, `+1`(통과) 또는 남은 finding 전건 비수용에서 종료한다. 종료 시 **통과/비수용 목록**을 반환한다 — 호출부(pr-cycle 등)가 이 결과로 머지 진행을 정한다.

`{owner}/{repo}` 는 대상 저장소로 치환한다(예: `alphaeveryday/edge`).

## 진입 시 기존 finding 먼저 처리

독립 호출(이미 리뷰가 달린 PR 의 반영 요청 — frontmatter 가 광고하는 용도)로 들어오면, **베이스라인을 잡기 전에** 현재 살아 있는 Codex finding 을 먼저 읽어 수용 판단한다. 여기서 "살아 있음"의 판정은 **GraphQL 리뷰 스레드의 `isResolved`/`isOutdated`** 로 한다:

```bash
gh api graphql -f query='
{ repository(owner:"{owner}", name:"{repo}") { pullRequest(number:<N>) {
    reviewThreads(first:100) { nodes {
      isResolved isOutdated
      comments(first:1) { nodes { author{login} path line body } } } } } } }'
```

`isResolved==false && isOutdated==false` 이고 작성자가 `chatgpt-codex-connector` 인 스레드만 현재 finding 이다. 이 기존 finding 을 수용 판단한 뒤 분기한다:

- **하나라도 수용** → 아래 반영 루프(수정→push→재리뷰→풀링)로 간다.
- **전건 비수용** → **여기서 종료**하고 비수용 목록을 반환한다. 풀링으로 내려가지 마라 — 새 커밋도 재리뷰 요청도 없어 관찰할 새 신호가 없고, "전건 비수용"은 이미 터미널 조건이다(풀링으로 가면 타임아웃까지 헛돈다).
- **현재 finding 이 없음**(pr-cycle 경유 신선 진입 등) → 아래 베이스라인·풀링으로 간다.

**REST `pulls/<N>/comments` 의 `commit_id == headRefOid` 로 최신성을 판정하지 마라** — `commit_id` 는 코멘트가 *달린* 커밋일 뿐이고, 이후 push 가 그 finding 의 줄을 건드리지 않으면 스레드는 여전히 active 인데 `commit_id` 는 옛 SHA 그대로다. SHA 로 거르면 **아직 살아 있는 finding 을 떨어뜨려** 조용히 놓친다. 반대로 REST 를 필터 없이 쓰면 resolved·outdated 를 되살린다 — 그래서 스레드 상태가 정답이다. (베이스라인부터 잡으면 사용자가 처리하려던 바로 그 기존 finding 이 베이스라인에 묻혀 새 신호를 기다리다 타임아웃하므로, 위 분기를 베이스라인보다 **먼저** 둔다.)

## 풀링

풀링을 시작하기 전(pr-cycle 경유 최초 진입·재리뷰 요청 직전·재진입) 현재 리액션·리뷰·봇 이슈 코멘트 상태를 **베이스라인으로 기억**하고, **그 이후 생긴 신호만** 유효로 판정한다. 세 신호 모두 PR에 붙지 커밋에 붙지 않아서, 이전 라운드의 `+1`·리뷰·에러 코멘트를 새 응답으로 오인하면 새 커밋이 무리뷰로 통과되거나, 이미 반영한 지적을 다시 돌거나, 지나간 에러로 사이클을 중단한다. 60초 간격으로 확인한다 (보통 수 분 내 응답, 10분 넘게 무응답이면 사용자에게 보고하고 대기 여부를 확인):

```bash
gh api repos/{owner}/{repo}/issues/<N>/reactions --jq '.[] | select(.user.login == "chatgpt-codex-connector[bot]") | .content'   # 통과 시 "+1"
gh pr view <N> --json reviews --jq '[.reviews[] | select(.author.login == "chatgpt-codex-connector")] | length'                    # 코멘트 리뷰 수
```

- PR 본문에 **`+1` 리액션** → 통과. 루프를 끝내고 통과로 반환한다.
- **리뷰 코멘트** → 인라인 코멘트를 모두 읽고(`gh api repos/{owner}/{repo}/pulls/<N>/comments`) finding별로 수용 여부를 판단한다.
- **봇의 이슈 코멘트**(`gh api repos/{owner}/{repo}/issues/<N>/comments`)도 함께 본다 — 계정 미연동("create a Codex account") 같은 **에러가 리뷰가 아니라 코멘트로 오므로**, 이 신호를 안 보면 타임아웃까지 헛돈다. 베이스라인 이후 새로 달린 에러만 현재 라운드의 실패로 보고한다.

주의: **PR 본문·코멘트에 `@codex` 문자열을 설명 용도로도 넣지 마라** — 백틱 안이어도 봇이 raw 텍스트를 파싱해 작성자 계정의 호출로 처리한다(미연동 계정이면 리뷰 거부). 재리뷰 요청으로 의도할 때만 쓴다.

## 수용 판단

Codex는 과하게 엄밀한 경향이 있으므로 지적을 그대로 받아들이지 말고 두 축으로 거른다:

1. **실질 여부**: 실제 버그·계약 위반·데이터 손상 경로인가, 아니면 이론상 엣지·스타일 취향·과잉 방어 요구인가. 후자는 비수용.
2. **의도적 생략 여부**: 이 PR의 스코프에서 의도적으로 하지 않은 것(YAGNI, 후속 티켓, 기존 컨벤션 준수)을 지적한 것인가. 그렇다면 비수용.

## 반영 루프

**프리플라이트(수정 커밋 전 1회)**: 두 가지를 확인한다.
1. 로컬 체크아웃이 PR `<N>` 의 head 브랜치와 일치하는가 — `gh pr view <N> --json headRefName` 과 `git branch --show-current` 대조. 불일치면(사용자가 `dev`·다른 브랜치에 있는 상태로 독립 호출) **커밋하지 말고 멈춰** 알린다. 자동 checkout 은 하지 않는다(이 저장소는 워크트리가 브랜치를 점유할 수 있어 checkout 이 실패·충돌할 수 있다).
2. 작업트리에 이 수정과 **무관한 변경이 있는가**(`git status --short`) — 있으면 알리고, 커밋은 `git add <수정 파일>` 로 대상 파일만 담는다(`git add -A` 로 무관한 변경을 쓸어담지 않는다).

pr-cycle 경유 호출은 Phase 0–2 가 이미 PR 브랜치·단일 작업 단위를 보장하므로 이 프리플라이트는 통과한다.

하나라도 수용했다면: 수정 커밋 → push → **베이스라인 기록** → PR에 `@codex review` 코멘트로 재리뷰 요청 → 다시 풀링. 베이스라인은 반드시 요청 **전에** 기록한다 — 봇은 수 초 만에도 응답하므로, 요청 후에 기록하면 그 사이 도착한 응답이 베이스라인에 묻혀 타임아웃까지 헛돈다. 새 `+1`이 달리거나, 남은 finding이 전부 비수용 판정이면 루프를 끝낸다.

## 반환

종료 시 **비수용한 finding과 그 이유를 반드시 포함해 반환**한다 — 판단을 숨기지 않는다. 호출부는 이 결과(통과 여부 + 비수용 목록)를 후속(머지·보고)에 쓴다.
