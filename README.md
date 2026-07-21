# edge

세 가지 런타임(JVM · Node · Python)을 한 저장소에서 관리하는 폴리글랏 모노레포입니다.
실제 코드는 `src/` 아래에 있으며, 배포되는 실행 단위는 `apps/`, 가져다 쓰는 공유 코드는 `libs/`에 둡니다.

> **프로젝트 상태 — 하이브리드 피벗 재편 중.** JVM 앱(tenant-console-api·super-admin-api·tenant-sync-api·publication-api)은 Spring Boot로 스캐폴드되어 빌드·기동되며, `libs/schema`(Flyway)·`libs/jvm-common`(공통 응답 규약)도 채워졌습니다. 벤더 서빙 embed widget 서버(widget-api)는 하이브리드 온프렘 피벗([ADR-0010](docs/adr/0010-hybrid-onprem-pivot.md))으로, 클라우드 gateway는 [ADR-0032](docs/adr/0032-retire-gateway.md)로 삭제됐고 (위젯 **UI 자체는 빌드 산출물로 납품** — [ADR-0035](docs/adr/0035-widget-ui-build-artifact.md), 벤더 실행 서버 없음), 배포는 **아티팩트 2종(edge-cloud / edge-onprem)** 경계로 재편됩니다([docs/implementation.md](docs/implementation.md) §1). 신규 온프렘 컴포넌트(sync-agent·intake·screening-worker·publication-api)는 walking skeleton 단계에서 추가됩니다(sync-agent=DMZ Pull·검증, intake=내부망 수신·저장 — [ADR-0036](docs/adr/0036-sync-agent-intake-topology.md)).

## 한눈에 보기

```
src/
├── apps/                     # 배포되는 실행 단위 (플레인별 그룹 — ADR-0029)
│   ├── cloud/                #   edge-cloud (벤더 운영)
│   │   ├── tenant-sync-api/  # JVM    · Sync Agent Pull 표면 (cursor delta)
│   │   ├── super-admin-api/  # JVM    · 운영자용 · cross-tenant · 최고 권한
│   │   ├── super-admin-ui/   # Node   · 플랫폼 운영자 콘솔 (cross-tenant)
│   │   ├── data-pipeline/    # Python · 파이프라인 SFN raw→정제→feature 페이즈
│   │   └── analysis-engine/  # Python · 같은 SFN 의 analyze 페이즈 → 분석 결과 DB 저장
│   └── onprem/               #   edge-onprem (증권사 관리 환경)
│       ├── tenant-console-ui/  # Node · 테넌트 검수·정책 콘솔
│       ├── tenant-console-api/ # JVM  · 테넌트용 · 읽기/쓰기
│       └── publication-api/        # JVM  · MTS 조회 표면 (Published만)
├── libs/                     # 가져다 쓰는 공유 코드 (플레인 무관 공유)
│   ├── schema/               # ★ DB 스키마 = 단일 진실 공급원(SSOT)
│   │   ├── migrations-cloud/ #   Flyway cloud 세트 (+ migrations-onprem/ = 온프렘 세트)
│   │   └── generated/        #   스키마에서 생성한 각 언어 모델 (생성기 후속 도입)
│   ├── jvm-common/           # JVM    · 공통 응답 규약(apipayload) + 공유 도메인
│   ├── ui-kit/               # Node   · 두 UI 공유 디자인 시스템
│   └── py-common/            # Python · 공통 유틸
├── settings.gradle           # JVM 루트 (Groovy DSL 멀티모듈)
├── pnpm-workspace.yaml       # Node 루트
└── pyproject.toml            # Python 루트
```

위 트리는 `src/`(코드) 내부다. 저장소 최상위에는 그 밖에 `docs/`(설계 문서) · `tests/`(검증 인프라) · `demo/`(데모 — 가상 MTS 정적 화면과 mock-broker 데모 서버) · `.dev/`(로컬 개발 도구·스크립트) · `out/`(빌드 산출물, git 미추적) · `.claude/`(에이전트 설정)가 있다.

## 런타임별 워크스페이스

각 런타임은 독립된 루트 설정 파일로 자기 모듈만 묶습니다.

JVM은 `src/settings.gradle`(Groovy DSL) 단일 멀티모듈 빌드다. 현재 `libs:schema`·`libs:jvm-common`과 4개 앱(tenant-console-api·tenant-sync-api·publication-api·super-admin-api)이 등록되어 있다. 배포는 여전히 서비스별 독립(각 앱이 자기 bootJar·이미지).

| 런타임 | 루트 설정 | 포함 모듈 |
|---|---|---|
| JVM | `src/settings.gradle` | schema · jvm-common · tenant-console-api · tenant-sync-api · publication-api · super-admin-api |
| Node | `src/pnpm-workspace.yaml` | tenant-console-ui · super-admin-ui · ui-kit |
| Python | `src/pyproject.toml` | analysis-engine · data-pipeline · py-common |

## apps — 배포 단위

| 앱 | 런타임 | 아티팩트 | 역할 |
|---|---|---|---|
| `tenant-console-ui` | Node | **edge-onprem** | 테넌트 검수·정책 콘솔 (증권사 관리 환경 배포, [console-ia](docs/console-ia/tenant-console.md) 기준 재구축 예정) |
| `super-admin-ui` | Node | **edge-cloud** | 플랫폼 운영자용 콘솔 (**cross-tenant**) |
| `tenant-console-api` | JVM | **edge-onprem** | 테넌트용 API. **읽기/쓰기** (증권사 관리 환경 배포 예정) |
| `tenant-sync-api` | JVM | **edge-cloud** | Sync Agent가 Pull하는 Event Bundle 제공 — cursor 기반 delta ([contracts/sync-protocol.md](docs/contracts/sync-protocol.md)). tenant_delivery(outbox) 조회로 번들 조립, mTLS 인가는 후속 |
| `publication-api` | JVM | **edge-onprem** | 증권사 백엔드가 호출하는 조회 표면 — **Published만 반환** + 조회 시 Exposure 기록 ([contracts/publication-api.md](docs/contracts/publication-api.md)). 온프렘 Published Store(PG) 조회 |
| `super-admin-api` | JVM | **edge-cloud** | 운영자용 API. **cross-tenant 읽기/쓰기**, 최고 권한 표면 |
| `data-pipeline` | Python | **edge-cloud** | 통합 파이프라인 SFN 의 raw 수집→정제→feature 페이즈 담당 |
| `analysis-engine` | Python | **edge-cloud** | 같은 SFN 의 마지막 analyze 페이즈 → 분석 결과를 DB에 저장 |

신규 온프렘 컴포넌트(sync-agent · intake · screening-worker)는 walking skeleton 단계에서 **edge-onprem**으로 추가됩니다 (sync-agent=DMZ Pull·검증, intake=내부망 수신·저장 — [ADR-0036](docs/adr/0036-sync-agent-intake-topology.md); [docs/implementation.md](docs/implementation.md) §1). `tenant-sync-api`는 별도 엣지로 mTLS 직접 종단해 노출됩니다([ADR-0032](docs/adr/0032-retire-gateway.md)로 클라우드 gateway 은퇴).

### 표면 분리
- **콘솔 경로**: `tenant-console-ui` → `tenant-console-api` (읽기/쓰기, 한 테넌트 범위 — 온프렘에서 UI·API 동거)
- **운영 경로**: `super-admin-ui` → `super-admin-api` (cross-tenant 읽기/쓰기, 최고 권한)

클라우드 gateway는 은퇴했습니다([ADR-0032](docs/adr/0032-retire-gateway.md)) — super-admin 공개 도달이 필요해지면 ALB 직결(listener rule)로 재도입하고, admin은 운영자(소수·알려진 집합) 전용이라 망 수준(VPN/IP allowlist)으로 제한합니다. 고객 접점은 벤더가 아니라 증권사 MTS/HTS → 온프렘 Publication API 경로입니다. 신뢰 경계 상세는 [docs/context.md](docs/context.md)·[ADR-0008](docs/adr/0008-super-admin-console.md) 참고.

## libs — 공유 코드

| 라이브러리 | 런타임 | 역할 |
|---|---|---|
| `schema` | — | **DB 스키마 단일 진실 공급원(SSOT)**. 마이그레이션과 언어별 생성 모델을 모두 관리 |
| `jvm-common` | JVM | 공통 API 응답 규약(apipayload — `ApiResponse`·`BaseErrorCode`·`GeneralException`) + 공유 도메인 모델·Cloud Event Store(`explanation_result` 등) 접근 로직 |
| `ui-kit` | Node | 콘솔 UI 공유 디자인 시스템 (스텁 — 콘솔 재구축 시 채움) |
| `py-common` | Python | Python 공통 유틸 |

### schema — 단일 진실 공급원(SSOT)
DB 스키마를 `schema/` 한 곳에서 정의합니다.
- `migrations-cloud/`(cloud)·`migrations-onprem/`(온프렘) — Flyway 세트 2개, 아티팩트 분리(ADR-0016). 스키마 변경은 여기서만 관리합니다. 실행은 [`libs/schema`](src/libs/schema/README.md)의 Gradle Flyway 태스크로.
- `generated/` — 스키마로부터 각 언어용 모델을 생성합니다(생성기는 후속 티켓에서 도입; 그 전까지 Flyway SQL이 계약 SSOT). JVM·Python 등 여러 런타임이 동일한 스키마 정의를 공유하도록 보장합니다.

## 데이터 흐름

```
[스케줄러] ─→ 파이프라인 SFN: raw 수집 ─→ 정제 ─→ feature ─→ analyze ──→ DB
              (raw lake 보존)   └──── data-pipeline ────┘   (analysis-engine)   │
                                                                                │
   콘솔:  tenant-console-ui → tenant-console-api (읽기/쓰기, 한 테넌트) ─┘
   운영:  super-admin-ui → super-admin-api (읽기/쓰기, cross-tenant) ─┘

   schema(Flyway SQL = 현재 SSOT) ─→ DB 계약 ─→ 모든 JVM/Python 모듈이 공유   (generated 모델은 후속 도입)
```

- `data-pipeline`이 raw 수집→정제→feature 페이즈에서 외부 데이터를 raw lake에 보존·정규화하고, feature 산출물(가격 트리거·종목 마스터 등)을 DB에 적재합니다.
- `analysis-engine`이 같은 SFN 의 마지막 페이즈(analyze)로 돌며 feature 산출물만 읽어 분석하고, Cloud Event Store(`explanation_result` 등)로 DB에 저장합니다 ([ADR-0028](docs/adr/0028-unified-pipeline-sfn.md)).
- API 계층(`tenant-console-api`/`super-admin-api`)이 DB를 읽어 UI에 제공하며, Cloud Event Store 접근은 `jvm-common`이 담당합니다.
- 고객 대면 흐름(Cloud Event Store → Tenant Sync API → 온프렘 Sync Agent(DMZ) → Intake(내부망) → Compliance → Publication API)은 목표 아키텍처([docs/context.md](docs/context.md) §3)이며 walking skeleton 단계에서 구현됩니다.

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
  - apps: `tenant-console-ui` · `tenant-console-api` · `tenant-sync-api` · `publication-api` · `super-admin-ui` · `super-admin-api` · `data-pipeline` · `analysis-engine`
  - libs: `schema` · `jvm-common` · `ui-kit` · `py-common`
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

티켓 한 장을 끝까지 가져가는 표준 흐름입니다. 사람이 직접 하든 에이전트(Claude Code 등)에게 맡기든 **같은 순서**를 따릅니다.

1. **스프린트에서 본인 티켓 확인** — 현재 열린 스프린트에 자신에게 할당된 이슈가 있는지 봅니다 (`assignee = currentUser() AND sprint in openSprints()`).
2. **처리할 티켓 선택** — 무엇부터 할지 정합니다(각자 판단 또는 에이전트의 우선순위 추천). 기능·버그는 **이슈 우선** — 해당 이슈가 없으면 Jira 이슈를 먼저 만들고 키를 확보합니다.
3. **브랜치 생성 + 즉시 push** — `git switch dev && git pull` 후 `feature/<이슈키>-<슬러그>`로 분기하고 곧바로 `git push -u origin <브랜치>`. 이 push가 Jira 자동화를 깨워 이슈를 **`해야 할 일` → `진행 중`** 으로 옮깁니다.
4. **개발** — 위 [커밋·PR 제목](#커밋pr-제목) 형식(Conventional Commits)을 따르고, 논리 단위로 나눠 커밋합니다.
5. **`dev` 대상 PR** — PR 설명 맨 아래에 `Refs: <이슈키>`(PR 템플릿이 자동 삽입). 브랜치에 키가 있으면 Jira가 PR을 해당 이슈에 자동 연결합니다.
6. **Codex 리뷰 대응** — 자동 리뷰 결과를 확인하고, 수용한 지적은 반영 후 재리뷰를 요청합니다. 통과(👍) 또는 잔여 지적 전건 비수용이면 머지로 넘어갑니다.
7. **`dev`로 Squash 머지** — PR 하나 = 커밋 하나로 `dev`에 합칩니다.
8. **브랜치 삭제** — 머지 후 브랜치를 지우고, 다음 작업은 갱신된 `dev`에서 새로 분기합니다.

> **왜 분기 직후 바로 push하나.** Jira의 "브랜치 생성 → 진행 중" 자동화는 **GitHub for Jira 연동(원격 이벤트)** 으로만 동작합니다. 로컬 전용 브랜치는 Jira가 알 수 없어 트리거되지 않습니다. 키가 들어간 브랜치를 **원격에 올리는 순간** 보드가 움직입니다. 그러니 브랜치는 **만들면 바로 push**하는 것을 습관으로 둡니다.

> **한 사이클은 `dev`까지입니다.** 위 8단계는 티켓 한 장의 단위입니다. `dev → main`은 개별 티켓이 아니라 **릴리스 단위**로 묶는 별도 경계이며, Squash가 아닌 Merge commit을 씁니다 — 위 [머지 정책](#머지-정책) 참고.
