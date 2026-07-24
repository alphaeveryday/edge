---
doc_type: index
status: Draft
owner: engineering
created: 2026-07-20
updated: 2026-07-22
---
# analysis-engine 문서

이 디렉터리는 **ETF 가격변동 설명 시스템**의 설계 문서다. 제품·저장소 전역 규칙은 루트 [`../../../../docs/`](../../../../docs/)·[`../../../../AGENTS.md`](../../../../AGENTS.md)가 정본이고, 아래는 그것을 참조·상세화한다.

이 페이지는 **길잡이**다 — 무엇이 어디 있는지만 가리킨다. 내용은 각 문서가 소유한다.

## 여기서 시작

1. [baseline/analysis-engine-design.md](baseline/analysis-engine-design.md) — **전체 아키텍처(C4 L1/L2/L3) + 하루가 설명되는 런타임 흐름.** 시스템을 처음 본다면 이 문서 하나면 된다.
2. [baseline/data-ingestion.md](baseline/data-ingestion.md) — 수집 컨테이너 내부 상세.
3. [proposals/0004-dynamic-analyzer-extension.md](proposals/0004-dynamic-analyzer-extension.md) — 동적 분석기를 연구 루프로 키우는 확장안(드래프트).

## 문서 타입

| 타입 | 위치 | 담는 것 | 언제 바뀌나 |
|---|---|---|---|
| **Baseline** | [`baseline/`](baseline/) | 현재 만들어져 돌아가는 시스템의 as-built 설계 (정적 + 런타임 뷰) | 코드와 함께 |
| **Proposals** | [`proposals/`](proposals/) | 변경·확장 제안(드래프트). 1 제안 = 1 문서 | 승인 시 baseline/spec 흡수 + ADR 증류 |
| **Specs** | [`specs/`](specs/) | 정밀 계약 — 이벤트/스레드/공시/엔티티 타입, 설명 정당화 기준 | 계약 변경 시 |
| **Decisions** | [`decisions/`](decisions/) | 모듈 로컬 ADR (`ADR-NNN`) | append-only |
| **Reference** | [`reference/`](reference/) | 참조 데이터·빌드 계약 (논리 ERD) | 데이터 변경 시 |
| **Diagrams** | [`diagrams/`](diagrams/) | 렌더 산출물 (drawio/png). 다이어그램 **소스는 각 문서 안의 mermaid** | — |

## 핵심 용어

베이스라인에서 쓰는 이름을 여기서 한 줄로 해독한다. 어려운 약어는 쓰지 않는다.

| 용어 | 뜻 | 소유 |
|---|---|---|
| 수집 (Data Ingestion) | 외부 원천 → 정규화된 원장 + 사건 색인 | [baseline §6](baseline/analysis-engine-design.md) · [data-ingestion](baseline/data-ingestion.md) |
| 정적 분석기 (Static Analysis Engine) | 가격 항등식 분해 → 시장·업종 설명 판정 → 경로·대상 확정 | [baseline §4](baseline/analysis-engine-design.md) |
| 동적 분석기 (Dynamic Analysis Engine) | 남은 움직임을 사건으로 설명하는 에이전트 (현재 V0) | [baseline §5](baseline/analysis-engine-design.md) · 확장 [0004](proposals/0004-dynamic-analyzer-extension.md) |
| 설명 API (Explanation API) | 완성된 설명을 MTS에 게시(읽기 전용) | [baseline §8](baseline/analysis-engine-design.md) |
| 이상 게이트 | 오늘 설명할 만큼 움직였는지 진입 판정 | [baseline §4.1](baseline/analysis-engine-design.md) |
| 항등식 분해 | ETF 수익률을 구성종목 기여로 쪼갬(관측) | [baseline §4.1](baseline/analysis-engine-design.md) |
| 공통요인 분해 | 시장·업종(피어) 요인을 걷어내 고유 움직임 추정 | [baseline §4.2](baseline/analysis-engine-design.md) |
| 경로 / 분석 대상 (route / scope) | 여기서 끝낼지, 열리면 어느 종목을 볼지 정하는 스위치 | [baseline §4.3](baseline/analysis-engine-design.md) |
| 사건 스레드 / 신규성 | 같은 사건을 계보로 잇고 첫 보도·후속·재보도를 판정 | [스레드 타입](specs/data/thread-types.md) |

## 규약

- **정적 뷰와 런타임 뷰는 폴더가 아니라 문서 안의 이웃 섹션**이다. 시스템 스코프 흐름 → `baseline/analysis-engine-design.md`, 컨테이너 내부 → 각 baseline/spec.
- **하나의 canonical** — 한 주제는 한 문서가 소유하고, 나머지는 링크로 가리킨다.
- **다이어그램은 mermaid** — C4 다이어그램은 문서 안 mermaid가 소스다(색·모양 규약은 [`c4-mermaid-diagrams` 스킬](../../../../.claude/skills/c4-mermaid-diagrams/SKILL.md)). `diagrams/`의 drawio/png는 렌더 산출물.
- **제안 문서 형식** — Context / Goals / Non-goals / Design / Alternatives / Rollout / References.
- **쉬운 말** — 어려운 약어·불명료한 표현을 쓰지 않는다. 단계 이름은 처음 나올 때 한글 이름과 짧은 설명을 병기한다.
