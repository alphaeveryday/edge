---
name: causeflow
description: 종목 하루 변동의 인과 설명을 하네스 안에서 한 번에 만든다 — 정적분해(시장·섹터·고유) → 메인 에이전트가 경쟁가설 튜플(간선마다 검정 의도) → 코드 심사 → 검정 서브에이전트 병렬 → 구조방정식 + 직관 설명. "오늘 왜 움직였어", "인과 설명 만들어줘", "causeflow 돌려줘", 종목·날짜의 하루 귀속 요청 시 사용. 원격 모델 직렬 파이프라인(causeflow CLI 전체 실행)의 하네스 병렬판 — 직렬 1시간+ 를 ~15분으로 줄인다(실측). ETF 하루 요약은 etfday, 층 분해만은 layers 소관.
---

# causeflow — 하루 인과 설명 (하네스 병렬 오케스트레이션)

**역할 분담이 이 스킬의 전부다**: 결정론(층·사실·심사·패널)은 CLI 코드가, 가설과
구조방정식은 **메인 에이전트(너)** 가 컨텍스트를 유지한 채, 간선 검정은 **서브에이전트
병렬**이 맡는다. 원격 모델 왕복은 0회.

설계 SSOT: `docs/analysis-engine/causal-attribution-design.md`. 어휘·심사·패널의
구현은 `src/edge_analysis/statics/{vocab,hypothesize,judge,causeflow}.py` — 스킬과
코드가 충돌하면 코드가 이긴다.

## 전제

```bash
ENGINE=D:/Github/edge-dyntool/src/apps/cloud/analysis-engine
PY=D:/Github/edge/src/.venv/Scripts/python.exe
ENV='PYTHONPATH=src CAUSAL_BACKFILL_DIR="D:/Github/edge-dyntool/.tmp/causal-backfill" EDGE_RDB_DSN="host=127.0.0.1 port=15432 dbname=edge user=edge password=<secretsmanager edge-dev-rds>"'
```

- RDS 터널 필수: `Catalog "rdb" does not exist` 가 나오면 `hub restart rds-tunnel` 후 12초 대기.
- 작업 디렉터리: `.tmp/cf/<ticker>_<day>/` 를 만들어 산출물을 모은다.

## 1. 사실 (결정론 · ~3분 · CLI 1회)

```bash
cd $ENGINE && $ENV $PY -m edge_analysis.statics.causeflow facts <ticker.KS> <instrument_id> <YYYY-MM-DD> > .tmp/cf/<..>/facts.txt
```

산출: 층 분해(시장 β·섹터 β·고유) · 밤사이 미국장 · 수급(원인 아니라 회계 라벨) ·
시간 분해 · 계열 z 전체 · **코스피 중 미국 설명 몫 구간** · 대상 ≤3 · 접지(사건타입·발화 계열).

## 2. 경쟁가설 (메인 에이전트 = 너)

대상마다 **경쟁가설 3개**를 서로 다른 채널로 낸다. JSON envelope 를 대상별로 저장:

```json
{"event_types": [...facts 의 접지 목록, 시장·섹터 대상은 []...],
 "series_families": [...고유: 발화 계열만 · 시장/섹터: 발화 ∪ {"거시","수급","지수잔차"}...],
 "hypotheses": [ {"vulnerabilities": [], "trigger": {"kind": "점|계열", "ident": "..."},
                  "channel": "...", "exposure": {"kind": "속성", "ident": "계열족", "transform": "변환"},
                  "outcome": "수익률", "sign": 1, "reduction_note": "...",
                  "intent": "무엇이 사실이면 성립인가 - 반증 조건까지"} , ×3 ] }
```

규칙: 어휘는 닫혀 있다(`vocab.py`) · **intent 는 반증 조건을 포함**해야 한다(예: "거시가
오늘 발화했어야 성립. z<2 면 기각") · 기각될 가설을 일부러 섞어라 — 기각이 곧 "아닌 것
먼저"의 재료다. 심사:

```bash
$ENV $PY -m edge_analysis.statics.causeflow validate .tmp/cf/<..>/env_<T>.json
```

`[REJ]` 는 고쳐서 재심사. 간선 확정 후 **간선당 env 파일 1개**(env_M1.json …)로 쪼갠다
(panel CLI 가 hypotheses[0] 만 읽는다).

## 3. 검정 (서브에이전트 병렬 · task 1배치)

간선 수만큼 `task` 를 **한 배치**로 띄운다. context 에 공통 계약, tasks[i] 에 간선 지정.
계약 원문(그대로 복사):

- 표본 선택 금지: panel CLI 인자는 튜플 파일뿐.
- 수치는 facts.txt 와 panel 출력만 인용. **판정불가는 희소** — n 작다고 도망가지 말고
  오늘 사실(창·발화·미국장)로 기울여라. 재료가 정말 상충할 때만 + 사유.
- 성립 → `se`(kind 0/1|시계열|수준 · name · value · **meaning**) 필수. 기각 → `cut_reason`
  필수(가설 에이전트에게 보고된다).
- 노출이 측정 불가(n=0)면 **측정 가능한 이웃 노출로 프로브**하고 그 사실을 명시하라
  (특징 선택 — 표본 선택이 아니다).
- panel: `cd $ENGINE && $ENV $PY -m edge_analysis.statics.causeflow panel <ticker.KS> <iid> <day> <env_XX.json>` (1~6분).
- 출력: `{"edge","causal":true|false|null,"confidence","conclusion","se":{},"cut_reason"}` JSON 하나.

DAG 맥락(경쟁 간선 목록·공통요인·통제됨: 시장차감·산업이중차감)을 context 에 싣는다.

## 4. 갱신 (최대 2라운드)

성립이 0개인 대상만: 기각 사유(cut_reason)를 모아 **새 가설구조**를 낸다(끊긴
채널·방아쇠 반복 금지). 2단계→3단계 반복. 전 대상에 성립이 있으면 건너뛴다.

## 5. 구조방정식 + 직관 설명 (메인 에이전트 = 너)

성립 간선의 se 재료로만 방정식을 세운다. 항등식 검산 필수:

```
r(종목) = β_m×코스피 + β_s×섹터⊥ + e   ← facts 의 층 숫자 그대로, 합이 하루와 일치
코스피  = 미국 몫 구간 + 국내잔차       ← 성립 간선의 se
```

설명 규율(narrate 계약의 이식): **아닌 것 먼저**(기각 간선) · 비자명 연결 한 문장
강조(최종 목표: 일반 투자자가 못 떠올리는 인과) · 숫자는 재료에 있는 것만 · 미설명
잔여를 숨기지 않고 "줄이는 것은 서사가 아니라 데이터"로 끝낸다.
산출: `.tmp/cf/<..>/result.md` (DAG 판정 전문 + 방정식 + 설명).

## 실측 기준선 (000660 2026-07-29)

경쟁가설 9 → 성립 4 · 기각 5 · 판정불가 0 · 벽시계 ~15분(사실 3분 + 검정 병렬 최장
10.5분). 원격 직렬 파이프라인은 38분에 간선 4/9. 검정자 특징 선택 실례: 수급/변화
(측정 불가) → 거래량/변화 프로브 n=343 p=0.086.
