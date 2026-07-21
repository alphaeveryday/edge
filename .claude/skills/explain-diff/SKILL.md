---
name: explain-diff
description: 코드 변경(PR·브랜치·커밋·작업트리 diff)을 배경→직관→코드→퀴즈로 풀어낸 인터랙티브 학습 페이지로 만들어 Notion 에 생성한다. 온보딩·지식 공유·"이 PR 설명해줘"·"explain-diff 돌려줘"·"이 변경 설명 페이지로 만들어줘" 요청 시 사용. 정합성 게이트(edge-review·docs-sync)나 리뷰가 아니다 — 가르치는 산출물이다. pr-cycle 에 자동 편입되지 않는다(수동 호출 전용). 출처: geoffreylitt explain-diff gist 의 Notion 판을 edge 특화로 감쌌다.
---

# explain-diff — 변경을 가르치는 Notion 페이지

diff 를 남(또는 나중의 나)에게 가르치는 학습용 페이지로 뽑는다. 리뷰가 아니라 **설명**이다 — 결함을 찾지 않고, 변경의 본질·배경·직관을 전달한다. diff 가 확정된 뒤(PR 생성 후·머지 후)에 돌리는 게 자연스럽다.

## 전제

- **신뢰 경계**: 이 스킬은 읽는 코드를 신뢰한다(프롬프트 인젝션에 노출). 우리 사내 브랜치에만 돌린다. 외부·미신뢰 diff 에는 쓰지 않는다.
- **Notion 연결 필수**: claude.ai Notion MCP 가 붙어 있어야 한다. 안 붙었으면 사용자에게 `/mcp` → `claude.ai Notion` 인증을 안내하고 멈춘다.

## 절차

### 1. 대상 diff 확정

사용자가 준 형태로 범위를 잡는다:

- **PR 번호**: `gh pr view <N> --json number,title,body,headRefName,baseRefName,url,additions,deletions,files` + `gh pr diff <N>`
- **브랜치**: `git diff dev...<branch>`
- **커밋/범위**: 그 SHA/범위
- **미지정**: 현재 브랜치의 `git diff dev...HEAD` + 작업트리(`git status --short`)

PR 이 아직 로컬 checkout 이 아니면, 배경 설명에 필요한 원본 파일은 PR head SHA 에서 꺼낸다(`gh pr view <N> --json headRefOid`, `git fetch origin <sha>`, `git show <sha>:<path>`). merge 되지 않은 SSOT 함수 정의 등이 현재 checkout 에 없을 수 있다.

### 2. 배경을 위한 주변 코드 탐색

diff 만으로는 배경을 못 쓴다. 변경이 건드리는 함수의 정의·호출부·공유 유틸을 읽어 "이 변경 이전의 세계"를 파악한다(AGENTS Rule 8). 바뀐 상수·정규식·게이트의 **의도**를 코드에서 확인한다 — 추측하지 말고 근거를 짚는다.

### 3. Notion 연결·경로 확인

모든 explain-diff 페이지는 **팀 공통 경로 하나**에 쌓는다 — 각자 워크스페이스가 아니다. 팀은 공유 Claude 계정(`asm.alphaeveryday@gmail.com`)을 쓰므로, Notion 을 붙이면 `alpha everyday` 워크스페이스로 붙고 그 안의 고정 상위 페이지에 모인다.

- **고정 부모 페이지**: `Explain Diff` — page_id `3a423c55-5bf4-8029-987e-d9debf48fddc`
  (URL: https://app.notion.com/p/Explain-Diff-3a423c555bf48029987ed9debf48fddc)

먼저 연결 계정을 확인한다:

```
notion-fetch(id="self")   # workspace 가 alpha everyday 인지 확인
```

- `alpha everyday` 가 아니면(개인/게스트 워크스페이스로 붙은 경우) 고정 page_id 는 404 난다. 그때는 페이지를 만들지 말고, 사용자에게 "`/mcp` 로 Notion 을 alpha everyday 워크스페이스로 다시 인증하라"고 안내하고 멈춘다. 임의로 다른 경로에 만들지 않는다.
- 계정이 여러 개 오가는 환경이라(과거 계정 꼬임 사례) **만들기 전에 어느 워크스페이스인지 밝힌다**.

### 4. 페이지 작성

작성 직전 **`notion://docs/enhanced-markdown-spec` 리소스를 읽어** Notion-flavored Markdown 문법(toggle `<details>`, `<callout>`, `<table>`, mermaid)을 정확히 맞춘다. 구성은 4단:

- **배경(Background)** — 두 층으로. (a) 초보자용 깊은 배경(이미 아는 독자는 건너뛰어도 된다고 명시), (b) 이 변경에 직접 닿는 좁은 배경. 관련 주변 코드를 폭넓게 탐색해 쓴다.
- **직관(Intuition)** — 변경의 핵심을 토이 데이터로. 세부가 아니라 본질. 재사용 가능한 다이어그램 패밀리(옛/새 동작 비교 표 · mermaid 데이터흐름도)를 골라 반복 사용한다. 예시 데이터를 꼭 넣는다.
- **코드(Code)** — 변경을 이해되는 순서로 묶어 고수준 워크스루. 핵심 diff 는 ```diff 블록으로.
- **퀴즈(Quiz)** — 중간 난이도 5문항(함정 아님, 실제로 PR 을 이해해야 풀리는 것). 각 문항의 보기를 **toggle 블록(`<details><summary>보기</summary> ✅/❌ 해설</details>`)** 으로 만들어, 펼치면 정답 여부와 이유가 나오게 한다.

문체: [docs/writing-rules.md](../../../docs/writing-rules.md) 톤 + Martin Kleppmann 식의 명료함. 콜아웃으로 핵심 개념·엣지케이스를 강조. 코드베이스가 한국어이므로 한국어로 쓴다.

Codex 리뷰 비수용 finding 등 PR 본문에 담긴 판단 근거가 있으면 코드 절에 함께 풀어준다 — 왜 그 지적을 안 받았는지가 종종 변경의 핵심 이해다.

### 5. 생성·반환

`notion-create-pages(parent={type:"page_id", page_id:"3a423c555bf48029987ed9debf48fddc"}, pages=[{title, icon, content}])` 로 고정 팀 경로 밑에 한 페이지 생성하고 **URL 을 반환**한다. 만든 위치(워크스페이스·상위 페이지)를 함께 보고한다.

`content`·`parent`·`pages` 는 tool 인자로 **직접** 넘긴다 — 전체를 raw JSON 문자열로 직렬화해 넘기면 긴 본문의 이스케이프가 깨져 파싱 실패한다(실측).
