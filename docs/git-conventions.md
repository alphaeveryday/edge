# edge — Git 컨벤션

> 루트 README 이력서(쇼케이스) 재구성으로 옮겨온 문서다. 브랜치 전략 · 커밋/PR 제목 · 머지 정책의 SSOT.

### 브랜치 전략

```
feature/* ─┐
           ├─→  dev  ─→  main  ──(tag)
fix/*     ─┘
```

- `main` — 릴리스 기준. 항상 배포 가능한 상태를 유지합니다.
- `dev` — 통합 브랜치. 모든 작업이 먼저 모이는 곳입니다.
- `feature/*` — 기능 작업. `dev`에서 분기합니다.
- `fix/*` — 버그/핫픽스. `dev`에서 분기합니다.

**브랜치 이름과 Jira 이슈 키**
- 브랜치 종류는 `feature/*`·`fix/*` 둘뿐입니다. 접두어는 **작업 성격**(추가/수정)을, 커밋 `type`(feat·fix·docs·chore…)은 **각 변경**을 가리키는 별개 축입니다. 그래서 `feature/*` 브랜치에 `docs:` 커밋이 와도 정상입니다.
- **이슈 우선(issue-first)** — 기능·버그 작업은 **Jira 이슈를 먼저 만들고** 그 키로 분기합니다(키 필수): `feature/<이슈키>-<슬러그>`. 예: `feature/ALPHA-121-login-oauth`, `fix/ALPHA-130-duplicate-save`. 키가 브랜치에 있으면 Jira가 PR을 해당 이슈에 **자동 연결**합니다.
- **키 없는 예외** — 추적할 이슈가 없는 자명한 문서·잡무(`docs`·`chore`)는 키 없이 분기하고 `Refs:` 푸터를 생략할 수 있습니다. 예: `feature/add-contributing-guide`, `fix/readme-typo`.

**PR 규칙 (엄격한 사다리)**
- `feature/*`·`fix/*` → **`dev`에만** PR 한다.
- `dev` → **`main`에만** PR 한다.
- 따라서 `main`은 **오직 `dev`에서 온 PR만** 받는다. 핫픽스도 예외 없이 `fix/* → dev → main`을 거친다. `main` 직결 경로는 없다.

**스키마 마이그레이션 머지 게이트 (branch protection 도입 전 수동 규율)**
- 마이그레이션(`src/libs/schema/migrations-*`)을 담은 PR은 **머지 직전** 최신 `dev`를 fetch 해 신규 버전이 해당 세트의 최고 버전보다 큰지 재확인한다. CI의 버전 단조성 guard 는 체크 실행 시점의 base 기준이라, 병렬 PR 이 순서대로 머지되면 역행 착지 창이 열린다(2026-07-29 하루 3건 실증, ALPHA-623).
- 역행이면 **전방 리네임**(내용 그대로 더 큰 버전으로) 후 재검증한다. 규칙 상세와 복구 절차: [src/libs/schema/README.md](../src/libs/schema/README.md) "CI 운영 설정".
- 릴리스는 `dev → main` 머지 후 `main`에 태그한다.

### 병렬 작업 (worktree)

여러 브랜치를 **동시에** 진행할 때(특히 에이전트 세션 여러 개)는 **하나의 체크아웃을 공유하지 않는다.** git 저장소 폴더 하나에는 브랜치·작업트리가 각각 하나뿐이라, 두 세션이 같은 폴더에서 일하면 한쪽의 브랜치 전환·파일 저장이 다른 쪽에 섞여 **커밋이 엉키고 작업이 유실**된다.

동시 작업은 `git worktree`로 **폴더를 분리**한다 — 같은 `.git`(커밋 이력·원격)을 공유하면서 폴더·브랜치·작업트리는 따로 간다.

```bash
git worktree add ../edge-<슬러그> -b feature/<KEY>-<슬러그> dev   # 새 브랜치로 새 폴더
git worktree add ../edge-<슬러그> feature/<KEY>-<슬러그>          # 기존 브랜치를 폴더로
git worktree list                                                # 어떤 폴더가 어떤 브랜치인지
git worktree remove ../edge-<슬러그>                             # 머지 후 정리
git worktree prune                                               # 폴더를 그냥 지웠을 때 잔재 청소
```

- 메인 체크아웃은 `dev`용으로 두고, 실제 작업은 각 worktree 폴더에서 한다.
- 같은 브랜치를 두 worktree에 동시 체크아웃하지 않는다 — git이 기본으로 막으며, `--force`로 우회하지 않는다(우회하면 엉킴 위험이 되살아난다).
- **worktree는 파일·브랜치의 기술적 충돌만 막는다.** *두 세션이 같은 작업을 각자 하는 중복*은 못 막으므로, **겹치는 티켓·작업 단위를 동시에 잡지 않도록 배정으로 조율**한다(둘 다 필요 — 폴더 분리 + 비겹침 배정).

### 커밋·PR 제목

[Conventional Commits](https://www.conventionalcommits.org)를 따릅니다. 제목(subject)은 한국어로 작성합니다.
Squash 머지 시 **PR 제목이 최종 커밋 메시지**가 되므로, PR 제목도 아래 형식을 그대로 따릅니다.
`dev` 대상 PR 의 제목 형식(type·scope·마침표·키 위치)은 CI(`pr-title-check`)가 검증해 체크
실패로 드러냅니다(브랜치 보호 불가 플랜이라 강제 차단은 아님 — 머지 전 체크 확인은 운영 규율).
한국어·50자 규약은 기계 강제하지 않습니다(봇 PR·영문 용어 혼용, 리뷰 소관).

```
type(scope): 제목

[본문 — 무엇이 아니라 왜를 설명 (선택)]

Refs: ALPHA-121
```

- **type** — `feat`(기능) · `fix`(버그) · `docs`(문서) · `refactor`(리팩터) · `test`(테스트) · `chore`(잡무) · `build`(빌드/의존성) · `ci`(CI) · `perf`(성능)
- **scope** — 변경된 패키지명. 모노레포라 어느 모듈인지 드러냅니다 (선택, 전역 변경 시 생략).
  - apps: `tenant-console-ui` · `tenant-console-api` · `tenant-sync-api` · `publication-api` · `sync-agent` · `intake` · `screening-worker` · `super-admin-ui` · `super-admin-api` · `data-pipeline` · `analysis-engine`
  - libs: `schema` · `jvm-common` · `ui-kit` · `py-common` · `ontology`
  - 전역: `repo` · `config` 등
- **제목** — 한국어, 50자 이내, 마침표 없음. 명령형(예: "추가", "수정").
- **푸터 (Jira 이슈 키)** — 본문 아래 마지막 줄에 `Refs: <이슈키>`로 이슈를 참조합니다. 제목 형식(Conventional Commits)은 그대로 두고 키는 **푸터에만** 둡니다. 여러 이슈는 `Refs: ALPHA-121, ALPHA-122`.
  - Squash 머지(feature/fix → dev) 시 최종 커밋 메시지는 **PR 제목 + PR 설명**으로 합쳐지므로, `Refs:`는 **PR 설명 맨 아래**에 둡니다(아래 PR 템플릿이 자동으로 넣습니다). 그래야 `dev`의 squash 커밋에 이슈 키가 남고, `dev → main` 머지 때 그대로 `main`까지 따라옵니다.

### 예시
```
feat(tenant-console-api): 검수 승인 엔드포인트 추가
fix(analysis-engine): 분석 결과 중복 저장 방지
docs(repo): 모노레포 구조 README 작성
chore(schema): 마이그레이션 도구 설정
```

이슈 키 푸터까지 포함한 전체 형태:
```
feat(tenant-console-api): OAuth 로그인 구현

기존 세션 방식 대신 OAuth로 외부 IdP 로그인을 지원한다.

Refs: ALPHA-121
```

### 원칙
- 하나의 커밋은 하나의 논리적 변경만 담습니다. 여러 관심사는 나눠 커밋합니다.
- 스키마 변경(`schema`)과 그 생성 모델(`generated`) 갱신은 함께 커밋합니다.

### 머지 정책

머지 방식은 **경계마다 다릅니다.** 배경은 [adr/0007](adr/0007-merge-strategy.md).

| 경계 | 머지 방식 | PR |
|---|---|---|
| `feature/*`·`fix/*` → `dev` | **Squash** (+ 머지 후 브랜치 삭제) | 필수 |
| `dev` → `main` | **Merge commit (`--no-ff`)** | 필수 (릴리스) |

**feature/fix → dev (Squash)**
- PR 하나 = 커밋 하나 = 되돌릴 수 있는 단위. `dev`에 PR당 커밋 하나만 남습니다.
- **PR은 작게 유지합니다.** 리뷰 부담이 줄고, 되돌리는 범위가 좁아집니다.
- PR 안의 중간 커밋은 squash로 합쳐지므로 자유롭게 쌓되, **PR 제목은 정확히** 작성합니다(최종 커밋 메시지가 됨).
- 머지 후 feature/fix 브랜치는 **삭제**합니다. 다음 작업은 갱신된 `dev`에서 새로 분기합니다.

**dev → main (Merge commit, 릴리스 PR)**
- 리뷰와 CI를 이 PR에서 통과시킨 뒤 머지하고, `main`에 태그합니다.
- **Squash·Rebase로 머지하지 않습니다.** 그 둘은 새 SHA의 커밋을 만들어 장수 브랜치 `dev`를 `main`과 **발산**시키고(같은 내용·다른 커밋), 릴리스마다 `dev`를 강제 재정렬해야 합니다. Merge commit은 기존 커밋을 공유해 발산이 없습니다.

### 작업 워크플로 — 한 사이클

티켓 한 장을 끝까지 가져가는 표준 흐름입니다. 사람이 직접 하든 에이전트(Claude Code 등)에게 맡기든 **같은 순서**를 따릅니다.

1. **스프린트에서 본인 티켓 확인** — 현재 열린 스프린트에 자신에게 할당된 이슈가 있는지 봅니다 (`assignee = currentUser() AND sprint in openSprints()`).
2. **처리할 티켓 선택** — 무엇부터 할지 정합니다(각자 판단 또는 에이전트의 우선순위 추천). 기능·버그는 **이슈 우선** — 해당 이슈가 없으면 Jira 이슈를 먼저 만들고 키를 확보합니다.
3. **브랜치 생성 + 즉시 push** — `git switch dev && git pull` 후 `feature/<이슈키>-<슬러그>`로 분기하고 곧바로 `git push -u origin <브랜치>`. 다른 세션과 병렬이면 같은 체크아웃에서 분기하지 않고 위 [병렬 작업](#병렬-작업-worktree)대로 worktree 를 씁니다. 이 push가 Jira 자동화를 깨워 이슈를 **`해야 할 일` → `진행 중`** 으로 옮깁니다.
4. **개발** — 위 [커밋·PR 제목](#커밋pr-제목) 형식(Conventional Commits)을 따르고, 논리 단위로 나눠 커밋합니다.
5. **`dev` 대상 PR** — PR 을 올리기 전에 로컬 검수 게이트를 통과시킵니다: 코드 리뷰를 **수용한 지적이 없어질 때까지** 반복(반복 상한 있음)한 뒤 문서 정합성 점검 — 종료 조건·상한 등 상세 절차는 `.claude/skills/pr-cycle` §4(edge-review 수렴 루프·docs-sync). PR 설명 맨 아래에 `Refs: <이슈키>`(PR 템플릿이 자동 삽입). 브랜치에 키가 있으면 Jira가 PR을 해당 이슈에 자동 연결합니다.
6. **Codex 리뷰 대응** — 자동 리뷰 결과를 확인하고, 수용한 지적은 반영 후 재리뷰를 요청합니다. 통과(👍) 또는 잔여 지적 전건 비수용이면 머지로 넘어갑니다.
7. **`dev`로 Squash 머지** — 머지 전에 **GitHub 체크가 전건 통과했는지 확인합니다**(`gh pr checks <PR번호> --watch`). Codex 통과는 리뷰어 판정일 뿐 CI 통과가 아니며, 체크가 하나도 보고되지 않은 것은 통과가 아니라 워크플로 미실행입니다. 통과했으면 PR 하나 = 커밋 하나로 `dev`에 합칩니다.
8. **브랜치 삭제** — 머지 후 브랜치를 지우고, 다음 작업은 갱신된 `dev`에서 새로 분기합니다.

> **왜 분기 직후 바로 push하나.** Jira의 "브랜치 생성 → 진행 중" 자동화는 **GitHub for Jira 연동(원격 이벤트)** 으로만 동작합니다. 로컬 전용 브랜치는 Jira가 알 수 없어 트리거되지 않습니다. 키가 들어간 브랜치를 **원격에 올리는 순간** 보드가 움직입니다. 그러니 브랜치는 **만들면 바로 push**하는 것을 습관으로 둡니다.

> **한 사이클은 `dev`까지입니다.** 위 8단계는 티켓 한 장의 단위입니다. `dev → main`은 개별 티켓이 아니라 **릴리스 단위**로 묶는 별도 경계이며, Squash가 아닌 Merge commit을 씁니다 — 위 [머지 정책](#머지-정책) 참고.
