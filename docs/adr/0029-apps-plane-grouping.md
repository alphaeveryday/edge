# ADR-0029: apps 플레인 그룹핑 · schema 마이그레이션 세트 대칭 명명

- 상태: 승인됨
- 날짜: 2026-07-20

## 맥락
[[0001-monorepo-structure]]는 `src/apps`(실행 단위)·`src/libs`(공유 코드)만 정하고
그 아래를 flat하게 뒀다. 그런데 시스템은 두 실행 환경으로 갈린다 —
edge 클라우드(벤더 운영)와 증권사 온프렘(고객 통제, [[0010-hybrid-onprem-pivot]]).
앱이 9개인 지금도 어떤 모듈이 어느 환경에 배포되는지 트리에서 안 보이고,
온프렘 쪽은 곧 sync-agent·compliance-engine·widget-ui 3종이 더 붙어 12개를 넘는다.

핵심 사실: **앱은 클라우드 아니면 온프렘 중 정확히 하나에 속한다.
반대로 libs는 대부분 두 환경을 가로지른다** — jvm-common은 gateway(클라우드)와
serving-api(온프렘)가 공유하고, ui-kit도 super-admin-ui·tenant-console-ui가 공유하며,
schema는 클라우드/온프렘 테이블을 한 SSOT에 담는다([[0016-single-repo-two-artifacts]]).

같은 비대칭이 schema 안에도 있다. Flyway 세트가 `migrations/`(암묵적 클라우드) +
`migrations-onprem/`로 비대칭이라, 기본 폴더가 어느 플레인인지 이름에 드러나지 않는다.

## 결정
플레인을 이름으로 드러낸다.
- `src/apps/` 아래에만 플레인 디렉터리를 둔다. `src/libs/`는 flat 유지.
  - `apps/cloud/` — gateway, tenant-sync-api, super-admin-api, super-admin-ui, data-pipeline, analysis-engine
  - `apps/onprem/` — serving-api, tenant-console-api, tenant-console-ui, (예정) sync-agent, compliance-engine, widget-ui
- `src/libs/`는 schema·jvm-common·py-common·ui-kit을 flat로 둔다(플레인 무관 공유).
  schema 내부의 `migrations-cloud/`·`migrations-onprem/` 플레인 분할은 유지 — 모듈은 공유이되
  마이그레이션 세트만 환경별이고, DDL 권한은 단일 SSOT(libs/schema)가 갖는다([[0005-db-as-contract]]).
- **schema Flyway 세트를 대칭 명명한다: `migrations/` → `migrations-cloud/`** (온프렘 세트
  `migrations-onprem/`는 그대로). 두 세트는 서로 다른 DB에 적용된다.
- 디렉터리·세트 이름은 배포 아티팩트([[0016-single-repo-two-artifacts]]의 edge-cloud/edge-onprem)와
  같은 어휘(cloud/onprem)를 쓴다.

widget-ui는 실행 서버가 아니라 **빌드 산출물**로 `apps/onprem/`에 둔다 — 증권사가 받아서
자기 MTS/HTS에 임베드/웹뷰로 띄운다([[0010-hybrid-onprem-pivot]] 재확인, 위젯 서버 부활 아님).

## 대안
- **src 통째 분할(src/cloud·src/onprem·src/shared)** — 공유 libs(schema·jvm-common·ui-kit)가
  갈 곳이 shared뿐이라 앱이 매번 플레인을 가로질러 참조하고 Gradle 경로가 지저분해진다. 플레인
  경계가 깨끗한 건 apps뿐이라 apps에서만 나눈다.
- **libs도 플레인 분할 / schema를 schema-cloud·schema-onprem 두 lib으로** — 공유 라이브러리를
  중복시키거나 가로질러 참조하게 만들고, DDL 권한(SSOT)이 둘로 쪼개진다([[0005-db-as-contract]] 위반).
  세트만 폴더로 나누고 모듈은 하나로 둔다.
- **vendor/tenant 또는 control/data(플레인) 네이밍** — 소유·개념 축은 더 정확하나, 레포가
  이미 cloud/onprem으로 표준화(0010·0011·0016·아티팩트명)돼 있어 새 축 도입은 어휘를 3중으로
  쪼갠다. 일관성 > 미세한 정확성.

## 결과
- 트리·폴더 이름만으로 배포 환경이 드러난다. 온프렘 확장(3종 추가) 시 legibility가 유지된다.
- 앱→libs 참조(`project(':libs:...')`)는 안 바뀐다(libs 이동 없음) — 이게 flat lib의 실무 이득.
- 갱신 대상(전부 기계적): settings.gradle·각 앱 Dockerfile·docker-compose*.yml·pnpm-workspace.yaml·
  pyproject.toml·CI(앱 경로), 그리고 schema build.gradle·Dockerfile·compose flyway 마운트·
  schema-validate.yml·schema-migrate.yml·문서(migrations 경로).
- **[[0005-db-as-contract]]는 불변 규칙상 수정하지 않는다** — 본문의 `migrations/` 언급은
  이 ADR-0029가 갱신 기록으로 대체한다.
- 앱·폴더 이동은 `git mv`로 이력 보존.
- [[0001-monorepo-structure]]를 대체하지 않고 apps/·schema 하위 규약만 보강한다.
