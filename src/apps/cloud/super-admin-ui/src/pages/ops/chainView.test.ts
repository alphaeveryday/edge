import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { intradayLostAtEntry, pendingTriggers, stagesBlind } from './chainView.ts';
import type { Facts } from '../../rules/types.ts';

const chain = (fired: number, observed: number | null): Facts['chain'] => ({
  feeds: [
    { id: 'feed.batch', label: '배치 트리거', v: 20, unit: 'ETF', src: 'price_movement_trigger' },
    { id: 'feed.intraday', label: '장중 트리거', v: fired, unit: '건', src: 'minute_price_trigger' },
  ],
  stages: [
    { id: 'c.obs', label: '관측', batch: 18, intraday: observed, src: 'etf_contribution_observation' },
    { id: 'c.route', label: '라우트', batch: 17, intraday: 0, src: 'explanation_route' },
  ],
});

test('발화가 있는데 관측이 0 이면 입구에서 사라진 것이다', () => {
  assert.equal(intradayLostAtEntry(chain(65, 0)), true);
});

test('🔴 장중 계보가 살아나면 문장이 사라진다 — 값이 정하지 축의 유무가 정하지 않는다', () => {
  /* 이 단언이 없으면 "축이 있으면 참"으로 되돌리는 변이가 전건 초록이다. 그 상태에서 화면은
   * 장중 관측이 실제로 생긴 날에도 "전량이 사라집니다"를 단정한다 — 응답이 하지 않은 관측이다. */
  assert.equal(intradayLostAtEntry(chain(65, 3)), false, '관측이 생겼는데 전량 유실이라 말한다');
});

test('발화가 0 인 날은 거짓이다 — 사라질 것이 없던 날과 막힌 날을 가른다', () => {
  assert.equal(intradayLostAtEntry(chain(0, 0)), false);
});

test('축이 없으면 아무 관측도 단정하지 않는다', () => {
  assert.equal(intradayLostAtEntry(undefined), false);
  /* 갈래가 빠진 응답(검증 경계가 막지만 타입상 가능)에서도 단정하지 않는다 */
  assert.equal(intradayLostAtEntry({ feeds: [], stages: [] }), false);
});

test('🔴 화면이 그 판정을 실제로 쓴다 — 순수 모듈로 내려도 소비 자리를 안 재면 무의미하다', () => {
  /* ⭐ 이 트랙이 반복해 겪은 자리다: 판정을 `.ts` 로 내리고 단언까지 썼는데 **화면이 다른
   * 조건을 쓰고 있었다**. 파일을 옮긴 것으로 끝내지 말고 소비 자리까지 따라와야 한다.
   * 주석은 걷고 잰다 — 결함을 설명하는 주석이 그 문장을 인용하는 순간 단언이 거기 걸린다. */
  const code = readFileSync(new URL('./ChainPage.tsx', import.meta.url), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '');
  assert.match(code, /intradayLostAtEntry\(q\.facts\.chain\)/);
  assert.doesNotMatch(code, /\{q\.facts\.chain && \(/, '축의 유무로 관측 문장을 그린다');
});

test('🔴 입구 단계가 관측 불가면 "전량이 사라졌다"고 말하지 않는다 — 그 0 은 셀 쪽이 없다는 뜻이다', () => {
  /* 새 분석 엔진의 요청이 아직 없던 날: 장중 트리거 256, 첫 단계 0(관측 불가). 0 만 보면 이 문장이 선다. */
  const blind = chain(256, 0);
  blind!.stages = blind!.stages.map((s) => ({ ...s, batch: 0, intraday: 0, blind: true }));
  assert.equal(intradayLostAtEntry(blind), false);
  assert.equal(stagesBlind(blind), true);
  /* 대조 — 같은 수인데 관측 가능하면 진짜 손실이다 */
  assert.equal(intradayLostAtEntry(chain(256, 0)), true);
  assert.equal(stagesBlind(chain(256, 0)), false);
  assert.equal(stagesBlind(undefined), false, '축이 없는 것은 관측 불가 표시가 아니다');
  /* 끝 단계 하나만 관측 불가인 사실(동봉 스냅샷의 온프렘 수신)은 이 사정이 아니다 — 앞 단계는
   * 실측이고 R10 도 돈다. 여기서 참이면 흐름 화면이 "관측부터 값이 없다"고 거짓 안내를 한다. */
  const tail = chain(65, 3);
  tail!.stages = [...tail!.stages, { id: 'c.onprem', label: '온프렘 수신', src: 'x', blind: true }];
  assert.equal(stagesBlind(tail), false);
});

test('🔴 아직 판정할 수 없는 트리거는 단계별 수를 더한 것이다 — 그리고 그게 전부면 "하나도 안 이어졌다"고 말하지 않는다', () => {
  /* 방금 발화한 날의 형상: 장중 트리거 5, 요청 0. 다섯 다 유예 안이면 요청이 수용되기 전일 수 있다. */
  const fresh = chain(5, 0);
  fresh!.stages[0].pending = 5;
  assert.equal(intradayLostAtEntry(fresh), false, '진행 중일 수 있는 건을 유실로 단정했다');
  assert.equal(pendingTriggers(fresh), 5);
  /* 판정 가능한 트리거가 하나라도 요청 없이 남으면 참이다 */
  fresh!.stages[0].pending = 4;
  assert.equal(intradayLostAtEntry(fresh), true);
  /* 단계별 수는 겹치지 않아 더한다. 수가 없는 응답은 0 이다 */
  fresh!.stages[1].pending = 3;
  assert.equal(pendingTriggers(fresh), 7);
  assert.equal(pendingTriggers(chain(5, 0)), 0);
  assert.equal(pendingTriggers(undefined), 0);
});
