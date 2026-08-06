/* 추이 그래프의 기하 — 값 배열 하나를 SVG 좌표로 (ALPHA-738).
 *
 * 판정·임계는 여기 없다(trendMetrics 소관). 이 모듈은 **그리는 일**만 한다 —
 * 중앙값·정상 범위·오늘 값이 모두 **같은 계열**에서 나오므로 그래프와 옆 숫자가 어긋날 수 없다.
 *
 * 지키는 선: 지표마다 자기 y 범위를 갖는다. 단위·규모가 다른 지표를 공통 축에 겹치면
 * 작은 쪽이 바닥에 붙어 아무것도 안 보인다.
 */

/** 짝수 개면 가운데 두 값의 평균 — R13 이 쓰는 중앙값과 같은 정의 */
export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 === 0 ? (s[mid - 1] + s[mid]) / 2 : s[mid];
}

/** 그래프 y 범위 — 계열과 기준선·정상 범위를 모두 담는다(잘리면 이탈이 안 보인다) */
export function extent(values: number[], extra: number[] = []): [number, number] {
  const all = [...values, ...extra];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  /* 완전히 평평하면 0 나눗셈이 된다 — 최소 폭을 준다 */
  if (hi === lo) return [lo - Math.max(1, Math.abs(lo) * 0.1), hi + Math.max(1, Math.abs(hi) * 0.1)];
  /* 여백을 넉넉히 — 오늘 점(r=5)이 경계에 닿으면 아래 숫자 줄과 붙어 보인다 */
  const pad = (hi - lo) * 0.14;
  return [lo - pad, hi + pad];
}

/** 계열을 0~1 좌표로 — SVG 는 이 값에 폭·높이만 곱한다. 마지막 점이 오늘이다 */
export function points(values: number[], range: [number, number]): { x: number; y: number }[] {
  const [lo, hi] = range;
  const span = values.length > 1 ? values.length - 1 : 1;
  return values.map((v, i) => ({ x: i / span, y: 1 - (v - lo) / (hi - lo) }));
}
