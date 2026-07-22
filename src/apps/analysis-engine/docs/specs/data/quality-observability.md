---
doc_type: spec
status: Draft
owner: engineering
created: 2026-07-11
updated: 2026-07-12
related:
  - disclosure-parsing.md
  - ../../../EDGE_ETF_통합논리ERD_v2.0.md
---
# 품질 관측 카탈로그 (발견 루프)

## Summary

온톨로지·파서·스레딩의 허점을 우연이 아니라 구조로 발견하기 위한 4채널 루프.
모든 채널의 산출은 `quality.sqlite`의 두 테이블(`quality_metric` 시계열, `review_item` 큐)로 수렴하고,
Datasette 콘솔에서 FAIL 우선으로 노출된다. ERD v2.0 H도메인의 물리 구현.

## 발견 4채널

| 채널 | 무엇을 잡나 | 실행 |
|---|---|---|
| 1 불변식 검사 | 문서 계약(§7·canonical-event·PIT)의 기계 검증 위반 | `uv run python scripts/data/check_invariants.py` |
| 2 표본 감사 | 파서 출력 vs 원문의 codex 골드 대조(시드 고정 20건) | `uv run python scripts/data/audit_manual20.py` (`--relabel` 재감사) |
| 3 에이전트 이상 보고 | 설명 에이전트가 판정 중 발견한 패킷 이상(`data_anomaly`) | `llm_explain.py` 실행 시 자동 적재 |
| 4 회귀 골드 | 고친 버그의 영구 케이스화 — 재발 시 채널 1이 FAIL | 예: 스레드 갭 규칙(`thread_gap_rule_violations`) |

## quality_metric 카탈로그

| 단계 | 지표_코드 | 의미 | 임계값 | 산출처 |
|---|---|---|---:|---|
| PARSE | supply_confidence_full_ratio | 필수 필드 전부 채운 문서 비율(채움율) | 0.65 | build_quality_db.py |
| PARSE | supply_docs_total | 공급계약 원장 총량 | — | build_quality_db.py |
| PARSE | counterparty_withheld_rate | 상대방 비공개 비율 | — | build_quality_db.py |
| PARSE | supply_accuracy_{counterparty,amount_krw,ratio_pct,start,end,object} | 골드 라벨 대비 원장 정답율 ok/(ok+wrong) | 0.95 | audit_manual20.py / promote_supply_v3.py |
| PARSE | supply_year_continuity_gaps | 이웃 연도 대비 60% 미만 연도 수 | 0 | check_invariants.py |
| PARSE | confidence_contradiction | full 계약(상대+대상+금액\|비율+기간 한쪽) 위반 문서 수 | 0 | check_invariants.py / promote_supply_v3.py |
| PARSE | raw_error_body_rate | 원시 아카이브 에러 바디 비율(표본 2000) | 0 | check_invariants.py |
| PARSE | v3_gold_accuracy_* | manual_20 골드 기준 v3 정답율 | 0.95 | eval_supply_v3.py |
| PARSE | v3_agree_* / v3_eval_n | v2 대비 섀도 일치율 / 표본 | — | eval_supply_v3.py |
| PARSE | v3_promoted_ratio | 원장 중 v3 재파싱 비율 | — | promote_supply_v3.py |
| PARSE | purge_refetch_backlog | 정화 후 미재수집 잔량 | 0 | check_invariants.py |
| PARSE | label_vocab_size / label_top100_coverage / label_unmatched_top50_freq | 라벨 어휘 통계(Zipf) | — | mine_labels.py |
| ENTITY_RESOLVE | news_issuer_resolve_rate | 기업 이벤트 ISSUER 해소율(직접+제목폴백, news_build_stats 라이브) — 커버리지 지표. 상한 구조적(비상장·외국·다회사) | — | build_quality_db.py |
| ENTITY_RESOLVE | news_issuer_fallback_precision | 제목 폴백 해소 정밀도(표본 30 codex 감사) | 0.95 | build_quality_db.py |
| THREAD | news_thread_p99_size | 뉴스 스레드 크기 p99 | — | build_quality_db.py |
| THREAD | news_mega_thread_count | 패밀리 분할 후 잔존 n≥50 스레드 수(회귀 트립와이어) | ≤5 | build_quality_db.py |
| THREAD | news_duplicate_rate | 재탕 비율 | — | build_quality_db.py |
| THREAD | thread_gap_rule_violations | 스레드 내 180일 초과 갭 수(회귀 골드) | 0 | check_invariants.py |
| THREAD | first_in_thread_contradiction | FIRST인데 prior>0 수 | 0 | check_invariants.py |
| THREAD | thread_heterogeneity_suspects | 제목 다양도>0.8인 20건+ 스레드 수 — 분할 후엔 표현 다양성일 뿐(관찰용) | — | check_invariants.py / build_quality_db.py |
| EVENT | available_at_future | PIT 위반(미래 시각) 수 | 0 | check_invariants.py |
| GATE | review_backlog | 게이트 저마진 미검토 잔량 | — | build_quality_db.py |
| GATE | gold_labeled_count | manual_20 라벨 완료 수 | 20 | build_quality_db.py / audit_manual20.py |
| EXPLAIN | llm_judgment_ok_rate | 직전 데모 배치 판정 성공률 (현재 빌더에 하드코딩 — llm_explain 결과 연동 필요) | 1.0 | build_quality_db.py |

임계값 `—` = 연구 미정(판정 없이 추적만). PASS/FAIL 방향은 지표별(대부분 ≥, mega/불변식 계열은 ≤).

## review_item 사유 카탈로그

| 사유 | 설명 | 할일 | 생산자 | 규모 |
|---|---|---|---|---:|
| gate_low_margin | 게이트 모델 저마진 판정 문서 | top1 분류 정오 판정 | build_quality_db.py | 69,200 |
| gold_candidate | 온톨로지 골드셋 후보 | doc_class·event_types 승인/수정 | build_quality_db.py | 1,705 |
| parser_gold_audit | 파서 정답율 감사 표본 | codex 자동 감사(원문 대조) | build_quality_db.py + audit_manual20.py | 20 |
| mega_thread_overmerge | n≥50 잔존 스레드(분할 불가 판정) | 제목 훑고 분할 규칙 재조정 | build_quality_db.py | 3 |
| invariant_violation | 불변식 위반 표본(체크당 ≤20) | 해당 파이프라인 단계 조사 | check_invariants.py | 실행 의존 |
| agent_reported_anomaly | 에이전트 data_anomaly 보고 | 보고 내용 재현·회부 | llm_explain.py | 실행 의존 |
| dictionary_candidate | 라벨 사전 미등재 고빈도 후보 상위 50 | 필드 배정 또는 무시 | mine_labels.py | 50 |

## 스키마 (quality.sqlite — 실제 DDL)

- `review_item(검토항목_ID PK, 대상_유형, 대상_ID, 사유, 설명, 할일, 링크, 상태, 정답_라벨, 검토자, 판정시각, 제목, 컨텍스트, 원천, 보조점수)`
  - 상태: queued → labeled → applied
- `quality_metric(실행시각+단계_코드+지표_코드 PK, 값, 임계값, 통과_여부, 의미, FAIL_조치)` — 빌드마다 스냅샷 append(시계열)

**소유권·원자성 규칙** (`build_quality_db.py`):
- 소유 사유 4종(gate_low_margin·gold_candidate·parser_gold_audit·mega_thread_overmerge)만 재생성. 외부 소유 행(invariant_violation·agent_reported_anomaly·dictionary_candidate)은 통째 보존, labeled 라벨(정답_라벨·검토자·판정시각·컨텍스트)은 복원.
- **지표 시계열은 영구 append** — `quality_metric`은 절대 DROP하지 않는다. `review_item` 큐 재생성은 트랜잭션 내 DELETE로 원자화(부분 커밋 사고 방지, 2026-07-12).
- **골드 패널은 고정 패널** — `parser_gold_audit` 20건은 모집단(원장)이 자라도 재추출하지 않고 승계한다. 라벨은 감사 당시 원장값 대비 판정이므로 컨텍스트도 함께 고정(자기확증 방지).

## 콘솔 운영

```
uv run python scripts/data/build_quality_db.py      # 큐·지표 재적재 (라벨 보존)
uv run python scripts/data/check_invariants.py      # 불변식 (FAIL 시 exit 1)
uv run python scripts/data/audit_manual20.py        # codex 표본 감사
uv run python scripts/data/mine_labels.py           # 라벨 어휘 마이닝 + 사전 후보
uv run python scripts/data/eval_supply_v3.py --sample 2000   # v3 섀도·골드 평가
uv run python scripts/data/promote_supply_v3.py --dry-run    # v3 원장 승격 (게이트 통과 시 --dry-run 제거)
uv run python scripts/data/sample_fallback_issuers.py        # 이슈어 폴백 정밀도 감사 표본
uvx datasette serve data/processed/quality/quality.sqlite --metadata scripts/data/quality_metadata.json --port 8965
```

장기 백필(재수집 등)은 하네스 비동기 잡이 아니라 분리 프로세스로:
`powershell -NoProfile -File scripts/data/_launch_refetch.ps1` (로그 `data/manifests/graph/_refetch_full.log`,
완료 판정은 `purge_refetch_backlog` 지표).

바로가기 쿼리: `지표_최신`(FAIL 우선) · `지표_추이` · `검토_잔량` · `파서_골드_감사_20` · `파서_오류_상세` · `메가스레드` · `저마진_최악_100`
