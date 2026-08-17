# edge

증권사 MTS 안에서 ETF 가격 변동의 이유를 공개 정보 기반 변동 요인 후보로 설명하는, 증권사가 통제 가능한 운영 인프라(B2B)입니다.

<!-- 이 README 는 쇼케이스(이력서·포트폴리오) 용도다. 저장소 구조·개발 규약은 하단 "개발 문서" 링크 참조. -->

## 1. 프로젝트 개요

### 1-1. 프로젝트 소개

<!-- TODO: 한 문단 피치 — 무엇을 푸는 서비스인지, BYOC 납품 형태(벤더 control plane + 증권사 클라우드 계정 data plane, ADR-0051) -->

### 1-2. 시스템 구성도

<!-- TODO: BYOC 토폴로지 다이어그램 1장 + 3~4줄 요약 (아키텍처 포털 재작도 후 삽입) -->

### 1-3. 주요 기능 · 데모

<!-- TODO: MTS 데모 스크린샷 + QR/링크, 테넌트 콘솔·운영자 콘솔 스크린샷 -->

### 1-4. 기술 스택

<!-- TODO: 폴리글랏 모노레포(JVM Spring Boot 4 · Node · Python) + AWS + Terraform -->

## 2. 개발 결과물

### 2-1. Information Architecture

<!-- TODO: 위젯·테넌트 콘솔·슈퍼 어드민 콘솔 정보구조 — docs/architecture/information-architecture.md 기반 -->

### 2-2. System Architecture

<!-- TODO: 논리 컴포넌트(Service/Worker/Cache/DB) 매핑 — BYOC 기준 재작도 후 채움 -->

### 2-3. Cloud Architecture

<!-- TODO: 두 계정의 AWS 배치 — VPC 서브넷 분리·PrivateLink·클러스터 분리, BYOC 기준 재작도 후 채움 -->

### 2-4. DB 스키마 SSOT

[![Cloud 데이터 모델 개요](docs/data-model/cloud-overview.svg)](docs/data-model/README.md)

| 도메인 | 책임 | 상세 ERD |
|---|---|---|
| 기준정보·금융상품 | 공유 동일성, 분류, 상품 프로필 | [열기](docs/data-model/domains/reference/) |
| 문서·공시 사실 | 뉴스 수집 상태, 원문, 명제, 구조화된 공시 사실 | [열기](docs/data-model/domains/documents/) |
| 시장 데이터·수집 | 수집 상태, 가격·수급·NAV·구성종목 관측, 변동 분해 | [열기](docs/data-model/domains/market/) |
| 이벤트·근거 | 사건, 참여자 역할, 증거, 사건 스레드 | [열기](docs/data-model/domains/events/) |
| 설명 생성·계보 | 설명 경로, 실행 입력, 결과와 증거 계보 | [열기](docs/data-model/domains/explanation/) |
| 테넌트 전송 | 릴리스, 전송 원장, 동기화 커서 | [열기](docs/data-model/domains/delivery/) |
| 운영 원장 | 작업 기대값·시도, 조정, 관리자 활동 | [열기](docs/data-model/domains/operations/) |

루트에서는 도메인 사이의 흐름만 보여줍니다. 각 도메인 디렉터리에 들어가면 해당 도메인의
상세 물리 FK ERD를 확인할 수 있습니다.

### 2-5. 데이터 파이프라인 · 분석 엔진

<!-- TODO: SFN 페이즈 구조, 온톨로지 SSOT, 인과 설명 생성 -->

### 2-6. 성능 실험

<!-- TODO: publication-api 캐시 실험 (p99 실측) -->

### 2-7. CI/CD

<!-- TODO: 러너 왕복 이력(hosted↔self-hosted, 현재 GitHub-hosted), 자작 게이트(PR 타이틀·스키마 단조성·ERD 대조), SSM 배포 -->

### 2-8. 설계 결정 기록(ADR)

<!-- TODO: 50편+ 코퍼스 소개, 대표작 3~4편 하이라이트 -->

## 3. 수행 방법 · 프로젝트 관리

### 3-1. 개발 프로세스

<!-- TODO: Jira 이슈 우선·스프린트, 티켓→브랜치→PR→머지 사이클 -->

### 3-2. 형상 관리

<!-- TODO: 경계별 머지 전략(Squash/Merge commit)·마이그레이션 머지 게이트 요약 — 상세는 docs/git-conventions.md -->

### 3-3. AI 에이전트 하네스

<!-- TODO: pr-cycle·edge-review·docs-sync 스킬, Codex 리뷰 수렴 루프 -->

---

## 개발 문서

- [저장소 구조](docs/repo-structure.md) — 모노레포 구조·워크스페이스·모듈 역할·데이터 흐름
- [Git 컨벤션](docs/git-conventions.md) — 브랜치 전략·커밋/PR 제목·머지 정책 (SSOT)
- [docs/](docs/) — 설계 문서(context·ADR·계약·아키텍처 뷰)
