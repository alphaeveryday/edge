# ADR-0037: 점검 실행 모듈 명칭 — Compliance Engine → Screening Worker

- 상태: 승인됨
- 날짜: 2026-07-21

## 맥락
온프렘의 점검 실행 컴포넌트는 SSOT 문서에서 "Compliance Engine"(코드 예정명 `compliance-engine`)으로 불려 왔다. 그러나 "Compliance"는 우산 용어로 **점검 실행(screening)·검수(review)·정책 기준(policy)** 을 모두 덮어, 세 관심사를 이름으로 구분하지 못한다. 이 모듈이 실제로 하는 일은 금칙어·금지 표현·처리 기준을 적용해 상태를 분기하는 **점검 실행**뿐이다(기준을 *정하는* 것은 Policy, 사람이 *승인/반려* 하는 것은 Review).

온프렘 워커는 아직 walking skeleton 미구현이며, 로컬 E2E 구축에서 점검 워커의 코드 모듈명을 **`screening-worker`** 로 확정하고 `compliance-engine` 명을 배제했다(워커 3종 Spring Boot 체계). SSOT 문서가 여전히 "Compliance Engine"을 쓰면 코드 모듈명과 어긋난다.

## 결정
**점검 실행 모듈의 SSOT 명칭을 `screening-worker`(표기: Screening Worker)로 정렬한다 — 엔진 only.**

- living 문서의 컴포넌트/모듈명 "Compliance Engine"·"compliance-engine" → **Screening Worker·`screening-worker`**.
- **리네임 범위는 점검 실행 모듈 하나**다. 아래는 그대로 둔다 — 서로 다른 관심사다:
  - **Compliance Policy** (콘솔 메뉴) — 점검 *기준* 설정(Policy). 이름 유지.
  - **Compliance Reviewer** (역할) — 검수(Review). 이름 유지.
  - 일반 "컴플라이언스" 개념·"컴플라이언스 검사 결과"(점검 산출 필드) — 도메인 어휘, 유지.
- **과거 ADR은 불변**([README](README.md) 원칙) — "Compliance Engine"을 언급한 승인된 ADR(0010·0016·0017·0019·0029·0033 등)은 점(点)-in-time 기록으로 두고, 현행 명칭은 이 ADR과 living 문서가 기준이다.
- 코드 모듈(`screening-worker`) 구축은 이 ADR 범위 밖(로컬 E2E 슬라이스에서 담당). 이 결정은 문서-코드 명칭 정합만 고정한다.

## 대안
- **Compliance Engine 유지** — 코드 모듈명(`screening-worker`)과 영구 불일치. 배제.
- **"Compliance" 전면 교체(Policy·Reviewer 포함)** — 점검≠검수≠정책인데 셋을 한 번에 Screening으로 바꾸면 관심사 구분이 무너진다. 엔진만 교체가 정확. 배제.
- **"Screening Engine"** — 워커 3종 체계의 코드 모듈명이 `screening-worker`라 "Worker"로 정렬(엔진→워커). 배제.

## 결과
- context.md(다이어그램·네트워크 배치·§4.2·§5), implementation.md, README(오너십), domain/state-machine.md, architecture/system-architecture.md를 Screening Worker로 갱신한다.
- 아키텍처 뷰([system-architecture.md](../architecture/system-architecture.md))의 "점검 엔진명" 전진 축이 이 결정으로 완료된다(뷰의 Screening Worker ↔ 배포 `screening-worker`).
- Jira 에픽 ALPHA-421 "Compliance Engine"·관련 스토리 명칭 정렬은 보드 위생 후속(문서 범위 밖).
