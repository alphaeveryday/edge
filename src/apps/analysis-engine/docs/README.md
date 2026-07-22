---
doc_type: index
status: Draft
owner: engineering
created: 2026-07-20
updated: 2026-07-20
---
# analysis-engine 문서

이 디렉터리는 analysis-engine 모듈의 **내부 설계 문서**다. 제품·크로스컴포넌트 설계·운영 규칙은 저장소 루트 [`../../../../docs/`](../../../../docs/)·[`../../../../AGENTS.md`](../../../../AGENTS.md)가 SSOT이며, 아래는 그것을 참조·상세화한다.

이 페이지는 **길잡이**다 — 무엇이 어디 있는지만 가리킨다. 내용은 각 문서가 소유한다.

## 문서 타입 (수명주기별)

| 타입 | 위치 | 담는 것 | 가변성 |
|---|---|---|---|
| **Baseline** | [`baseline/`](baseline/) | 현재 구현 as-built 설계 (정적 구조 + 동작 뷰) | 코드와 함께 갱신 |
| **Proposals** | [`proposals/`](proposals/) | 변경 제안·추상설계 (draft). 1 제안 = 1 문서 | 승인 시 baseline/spec 흡수 + ADR 증류 |
| **Specs** | [`specs/`](specs/) | 정밀 계약 — 산식·타입·필드·단계 I/O·스코어 계약 | 계약 변경 시 |
| **Decisions** | [`decisions/`](decisions/) | 모듈 로컬 ADR (`ADR-NNN`) | append-only |
| **Reference** | [`reference/`](reference/) | 참조 데이터·빌드 계약 (ERD, 엔티티 마스터) | 데이터 변경 시 |
| **Operations** | [`operations/`](operations/) | 운영 규약 (lake, DB ground rules) | 운영 변경 시 |
| **Diagrams** | [`diagrams/`](diagrams/) | 렌더 산출물 (svg/png/drawio). 소스 = 각 문서 내 mermaid | — |

## 시작점

1. [baseline/analysis-engine-design.md](baseline/analysis-engine-design.md) — 시스템 컨텍스트·컨테이너 + 분석·설명 엔진(컨2·3) 내부 (C4 L1/L2/L3 + 동작 뷰)
2. [baseline/data-ingestion.md](baseline/data-ingestion.md) — 수집 컨테이너 as-built

## 규약

- **정적 뷰와 동작 뷰는 폴더가 아니라 문서 안의 섹션**이다 (arc42 §5/§6). 시스템 스코프 동작 → `baseline/analysis-engine-design.md`, 모듈 스코프 동작 → 각 `baseline/<module>.md`, 컴포넌트 스코프 시퀀스 → 각 `specs`.
- **하나의 canonical**: 한 주제는 한 문서가 소유하고, 나머지는 링크로 가리킨다.
- 제안 문서는 Google 디자인 독스 형식(Context / Goals / Non-goals / Design / Alternatives)을 따른다.
- **기호 병기**: 모듈·단계 기호(R·L\*·A–G·O\*·P\*)는 문서당 첫 등장에서 `L1(항등식 분해)` 꼴로 이름을 병기한다. 이후 등장은 기호 단독 허용. 다이어그램 노드·컴포넌트 표 셀은 항상 기호+이름을 함께 쓴다. 해독의 canonical은 아래 [용어 맵](#용어-맵-기호-해독).

## 용어 맵 (기호 해독)

기호는 문서·spec·데이터 계약을 잇는 식별자다 — 풀어쓰지 않고 여기서 해독한다. 결번·폐기 구멍은 설계 역사이므로 재번호하지 않는다.

| 기호 | 이름 | 한 줄 | 상태 | 소유 |
|---|---|---|---|---|
| 컨1–컨4 | Data Ingestion / Analysis Engine / Explanation Engine / Explanation API | 컨테이너 번호 (C4 L2) | 현역 | [baseline §3](baseline/analysis-engine-design.md) |
| R | 타입 라우터 | ETF 유형 → 분해 템플릿 선택. **경로 라우터(route·scope 판정)와 별개 모듈** | 현역 (제안) | [항등식 spec §R](specs/etf-identity-decomposition.md) |
| L0 | 이상 게이트 | 오늘 설명이 필요한 움직임인지 진입 판정 (`l0_entry`) | 현역 (제안) | [항등식 spec](specs/etf-identity-decomposition.md) |
| L1 | 항등식 분해 | 가격 변동 → 구성종목 기여·괴리·환율 **관측** | 현역 | [항등식 spec](specs/etf-identity-decomposition.md) |
| L2 | 공통요인 분해 | 구성종목 변동 → 시장·테마⊥·고유 leg **추정** | 현역 | [가격 분해 spec](specs/price-decomposition-engine.md) |
| L3 | (구) 이벤트 타깃 선정 레이어 | `scope_targets[]`로 대체 (구 `l3_targets`) | 결번 (2026-07-20) | [baseline §7](baseline/analysis-engine-design.md) |
| L4 | 설명 서술 규율 | 관측/추정/가설 분리·checkpoint·honest unknown | 현역 | [baseline §6·§9](baseline/analysis-engine-design.md) |
| 경로 라우터 | — | 분해 결과의 성격 판정 → `explanation_route`+scope 확정에서 컨2 종결 | 현역 (제안) | [baseline §5](baseline/analysis-engine-design.md) |
| A · B · E · G | 컨3 설명 단계 | novelty 기준선 / 이벤트 수집 / 중요도 / 합성 | 현역 | [baseline §6](baseline/analysis-engine-design.md) (정밀 spec 미존재) |
| C · D | 기대 대비 차이 / 영향 경로 | 소스 부재로 **강등** — 결번 유지 | 강등 | [0003](proposals/0003-market-expectation.md) · [0002](proposals/0002-relationship-graph.md) |
| F | 이벤트-가격 정합성 검사 | 방향 불검증 원칙으로 **제거** — 결번 유지 | 제거 (2026-07-20) | [baseline §헤더·§6](baseline/analysis-engine-design.md) |
| O1–O6 | 사건 색인 단계 | O1 게이트 → O2–O4 canonical event 조립 → O5 근거 → O6 스레드 | 현역 — **컨2→컨1 이관** (2026-07-20) | [data-ingestion §4절](baseline/data-ingestion.md) · [스레드 spec](specs/data/thread-types.md) |
| P0 | 가격 입력 계약 | `price_intraday`/`price_daily` 재사용 경계 | 현역 | [가격 분해 spec](specs/price-decomposition-engine.md) |
| P5–P7 | event-price 정합검증 | 대상 사건 추림·가격 반응·선후 검증 | **폐기** (2026-07-20) | [baseline §10 폐기 그룹](baseline/analysis-engine-design.md) |
| AE-R\* / EE-R\* / EI-R\* | 요구 동작 규칙 ID | 컨2 / 컨3 / 사건 색인 — §12 검증이 참조 (구 SYS-R\*은 §4 서술·§9 원칙으로 흡수) | 현역 | [baseline §5–§6·§12](baseline/analysis-engine-design.md) · [data-ingestion §4절](baseline/data-ingestion.md) |
| route 값 | `normal_range` 등 6종 | 실행 스위치 enum — 정의는 한 곳만 | 현역 (이름 제안) | [baseline §7](baseline/analysis-engine-design.md) |
| C4 L1–L3 | 다이어그램 줌 레벨 | 컨텍스트/컨테이너/컴포넌트 뷰 — 파이프라인 레이어 L0–L4와 **무관**, 항상 `C4` 접두와 함께만 쓴다 | 현역 | [baseline §2–§5](baseline/analysis-engine-design.md) |
