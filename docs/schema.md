# 데이터 스키마 — 소유권과 변경 절차

DB는 이 시스템에서 서비스 간 **계약**이다([[adr/0005-db-as-contract]]). 이 문서는 그 계약의 운영 SSOT로,
두 가지를 다룬다 — **(1) 누가 어떤 테이블을 쓰고 읽는지(소유권)**, **(2) 컬럼/테이블을 어떻게 바꾸는지(확장-수축 절차)**.
스키마 정의 자체(마이그레이션·생성 모델)는 `libs/schema`에 있다. 결정의 배경은 [ADR-0005](adr/0005-db-as-contract.md), 전체 구조는 [architecture.md](architecture.md).

## 1. 소유권 모델

**원칙**
- 모든 테이블은 **단일 쓰기 소유자(writer)** 를 갖는다. 그 서비스만 INSERT/UPDATE/DELETE 한다.
- 읽기는 여러 서비스가 할 수 있다(reader). 소유자가 아닌 서비스의 쓰기는 금지.
- 소유자는 그 테이블의 컬럼 변경에 책임을 진다 — 절차(§2)를 주도하고, 다른 reader가 깨지지 않도록 보장한다.

**서비스별 기본 역할**

| 서비스 | 쓰기(writer) | 읽기(reader) |
|---|---|---|
| `data-pipeline` | 적재(raw/ingested) 테이블 | — |
| `analysis-engine` | 분석 마트(`analysis_reports` 등) | 적재 테이블 |
| `tenant-console-api` | 테넌트 설정/관리 테이블 | 분석 산출 |
| `widget-api` | **없음(읽기 전용)** | 위젯이 필요로 하는 테넌트 설정·분석 산출의 일부 |

분석 마트(`analysis_reports` 등) 접근 로직은 `libs/jvm-common`에 모아 JVM 서비스가 공유한다.

**테이블 레지스트리** — 실제 테이블이 생길 때마다 여기에 등록한다(현재 시드만 존재).

| 테이블 | 쓰기 소유자 | 읽는 쪽 | 비고 |
|---|---|---|---|
| **분석 콘텐츠 마트**: `analysis_reports`, `daily_pulses`, `pulse_factors`, `pulse_claims`, `claim_steps`, `claim_news_links`, `investment_theses`, `thesis_dialectic_steps`, `thesis_dialectic_news_links`, `thesis_decision_points`, `thesis_scenarios`, `issue_map_events`, `issue_map_edges` | `analysis-engine` | `widget-api`(일부), `tenant-console-api` | AI 분석 결과 마트. `analysis_reports`가 리포트 루트. 접근은 `jvm-common` 경유 |
| `instruments` | `analysis-engine` | `tenant-console-api`, `widget-api` | 분석 대상 마스터 |
| `news` | `analysis-engine`(잠정) | `widget-api`, `tenant-console-api` | 리포트 **출처 표시용** 뉴스 메타데이터(제목·언론사·URL). 분석 입력용 뉴스 테이블과는 별개 |
| **SaaS 전역**: `organizations`, `super_admins`, `roles` | `super-admin-api` | `tenant-console-api` | cross-tenant 프로비저닝 |
| **SaaS 테넌트**: `members`, `invitations`, `compliance_settings`, `applications`, `widgets`, `target_symbols` | `tenant-console-api` (일부 `super-admin-api`) | `widget-api`(`widgets`·`target_symbols`·`compliance_settings` 일부) | 테넌트 범위 |

> 위 소유권은 **잠정**이다 — 해당 앱(`analysis-engine` 외 JVM 앱들)이 아직 구현 전이라, 실제 writer/reader는 앱 구현 PR에서 확정·정정한다.

## 2. 확장-수축(expand-contract) 절차

**왜 필요한가**
머지 ≠ 배포다. PR이 머지돼도 서비스 롤아웃에는 시차가 있어, 한순간 **구버전 코드와 신버전 코드가 같은 DB**를
동시에 바라본다. 그래서 스키마를 한 번에 파괴적으로 바꾸면(컬럼 rename/drop) 롤아웃 도중 한쪽이 깨진다.
변경을 **후방 호환되는 단계**로 쪼개 이 시차를 견딘다.

**3단계**

1. **확장(expand)** — 새 컬럼/테이블을 **추가만** 한다. 기존 구조는 그대로 두어 구버전 코드가 계속 동작한다. (NOT NULL은 기본값과 함께, 또는 일단 nullable로.)
2. **전환(migrate)** — 모든 쓰기/읽기 코드를 새 구조로 옮긴다. 필요하면 백필(backfill)로 기존 데이터를 채운다. **모든 서비스의 배포가 끝날 때까지** 기다린다.
3. **수축(contract)** — 더 이상 아무도 참조하지 않는 옛 컬럼/테이블을 제거한다.

**변경 유형별 적용**

| 변경 | 절차 |
|---|---|
| 컬럼 추가 | 보통 확장 1단계로 충분(nullable/기본값). |
| 컬럼 이름 변경 | 확장(새 컬럼 추가) → 전환(양쪽 쓰기→읽기 이전, 백필) → 수축(옛 컬럼 제거). **직접 rename 금지.** |
| 컬럼 삭제 | 전환(모든 참조 제거) → 수축(컬럼 제거). |
| 타입 변경 | 이름 변경과 동일하게 새 컬럼으로 우회. |

**PR·배포 매핑**
- 각 단계는 **별도 PR**이다(확장 / 전환 / 수축). 한 PR에 몰지 않는다.
- 코드 변경 자체는 PR 단위로 원자적이되, **DB 계약의 단계 전환 사이에는 배포 완료를 기다린다.**
- 배포 순서: **확장 마이그레이션을 코드 배포보다 먼저** 적용한다(새 컬럼이 있어야 새 코드가 동작).

## 3. 변경 체크리스트
스키마를 바꿀 때:
- [ ] `libs/schema/migrations/`에 마이그레이션 추가(Flyway, timestamp 버전 `VyyyyMMddHHmm__`).
- [ ] (생성기 도입 후) `libs/schema/generated/` 모델 재생성(§4).
- [ ] 마이그레이션과 생성 모델을 **같은 PR/커밋**으로 함께 올린다([[adr/0005-db-as-contract]]).
- [ ] 이 변경이 확장-수축 중 **어느 단계인지** PR 설명에 명시한다.
- [ ] 새 테이블이면 §1 레지스트리에 등록.
- [ ] 리뷰: `libs/schema`는 JVM·Python 양쪽 소비자가 영향을 받으므로 **양쪽 리뷰**를 받는다(CODEOWNERS로 강제 예정).

## 4. generated 모델 재생성
> **현황:** generated 모델 **생성기가 아직 없다.** ADR-0005·README의 "스키마 변경 시 generated
> 동반 커밋" 규칙은 **그대로 유효하다.** 다만 그 **전제인 생성기가 아직 없어 현재 생성할 산출물이 없고**
> (`generated/`는 비어 있음), 그 전까지는 `libs/schema/migrations/`의 Flyway SQL이 사실상 계약을 정의한다.
> 생성기는 별도 후속 티켓에서 도입하며, 도입되는 순간 아래 규칙이 그대로 적용된다(규칙 자체를 보류·완화하지 않는다).

생성기 도입 이후의 규칙:
- `generated/`는 **손으로 고치지 않는다.** 항상 `schema`(마이그레이션/정의)로부터 생성한다.
- 재생성은 스키마 변경과 **동일 PR**에 포함한다 — 정의와 모델이 어긋난 채 머지되면 안 된다.
- 여러 런타임(JVM·Python)이 같은 정의에서 모델을 생성해 **동일한 계약**을 공유하도록 보장한다.
