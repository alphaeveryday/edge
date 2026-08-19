import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseEntityResolutionTrend } from './entityResolutionTrend.ts';

const valid = {
  points: [
    { date: '2026-08-18', totalArguments: 10, resolvedArguments: 7, rate: 0.7 },
    { date: '2026-08-19', totalArguments: 0, resolvedArguments: 0, rate: null },
  ],
};

test('정상 점과 실제 0/0을 보존한다 — null을 0%로 강제하지 않는다', () => {
  assert.deepEqual(parseEntityResolutionTrend(valid, '2026-08-19'), valid);
});

test('손상된 컬렉션·비율·계수는 판정 전에 거부한다', () => {
  for (const bad of [
    { points: {} },
    { points: [{ ...valid.points[0], rate: '0.7' }] },
    { points: [{ ...valid.points[0], rate: 1.2 }] },
    { points: [{ ...valid.points[0], resolvedArguments: 11, rate: 1.1 }] },
    { points: [{ ...valid.points[0], totalArguments: 0, resolvedArguments: 0, rate: 0 }] },
  ]) {
    assert.throws(() => parseEntityResolutionTrend(bad), '거짓 정상·경보나 렌더 크래시로 흘리면 안 된다');
  }
});

test('날짜 축은 유효한 오름차순이며 조회 기준일을 넘지 않는다', () => {
  assert.throws(() => parseEntityResolutionTrend({ points: [{ ...valid.points[0], date: '2026-02-30' }] }));
  assert.throws(() => parseEntityResolutionTrend({ points: [...valid.points].reverse() }));
  assert.throws(() => parseEntityResolutionTrend(valid, '2026-08-18'));
});
