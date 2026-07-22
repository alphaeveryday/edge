---
doc_type: spec
status: Draft
owner: engineering
created: 2026-07-11
updated: 2026-07-11
related:
  - disclosure-types.md
  - quality-observability.md
  - ../../baseline/data-ingestion.md
---
# 공시 파싱 계약: `supply_contract` (원장 파서: `supply-v3`, 2026-07-12 승격)

## Summary

| 항목 | 내용 |
|---|---|
| 목적 | 공시 원문 획득과 `supply-v2` 파싱의 계약 규격 정의 |
| 범위 | `dart_documents.doc_type = supply_contract` 중심 |
| 저장 계약 기본값 | `parser_version: supply-v2`, `raw_path` 유지 |

## 원문 소스 체인 (`backfill_supply._fetch_or_reuse_raw` 우선순위)

| 우선순위 | 소스 | 처리 규칙 | 비고 |
|---|---|---|---|
| 1 | 캐시 `{rcept}.html` | 존재하면 사용 (에러 바디면 스킵) | 공개 뷰어 본문 캐시 |
| 2 | 캐시 `{rcept}.xml` | 존재하면 사용 (에러 바디면 스킵) | document API 캐시 |
| 3 | OpenDART `document.xml` API | zip에서 primary 멤버 추출 후 저장 | `DART_API_KEY` 필요, 에러 바디는 저장 금지(`is_error_body` 게이트) |
| 4 | 공개 뷰어 HTML | `main.do → dcm_no 추출 → viewer.do` | 키 불필요, 재시도 4회 |

| 관측 (2026-07-11) | 수치 |
|---|---:|
| `dart_raw` `.xml` | 37,405 |
| `dart_raw` `.html` | 594 |
| 특이사항 | `.xml` 파일 내용이 HTML(SGML)인 경우 다수 — document API가 HTML성 본문 반환 |

## 파서 계약 (`supply-v2`, `src/alphamale/filings/dart/supply.py`)

| 단계 | 계약 |
|---|---|
| 입력 파싱 | `BeautifulSoup(text, "html.parser")` — HTML/XML 무차별 처리 |
| 테이블 추출 | `<table>` 전체 추출 (`_table_rows`) |
| 테이블 선택 | `_table_score` 최고점 표 1개 선택 |
| 라벨 정규화 | `_canonical_label`: NFKC + 공백 정리 + 번호/기호 제거 |
| 값 조회 | 정규화 라벨로 `_find_value` 매칭 |

| 필드 | 규칙 | 비고 |
|---|---|---|
| `counterparty` | "계약상대방/계약상대" 라벨 | 비공개 마커(비공개·공시유보 등) 감지 시 `None` + `counterparty_withheld=True` |
| `amount_krw` | `parse_krw_amount` | 단위 환산표: 조/천억/억/백만/만/원 |
| `ratio_pct` | "매출액대비(%)" 계열 라벨 | |
| `start`, `end` | `_find_period` | |
| `object` | "체결계약명/판매ㆍ공급계약 내용" 계열 | |
| `confidence` | full = corp_name·counterparty·object·(amount 또는 ratio)·(start 또는 end) 전부 충족 | 아니면 partial. **채움율이지 정답율 아님** |

## 알려진 결함 (2026-07-11 실측)

| 이슈 | 내용 | 대응 |
|---|---|---|
| ① 에러 바디 오염 | 원시 아카이브 표본 19.4%가 OpenDART 에러 바디(status 014)로 저장·파싱됨 | fetch 게이트(`is_error_body`) 적용 + `purge_error_bodies.py` 정화·재수집, 불변식 `raw_error_body_rate` |
| ② confidence 모순 | full인데 핵심 필드 null 196건 | 불변식 `confidence_contradiction`, 대부분 ①의 하류 증상 |
| ③ 정답율 | manual_20 codex 감사 0.80~0.85 (필드별) | ① 정화 후 재감사 예정 |
| ④ 레이아웃 변형 | 구형/정정 레이아웃에서 전량 누락 사례 | 표본 3건 전부 ①로 판명 — 정화 후 재평가 |

## 저장 계약

| 항목 | 규칙 |
|---|---|
| 테이블 | `dart_documents` (concepts.sqlite) |
| 키 | `rcept_no` |
| 주요 컬럼 | `doc_type`, `parsed`(JSON), `parser_version`, `raw_path` |
| 하류 물질화 | `build_event_threads.py` → `event_thread` / `event_thread_link` / `document_assertion` |

## supply-v3: 라벨 사전 캐스케이드 (S1–S3 — 2026-07-12 원장 승격 완료)

### 구성요소

| 단계 | 모듈 | 역할 | 성격 |
|---|---|---|---|
| S1 | `src/alphamale/filings/ir.py` | HTML/XML → 표 IR(라벨·값·좌표 보존) + `canonical_label` | 결정론 IR |
| S2 | `…/dart/resources/label_dictionary_v1.json` | 라벨 동의어 → 정규 필드 매핑 (역인덱스 로드) | 데이터 규칙 |
| S3 | `…/dart/supply_v3.py` | 사전 매칭 추출 + provenance + `parser_version` | 추출 실행 |
| 마이닝 | `scripts/data/mine_labels.py` | 코퍼스 라벨 빈도 통계 + 미등재 Top50 후보 생성 | 사전 확장 입력 |
| 평가 | `scripts/data/eval_supply_v3.py` | v2 섀도 비교 + 골드 대조 + quality_metric 적재 | 품질 게이트 |

### 사전 계약

```json
{"doc_type": "supply_contract", "version": 1,
 "fields": {"counterparty": {"labels": ["계약상대방", "…"], "source": "seed"}, "…": {}}}
```

| 계약 조항 | 내용 |
|---|---|
| 기본 원칙 | **표기 관행은 코드가 아니라 데이터로 축적** — `label_dictionary_v1.json`만 수정 |
| canonical 규칙 | NFKC + NBSP/연속공백 정리 + 앞 번호·기호 제거 + 영문/숫자/한글 외 제거 |
| 확장 절차 | `mine_labels` → `review_item(dictionary_candidate)` 상위 50 → 승인 → `labels` append |
| 역인덱스 | labels를 `canonical_label`로 전처리해 `label_norm -> field` 로딩 |

### v3 추출 규칙

| 규칙 | 구현 |
|---|---|
| 표 선택 | 사전 매칭 수 최대 표, 동률 시 매칭 비율 |
| 라벨-값 매칭 | 행 내 인접 셀 쌍 `(cells[i], cells[i+1])` 전부 후보 (v2 `_row_pairs` 의미론 — 섹션 헤더가 첫 셀인 레이아웃 대응) |
| 정정 병기 | 동일 필드 재등장 시 마지막(정정 후) 값 채택 + `provenance.multiple_values=true` |
| provenance | 모든 필드에 `table_idx`·`row`·`label_raw` 부착 |
| 인코딩 | 원문 소비는 반드시 `fetch.read_document_text`(utf-8 strict → cp949 폴백). `errors="replace"` 금지 — 라벨 전멸 |

### 실측 (2026-07-11)

| 분류 | 항목 | 수치 |
|---|---|---|
| 마이닝 | 코퍼스 8,129 원문 | 유니크 라벨 6,204 / 총 등장 207,053 |
| 마이닝 | **상위 100 라벨 커버리지** | **95.64% — Zipf 가설 실증** (어휘 단위 접근의 근거) |
| 미등재 고빈도 | 계약수주일자 7,767 · 조건부계약금액 4,162 · 최근매출액원 12,303 등 | `dictionary_candidate` 상위 50 자동 적재 |
| 섀도 (n=2,000, seed 7) | counterparty/amount/ratio/start/end/object 일치율 | 99.30 / 98.20 / 99.30 / 99.45 / 99.30 / 99.30 (%) |
| amount 불일치 내역 | v2 자릿수 폭주 1건(v3=∅ 안전) · v2 누락→v3 회수 19건 · 정정 병기 v3=정정 후 값 16건 | 불일치의 상당수가 v3 우세 신호 |
| 골드 | 표본 2~3건(정화·재수집 진행 중) | **미판정** |
| 픽스처 | fiberpro·taeyoung·namkwang | v2-v3 전 필드 패리티 |

### 승격 기록 (2026-07-12)

| 게이트 | 판정 근거 | 결과 |
|---|---|---|
| 골드 정확도 | v3 골드 전 필드 100% (n=14~17, 판정가능 표본) | 통과 |
| 섀도 회귀 | 전 코퍼스(32,669 raw) 표본 2,000: 일치 98.6~99.8%, **v2만 아는 값 0건**, 불일치는 v3 우세(금액 회수·정정 후 값) | 통과 |
| 적용 | `promote_supply_v3.py`: 32,669건 재파싱(v3 비중 100%), 금액 259건 회수, confidence 재계산 | 완료 |
| 사후 지표 | supply_accuracy 전 필드 ≥95% (5필드 100%, object 95.0) · confidence_contradiction 231→0 · 채움율 0.697→0.848 | PASS |

운영 규칙: 원장 재파싱·정확도 재계산은 `promote_supply_v3.py`가 담당(골드 라벨 대비 결정론 재감사 포함).
골드 패널 20은 **고정 패널**(build_quality_db가 승계) — 모집단이 자라도 재추출하지 않는다.

### 실행

```bash
uv run python scripts/data/mine_labels.py                 # 전 코퍼스 어휘 마이닝
uv run python scripts/data/eval_supply_v3.py --sample 2000
uv run python scripts/data/audit_manual20.py --relabel    # 골드 패널 codex 재감사
uv run python scripts/data/promote_supply_v3.py           # 원장 재파싱 + 정확도 재계산
```

## 향후 방향

S4 LLM 폴백(사전 미매칭 꼬리 → 구조화 출력 + 스팬 인용 → 사전 후보 환류)과 XBRL 쌍·[기재정정] 전후 쌍의 distant supervision 골드화가 남은 단계다. doc_type 확장(유상증자·합병 등)은 사전에 필드 그룹 추가 + 매핑 계약 한 줄이 경로다.
