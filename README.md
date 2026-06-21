# edge

세 가지 런타임(JVM · Node · Python)을 한 저장소에서 관리하는 폴리글랏 모노레포입니다.
실제 코드는 `src/` 아래에 있으며, 배포되는 실행 단위는 `apps/`, 가져다 쓰는 공유 코드는 `libs/`에 둡니다.

> **프로젝트 상태 — 초기 스캐폴드.** 디렉토리 골격과 설계 문서는 갖춰졌지만, 앱·라이브러리 구현은 아직 비어 있습니다(`.gitkeep`). 아래 구조와 [`docs/`](docs/)의 문서는 **현재 동작하는 시스템이 아니라 목표 아키텍처**를 기술합니다.

## 한눈에 보기

```
src/
├── apps/                     # 배포되는 실행 단위
│   ├── widget-ui/            # Node   · 외부 임베드 위젯
│   ├── tenant-console-ui/    # Node   · 내부 관리자 콘솔
│   ├── gateway/              # JVM    · 공개 엣지 (widget·console 앞단)
│   ├── widget-api/           # JVM    · 외부용 · 읽기 전용 · 좁은 표면
│   ├── tenant-console-api/   # JVM    · 내부용 · 읽기/쓰기 · 넓은 표면
│   ├── data-pipeline/        # Python · 스케줄러 → DB 적재
│   └── analysis-engine/      # Python · 스케줄러 → 분석 결과 DB 저장
├── libs/                     # 가져다 쓰는 공유 코드
│   ├── schema/               # ★ DB 스키마 = 단일 진실 공급원(SSOT)
│   │   ├── migrations/       #   마이그레이션 (한 곳에서 관리)
│   │   └── generated/        #   스키마에서 생성한 각 언어 모델
│   ├── jvm-common/           # JVM    · 공유 도메인 + analysis_result 접근 로직
│   ├── ui-kit/               # Node   · 두 UI 공유 디자인 시스템
│   └── py-common/            # Python · 공통 유틸
├── settings.gradle.kts       # JVM 루트
├── pnpm-workspace.yaml       # Node 루트
└── pyproject.toml            # Python 루트
```

위 트리는 `src/`(코드) 내부다. 저장소 최상위에는 그 밖에 `docs/`(설계 문서) · `tests/`(검증 인프라) · `.dev/`(로컬 개발 도구·스크립트) · `out/`(빌드 산출물, git 미추적) · `.claude/`(에이전트 설정)가 있다.

## 런타임별 워크스페이스

각 런타임은 독립된 루트 설정 파일로 자기 모듈만 묶습니다.

| 런타임 | 루트 설정 | 포함 모듈 |
|---|---|---|
| JVM | `src/settings.gradle.kts` | gateway · widget-api · tenant-console-api · jvm-common |
| Node | `src/pnpm-workspace.yaml` | widget-ui · tenant-console-ui · ui-kit |
| Python | `src/pyproject.toml` | analysis-engine · data-pipeline · py-common |

## apps — 배포 단위

| 앱 | 런타임 | 역할 |
|---|---|---|
| `widget-ui` | Node | 외부 사이트에 임베드되는 위젯 |
| `tenant-console-ui` | Node | 내부 관리자용 콘솔 |
| `gateway` | JVM | 공개 엣지. widget·console 트래픽을 모두 받아 라우트별 필터를 적용해 전달 |
| `widget-api` | JVM | 외부용 API. **읽기 전용**, 좁은 표면(노출 최소화) |
| `tenant-console-api` | JVM | 내부용 API. **읽기/쓰기**, 넓은 표면 |
| `data-pipeline` | Python | 스케줄러로 동작 → DB에 데이터 적재 |
| `analysis-engine` | Python | 스케줄러로 동작 → 분석 결과를 DB에 저장 |

### 외부 표면 vs 내부 표면
- **외부 경로**: `widget-ui` → `gateway`(widget 라우트) → `widget-api` (읽기 전용, 좁은 표면)
- **콘솔 경로**: `tenant-console-ui` → `gateway`(console 라우트) → `tenant-console-api` (읽기/쓰기, 넓은 표면)

`gateway`가 두 트래픽을 모두 앞단에서 받되 **라우트별 독립 필터(fail-closed)** 로 분리하고, `widget-api`는 읽기 전용으로 표면을 좁게 유지합니다. 신뢰 경계 상세는 [docs/architecture.md](docs/architecture.md) 참고.

## libs — 공유 코드

| 라이브러리 | 런타임 | 역할 |
|---|---|---|
| `schema` | — | **DB 스키마 단일 진실 공급원(SSOT)**. 마이그레이션과 언어별 생성 모델을 모두 관리 |
| `jvm-common` | JVM | 공유 도메인 모델 + `analysis_result` 접근 로직 |
| `ui-kit` | Node | `widget-ui`·`tenant-console-ui` 공유 디자인 시스템 |
| `py-common` | Python | Python 공통 유틸 |

### schema — 단일 진실 공급원(SSOT)
DB 스키마를 `schema/` 한 곳에서 정의합니다.
- `migrations/` — 스키마 변경은 여기서만 관리합니다.
- `generated/` — 스키마로부터 각 언어용 모델을 생성합니다. JVM·Python 등 여러 런타임이 동일한 스키마 정의를 공유하도록 보장합니다.

## 데이터 흐름

```
[스케줄러] ─→ data-pipeline ──→ DB ←── analysis-engine ←─ [스케줄러]
                                  │            (분석 결과 저장)
                                  │
   외부:  widget-ui → gateway → widget-api (읽기) ─┘
   콘솔:  tenant-console-ui → gateway → tenant-console-api (읽기/쓰기) ─┘

   schema(SSOT) ─→ generated 모델 ─→ 모든 JVM/Python 모듈이 공유
```

- `data-pipeline`이 외부 데이터를 DB에 적재합니다.
- `analysis-engine`이 적재된 데이터를 분석해 `analysis_result`로 DB에 저장합니다.
- API 계층(`widget-api`/`tenant-console-api`)이 DB를 읽어 UI에 제공하며, `analysis_result` 접근은 `jvm-common`이 담당합니다.

## Git 컨벤션

브랜치 전략 · 커밋/PR 제목 · 머지 정책을 함께 정의합니다.

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
- 릴리스는 `dev → main` 머지 후 `main`에 태그한다.

### 커밋·PR 제목

[Conventional Commits](https://www.conventionalcommits.org)를 따릅니다. 제목(subject)은 한국어로 작성합니다.
Squash 머지 시 **PR 제목이 최종 커밋 메시지**가 되므로, PR 제목도 아래 형식을 그대로 따릅니다.

```
type(scope): 제목

[본문 — 무엇이 아니라 왜를 설명 (선택)]

Refs: ALPHA-121
```

- **type** — `feat`(기능) · `fix`(버그) · `docs`(문서) · `refactor`(리팩터) · `test`(테스트) · `chore`(잡무) · `build`(빌드/의존성) · `ci`(CI) · `perf`(성능)
- **scope** — 변경된 패키지명. 모노레포라 어느 모듈인지 드러냅니다 (선택, 전역 변경 시 생략).
  - apps: `widget-ui` · `widget-api` · `gateway` · `tenant-console-ui` · `tenant-console-api` · `data-pipeline` · `analysis-engine`
  - libs: `schema` · `jvm-common` · `ui-kit` · `py-common`
  - 전역: `repo` · `config` 등
- **제목** — 한국어, 50자 이내, 마침표 없음. 명령형(예: "추가", "수정").
- **푸터 (Jira 이슈 키)** — 본문 아래 마지막 줄에 `Refs: <이슈키>`로 이슈를 참조합니다. 제목 형식(Conventional Commits)은 그대로 두고 키는 **푸터에만** 둡니다. 여러 이슈는 `Refs: ALPHA-121, ALPHA-122`.
  - Squash 머지(feature/fix → dev) 시 최종 커밋 메시지는 **PR 제목 + PR 설명**으로 합쳐지므로, `Refs:`는 **PR 설명 맨 아래**에 둡니다(아래 PR 템플릿이 자동으로 넣습니다). 그래야 `dev`의 squash 커밋에 이슈 키가 남고, `dev → main` 머지 때 그대로 `main`까지 따라옵니다.

### 예시
```
feat(widget-api): 위젯 조회 엔드포인트 추가
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

머지 방식은 **경계마다 다릅니다.** 배경은 [docs/adr/0007](docs/adr/0007-merge-strategy.md).

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

이슈 하나를 끝까지 가져가는 표준 흐름입니다. 사람이 직접 하든 에이전트(Claude Code 등)에게 맡기든 **같은 순서**를 따릅니다.

1. **Jira 이슈 확보** — 기능·버그는 이슈 우선. 작업할 이슈 키(`ALPHA-###`)를 확인합니다.
2. **`dev` 최신화 후 분기** — `git switch dev && git pull` 후 `feature/<이슈키>-<슬러그>`로 분기합니다.
3. **분기 직후 원격 push** — `git push -u origin <브랜치>`. 이 push가 Jira 자동화를 깨워 이슈를 **`해야 할 일` → `진행 중`** 으로 옮깁니다.
4. **작업 + 커밋** — 위 [커밋·PR 제목](#커밋pr-제목) 형식(Conventional Commits)을 따르고, 논리 단위로 나눠 커밋합니다.
5. **`dev` 대상 PR** — PR 설명 맨 아래에 `Refs: <이슈키>`(PR 템플릿이 자동 삽입). 브랜치에 키가 있으면 Jira가 PR을 해당 이슈에 자동 연결합니다.
6. **Squash 머지 → 브랜치 삭제** — 머지 후 브랜치를 지우고, 다음 작업은 갱신된 `dev`에서 새로 분기합니다.

> **왜 분기 직후 바로 push하나.** Jira의 "브랜치 생성 → 진행 중" 자동화는 **GitHub for Jira 연동(원격 이벤트)** 으로만 동작합니다. 로컬 전용 브랜치는 Jira가 알 수 없어 트리거되지 않습니다. 키가 들어간 브랜치를 **원격에 올리는 순간** 보드가 움직입니다. 그러니 브랜치는 **만들면 바로 push**하는 것을 습관으로 둡니다.
