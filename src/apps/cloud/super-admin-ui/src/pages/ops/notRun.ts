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
 *
 * `stale` = 데이터는 있는데 마지막 조회가 실패했다(react-query 는 에러에도 직전 값을 남긴다).
 * 판정은 서지만 그 판정이 **낡았다** — "실림"과 같은 칸에 그리면 낡은 사실이 현재로 읽힌다.
 * 값을 만드는 곳은 `consoleFacts.axisOf` 다(그쪽이 테스트가 붙는 자리).
 */
export type AxisFetch = 'loaded' | 'stale' | 'pending' | 'error';

/**
 * 조회 상태의 **어휘 한 벌**. 화면마다 따로 쓰면 갈리고, 갈리면 한쪽만 고쳐진다.
 *
 * ⚠️ 넷을 셋 이하로 접지 마라. 특히 `stale` 을 `loaded` 에 합치면 **판정은 섰는데 그 근거가
 * 직전 응답**이라는 사실이 사라지고, `error` 에 합치면 서 있는 판정을 없는 것처럼 그린다.
 */
export const FETCH_LABEL: Record<AxisFetch, string> = {
  loaded: '실림',
  stale: '낡음 — 마지막 조회 실패',
  pending: '응답 대기',
  error: '조회 실패',
};

/**
 * 그 상태에서 **무엇을 주장할 수 없는가**. 부재 문구 옆의 `title` 이 이걸 쓴다.
 *
 * ⚠️ **축 이름을 박지 않는다.** 이 어휘를 쓰는 자리가 셋인데 축이 서로 다르다 —
 * 배치 사실(`/console/facts`) · 실시간 세션(`/sources/minute`) · 테넌트(`/tenants`).
 * 문장에 "사실 축"이라고 쓰면 테넌트 조회가 실패한 순간 운영자가 **반대 API 를 의심한다**.
 * 축은 호출자가 붙인다(`fetchTip`).
 */
export const FETCH_TIP: Record<AxisFetch, string> = {
  loaded: '이번 조회가 성공했다 — 이 값은 지금 사실이다.',
  stale: '직전 응답은 있으나 마지막 조회가 실패했다 — 지금도 그런지는 알 수 없다.',
  pending: '응답이 아직 도착하지 않았다 — 없다는 뜻이 아니다.',
  error: '못 읽었다 — 없다는 뜻이 아니다.',
};

/** 축 이름을 앞에 붙인 사유 한 줄 — 어느 조회가 실패했는지 화면이 말해야 한다. */
export const fetchTip = (axis: string, fetch: AxisFetch): string =>
  `${axis} — ${FETCH_TIP[fetch]}`;

/**
 * **이번에 읽힌 축만 현재형으로 말할 수 있다.** 그건 `loaded` 하나뿐이다.
 *
 * ⚠️ `!== 'stale'` 로 쓰면 안 된다 — `pending`·`error` 는 값이 아예 없어 호출부가 `?? 0` 으로
 * 접는데, 그 0 이 "오늘 세션 없음"·"지금 도는 배치 없음" 같은 **현재형 단정**으로 그려진다.
 * 낡은 값보다 없는 값이 더 조용히 거짓이 되는 자리다.
 */
export const isCurrent = (f: AxisFetch): boolean => f === 'loaded';

/**
 * **신선함은 축마다 따로다.** 한 화면의 결론이 여러 조회 위에 서 있으면(사건 목록·현재 상태
 * 줄은 사실·실시간·개요를 합친다) 그중 **하나라도** 이번에 안 읽혔으면 현재형으로 못 말한다.
 *
 * 🔴 불리언 하나로 접으면 두 방향으로 틀린다: 한 축만 보면 다른 축의 사건이 현재형으로 서고,
 * 뭉뚱그리면 **어느 조회가 왜** 안 됐는지를 못 말해 운영자가 엉뚱한 API 를 본다. 그래서 이름을
 * 달아 넘기고 **읽히지 않은 축의 이름 + 상태**를 돌려준다 — 비면 전부 이번에 읽힌 것이다.
 */
export function unreadAxes(axes: Record<string, AxisFetch>): { name: string; fetch: AxisFetch }[] {
  return Object.entries(axes)
    .filter(([, f]) => !isCurrent(f))
    .map(([name, fetch]) => ({ name, fetch }));
}

/** 읽히지 않은 축이 있으면 그 사실을 한 문장으로 — 없으면 `null`(현재형으로 말해도 된다). */
export function unreadNote(axes: Record<string, AxisFetch>): string | null {
  const unread = unreadAxes(axes);
  if (unread.length === 0) return null;
  const parts = unread.map(({ name, fetch }) => `${name} ${FETCH_LABEL[fetch]}`);
  return `${parts.join(' · ')} — 아래 값은 이번 조회의 결과가 아니고, 지금 상태는 알 수 없습니다`;
}

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
 * `fetch` 에 기본값을 두지 않는다: 하필 그 기본값이 "축이 이번 응답에 없다"라, 인자를 빠뜨린
 * 다음 소비자가 **조용히 거짓 문장으로 회귀**한다. 필수로 두면 tsc 가 잡는다.
 *
 * `axis` 의 사유를 `dep` 로만 쓰지 않는 이유: `dep` 는 "어떤 계측이 없는가"라는 **정적 속성**이고,
 * 화면이 묻는 건 "이번에 왜 안 돌았나"다. R12 는 현재 SQS 축 미배선을 `dep` 로 직접 말하고,
 * R17~R19 는 `dep: null` 이다. 뒤 세 규칙에 "사실 축 부재"를 쓰면 **자기 데이터와 모순된 문장**이
 * 선다. 그 규칙들이 못 도는 진짜 이유는 실시간 응답이 아직 없거나 실패한 것이고, 그건 `fetch` 가
 * 답한다.
 *
 * ⚠️ `note` 를 사유로 쓰지 않는다(`identity` 제외) — `evaluate` 에서 `note` 는
 * `collision ?? (evaluated ? R.note?.(f) ?? null : R.dep)` 라 **돈 규칙이면 리포트 주석**이
 * 실려 있다. 못 돈 규칙에서는 `dep` 과 같은 값이지만, 그걸 근거로 쓰면 `note` 의 세 갈래 중
 * 하나만 보고 단정하는 것이라 갈래가 늘 때 조용히 틀린다. 사유는 `RULES` 에서 직접 읽는다.
 */
export function notRunReason(r: RuleResult, fetch: AxisFetch): string {
  if (r.notRun === 'identity') return `응답 결함 — ${r.note ?? '사유 미기록'}`;
  const R = RULES.find((X) => X.id === r.id);
  if (R?.dep) return `못 돎 — ${R.dep}`;
  /* 조회 상태를 사유로 붙이는 건 **그 축을 읽는 규칙에만** 이다. `dep: null` 이라는 사실은
   * "이 규칙이 실시간 응답을 읽는다"를 함의하지 않는다 — R12 의 축은 SQS 라, 실시간 조회가
   * 실패한 날 R12 에 "조회 실패"라고 쓰면 이 함수가 없애려던 오독을 반대 방향으로 낸다. */
  if (R?.axis === 'minute') {
    if (fetch === 'pending') return '아직 못 돎 — 이 규칙이 읽을 응답이 도착하지 않았다';
    if (fetch === 'error') return '못 돎 — 이 규칙이 읽을 응답을 못 받았다(조회 실패)';
  }
  return '못 돎 — 이 규칙이 읽을 사실 축이 이번 응답에 없다';
}
