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
import { buildSeries, businessDays } from './trendMetrics.ts';

/* 🔴 `businessDays` 가 주말 조회일을 건너뛰어, 토·일에 조회하면 **그 날 원장에서 온 실측값이
 * 금요일 날짜를 달고** 섰다(실측: 2026-08-15(토) → 마지막 점 2026-08-14).
 * `TrendPage` 는 "카드 날짜가 조회일과 다르면 그 지표는 응답 밖 축"이라는 규약을 화면에 쓰므로,
 * 실측이 정적 스냅샷으로 **출처가 뒤바뀐다**. `meta.today` 는 거래일 보장이 없고(`rules/types.ts`)
 * `?date=` 로 아무 날짜나 들어온다.
 * ⚠️ 기존 픽스처가 전부 평일이라 이 축이 **한 번도 안 재졌다** — 주말 날짜를 명시적으로 쓴다. */
test('🔴 주말에 조회해도 마지막 점은 조회일 그 자체다 — 실측이 전 영업일로 밀리면 출처가 뒤바뀐다', () => {
  for (const weekend of ['2026-08-15', '2026-08-16']) {
    const days = businessDays(weekend, 5);
    assert.equal(days.at(-1), weekend, `${weekend}: 조회일이 계열에서 사라졌다`);
    assert.equal(new Set(days).size, days.length, `${weekend}: 날짜가 중복됐다`);
    const last = buildSeries({
      today: 42, pin: 10, amplitude: 1, integer: true, min: 0, todayIsMock: false, endDate: weekend,
    }).at(-1)!;
    assert.equal(last.date, weekend, `${weekend}: 실측값이 남의 날짜를 달았다`);
    assert.equal(last.value, 42);
    assert.equal(last.isMock, false);
  }
  /* 과거 점은 여전히 영업일만 센다 — 주말을 계열에 끌어들이면 중앙값 표본이 오염된다 */
  const days = businessDays('2026-08-15', 4);
  assert.deepEqual(days, ['2026-08-12', '2026-08-13', '2026-08-14', '2026-08-15']);
  /* 평일 조회는 종전과 같다(회귀 대조군) */
  assert.deepEqual(businessDays('2026-08-14', 3), ['2026-08-12', '2026-08-13', '2026-08-14']);
});

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
