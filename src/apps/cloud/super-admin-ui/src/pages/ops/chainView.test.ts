import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { intradayLostAtEntry } from './chainView.ts';
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
