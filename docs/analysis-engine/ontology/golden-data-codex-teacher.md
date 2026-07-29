---
name: golden-data-codex-teacher
description: Use the local Codex CLI as the exact-span LLM teacher for golden and silver financial-event annotation in this project.
triggers:
  - golden data
  - silver label
  - LLM teacher
  - codex teacher
  - annotation generation
  - exact-span extraction
---

# Golden-Data Codex Teacher

## Purpose
`03_DATASET_ANNOTATION.md` §9가 정의한 "LLM teacher = annotation assistant"의 백엔드로 **로컬 Codex CLI**를 쓴다.
자유 요약 금지, 원문 substring span만 출력, ontology/offset 검증을 통과한 결과만 silver/gold 후보로 채택한다.

## Verified runtime (this machine)
- `@openai/codex` 설치됨. `codex doctor`로 health 확인: auth=chatgpt, model=`gpt-5.5`, websocket reachable.
- **반드시 PTY 없이 실행한다.** 이 머신에서 `pty:true` 경로는 WSL로 가고 거기엔 `node`가 없어 `exec: node: not found`로 죽는다. 평범한 bash(MSYS, `codex.cmd`가 해석되는 셸)에서 실행.
- `exec`는 비대화형이라 승인 프롬프트가 없다. `--ask-for-approval`는 `exec` 하위에서 거부됨 → sandbox는 `-s`로만 지정.

## Invocation (schema-enforced)
```bash
codex -s read-only exec \
  --output-schema teacher_output.schema.json \
  -o out.json \
  -c model_reasoning_effort="high" \
  "$(cat teacher_prompt.txt)"
```
- `--output-schema <FILE>`: 모델 최종 응답을 JSON Schema로 강제 → exact-span 계약을 구조적으로 보장.
- `-o <FILE>`: 최종 메시지만 파일로(터미널 TUI 노이즈 제외). 재현성을 위해 prompt도 파일/stdin 고정.
- `-s read-only`: teacher는 파일/셸을 만지면 안 된다. read-only 고정.

## Teacher contract (03 §9)
prompt에 포함: 해당 ontology type/role 설명(02) + 허용 role form/value_kind + 문장 ID가 붙은 기사 전문 + 출력 schema + "원문 substring만 span 허용".
출력(teacher_output.schema):
```json
{"events":[{"event_type_id":"...","trigger_quote":"...",
  "arguments":[{"role_id":"...","quote":"..."}]}]}
```

## Post-processing (채택 전 필수)
1. exact substring align — 모든 quote가 canonical_text에 정확히 존재.
2. offset round-trip — `text == canonical_text[start:end]`.
3. ontology validation — type 존재, predicate/role 허용, required·min·max, value_kind (`src_contracts/validate_examples.py` 규칙 재사용).
4. teacher↔rule(또는 다른 teacher) 합의 없으면 gold 자동 승격 금지 (03 §8).
5. 표본 human audit.

## Guardrails (절대)
- 자유 문장·요약·추론 ticker·감성/영향 저장 금지 (03 §9, IMPLEMENTATION 위반금지).
- substring 아닌 span은 reject. 불확실하면 ABSTAIN.
- 같은 quote가 여러 곳이면 sentence ID로 재확인.

## Gotchas
- `pty:true`(WSL)엔 node 없음 → 일반 bash로 실행.
- codex 시작 시 `~/.agents/skills`의 깨진 SKILL.md(YAML) 경고가 떠도 텍스트 생성에는 무해.
- figma/github MCP auth 경고도 무해.
