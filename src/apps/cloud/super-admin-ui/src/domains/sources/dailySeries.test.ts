/* 일별 계열의 규칙 (ALPHA-1289).
 *
 * 지키는 의도: 두 계열의 "없음"이 서로 반대라는 것. 산출은 결과에 없는 기준일이 **0**(실측)이고,
 * 1분 세션은 세션이 없는 날이 **빈 점**(모름)이다. 둘을 한 규칙으로 접으면 한쪽이 거짓이 된다.
 * 그리고 조회일 값이 산출 계열의 마지막 점이 되면 안 된다.
 *
 * 실행: node --test src/domains/sources/dailySeries.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ALL_DATASETS } from './datasetCatalog.ts';
import { analysisDailySeries, minuteDailySeries, outputSeries } from './dailySeries.ts';
import type { MinuteDailyDataset, MinuteDailyDay } from './types.ts';

const REALTIME = ALL_DATASETS.filter((d) => !d.inOpsGrid);
const only = (id: string) => REALTIME.filter((d) => d.id === id);

const windows = (o: Partial<NonNullable<MinuteDailyDataset['windows']>> = {}) => ({
  expected: 390, due: 0, claimed: 0, valid: 390, validEmpty: 0, incomplete: 0, missing: 0, invalid: 0, ...o,
});
const entry = (dataset: string, o: Partial<MinuteDailyDataset> = {}): MinuteDailyDataset => ({
  dataset, state: 'NORMAL', basis: 'kis 정상 종료', failedSessions: 0, totalSessions: 1, ...o,
});

test('🔴 산출 계열은 서버가 준 기준일 그대로다 — 조회일 값을 마지막 점으로 붙이지 않는다', () => {
  /* 조회일(today 3)은 아직 쌓이는 중인 값이다. 계열 끝에 서면 147 → 3 의 급락이 매일 그려진다. */
  const output = {
    id: 'o.doc', today: 3, base: null,
    history: [{ date: '2026-10-08', count: 140 }, { date: '2026-10-12', count: 147 }],
  };
  assert.deepEqual(outputSeries(output), { dates: ['2026-10-08', '2026-10-12'], values: [140, 147] });
});

test('🔴 산출의 0 은 0 으로 남는다 — 그날 산출이 없었다는 실측이지 빈 점이 아니다', () => {
  const series = outputSeries({ history: [{ date: '2026-10-08', count: 0 }, { date: '2026-10-12', count: 12 }] });
  assert.deepEqual(series?.values, [0, 12]);
});

test('🔴 history 가 없는 옛 응답은 계열이 없다 — 빈 목록(표본 없음)과 가른다', () => {
  assert.equal(outputSeries({}), null);
  assert.deepEqual(outputSeries({ history: [] }), { dates: [], values: [] });
});

test('1분 세션 계열은 유효 창을 VALID 와 VALID_EMPTY 의 합으로, 결손 창을 INCOMPLETE·MISSING·INVALID 의 합으로 센다', () => {
  const days: MinuteDailyDay[] = [{
    date: '2026-10-12',
    datasets: [entry('price_minute', {
      windows: windows({ expected: 780, due: 4, claimed: 1, valid: 700, validEmpty: 60, incomplete: 3, missing: 2, invalid: 1 }),
    })],
  }];
  const [series] = minuteDailySeries(days, only('price_minute'));
  /* 760 = 700 + 60, 6 = 3 + 2 + 1. 진행 중인 창(due·claimed)은 유효에도 결손에도 세지 않는다 */
  assert.deepEqual(series.points, [{ date: '2026-10-12', expected: 780, valid: 760, defects: 6 }]);
});

test('🔴 세션이 없는 날은 빈 점이다 — 0 창으로 그리면 "하루 종일 결손 없이 0 창"이 된다', () => {
  const days: MinuteDailyDay[] = [
    { date: '2026-10-10', datasets: [] },
    { date: '2026-10-11', datasets: [entry('news_minute', { windows: windows() })] },
    { date: '2026-10-12', datasets: [entry('price_minute', { windows: windows({ incomplete: 0 }) })] },
  ];
  const [series] = minuteDailySeries(days, only('price_minute'));
  assert.deepEqual(series.points, [
    { date: '2026-10-10', expected: null, valid: null, defects: null },
    /* 다른 데이터셋의 세션은 이 데이터셋의 값이 아니다 */
    { date: '2026-10-11', expected: null, valid: null, defects: null },
    /* 결손이 없는 날의 0 은 실측이라 0 이다 — 빈 점과 반대다 */
    { date: '2026-10-12', expected: 390, valid: 390, defects: 0 },
  ]);
});

test('🔴 창 수가 없는 항목은 빈 점이다 — 옛 응답과, 세션 없이 날짜 축 job 만으로 선 뉴스 항목', () => {
  const days: MinuteDailyDay[] = [
    /* 옛 API: windows 키가 아예 없다 */
    { date: '2026-10-11', datasets: [entry('news_minute')] },
    /* 새 API: 세션이 없어 서버가 null 을 준다 */
    { date: '2026-10-12', datasets: [entry('news_minute', { totalSessions: 0, state: 'WAITING', windows: null })] },
  ];
  assert.ok(!('windows' in days[0].datasets[0]), '전제: 첫 항목은 옛 응답 형상이다');
  const [series] = minuteDailySeries(days, only('news_minute'));
  assert.deepEqual(series.points.map((p) => [p.expected, p.valid, p.defects]), [
    [null, null, null],
    [null, null, null],
  ]);
});

test('행은 카탈로그의 1분 데이터셋 전부이고 점은 응답의 날짜 그대로다 — 날짜를 채우거나 고르지 않는다', () => {
  const days: MinuteDailyDay[] = [
    { date: '2026-10-09', datasets: [] },
    { date: '2026-10-12', datasets: [] },
  ];
  const result = minuteDailySeries(days, REALTIME);
  assert.deepEqual(result.map((s) => s.dataset.id), REALTIME.map((d) => d.id));
  assert.ok(result.length >= 5, `1분 데이터셋 ${result.length}종`);
  for (const s of result) assert.deepEqual(s.points.map((p) => p.date), ['2026-10-09', '2026-10-12'], s.dataset.id);
});

test('🔴 분석 추이는 행이 없던 날을 빈 점으로 둔다 — 0 건으로 채우지 않고, 행이 있던 날의 0 은 0 이다', () => {
  /* 가격변동 분석은 장이 열린 날에만 생기고, v2 표가 생기기 전 날짜에는 값이 없다. 그 날들을 0 으로
   * 그리면 "분석이 돌았는데 하나도 완료되지 않았다"가 된다. */
  const series = analysisDailySeries({
    from: '2026-09-30',
    to: '2026-10-03',
    kinds: [
      {
        kind: 'outlook',
        points: [
          { date: '2026-10-02', completed: 150, failed: 3, running: 0, completedEtfs: 148, failedEtfs: 3 },
          { date: '2026-10-03', completed: 0, failed: 0, running: 4, completedEtfs: 0, failedEtfs: 0 },
        ],
      },
      { kind: 'movement', points: [] },
    ],
  });
  assert.deepEqual(series.map((s) => s.kind), ['outlook', 'movement']);
  assert.deepEqual(series[0].points, [
    { date: '2026-09-30', completed: null, failed: null, running: null },
    { date: '2026-10-01', completed: null, failed: null, running: null },
    { date: '2026-10-02', completed: 150, failed: 3, running: 0 },
    /* 끝나지 않은 4건은 실패 칸에 더해지지 않는다 */
    { date: '2026-10-03', completed: 0, failed: 0, running: 4 },
  ]);
  /* 기간 내내 행이 없던 종류도 날짜만큼 빈 점을 갖는다 — 계열이 사라지지 않는다 */
  assert.deepEqual(series[1].points.map((p) => [p.date, p.completed]), [
    ['2026-09-30', null], ['2026-10-01', null], ['2026-10-02', null], ['2026-10-03', null],
  ]);
});
