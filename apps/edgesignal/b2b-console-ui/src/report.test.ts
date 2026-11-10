/* 실행: node --test src/report.test.ts */
import { strict as assert } from 'node:assert';
import test from 'node:test';
import { QUEUE, REPORTS } from './data/reports.ts';
import { DEMO_BASE, MIN, formatPp, hhmm, orderContributions, relative, sectorKind } from './report.ts';

test('차트는 09:00~15:30 10분봉 40개이고 마지막 종가가 리포트 등락률과 같다 — 화면 상단 수치와 차트가 어긋나지 않는다', () => {
  for (const r of [...REPORTS, ...QUEUE]) {
    assert.equal(r.bars.length, 40, r.name);
    assert.equal(r.bars[r.bars.length - 1].c, r.pct, r.name);
    for (const b of r.bars) assert.ok(b.l <= Math.min(b.o, b.c) && b.h >= Math.max(b.o, b.c), `${r.name}: 고저가 시가·종가를 감싸지 않는다`);
  }
});

test('기여 종목 합은 ETF 등락과 크게 어긋나지 않는다 — 상위 종목만으로 등락을 넘지 않는다', () => {
  for (const r of [...REPORTS, ...QUEUE]) {
    const top = r.contrib.reduce((a, c) => a + c[1], 0);
    assert.ok(top <= Math.abs(r.pct) + 0.05, `${r.name}: 상위 기여 합 ${top} > 등락 ${r.pct}`);
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
  assert.equal(hhmm(DEMO_BASE), '15:40');
  assert.equal(relative(DEMO_BASE, DEMO_BASE - 30_000), '방금');
  assert.equal(relative(DEMO_BASE, DEMO_BASE - 9 * MIN), '9분 전');
  assert.equal(relative(DEMO_BASE, DEMO_BASE - 85 * MIN), '1시간 25분 전');
});

test('데모 테마는 모두 업종 아이콘에 대응한다(기본 아이콘으로 빠지지 않는다)', () => {
  for (const r of [...REPORTS, ...QUEUE]) assert.notEqual(sectorKind(r.theme), 'etc', r.theme);
});
