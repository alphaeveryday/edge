/* 실행: node --test src/report.test.ts */
import { strict as assert } from 'node:assert';
import test from 'node:test';
import { QUEUE, REPORTS } from './data/reports.ts';
import { DEMO_BASE, MIN, demoCandles, formatPp, hhmm, orderContributions, relative, sectorKind } from './report.ts';

test('차트 마지막 종가는 리포트 등락률과 같다 — 화면 상단 수치와 차트가 어긋나지 않는다', () => {
  for (const r of [...REPORTS, ...QUEUE]) {
    const cs = demoCandles(r.code, r.pct, DEMO_BASE);
    assert.ok(cs.length > 0, r.name);
    assert.ok(Math.abs(cs[cs.length - 1].c - r.pct) < 1e-9, `${r.name}: ${cs[cs.length - 1].c} ≠ ${r.pct}`);
    // 시드가 종목 코드라 다시 그려도 같은 모양이다(선택을 오갈 때 차트가 바뀌지 않는다).
    assert.deepEqual(demoCandles(r.code, r.pct, DEMO_BASE), cs);
  }
});

test('기여 종목은 ETF 방향과 같은 쪽이 먼저, 크기순이다', () => {
  const up = orderContributions(1.2, [['A', -0.07], ['B', 0.33], ['C', 0.92], ['D', 0.61]]);
  assert.deepEqual(up.lead.map((c) => c[0]), ['C', 'D', 'B']);
  assert.deepEqual(up.all.map((c) => c[0]), ['C', 'D', 'B', 'A']);

  const down = orderContributions(-3.65, [['A', 0.12], ['B', -0.88], ['C', -1.32]]);
  assert.deepEqual(down.lead.map((c) => c[0]), ['C', 'B']);
  // 반대 방향 종목은 접힌 TOP 3 에 끼지 않는다.
  assert.ok(!down.lead.some((c) => c[1] > 0));
});

test('표기 형식', () => {
  assert.equal(formatPp(0.92), '+0.92%p');
  assert.equal(formatPp(-0.07), '−0.07%p');
  assert.equal(hhmm(DEMO_BASE), '14:20');
  assert.equal(relative(DEMO_BASE, DEMO_BASE - 30_000), '방금');
  assert.equal(relative(DEMO_BASE, DEMO_BASE - 9 * MIN), '9분 전');
  assert.equal(relative(DEMO_BASE, DEMO_BASE - 85 * MIN), '1시간 25분 전');
});

test('데모 테마는 모두 업종 아이콘에 대응한다(기본 아이콘으로 빠지지 않는다)', () => {
  for (const r of [...REPORTS, ...QUEUE]) assert.notEqual(sectorKind(r.theme), 'etc', r.theme);
});
