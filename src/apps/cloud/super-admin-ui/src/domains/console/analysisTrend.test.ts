import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseAnalysisTrend } from './analysisTrend.ts';

const point = { date: '2026-10-02', completed: 150, failed: 5, running: 2, completedEtfs: 148, failedEtfs: 3 };
const valid = () => ({
  from: '2026-10-01',
  to: '2026-10-03',
  kinds: [
    { kind: 'outlook', points: [{ ...point, date: '2026-10-01' }, point] },
    { kind: 'movement', points: [] },
  ],
});

test('유효한 분석 추이는 그대로 통과한다 — 행이 없던 종류의 빈 배열도 정상이다', () => {
  assert.deepEqual(parseAnalysisTrend(valid()), valid());
});

test('손상된 분석 추이는 화면으로 흘리지 않고 거부한다', () => {
  /* 통과시키면 카드가 NaN·음수를 값으로 그리거나, 실패 수와 끝나지 않은 수가 뒤바뀐 채 선다 */
  const broken: [string, (v: ReturnType<typeof valid>) => unknown][] = [
    ['객체 아님', () => null],
    ['kinds 없음', (v) => ({ from: v.from, to: v.to })],
    ['movement 계열 없음', (v) => ({ ...v, kinds: [v.kinds[0]] })],
    ['from 이 날짜 아님', (v) => ({ ...v, from: '2026-10-1' })],
    ['범위 뒤집힘', (v) => ({ ...v, from: '2026-10-04' })],
    ['음수 건수', (v) => { v.kinds[0].points[1].failed = -1; return v; }],
    ['running 누락', (v) => { delete (v.kinds[0].points[1] as { running?: number }).running; return v; }],
    ['범위 밖 날짜', (v) => { v.kinds[0].points[1].date = '2026-10-04'; return v; }],
    ['날짜 역순', (v) => { v.kinds[0].points.reverse(); return v; }],
    ['종 수가 건수보다 큼', (v) => { v.kinds[0].points[1].failedEtfs = 6; return v; }],
  ];
  for (const [name, mutate] of broken) {
    assert.throws(() => parseAnalysisTrend(mutate(valid())), `${name} 을 통과시켰다`);
  }
});
