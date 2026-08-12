/* 추이 그래프의 기하 (ALPHA-738).
 *
 * 지키는 의도: **그래프가 계열을 잘라 먹지 않는다.** 오늘 값이나 정상 범위가 범위 밖으로
 * 나가면 이탈이 화면에서 사라진다.
 *
 * 실행: node --test src/pages/ops/trendSeries.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { extent, median, points } from './trendSeries.ts';

test('중앙값은 짝수 개면 가운데 두 값의 평균이다 — R13 과 같은 정의', () => {
  assert.equal(median([1, 2, 3, 4]), 2.5);
  assert.equal(median([3, 1, 2]), 2);
  assert.equal(median([]), null);
});

test('y 범위가 계열과 기준선·정상 범위를 모두 담는다', () => {
  const values = [100, 110, 90];
  const band: [number, number] = [75, 125];
  const [lo, hi] = extent(values, band);
  assert.ok(lo <= Math.min(...values) && lo <= band[0], '아래로 안 잘린다');
  assert.ok(hi >= Math.max(...values) && hi >= band[1], '위로 안 잘린다');
});

test('평평한 계열도 0 으로 나누지 않는다', () => {
  const [lo, hi] = extent([5, 5, 5]);
  assert.ok(hi > lo);
  const [zlo, zhi] = extent([0, 0]);
  assert.ok(zhi > zlo, '0 만 있는 계열도 폭을 갖는다');
});

test('좌표는 0~1 로 정규화되고 마지막 점이 오늘이다', () => {
  const values = [10, 20, 30];
  const range = extent(values);
  const p = points(values, range);
  assert.equal(p.length, 3);
  assert.equal(p[0].x, 0);
  assert.equal(p[2].x, 1);
  for (const q of p) assert.ok(q.y >= 0 && q.y <= 1, `y 가 범위 안: ${q.y}`);
  /* 값이 클수록 y 가 작다(위로 간다) */
  assert.ok(p[2].y < p[0].y);
});

test('점이 하나여도 오늘은 오른쪽 끝이다 — 기준 없는 날의 계열이 정확히 한 점이다', () => {
  /* 서버가 기준(`base`)을 안 주는 날(휴장일)은 과거를 지어내지 않아 오늘 점만 남는다.
   * 왼쪽 끝에 두면 같은 "오늘"이 계열 길이에 따라 화면 반대편에 그려진다. */
  const p = points([7], extent([7]));
  assert.equal(p.length, 1);
  assert.equal(p[0].x, 1);
  assert.ok(p[0].y >= 0 && p[0].y <= 1, `y 가 범위 안: ${p[0].y}`);
});

