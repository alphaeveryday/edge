# edge

세 가지 런타임(JVM · Node · Python)을 한 저장소에서 관리하는 폴리글랏 모노레포입니다.
실제 코드는 `src/` 아래에 있으며, 배포되는 실행 단위는 `apps/`, 가져다 쓰는 공유 코드는 `libs/`에 둡니다.

## 한눈에 보기

```
src/
├── apps/                     # 배포되는 실행 단위
│   ├── widget-ui/            # Node   · 외부 임베드 위젯
│   ├── tenant-console-ui/    # Node   · 내부 관리자 콘솔
│   ├── gateway/              # JVM    · 공개 엣지 (widget-api 앞단)
│   ├── widget-api/           # JVM    · 외부용 · 읽기 전용 · 좁은 표면
│   ├── tenant-console-api/   # JVM    · 내부용 · 읽기/쓰기 · 넓은 표면
│   ├── data-pipeline/        # JVM    · 스케줄러 → DB 적재
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

## 런타임별 워크스페이스

각 런타임은 독립된 루트 설정 파일로 자기 모듈만 묶습니다.

| 런타임 | 루트 설정 | 포함 모듈 |
|---|---|---|
| JVM | `src/settings.gradle.kts` | gateway · widget-api · tenant-console-api · data-pipeline · jvm-common |
| Node | `src/pnpm-workspace.yaml` | widget-ui · tenant-console-ui · ui-kit |
| Python | `src/pyproject.toml` | analysis-engine · py-common |

## apps — 배포 단위

| 앱 | 런타임 | 역할 |
|---|---|---|
| `widget-ui` | Node | 외부 사이트에 임베드되는 위젯 |
| `tenant-console-ui` | Node | 내부 관리자용 콘솔 |
| `gateway` | JVM | 공개 엣지. `widget-api` 앞단에서 외부 트래픽을 받음 |
| `widget-api` | JVM | 외부용 API. **읽기 전용**, 좁은 표면(노출 최소화) |
| `tenant-console-api` | JVM | 내부용 API. **읽기/쓰기**, 넓은 표면 |
| `data-pipeline` | JVM | 스케줄러로 동작 → DB에 데이터 적재 |
| `analysis-engine` | Python | 스케줄러로 동작 → 분석 결과를 DB에 저장 |

### 외부 표면 vs 내부 표면
- **외부 경로**: `widget-ui` → `gateway` → `widget-api` (읽기 전용, 좁은 표면)
- **내부 경로**: `tenant-console-ui` → `tenant-console-api` (읽기/쓰기, 넓은 표면)

외부에 노출되는 `widget-api`는 표면을 의도적으로 좁게 유지하고, 그 앞단을 `gateway`가 감쌉니다.

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
   내부:  tenant-console-ui → tenant-console-api (읽기/쓰기) ─┘

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
```

- **type** — `feat`(기능) · `fix`(버그) · `docs`(문서) · `refactor`(리팩터) · `test`(테스트) · `chore`(잡무) · `build`(빌드/의존성) · `ci`(CI) · `perf`(성능)
- **scope** — 변경된 패키지명. 모노레포라 어느 모듈인지 드러냅니다 (선택, 전역 변경 시 생략).
  - apps: `widget-ui` · `widget-api` · `gateway` · `tenant-console-ui` · `tenant-console-api` · `data-pipeline` · `analysis-engine`
  - libs: `schema` · `jvm-common` · `ui-kit` · `py-common`
  - 전역: `repo` · `config` 등
- **제목** — 한국어, 50자 이내, 마침표 없음. 명령형(예: "추가", "수정").

### 예시
```
feat(widget-api): 위젯 조회 엔드포인트 추가
fix(analysis-engine): 분석 결과 중복 저장 방지
docs(repo): 모노레포 구조 README 작성
chore(schema): 마이그레이션 도구 설정
```

### 원칙
- 하나의 커밋은 하나의 논리적 변경만 담습니다. 여러 관심사는 나눠 커밋합니다.
- 스키마 변경(`schema`)과 그 생성 모델(`generated`) 갱신은 함께 커밋합니다.

### 머지 정책

**Squash 머지만 허용합니다.** Merge commit·Rebase 머지는 사용하지 않습니다.

- **PR 하나 = 커밋 하나 = 되돌릴 수 있는 단위.** `main`/`dev` 히스토리에 PR당 커밋 하나만 남아, 추적과 롤백(`revert`)이 단순해집니다.
- **PR은 작게 유지합니다.** 리뷰 부담이 줄고, 문제 발생 시 되돌리는 범위가 좁아집니다.
- PR 안의 중간 커밋은 squash로 합쳐지므로 자유롭게 쌓되, **PR 제목은 정확히** 작성합니다(최종 커밋 메시지가 됨).
