# 엔티티 온톨로지 설계 워크스페이스 (v1 · ALPHA-509)

엔티티 체계(분류·속성·대등관계)의 설계 정본과 검토 산출물. 운영 규칙의 SSOT는 [docs/contracts/entity-relations.md](../../contracts/entity-relations.md)이고, 여기는 그 **근거층**(CQ·셰이프·명세·결정 로그)이다.

## 검토 동선 (권장 순서)

1. **[graph.html](graph.html)** — 전체 조망. 브라우저로 열면 타입 노드 + 관계 엣지. 레이어 필터(참조/이벤트/정적/트리), 노드·엣지 클릭 = 상세(OntoClean·식별 기준·판별식·앵커·NOT). 회색 점선 = 유예.
2. **[ontology.sqlite](ontology.sqlite)** — 전수 데이터. 검토용 뷰 5개:
   | 뷰 | 용도 |
   |---|---|
   | `v_relation_review` | 관계 17종 전체 명세 시트 (레이어순) |
   | `v_type_shape` | 타입별 슬롯 시트 (레인순: 분류→식별→본질→기술) |
   | `v_cq_coverage` | CQ 18문 → 답하는 요소 추적 (미추적 CQ 검출) |
   | `v_untraced_relations` | 근거(EO-CQ ∨ 코호트 케이스) 없는 관계 검출 — **0행이 정상** |
   | `v_open_items` | 유예·유보·design 전건 — 후속 결정 대상 목록 |
3. 문서 근거 확인: [cq-catalog.md](cq-catalog.md) · [entity-shapes.md](entity-shapes.md) · [relation-specs.md](relation-specs.md).

sqlite 열람: `sqlite3 ontology.sqlite "SELECT * FROM v_relation_review"` 또는 아무 SQLite 뷰어. 재생성: `python build_ontology_db.py` (stdlib만 사용) — **sqlite·html은 생성물이므로 직접 편집 금지**, 데이터 수정은 빌더 상수에서.

## 산출물 지도

| 파일 | 내용 | 편집 |
|---|---|---|
| [../news-ontology-cohort-cq.md](../news-ontology-cohort-cq.md) | 기준 CQ 배터리(90케이스) — event-ontology repo에서 2026-07-24 반입, 무수정 | 원본 repo에서만 |
| [cq-catalog.md](cq-catalog.md) | EO-CQ 18문 — 코호트 케이스 역추적 + 소비자 4곳 | 손편집 |
| [entity-shapes.md](entity-shapes.md) | OntoClean 백본 + 타입별 셰이프(등재 게이트 포함) | 손편집 |
| [relation-specs.md](relation-specs.md) | 관계 9필드 명세 + 게이트 결정 로그 D-01~10 | 손편집 |
| [build_ontology_db.py](build_ontology_db.py) | 구조 데이터 정본 + 생성기 | **데이터는 여기서** |
| ontology.sqlite · graph.html | 생성물 (검토용) | 편집 금지 |

## 설계 요약 (v1 상태)

- **타입 14** — active 10 (COMPANY·PERSON·AUTHORITY·BRAND·PRODUCT_FAMILY·PRODUCT·SECTOR·THEME·EQUITY·ETF) + 유예 4 (RULE·LOCATION·HAZARD·INDEX — 실측 0건, 발견⑦⑧).
- **관계 17** — 참조 6 (신규 어휘는 `in_sector` 하나, D-07) + 이벤트 9 (thread 계약 승계, projection 후속) + 정적 2 (복제 금지).
- **기각·유예** — 상태형 4종(D-04)·PARTNERSHIP(D-05, 대칭)·관계 속성화(D-06)·ultimate_parent(D-08, 전이 폐포 파생)·유예 마스터(D-09)·CQ 없는 속성(D-10).
- **핵심 등재 게이트** — PERSON은 관계 동반 등재만(이름 단독 금지), BRAND는 owns_brand 동반, 고아 concept 금지.
- 원본 커버리지 스냅샷과의 접속: 발견②(방향 페어 0건)→이벤트층 projection 선행조건, 발견③(UNKNOWN 63.7%)→threading 개선이 관계보다 선행, 발견⑤(접지 오염)→역할→kind 제약 + AUTHORITY 별칭, 발견⑧(스키마 결측)→이 설계가 채우는 범위.

## 후속 티켓 경계

| 순서 | 작업 | 선행 |
|---|---|---|
| 1 | 참조 그래프 시드 (유니버스 지주·CEO·브랜드·제품·섹터 큐레이션 + KRX 업종 피드) | 이 설계 승인 |
| 2 | 멘션 적재 + kind_hint (표본 태깅으로 셰이프 검증 — 층화 표본, 전수 아님) | 1 |
| 3 | 별칭 해소 4축 + AUTHORITY 별칭 시드 | 1 |
| 4 | 이벤트 관계 projection | threading UNKNOWN 축소(발견③) |
