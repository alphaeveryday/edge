/* 부재 문장의 게이트 — `notRun.ts` (ALPHA-738).
 *
 * 지키는 의도: 이 판단들이 되돌아가면 화면이 **"모른다"를 "없다"로** 그린다. 그게 이 트랙에서
 * 라운드마다 재발한 결함이라, 문구가 아니라 **분기**를 변이 관점으로 못박는다.
 * (이 모듈이 `shared.tsx` 에 있던 동안은 JSX 때문에 `node --test` 가 import 조차 못 했다.)
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isKnownVid, notRunReason, unevaluatedFor, unevaluatedRules } from './notRun.ts';
import { RULES } from '../../rules/rules.ts';
import type { RuleResult } from '../../rules/types.ts';

const R = (id: string, o: Partial<RuleResult> = {}): RuleResult => ({
  id,
  name: id,
  layer: '런',
  evaluated: false,
  notRun: 'axis',
  violations: 0,
  depends_on_mock: false,
  note: null,
  ...o,
});

/* 실제 규칙 목록에 있는 id 를 쓴다 — `notRunReason` 이 `RULES` 에서 `dep` 를 읽으므로
 * 가짜 id 로 픽스처를 만들면 폴백만 밟고 진짜 분기를 한 번도 안 탄다. */
const ev = {
  rules: [
    R('R08'), //  dep 있음(계측 공백)
    R('R17'), //  dep 없음 + axis:'minute'(실시간 응답 축)
    R('R13', { notRun: 'identity', note: 'R13: 사건 식별자 충돌 R13:o.pub — …' }),
    R('R04', { evaluated: true, notRun: undefined, violations: 2 }),
    R('R12'), //  dep 없음 + 축 표기 없음(SQS) — 실시간 조회 상태를 붙이면 안 되는 규칙
  ],
};

test('못 돈 규칙은 종류를 안 가리고 전부 나온다 — `identity` 만 세면 계측 공백이 정상으로 읽힌다', () => {
  assert.deepEqual(unevaluatedRules(ev).map((r) => r.id), ['R08', 'R17', 'R13', 'R12']);
  /* 돈 규칙은 위반이 0이어도 여기 없다 — "봤고 괜찮다"와 "모른다"는 다른 칸이다 */
  assert.ok(!unevaluatedRules(ev).some((r) => r.id === 'R04'));
});

test('vid 로 규칙을 되찾는다 — 대상·범위에 콜론·@ 가 들어와도 첫 콜론이 경계다', () => {
  assert.equal(unevaluatedFor(ev, 'R17:news_minute/bigkinds@2026-08-03')?.id, 'R17');
  /* 대상 축 자체에 콜론이 있는 실측 형태(R04 는 런 키가 대상이다) */
  assert.equal(unevaluatedFor(ev, 'R08:etf_holdings')?.id, 'R08');
  /* 돈 규칙의 vid 는 못 찾는다 — 그래야 화면이 "해소"를 말할 수 있다 */
  assert.equal(unevaluatedFor(ev, 'R04:news:2026-08-03T15:30'), undefined);
});

test('접두사가 아니라 규칙 id 가 같아야 한다 — `R1` 이 `R10` 의 vid 를 먹으면 남의 사유를 그린다', () => {
  const two = { rules: [R('R1'), R('R10')] };
  assert.equal(unevaluatedFor(two, 'R10:x')?.id, 'R10');
  assert.equal(unevaluatedFor(two, 'R1:x')?.id, 'R1');
});

test('모르는 식별자는 "규칙이 돌았다"가 아니다 — 규칙을 지우면 옛 링크가 같은 모양이 된다', () => {
  assert.equal(isKnownVid('R17:news_minute/bigkinds@2026-08-03'), true);
  assert.equal(isKnownVid('R99:zzz'), false, '없는 규칙을 아는 것으로 읽었다');
  assert.equal(isKnownVid(''), false);
  /* 구분자가 없으면 vid 가 아니다 — 통째로 규칙 id 로 읽어 버리면 안 된다 */
  assert.equal(isKnownVid('R17'), false);
  /* 못 돈 규칙 조회도 같은 축이라 여기서 안 걸린다 */
  assert.equal(unevaluatedFor(ev, 'R99:zzz'), undefined);
});

test('못 돈 사유 — 계약 위반(응답 결함)과 계측 공백을 절대 같은 문장으로 내지 않는다', () => {
  const identity = notRunReason(ev.rules[2], 'loaded');
  const axis = notRunReason(ev.rules[0], 'loaded');
  assert.match(identity, /^응답 결함 — /);
  assert.match(identity, /사건 식별자 충돌/, '충돌 사유가 본문에 실려야 한다(호버로 숨기면 못 본다)');
  assert.match(axis, /^못 돎 — /);
  assert.ok(!axis.startsWith('응답 결함'), '두 분기가 뒤바뀌었다');
  /* dep 이 있는 규칙은 그 계측 이름이 사유다 */
  assert.match(axis, /DatasetContract actual_as_of writer/);
});

test('`dep` 이 없는 규칙에 "사실 축 부재"를 쓰지 않는다 — dep:null 은 "빠진 계측이 없다"는 뜻이다', () => {
  /* 응답이 아직·영영 안 온 것을 계측 공백으로 그리면 운영자가 **API 장애를 미배선으로** 읽는다.
   * R17 은 `dep: null`·`source: DB_LEDGER` 다 — 축은 원장에 있고, 없는 건 이번 응답이다. */
  const r17 = ev.rules[1];
  for (const s of [notRunReason(r17, 'loaded'), notRunReason(r17, 'pending'), notRunReason(r17, 'error')]) {
    assert.ok(!/사실 축 부재/.test(s), `계측 공백으로 단정했다: ${s}`);
  }
  assert.match(notRunReason(r17, 'pending'), /도착하지 않았다/);
  assert.match(notRunReason(r17, 'error'), /조회 실패/);
  /* 셋이 서로 다른 문장이어야 한다 — 하나로 뭉치면 갈라 낸 의미가 사라진다 */
  const kinds = new Set([r17, r17, r17].map((r, i) => notRunReason(r, (['loaded', 'pending', 'error'] as const)[i])));
  assert.equal(kinds.size, 3);
});

test('조회 상태는 **그 축을 읽는 규칙에만** 붙는다 — dep:null 이 "실시간을 읽는다"를 뜻하지 않는다', () => {
  /* R12 의 축은 SQS(`f.queues`)다. 실시간 조회가 실패한 날 R12 에 "조회 실패"라고 쓰면,
   * 이 함수가 없애려던 오독(장애↔미배선)을 **반대 방향으로** 낸다. */
  const r12 = ev.rules[4];
  for (const f of ['loaded', 'stale', 'pending', 'error'] as const) {
    assert.equal(
      notRunReason(r12, f),
      '못 돎 — 이 규칙이 읽을 사실 축이 이번 응답에 없다',
      `실시간 조회 상태(${f})가 남의 축 규칙에 새어 나갔다`,
    );
  }
  /* 같은 상태에서 R17 은 조회 상태를 받는다 — 축 표기가 갈라내는 것이 이 차이다 */
  assert.match(notRunReason(ev.rules[1], 'error'), /조회 실패/);
});

test('픽스처가 진짜 규칙 축을 밟는다 — 가짜 id 면 dep 분기를 한 번도 안 탄다', () => {
  for (const r of ev.rules) {
    assert.ok(RULES.some((X) => X.id === r.id), `${r.id} 가 RULES 에 없다 — 픽스처가 낡았다`);
  }
});

test('canRun 을 가진 규칙은 사유가 서로 갈린다 — 두 규칙이 같은 문장을 받으면 어느 축인지 못 읽는다', () => {
  /* A2 의 물음: "`canRun` 이 거짓인 **사유 문장**이 없다". 규칙마다 따로 문장을 손으로 다는 대신
   * **집합 불변식**으로 잡는다 — 손으로 유지되는 표기는 반드시 낡고, 새로 붙는 규칙은 아무도
   * 안 본다. 사유 없이 붙은 규칙은 폴백 문장을 받아 여기서 R12 와 충돌한다.
   *
   * `axis` 를 가진 규칙(R17~R19)은 제외한다 — 그쪽은 **같은 축·같은 조회 상태라 같은 문장이
   * 맞다**. 사유가 갈려야 하는 것은 축이 서로 다른 쪽이다. */
  const gated = RULES.filter((R) => R.canRun && !R.axis);
  assert.ok(gated.length >= 7, `canRun 규칙이 줄었다(${gated.length}) — 픽스처가 아니라 규칙이 낡았다`);
  const byReason = new Map<string, string[]>();
  for (const R of gated) {
    const why = notRunReason({ ...ev.rules[0], id: R.id, name: R.name }, 'loaded');
    byReason.set(why, [...(byReason.get(why) ?? []), R.id]);
  }
  const shared = [...byReason].filter(([, ids]) => ids.length > 1);
  assert.deepEqual(shared, [], `사유 문장이 겹친다 — ${JSON.stringify(shared)}`);

  /* 유일성만으로는 모자란다 — 두 규칙의 `dep` 를 **서로 바꿔도** 유일성은 유지된다. 각 규칙이
   * **자기** 사유를 받는지(남의 것이 아니라) 왕복으로 단언한다: 문장은 그 규칙의 `dep` 를 담아야
   * 한다. `notRunReason` 이 `RULES.find` 로 규칙을 되찾으므로 그 조회가 어긋나면 여기서 깨진다. */
  for (const R of gated) {
    if (!R.dep) continue;
    const why = notRunReason({ ...ev.rules[0], id: R.id, name: R.name }, 'loaded');
    assert.ok(why.includes(R.dep), `${R.id} 가 남의 사유를 받았다: ${why}`);
  }
});
