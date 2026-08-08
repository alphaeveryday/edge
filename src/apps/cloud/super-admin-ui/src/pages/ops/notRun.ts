/* 판정을 못 한 규칙 — 부재를 말하는 화면 문장이 **반드시 먼저 물어야 하는 것** (ALPHA-738).
 *
 * 규칙이 못 돌면 그 규칙의 사건은 없는 게 아니라 **걸렸는지조차 모르는** 것이다.
 * "…없습니다 · 해소 · 판정한 것이 없다" 를 쓰는 자리는 전부 여기를 통과한다 — 화면마다 문구를
 * 고치면 매번 한 곳이 남는다(그게 이 트랙에서 세 라운드 연속 재발한 발생기다).
 *
 * ⚠️ **JSX 를 쓰지 않는다.** `shared.tsx` 에 있던 동안은 `node --test` 가 import 자체를 못 해,
 * 이 판단들의 변이(종류 뒤바꿈·접두사 매칭·`identity` 만 세기)가 **하나도 안 잡혔다**.
 * 그래서 React 를 모르는 순수 모듈로 내렸다. 인자도 `ConsoleEvaluation` 이 아니라 구조 타입이다 —
 * 화면 타입을 참조하면 다시 JSX 를 끌고 온다.
 */
import { ruleOfVid } from '../../rules/evaluate.ts';
import { RULES } from '../../rules/rules.ts';
import type { RuleResult } from '../../rules/types.ts';

/**
 * 실시간 축 응답의 상태. **`못 돎`(axis) 을 읽는 뜻이 이 값에 달려 있다** — 응답이 아직 없거나
 * 실패한 것을 "계측이 없다"로 그리면, 운영자가 **API 장애를 미배선으로 읽는다**.
 */
export type AxisFetch = 'loaded' | 'pending' | 'error';

/** 판정을 못 한 규칙 전부 — 종류(`notRun`)는 호출자가 필요할 때만 가른다.
 *
 *  `identity`(응답이 사건을 못 가르게 줬다)만 묻지 않는 이유: 부재를 말하는 문장 앞에서는
 *  `axis` 도 같은 사실이다("이 규칙은 아무 답도 안 했다"). 종류가 갈리는 곳은 **개수를 세는
 *  자리**뿐이다 — 계약 위반은 어제까진 없다가 오늘 생기고, 축 부재는 늘 있을 수 있다. */
export function unevaluatedRules(ev: { rules: RuleResult[] }): RuleResult[] {
  return ev.rules.filter((r) => !r.evaluated);
}

/** `vid` 의 규칙이 이 콘솔에 존재하는가. 거짓이면 낡은·손으로 친 식별자다 —
 *  "규칙이 돌았고 안 걸렸다"로 읽으면 안 된다(규칙을 지우거나 개명해도 같은 모양이 된다). */
export function isKnownVid(vid: string): boolean {
  const id = ruleOfVid(vid);
  return id !== '' && RULES.some((R) => R.id === id);
}

/** 이 사건 식별자를 낼 규칙이 이번 평가에서 못 돌았는가.
 *  규칙 id 는 **엔진의 `ruleOfVid`** 로 되찾는다 — 여기서 구분자를 다시 적으면 `vidOf` 가
 *  바뀌는 날 조용히 아무것도 못 찾고, 화면은 그걸 "해소"로 그린다. */
export function unevaluatedFor(
  ev: { rules: RuleResult[] },
  vid: string,
): RuleResult | undefined {
  const id = ruleOfVid(vid);
  return id === '' ? undefined : unevaluatedRules(ev).find((r) => r.id === id);
}

/**
 * 못 돈 사유 한 줄 — 종류는 `notRun` 이 **구조로** 가른다(문구를 파싱해 추측하지 않는다).
 *
 * `axis` 의 사유를 `dep` 로만 쓰지 않는 이유: `dep` 는 "어떤 계측이 없는가"라는 **정적 속성**이고,
 * 화면이 묻는 건 "이번에 왜 안 돌았나"다. R12·R17~R19 는 `dep: null` 이라 — 빠진 계측이 없다는
 * 뜻이다 — 거기에 "사실 축 부재"를 쓰면 **자기 데이터와 모순된 문장**이 선다. 그 규칙들이 못 도는
 * 진짜 이유는 실시간 응답이 아직 없거나 실패한 것이고, 그건 `fetch` 가 답한다.
 *
 * ⚠️ `note` 를 사유로 쓰지 않는다(`identity` 제외) — `evaluate` 에서 `note` 는
 * `collision ?? R.note?.(f) ?? R.dep` 라 리포트 주석이 실려 있을 수 있다.
 */
export function notRunReason(r: RuleResult, fetch: AxisFetch = 'loaded'): string {
  if (r.notRun === 'identity') return `응답 결함 — ${r.note ?? '사유 미기록'}`;
  const dep = RULES.find((R) => R.id === r.id)?.dep;
  if (dep) return `못 돎 — ${dep}`;
  if (fetch === 'pending') return '아직 못 돎 — 이 규칙이 읽을 응답이 도착하지 않았다';
  if (fetch === 'error') return '못 돎 — 이 규칙이 읽을 응답을 못 받았다(조회 실패)';
  return '못 돎 — 이 규칙이 읽을 사실 축이 이번 응답에 없다';
}
