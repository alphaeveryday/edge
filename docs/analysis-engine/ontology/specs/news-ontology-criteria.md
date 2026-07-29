---
doc_type: spec
status: Proposed
owner: engineering
created: 2026-07-22
updated: 2026-07-22
method: skill://ontology-design-criteria
related:
  - event-argument-schema-v1.md
  - news-ontology-rulebook.md
  - news-ontology-query-battery.md
  - news-ontology-acceptance-sets.md
  - news-ontology-remediation.md
  - news-ontology-gold-spec.md
  - news-normalization-v3.md
---
# 뉴스 온톨로지 설계 기준 v1 — `ontology-design-criteria` 적용

> **방법:** skill `ontology-design-criteria`의 Move 1–5. **지표는 고정이 아니라 여기서 이유와 함께 도출된다.**
> **범위:** 뉴스층만 · 엔티티=id만(트리·관계 제외) · **개념 산출물(실측·코드·엔티티DB 구축 없음)** · 해석(호재/악재/impact) 배제.
> **게이트 규칙:** 게이트는 (A)불변식 또는 (B)적대시험만. 자연표본 통계정확도(C)는 진단.

---

## Move 1 — telos + 하부목표

**telos:** 한국 뉴스를 정본 이벤트로 만들어, 에이전트가 **주체·행위타입·시간으로 사건을 조회/조인/스레드**하고 나중에 이벤트스터디를 동적으로 할 수 있는 **1층 연구기반**을 만든다. 시장 의미체계의 첫 층 = 이벤트 + 경제주체. 해석·메커니즘·가설은 상위층.

**하부목표 (각 WHY):**

| Gk | 목표 | WHY (telos가 이걸 요구하는 이유) |
|---|---|---|
| **G1** | 식별=병합 | 같은 사건의 여러 보도가 하나로 합쳐져야 "X가 Y한 사건 전부" 질의가 정확 |
| **G2** | 스레딩·라이프사이클 | 선반영·전개·정정 추적 = 이벤트스터디의 시간축 |
| **G3** | 자매 타입 비중첩 | 행위-타입 질의가 자매타입(M&A/STAKE/PARTNERSHIP)을 안 섞어야 답이 맞음 |
| **G4** | 역할=의미슬롯 | 조인·해석이 정적 타입으로 걸림(kind 분기 없이); 다중필러도 가역 |
| **G5** | 술어=행위정규형·해석배제 | 결론을 미리 굽지 않아야 상위층 가설을 선점 안 함(층 경계) |
| **G6** | 근거·PIT·정직 | 거짓·사후정보 한 건이 이벤트스터디를 오염 |
| **G7** | 진화·버전 | 살아있는 의미체계가 안전하게 성장(에이전트 발견 포함) |
| **G8** | 엔티티 접지(id-only) | "빽다방 뉴스 바로 조회"는 stable id면 충분; 관계·계층은 상위층 |

---

## Move 2–4 — 결정별 기준 (한 번에 하나) · 5-슬롯 · 반-Goodhart

각 결정: **결정 | Gk | 기준(수치|정성) | 이유(=정합성) | 반-Goodhart[class]**. class A=불변식(연역), B=적대시험, C=진단강등.

**D1 타입 택소노미 경계**
- Gk: G3, telos(행위타입 질의). 기준(정성+B): 각 타입은 positive 예시 다수 + **자매 hard-negative 거절 셋**을 보유; "행위타입 질의"가 자매를 섞지 않음. 이유: 조회가 타입 경계로 걸리므로 경계가 흐리면 답 오염. 반-Goodhart: **[B]** hard-negative 거절이 통과조건 / 자연표본 type_agree = **[C]** 진단(오라클이 틀리면 무의미).

**D2 식별·병합 키**
- Gk: G1. 기준(A): `thread_key = type + identity_roles 정규화 정체값`; 불확실하면 **병합 안 함**(UNKNOWN link). 이유: 안정 병합이 "사건 전부" 질의 정확성의 전제. 반-Goodhart: **[A]** 결정론 키(코드) + **precision>recall**(오병합 금지) / 병합 recall(자연) = **[C]**.

**D3 술어 축**
- Gk: G5, G3. 기준(A): `predicate ∈ 타입별 통제 메뉴`; **감성·시장방향 술어 개수 = 0**. 이유: 자유술어는 그룹핑 불가(어휘 폭발), 감성술어는 해석을 온톨로지에 밀반입. 반-Goodhart: **[A]** 폐쇄집합·감성술어=0은 구조검사(연역).

**D4 라이프사이클·단계**
- Gk: G2. 기준(B): `stage_sensitive` 타입은 순서축에서 stage 포착; 포착시 정확도는 **자매-무관 hard 케이스**로 측정. 이유: 단계 순서가 시간축·선반영 판단의 근거. 반-Goodhart: **[B]** 상수 stage 출력은 hard 케이스에서 실패 / 자연표본 capture율 = **[C]**.

**D5 participant/measure 타입 분리**
- Gk: G4. 기준(A): 개체값 역할 ⊆ `participants`, 수량값 역할 ⊆ `measures`; **교차오염 0, union-dispatch 0**. 이유: 조인이 정적 타입으로 걸려 kind 분기 불필요. 반-Goodhart: **[A]** 구조 분리 검사(오염 0 = 연역).

**D6 바인딩·그룹 키**
- Gk: G4, 해석명료(단사성). 기준(A+B): `group_ord`로 라인아이템 바인딩; 다중필러에서 (개체↔값) **recall+precision**. 이유: 다중 제품·기간·단위에서 바인딩 소실=단사성 위반=조회 오답. 반-Goodhart: **[A]** 단사성(충돌 0, 증명) + **[B]** 바인딩 recall+precision(1개만 뱉기 차단).

**D7 엔티티 id 핸들**
- Gk: G8. 기준(A): 의미있는 개체마다 **stable id**; 결정론 조회는 층 안, 관계·계층은 상위층(제외); 미매핑은 리뷰큐. 이유: 지금 필요는 직접 조회뿐; 트리는 나중. 반-Goodhart: **[A]** id 안정성(같은 표면→같은 id, 결정론) + 미매핑을 **날조 아닌 리뷰큐**로.

**D8 스팬 접지·정직**
- Gk: G6. 기준(A): 모든 mention/surface **⊆ 원문(NFKC 부분문자열)**; 미상=UNKNOWN(0/평균 대체 금지); `value_source` 명시; value는 코드 산술. 이유: 거짓·사후정보 차단. 반-Goodhart: **[A]** 부분문자열 검증(연역) + **[B]** 공시無면 UNRESOLVED 강제(날조 0).

**D9 스레딩 키 = entity_id**
- Gk: G2, G1. 기준(A): identity 정체값을 verbatim mention이 아닌 **정규화 entity_id**로. 이유: 표기변이(신·구사명, "2분기/상반기")가 스레드를 가르는 파편화 방지. 반-Goodhart: **[A]** 결정론 키(재실행 동일은 코드 귀결).

**D10 진화 채널**
- Gk: G7. 기준(B): 새 역할/타입은 **positive/hard-negative 게이트 통과**해야 편입; 의미변경=새 ID; deprecate(삭제 금지); `ontology_version` 보존. 이유: 성장이 정확성을 깨지 않도록. 반-Goodhart: **[B]** 적대 수용 게이트가 편입의 유일 경로(에이전트 제안도 동일).

---

## Move 5 — 의도 정합 증명 (측정 아니라 논증)

**연역 tier (증명됨, 측정 불요):** D2·D3·D5·D6(단사)·D7·D8·D9는 불변식 → 통과 = **수학적 보장**. (예: D5 "오염 0"은 구조 검사; D8 "⊆원문"은 부분문자열 정리; D6 단사성은 group_ord로 충돌 0 증명.)

**수용 tier (의도-오라클 = 소비자 실제 질의 배터리):** 통과 = 의도 달성(프록시 아님, 그게 일 자체). 능력 목록 — 각 **held-out 슬라이스** 보유, 각 질의는 (a)원자료로 답가능 (b)한 능력만 (c)골드셋:
1. 주체별 사건 조회 (빽다방 언급 사건 전부)
2. 행위타입별 조회 (X가 계약 체결한 사건 전부)
3. 정책·주체 결합 (정부의 특정 정책 시도 이벤트)
4. 라이프사이클 스레드 (한 사건의 RUMORED→…→CLOSED 타임라인)
5. 다중바인딩 복원 (다중 제품·단위 계약에서 개체↔값)
6. 자매 구분 (M&A vs STAKE vs PARTNERSHIP)
7. 시간창 조회 (기간 내 특정 타입 이벤트)

**추적성 (고아 0):** G1→D2,D9 · G2→D4,D9 · G3→D1,D3 · G4→D5,D6 · G5→D3 · G6→D8 · G7→D10 · G8→D7. 모든 Gk가 ≥1개 (A/B) 게이트로 덮임, 모든 게이트가 어떤 Gk로 추적됨.

**충돌 우선순위 (사전식, 이유):** 평균 금지, 상위 채택+손실 로그(Rule 7).
1. **불가침** — G6 정직·근거, G5 해석중립 (거래 불가; 거짓/해석 밀반입은 연구기반 자체를 무효화)
2. **식별 정확** — G1 (오병합이 누락보다 질의를 더 망침 → merge는 precision 우선)
3. **해석명료·바인딩** — G4 (조인·이벤트스터디 정확성)
4. **커버리지·재현율** — 더 많은 사건/개체 포착 (진화로 성장 가능, 정확성은 소급 불가) → **precision > recall**
5. **효율** — 토큰·비용 (정확성과 거래 금지)

---

## 스코프 가드 / 비범위

- **범위:** 뉴스층 이벤트+주체, 엔티티 id만, 개념 기준.
- **비범위(나중):** 엔티티 트리·소유/거래 관계, 이벤트→시장 메커니즘/가설, 연구 에이전트, 실측·코드·엔티티DB 구축.

## 기존 문서 정렬

`event-argument-schema-v1.md`의 P0–P6는 **여기서 도출된 기준의 특수사례**(D1–D9의 측정 형태)이며 고정 법칙이 아니다. 게이트/진단 구분은 위 반-Goodhart class를 따른다. 그 문서는 이 기준의 **적용결과(스키마 형태)**로 참조한다.

## 근거/출처
- 방법: `skill://ontology-design-criteria`
- 적용결과: `event-argument-schema-v1.md`, 상위 계약 `news-normalization-v3.md`
- 타입/역할 SSOT: `src/alphamale/events/ontology/resources/`
